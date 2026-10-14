"""Lab 9.1 core: measure the latency cost of different write concerns.

Used by both ``labs/lab9/lab_01_write_concern_latency.py`` (manual CLI) and the API's explicit
"run live benchmark" action, so there is one implementation of the measurement.

Nothing here runs at import time or at application start-up.

About ``w:all``
---------------
MongoDB has no ``w: "all"`` write concern. A string value is looked up as a *named tag mode* in the
replica-set configuration and the server rejects it ("No write concern mode named 'all' found").
Acknowledgement by every member is expressed with the number of data-bearing members instead
(``w: 3`` for a three-member set). :func:`build_configurations` derives that number from the live
replica-set configuration and records the mapping in the result so it is never silent.
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pymongo import ReadPreference, WriteConcern
from pymongo.errors import PyMongoError, WTimeoutError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATABASE = "cap_latency_test"
DEFAULT_DOCS = 1000
DEFAULT_WTIMEOUT_MS = 10_000
# Stop a configuration early when the cluster plainly cannot acknowledge it (e.g. a member is down and
# w equals the member count). Prevents "docs x wtimeout" of dead time; the abort is recorded.
MAX_CONSECUTIVE_FAILURES = 5


class ExperimentError(Exception):
    """Pre-flight failure: the experiment was not started."""


@dataclass(frozen=True)
class WriteConfig:
    key: str            # w1_j0, wmaj_j1, wall_j0 ...
    label: str          # human label, e.g. "w:majority, j:true"
    w: Any              # 1, "majority" or an int
    journal: bool
    requested: str      # what the assignment asked for ("w:all")
    mapping: str | None = None


def results_dir() -> Path:
    return Path(os.getenv("LAB9_RESULTS_DIR") or PROJECT_ROOT / "labs" / "lab9" / "results")


# ------------------------------------------------------------------ topology

def data_bearing_members(client) -> int:
    """Members that store data (arbiters are excluded: they cannot acknowledge a numeric w)."""
    config = client.admin.command("replSetGetConfig")["config"]
    return sum(1 for m in config.get("members", []) if not m.get("arbiterOnly", False))


def topology_snapshot(client) -> dict[str, Any]:
    status = client.admin.command("replSetGetStatus", read_preference=ReadPreference.PRIMARY_PREFERRED)
    members = [
        {"name": m.get("name"), "state": m.get("stateStr"), "health": m.get("health")}
        for m in status.get("members", [])
    ]
    primary = next((m["name"] for m in members if m["state"] == "PRIMARY"), None)
    return {"set": status.get("set"), "primary": primary, "members": members, "taken_at": _now()}


def build_configurations(member_count: int) -> list[WriteConfig]:
    all_note = (
        f"'w:all' is not a valid MongoDB write concern; it is expressed as w:{member_count} "
        f"(all {member_count} data-bearing members must acknowledge)."
    )
    configs: list[WriteConfig] = []
    for journal in (False, True):
        j = "true" if journal else "false"
        suffix = f"j{int(journal)}"
        configs.append(WriteConfig(f"w1_{suffix}", f"w:1, j:{j}", 1, journal, "w:1"))
        configs.append(WriteConfig(f"wmaj_{suffix}", f"w:majority, j:{j}", "majority", journal, "w:majority"))
        configs.append(WriteConfig(
            f"wall_{suffix}", f"w:all (w:{member_count}), j:{j}", member_count, journal, "w:all", all_note))
    # present in the order w:1, w:majority, w:all for j:false then j:true
    return sorted(configs, key=lambda c: (c.journal, ["w1", "wmaj", "wall"].index(c.key.split("_")[0])))


# --------------------------------------------------------------- measurement

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _percentile(sorted_ms: list[float], fraction: float) -> float:
    return sorted_ms[min(len(sorted_ms) - 1, int(len(sorted_ms) * fraction))]


def run_configuration(
    client,
    config: WriteConfig,
    docs: int,
    *,
    database: str,
    collection: str,
    wtimeout_ms: int = DEFAULT_WTIMEOUT_MS,
    drop_first: bool = False,
) -> dict[str, Any]:
    """Insert ``docs`` small documents with one write concern and measure each acknowledgement."""
    db = client[database]
    if drop_first:
        db.drop_collection(collection)
    col = db.get_collection(collection, write_concern=WriteConcern(w=config.w, j=config.journal, wtimeout=wtimeout_ms))

    latencies_ms: list[float] = []
    timeouts = errors = consecutive = 0
    error_samples: list[str] = []
    aborted = False
    started_at = _now()
    began = time.perf_counter()
    attempted = 0

    for i in range(docs):
        attempted += 1
        t0 = time.perf_counter()
        try:
            col.insert_one({"value": time.time_ns(), "label": config.key, "i": i})
        except WTimeoutError as exc:
            timeouts += 1
            consecutive += 1
            if len(error_samples) < 3:
                error_samples.append(f"write concern timeout: {exc}")
        except PyMongoError as exc:
            errors += 1
            consecutive += 1
            if len(error_samples) < 3:
                error_samples.append(f"{type(exc).__name__}: {exc}"[:240])
        else:
            consecutive = 0
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            aborted = True
            break

    total_ms = (time.perf_counter() - began) * 1000.0
    result: dict[str, Any] = {
        "key": config.key,
        "label": config.label,
        "requested": config.requested,
        "w": config.w,
        "journal": config.journal,
        "mapping": config.mapping,
        "collection": collection,
        "started_at": started_at,
        "documents_requested": docs,
        "documents_attempted": attempted,
        "acknowledged": len(latencies_ms),
        "timeouts": timeouts,
        "errors": errors,
        "aborted_early": aborted,
        "wtimeout_ms": wtimeout_ms,
        "total_elapsed_ms": round(total_ms, 3),
        "avg_ms_per_attempt": round(total_ms / attempted, 3) if attempted else None,
        "error_samples": error_samples,
    }
    if latencies_ms:
        ordered = sorted(latencies_ms)
        result.update({
            "avg_ms": round(statistics.fmean(ordered), 3),
            "p50_ms": round(_percentile(ordered, 0.50), 3),
            "p99_ms": round(_percentile(ordered, 0.99), 3),
            "min_ms": round(ordered[0], 3),
            "max_ms": round(ordered[-1], 3),
        })
    else:
        result.update({"avg_ms": None, "p50_ms": None, "p99_ms": None, "min_ms": None, "max_ms": None})
    result["status"] = (
        "ABORTED" if aborted
        else "COMPLETED" if len(latencies_ms) == docs
        else "PARTIAL" if latencies_ms
        else "FAILED"
    )
    return result


def run_experiment(
    client,
    *,
    docs: int = DEFAULT_DOCS,
    wtimeout_ms: int = DEFAULT_WTIMEOUT_MS,
    database: str = DEFAULT_DATABASE,
    replace_historical: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run every configuration once. Raises ExperimentError before writing anything if unsafe."""
    say = progress or (lambda _msg: None)
    try:
        client.admin.command("ping")
        before = topology_snapshot(client)
        members = data_bearing_members(client)
    except PyMongoError as exc:
        raise ExperimentError(f"Cannot read the replica-set state: {exc}") from exc
    if not before["primary"]:
        raise ExperimentError("The replica set has no PRIMARY; refusing to run the benchmark.")
    unhealthy = [m["name"] for m in before["members"] if m["health"] != 1]
    if unhealthy:
        raise ExperimentError(
            "Members not healthy: " + ", ".join(map(str, unhealthy))
            + ". Restore the replica set first (w:all cannot be acknowledged otherwise)."
        )

    configs = build_configurations(members)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = _now()
    rows: list[dict[str, Any]] = []
    for config in configs:
        if replace_historical:
            collection = f"data_{config.key}"        # the collections the "historical" UI view reads
        else:
            collection = f"run_{stamp}_{config.key}"  # new collections: historical data is untouched
        say(f"Running {config.label} ({docs} inserts, wtimeout {wtimeout_ms} ms) ...")
        row = run_configuration(
            client, config, docs, database=database, collection=collection,
            wtimeout_ms=wtimeout_ms, drop_first=replace_historical,
        )
        say(f"  {row['status']}: {row['acknowledged']}/{row['documents_attempted']} acknowledged, "
            f"avg {row['avg_ms']} ms, timeouts {row['timeouts']}, errors {row['errors']}")
        rows.append(row)

    after = topology_snapshot(client)
    return {
        "experiment": "lab-9.1-write-concern-latency",
        "run_id": stamp,
        "started_at": started_at,
        "finished_at": _now(),
        "database": database,
        "docs_per_configuration": docs,
        "wtimeout_ms": wtimeout_ms,
        "storage_mode": "replaced_historical_collections" if replace_historical else "new_run_collections",
        "replica_set": {"name": before["set"], "data_bearing_members": members,
                        "topology_before": before, "topology_after": after},
        "w_all_mapping": next((c.mapping for c in configs if c.mapping), None),
        "configurations": rows,
        "interpretation": interpret(rows),
    }


# ------------------------------------------------------------ presentation

def interpret(rows: list[dict[str, Any]]) -> list[str]:
    """Observations derived from the measured numbers only; no expected values are assumed."""
    ok = [r for r in rows if r.get("avg_ms") is not None]
    notes: list[str] = []
    if not ok:
        return ["No configuration produced an acknowledged write, so there is nothing to compare."]
    ranked = sorted(ok, key=lambda r: r["avg_ms"])
    notes.append("Fastest to slowest by average latency in this run: "
                 + " < ".join(f"{r['label']} ({r['avg_ms']:.2f} ms)" for r in ranked) + ".")
    baseline = next((r for r in ok if r["key"] == "w1_j0"), None)
    if baseline and baseline["avg_ms"]:
        for r in ranked:
            if r is not baseline:
                notes.append(f"{r['label']} averaged {r['avg_ms'] / baseline['avg_ms']:.2f}x the w:1, j:false average.")
    degraded = [r["label"] for r in rows if r["timeouts"] or r["errors"] or r["aborted_early"]]
    if degraded:
        notes.append("Not every write was acknowledged for: " + ", ".join(degraded)
                     + ". See the timeout/error counts; those writes are not included in the latency figures.")
    notes.append("These figures are measurements from this machine and this moment. Disk and journal "
                 "behaviour, container networking, replica-set state and background load all change them, "
                 "so a stronger write concern is not guaranteed to be slower in every run.")
    return notes


def format_table(result: dict[str, Any]) -> str:
    header = f"{'Configuration':<26}{'Ack':>10}{'T/O':>5}{'Err':>5}{'Total ms':>11}{'Avg ms':>9}{'P50 ms':>9}{'P99 ms':>9}  Status"
    lines = [header, "-" * len(header)]
    for r in result["configurations"]:
        fmt = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
        lines.append(
            f"{r['label']:<26}{str(r['acknowledged']) + '/' + str(r['documents_attempted']):>10}"
            f"{r['timeouts']:>5}{r['errors']:>5}{r['total_elapsed_ms']:>11.1f}"
            f"{fmt(r['avg_ms']):>9}{fmt(r['p50_ms']):>9}{fmt(r['p99_ms']):>9}  {r['status']}")
    return "\n".join(lines)


CSV_FIELDS = ["label", "requested", "w", "journal", "documents_attempted", "acknowledged", "timeouts", "errors",
              "aborted_early", "total_elapsed_ms", "avg_ms_per_attempt", "avg_ms", "p50_ms", "p99_ms", "status",
              "collection"]


def save_artifacts(result: dict[str, Any], directory: Path | None = None) -> dict[str, str]:
    """Write timestamped JSON + CSV and refresh ``lab_01_latest.json`` (atomically)."""
    out = directory or results_dir()
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"lab_01_{result['run_id']}"
    json_path, csv_path, latest = base.with_suffix(".json"), base.with_suffix(".csv"), out / "lab_01_latest.json"
    payload = json.dumps(result, indent=2, default=str)
    json_path.write_text(payload, encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["configurations"])
    tmp = latest.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, latest)
    return {"json": str(json_path), "csv": str(csv_path), "latest": str(latest)}


def list_saved_runs(limit: int = 5, directory: Path | None = None) -> list[dict[str, Any]]:
    """Summaries of previously saved CLI runs (newest first). Unreadable files are skipped."""
    out = directory or results_dir()
    runs: list[dict[str, Any]] = []
    for path in sorted(out.glob("lab_01_2*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runs.append({
            "file": path.name,
            "run_id": data.get("run_id"),
            "finished_at": data.get("finished_at"),
            "docs_per_configuration": data.get("docs_per_configuration"),
            "storage_mode": data.get("storage_mode"),
            "w_all_mapping": data.get("w_all_mapping"),
            "configurations": [
                {k: row.get(k) for k in ("label", "acknowledged", "documents_attempted", "timeouts", "errors",
                                         "total_elapsed_ms", "avg_ms", "p50_ms", "p99_ms", "status")}
                for row in data.get("configurations", [])
            ],
            "interpretation": data.get("interpretation", []),
        })
    return runs
