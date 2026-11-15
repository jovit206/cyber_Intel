"""Scheduled MongoDB backups, isolated restore verification, and timed benchmarks."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import schedule
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BACKUP_DIR = BASE_DIR / "backups"
LOG_PATH = BASE_DIR / "logs" / "backup.log"
BENCHMARK_PATH = BASE_DIR / "reports" / "backup_restore_benchmarks.csv"
OWNERSHIP_MARKER = ".chapter14-backup.json"
MARKER_FORMAT = "chapter14-backup-verification"
TEST_DATABASE_PREFIX = "chapter14_restore_test_"
TEST_COLLECTION = "backup_verification"
BENCHMARK_SIZES = (1_000, 10_000, 100_000)
RETENTION_DAYS = 7


@dataclass(frozen=True)
class BackupResult:
    directory: Path
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    success: bool
    return_code: int | None
    error: str | None = None


def configure_logging(log_path: Path = LOG_PATH) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("chapter14.backup")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    resolved_log_path = log_path.resolve()
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved_log_path:
            return logger
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def _safe_text(text: str, *sensitive_values: str) -> str:
    for value in sensitive_values:
        if value:
            text = text.replace(value, "<redacted MongoDB URI>")
    return text


def _without_uri_database(uri: str) -> str:
    parts = urlsplit(uri)
    if parts.path in ("", "/"):
        return uri
    return urlunsplit((parts.scheme, parts.netloc, "/", parts.query, parts.fragment))


def _with_uri_database_path(uri: str, source_uri: str) -> str:
    target = urlsplit(uri)
    source = urlsplit(source_uri)
    return urlunsplit((
        target.scheme,
        target.netloc,
        source.path or "/",
        target.query,
        target.fragment,
    ))


def _tool_command(
    tool: str,
    uri: str,
    backup_root: Path,
    backup_path: Path,
    *arguments: str,
) -> tuple[list[str], str]:
    targeted_dump = tool == "mongodump" and "--db" in arguments
    explicit_restore_namespace = tool == "mongorestore" and any(
        argument.startswith("--nsInclude") for argument in arguments
    )
    command_uri = _without_uri_database(uri) if targeted_dump or explicit_restore_namespace else uri
    executable = shutil.which(tool)
    if executable:
        return [executable, "--uri", command_uri, *arguments, str(backup_path)], command_uri

    docker = shutil.which("docker")
    image = os.getenv("MONGODB_TOOLS_DOCKER_IMAGE", "").strip()
    if not docker or not image:
        raise FileNotFoundError(
            f"{tool} was not found. Install MongoDB Database Tools or configure "
            "MONGODB_TOOLS_DOCKER_IMAGE for the Docker fallback."
        )
    network = os.getenv("MONGODB_TOOLS_DOCKER_NETWORK", "").strip()
    tools_uri = os.getenv("MONGODB_TOOLS_URI", command_uri).strip()
    if targeted_dump or explicit_restore_namespace:
        tools_uri = _without_uri_database(tools_uri)
    else:
        tools_uri = _with_uri_database_path(tools_uri, command_uri)
    root = backup_root.resolve()
    relative = backup_path.resolve().relative_to(root)
    command = [docker, "run", "--rm"]
    if network:
        command.extend(["--network", network])
    command.extend([
        "--volume",
        f"{root}:/chapter14-backups",
        image,
        tool,
        "--uri",
        tools_uri,
        *arguments,
        f"/chapter14-backups/{relative.as_posix()}",
    ])
    return command, tools_uri


def _new_backup_directory(backup_root: Path) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_root / f"chapter14_backup_{timestamp}"
    destination.mkdir()
    return destination


def _write_marker(
    backup_dir: Path,
    *,
    database: str | None = None,
    collection: str | None = None,
    status: str = "success",
) -> None:
    marker = {
        "format": MARKER_FORMAT,
        "version": 1,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": database,
        "collection": collection,
    }
    (backup_dir / OWNERSHIP_MARKER).write_text(
        json.dumps(marker, indent=2) + "\n",
        encoding="utf-8",
    )


def run_backup(
    uri: str,
    backup_root: Path = DEFAULT_BACKUP_DIR,
    *,
    database: str | None = None,
    collection: str | None = None,
    logger: logging.Logger | None = None,
    run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> BackupResult:
    if (database is None) != (collection is None):
        raise ValueError("Specify both database and collection, or neither.")
    if database and not re.fullmatch(r"[A-Za-z0-9_-]+", database):
        raise ValueError("Database name contains unsupported characters.")
    if collection and not re.fullmatch(r"[A-Za-z0-9_$-]+", collection):
        raise ValueError("Collection name contains unsupported characters.")

    log = logger or logging.getLogger("chapter14.backup")
    backup_dir = _new_backup_directory(backup_root)
    started_at = datetime.now(timezone.utc)
    started_clock = time.perf_counter()
    log.info("Backup started at %s.", started_at.isoformat())
    options: list[str] = []
    if database:
        options.extend(["--db", database, "--collection", collection or ""])
    options.append("--out")
    try:
        command, tool_uri = _tool_command(
            "mongodump",
            uri,
            backup_root,
            backup_dir,
            *options,
        )
        result = run_process(command, capture_output=True, text=True, check=False)
    except (OSError, ValueError) as error:
        completed_at = datetime.now(timezone.utc)
        duration = time.perf_counter() - started_clock
        _write_marker(backup_dir, database=database, collection=collection, status="failed")
        log.error("Backup failed (%s) after %.3f seconds.", type(error).__name__, duration)
        return BackupResult(backup_dir, started_at, completed_at, duration, False, None, str(error))

    completed_at = datetime.now(timezone.utc)
    duration = time.perf_counter() - started_clock
    if result.returncode == 0:
        _write_marker(backup_dir, database=database, collection=collection)
        log.info(
            "Backup completed at %s; duration %.3f seconds; status PASS.",
            completed_at.isoformat(),
            duration,
        )
        return BackupResult(backup_dir, started_at, completed_at, duration, True, 0)

    detail = _safe_text((result.stderr or result.stdout or "").strip(), uri, tool_uri)
    _write_marker(backup_dir, database=database, collection=collection, status="failed")
    log.error(
        "Backup failed at %s; duration %.3f seconds; return code %s; error: %s",
        completed_at.isoformat(),
        duration,
        result.returncode,
        detail[:2000] or "mongodump returned a non-zero status",
    )
    return BackupResult(
        backup_dir,
        started_at,
        completed_at,
        duration,
        False,
        result.returncode,
        detail,
    )


def cleanup_expired_backups(
    backup_root: Path = DEFAULT_BACKUP_DIR,
    *,
    now: datetime | None = None,
    retention_days: int = RETENTION_DAYS,
) -> list[Path]:
    """Remove only direct child directories with this script's valid ownership marker."""
    if retention_days <= 0:
        raise ValueError("Retention must be greater than zero days.")
    root = backup_root.resolve()
    if not root.is_dir():
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    removed: list[Path] = []
    for candidate in root.iterdir():
        if not candidate.is_dir() or not candidate.name.startswith("chapter14_backup_"):
            continue
        resolved = candidate.resolve()
        if resolved.parent != root:
            continue
        marker_path = candidate / OWNERSHIP_MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if (
                marker.get("format") != MARKER_FORMAT
                or marker.get("version") != 1
                or marker.get("status", "success") not in {"success", "failed"}
            ):
                continue
            created_at = datetime.fromisoformat(marker["created_at"])
            created_at = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
            if created_at < cutoff:
                shutil.rmtree(resolved)
                removed.append(resolved)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            logging.getLogger("chapter14.backup").warning(
                "Skipped a Chapter 14 backup directory with an unreadable ownership marker."
            )
            continue
    return removed


def _test_database_name(run_id: str, document_count: int) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Test run ID contains unsupported characters.")
    return f"{TEST_DATABASE_PREFIX}{run_id}_{document_count}"


def _create_test_documents(count: int, run_id: str, created_at: datetime) -> list[dict[str, Any]]:
    return [
        {
            "_id": index,
            "run_id": run_id,
            "created_at": created_at,
            "original_value": f"original-{run_id}-{index}",
            "mutable_value": f"original-{run_id}-{index}",
            "category": f"category-{index % 10}",
            "quantity": index % 100 + 1,
        }
        for index in range(count)
    ]


def _same_utc_datetime(actual: Any, expected: datetime) -> bool:
    if not isinstance(actual, datetime):
        return False
    actual_utc = actual.replace(tzinfo=timezone.utc) if actual.tzinfo is None else actual.astimezone(timezone.utc)
    return actual_utc == expected


def _restore(
    uri: str,
    backup_root: Path,
    backup_dir: Path,
    database: str,
    collection: str,
    *,
    run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, float, str | None]:
    expected_database = _test_database_name_from_namespace(database)
    if database != expected_database or collection != TEST_COLLECTION:
        raise ValueError("Refusing restore: target is not this script's dedicated test namespace.")
    started_clock = time.perf_counter()
    try:
        command, tool_uri = _tool_command(
            "mongorestore",
            uri,
            backup_root,
            backup_dir,
            "--drop",
            f"--nsInclude={database}.{collection}",
        )
        result = run_process(command, capture_output=True, text=True, check=False)
    except (OSError, ValueError) as error:
        return False, time.perf_counter() - started_clock, str(error)
    duration = time.perf_counter() - started_clock
    if result.returncode == 0:
        return True, duration, None
    detail = _safe_text((result.stderr or result.stdout or "").strip(), uri, tool_uri)
    return False, duration, detail[:2000] or "mongorestore returned a non-zero status"


def _test_database_name_from_namespace(database: str) -> str:
    if not database.startswith(TEST_DATABASE_PREFIX) or not re.fullmatch(r"[A-Za-z0-9_-]+", database):
        raise ValueError("Refusing restore outside the chapter14_restore_test_ database prefix.")
    return database


def run_restore_verification(
    uri: str,
    backup_root: Path,
    document_count: int = 1_000,
    *,
    run_id: str | None = None,
    logger: logging.Logger | None = None,
    run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    client_factory: Callable[..., MongoClient] = MongoClient,
) -> dict[str, Any]:
    if document_count <= 0:
        raise ValueError("Document count must be greater than zero.")
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    database_name = _test_database_name(run_id, document_count)
    log = logger or logging.getLogger("chapter14.backup")
    client = client_factory(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        if database_name in client.list_database_names():
            raise RuntimeError(
                f"Refusing to overwrite existing test database {database_name}; choose a new run ID."
            )
        collection = client[database_name][TEST_COLLECTION]
        created_at = datetime.now(timezone.utc)
        created_at = created_at.replace(microsecond=created_at.microsecond // 1000 * 1000)
        documents = _create_test_documents(document_count, run_id, created_at)
        collection.insert_many(documents, ordered=True)
        if collection.count_documents({}) != document_count:
            raise RuntimeError("Initial test-data count does not match the requested document count.")

        backup = run_backup(
            uri,
            backup_root,
            database=database_name,
            collection=TEST_COLLECTION,
            logger=log,
            run_process=run_process,
        )
        if not backup.success:
            raise RuntimeError(f"Backup failed with return code {backup.return_code}: {backup.error or ''}")

        collection.update_many(
            {},
            {"$set": {"mutable_value": f"modified-{run_id}", "modified": True}},
        )
        modified_count = collection.count_documents({"modified": True})
        if modified_count != document_count:
            raise RuntimeError("The test documents could not all be modified before restore.")

        restore_ok, restore_duration, restore_error = _restore(
            uri,
            backup_root,
            backup.directory,
            database_name,
            TEST_COLLECTION,
            run_process=run_process,
        )
        if not restore_ok:
            raise RuntimeError(f"Restore failed: {restore_error or 'unknown mongorestore error'}")

        restored = client[database_name][TEST_COLLECTION]
        actual_count = restored.count_documents({})
        sample_ids = sorted({0, document_count // 2, document_count - 1})
        field_checks = []
        for index in sample_ids:
            document = restored.find_one({"_id": index})
            field_checks.append(
                document is not None
                and document.get("run_id") == run_id
                and document.get("original_value") == f"original-{run_id}-{index}"
                and document.get("mutable_value") == f"original-{run_id}-{index}"
                and _same_utc_datetime(document.get("created_at"), created_at)
                and "modified" not in document
            )
        modified_remaining = restored.count_documents({"modified": True})
        intact_filter = {
            "run_id": run_id,
            "modified": {"$exists": False},
            "$expr": {
                "$and": [
                    {
                        "$eq": [
                            "$original_value",
                            {"$concat": [f"original-{run_id}-", {"$toString": "$_id"}]},
                        ]
                    },
                    {"$eq": ["$mutable_value", "$original_value"]},
                    {"$eq": ["$created_at", created_at]},
                ]
            },
        }
        field_integrity_documents = restored.count_documents(intact_filter)
        passed = (
            actual_count == document_count
            and field_integrity_documents == document_count
            and all(field_checks)
            and modified_remaining == 0
        )
        return {
            "database": database_name,
            "collection": TEST_COLLECTION,
            "document_count": document_count,
            "backup_directory": str(backup.directory),
            "backup_duration_seconds": backup.duration_seconds,
            "restore_duration_seconds": restore_duration,
            "verification_status": "PASS" if passed else "FAIL",
            "field_checks_passed": sum(field_checks),
            "field_checks_total": len(field_checks),
            "field_integrity_documents": field_integrity_documents,
            "modified_documents_remaining": modified_remaining,
            "backup_size_bytes": _directory_size(backup.directory),
        }
    finally:
        client.close()


def _directory_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def run_benchmarks(
    uri: str,
    backup_root: Path,
    report_path: Path = BENCHMARK_PATH,
    *,
    sizes: tuple[int, ...] = BENCHMARK_SIZES,
    logger: logging.Logger | None = None,
    run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    client_factory: Callable[..., MongoClient] = MongoClient,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    log = logger or logging.getLogger("chapter14.backup")
    run_prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for count in sizes:
        result = run_restore_verification(
            uri,
            backup_root,
            count,
            run_id=f"{run_prefix}_{count}",
            logger=log,
            run_process=run_process,
            client_factory=client_factory,
        )
        rows.append({
            "dataset_size_documents": count,
            "document_count": result["document_count"],
            "backup_size_bytes": result["backup_size_bytes"],
            "backup_duration_seconds": f"{result['backup_duration_seconds']:.6f}",
            "restore_duration_seconds": f"{result['restore_duration_seconds']:.6f}",
            "verification_status": result["verification_status"],
        })

    report_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "dataset_size_documents",
        "document_count",
        "backup_size_bytes",
        "backup_duration_seconds",
        "restore_duration_seconds",
        "verification_status",
    ]
    with report_path.open("w", newline="", encoding="utf-8") as report:
        writer = csv.DictWriter(report, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def register_hourly_backup(uri: str, backup_root: Path, logger: logging.Logger) -> None:
    def scheduled_backup() -> None:
        removed = cleanup_expired_backups(backup_root)
        if removed:
            logger.info("Removed %s expired Chapter 14 backup directories.", len(removed))
        run_backup(uri, backup_root, logger=logger)

    schedule.every().hour.do(scheduled_backup)


def _print_table(rows: list[dict[str, Any]]) -> None:
    columns = [
        ("dataset_size_documents", "Documents"),
        ("backup_size_bytes", "Backup bytes"),
        ("backup_duration_seconds", "Backup seconds"),
        ("restore_duration_seconds", "Restore seconds"),
        ("verification_status", "Verify"),
    ]
    widths = {
        key: max(len(title), *(len(str(row.get(key, ""))) for row in rows))
        for key, title in columns
    }
    header = " | ".join(f"{title:<{widths[key]}}" for key, title in columns)
    print(header)
    print("-+-".join("-" * widths[key] for key, _ in columns))
    for row in rows:
        print(" | ".join(f"{str(row.get(key, '')):<{widths[key]}}" for key, _ in columns))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=("backup", "verify-restore", "benchmark", "scheduler"),
        default="backup",
    )
    parser.add_argument("--uri", default=os.getenv("MONGO_URI", "").strip())
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--report", type=Path, default=BENCHMARK_PATH)
    args = parser.parse_args(argv)
    if not args.uri:
        parser.error("Pass --uri or set MONGO_URI in the environment.")
    logger = configure_logging()
    try:
        if args.action == "backup":
            result = run_backup(args.uri, args.backup_dir, logger=logger)
            print(f"{'PASS' if result.success else 'FAIL'} backup: {result.directory}")
            return 0 if result.success else 1
        if args.action == "verify-restore":
            result = run_restore_verification(args.uri, args.backup_dir, logger=logger)
            print(json.dumps(result, indent=2))
            return 0 if result["verification_status"] == "PASS" else 1
        if args.action == "benchmark":
            rows = run_benchmarks(args.uri, args.backup_dir, args.report, logger=logger)
            _print_table(rows)
            return 0 if all(row["verification_status"] == "PASS" for row in rows) else 1

        logger.info("Hourly backup scheduler started.")
        register_hourly_backup(args.uri, args.backup_dir, logger)
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Backup scheduler stopped by user.")
        return 0
    except Exception as error:
        logger.error("Backup workflow stopped after %s.", type(error).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
