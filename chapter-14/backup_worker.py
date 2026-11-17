"""Durable worker for authenticated Chapter 14 backup and restore-verification jobs."""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import schedule
from pymongo import MongoClient, ReturnDocument

import backup_verify

LOGGER = logging.getLogger("chapter14.worker")
WORKER_ID = "chapter14-backup-worker"
JOBS = "chapter14_backup_jobs"
RUNS = "chapter14_backup_runs"


def _write_audit(database, job: dict, outcome: str, details: dict) -> None:
    action = "ops.backup" if job["job_type"] == "backup" else "ops.verify"
    database.admin_audit_log.insert_one({
        "administrator_id": job.get("actor_user_id") or WORKER_ID,
        "actor_role": "admin" if job.get("actor_user_id") else "system",
        "action": action,
        "target_record": job["job_id"],
        "created_at": datetime.now(timezone.utc),
        "outcome": outcome,
        "details": details,
    })


def _record_backup(database, job: dict, result: dict) -> None:
    backup_path = result.get("backup_directory")
    row = {
        "backup_id": job["job_id"],
        "job_id": job["job_id"],
        "kind": job["job_type"],
        "status": "success" if result.get("success") or result.get("verification_status") == "PASS" else "failed",
        "created_at": job.get("started_at") or datetime.now(timezone.utc),
        "completed_at": datetime.now(timezone.utc),
        "backup_path": backup_path,
        "backup_size_bytes": result.get("backup_size_bytes"),
        "backup_duration_seconds": result.get("backup_duration_seconds"),
        "restore_duration_seconds": result.get("restore_duration_seconds"),
        "document_count": result.get("document_count"),
        "verification_status": result.get("verification_status"),
        "oplog_enabled": bool(result.get("oplog_enabled", False)),
        "source_database": "full_replica_set" if job["job_type"] == "backup" else os.getenv("MONGO_DATABASE", "chapter07"),
        "actor_user_id": job.get("actor_user_id"),
    }
    database[RUNS].insert_one(row)
    safe_result = {key: value for key, value in row.items() if key not in {"_id", "backup_path", "actor_user_id"}}
    _write_audit(database, job, "succeeded" if row["status"] == "success" else "failed", safe_result)


def process_job(database, job: dict, uri: str, backup_root: Path) -> None:
    now = datetime.now(timezone.utc)
    try:
        if job["job_type"] == "backup":
            result = backup_verify.run_backup(
                uri,
                backup_root,
                # --oplog requires a full replica-set dump; a --db filtered dump is not a
                # consistent point-in-time backup according to MongoDB Database Tools.
                oplog=True,
                logger=logging.getLogger("chapter14.backup"),
            )
            result_data = {
                "success": result.success,
                "backup_directory": str(result.directory),
                "backup_size_bytes": backup_verify._directory_size(result.directory),
                "backup_duration_seconds": result.duration_seconds,
                "verification_status": "PASS" if result.success else "FAIL",
                "oplog_enabled": True,
                "error_type": "ToolFailure" if not result.success else None,
            }
        elif job["job_type"] == "verify":
            result_data = backup_verify.run_restore_verification(
                uri,
                backup_root,
                document_count=int(job.get("document_count", 1000)),
                logger=logging.getLogger("chapter14.backup"),
            )
            _record_backup(database, job, result_data)
        else:
            raise ValueError("Unknown Chapter 14 job type.")

        status = "success" if result_data.get("success") or result_data.get("verification_status") == "PASS" else "failed"
        if job["job_type"] == "backup":
            _record_backup(database, job, result_data)
        database[JOBS].update_one({"job_id": job["job_id"]}, {"$set": {
            "status": status,
            "completed_at": datetime.now(timezone.utc),
            "result": {key: value for key, value in result_data.items() if key not in {"backup_directory"}},
            "error_type": result_data.get("error_type"),
        }})
    except Exception as error:
        LOGGER.error("Backup job %s failed with %s", job.get("job_id"), type(error).__name__)
        details = {"job_type": job.get("job_type"), "error_type": type(error).__name__}
        try:
            _write_audit(database, job, "failed", details)
        except Exception:
            LOGGER.exception("Chapter 14 failure audit write failed")
        database[JOBS].update_one({"job_id": job["job_id"]}, {"$set": {
            "status": "failed", "completed_at": datetime.now(timezone.utc),
            "result": None, "error_type": type(error).__name__,
        }})


def _queue_hourly_backup(database) -> None:
    active = database[JOBS].find_one({
        "job_type": "backup", "scheduled": True, "status": {"$in": ["queued", "running"]},
    })
    if active:
        return
    now = datetime.now(timezone.utc)
    database[JOBS].insert_one({
        "job_id": f"scheduled-{now.strftime('%Y%m%dT%H%M%S')}", "job_type": "backup",
        "status": "queued", "actor_user_id": None, "scheduled": True,
        "created_at": now, "started_at": None, "completed_at": None,
        "document_count": 1000, "result": None, "error_type": None,
    })


def _claim(database):
    return database[JOBS].find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc), "worker_id": WORKER_ID}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def _heartbeat(database) -> None:
    database["chapter14_backup_worker_status"].update_one(
        {"worker_id": WORKER_ID},
        {"$set": {"status": "healthy", "last_seen_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def run_worker(uri: str, database_name: str, backup_root: Path) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    database = client[database_name]
    client.admin.command("ping")
    schedule.every().hour.do(_queue_hourly_backup, database)
    schedule.every().day.at("00:15").do(backup_verify.cleanup_expired_backups, backup_root)
    LOGGER.info("Chapter 14 backup worker started; hourly backups will be queued.")
    try:
        while True:
            schedule.run_pending()
            _heartbeat(database)
            job = _claim(database)
            if job:
                process_job(database, job, uri, backup_root)
            time.sleep(2)
    except KeyboardInterrupt:
        LOGGER.info("Chapter 14 backup worker stopped.")
    finally:
        database["chapter14_backup_worker_status"].update_one(
            {"worker_id": WORKER_ID}, {"$set": {"status": "stopped", "last_seen_at": datetime.now(timezone.utc)}}
        )
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args(argv)
    uri = os.getenv("MONGO_URI", "").strip()
    database_name = os.getenv("MONGO_DATABASE", "chapter07").strip()
    backup_root = Path(os.getenv("CHAPTER14_BACKUP_DIR", str(Path(__file__).resolve().parent / "backups")))
    if not uri:
        parser.error("MONGO_URI must be configured.")
    if args.health_check:
        with MongoClient(uri, serverSelectionTimeoutMS=2500) as client:
            client.admin.command("ping")
        return 0
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    run_worker(uri, database_name, backup_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
