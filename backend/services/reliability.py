"""Write-concern latency and network-partition reporting for the reliability views.

Everything here is read-only with respect to cluster topology and data, except
``run_live_benchmark``, which inserts into scratch ``live_benchmark_*`` collections
and is only reachable through an explicit, confirmed POST.

Nothing in this module executes the partition simulation script, touches Docker,
or changes the replica set configuration. The script is only *described* (and its
text scanned) so the UI can show how to run it manually.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId, Timestamp
from pymongo import ReadPreference
from pymongo.errors import PyMongoError

from backend.services import write_concern as wc_core

logger = logging.getLogger("aegis.reliability")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAB9_DIR = PROJECT_ROOT / "labs" / "lab9"
PARTITION_SCRIPT = LAB9_DIR / "lab_02_simulate_partition.py"
PARTITION_RESULTS_FILE = LAB9_DIR / "lab_02_partition_results.json"
PARTITION_SCRIPT_RELATIVE = "labs/lab9/lab_02_simulate_partition.py"

LATENCY_DATABASE = "cap_latency_test"
PARTITION_DATABASE = "lab9_partition"
# Name of the docker network the manual script operates on. Informational only:
# the backend cannot probe docker, so this is reported as configured, not verified.
MONGO_NETWORK = "mongo-net"

MIN_BENCHMARK_DOCS = 10
MAX_BENCHMARK_DOCS = 500

# key -> (w, j, label, description). Historical data lives in ``data_<key>``
# (written by labs/lab9/lab_01_write_concern_latency.py); live runs write to
# ``live_benchmark_<key>``.
LATENCY_CONFIGS: list[dict[str, Any]] = [
    {"key": "w1_j0", "wc": 1, "journal": False, "label": "w:1, j:false", "desc": "Local primary, in-memory ack"},
    {"key": "w1_j1", "wc": 1, "journal": True, "label": "w:1, j:true", "desc": "Local primary, on-disk journal ack"},
    {"key": "wmaj_j0", "wc": "majority", "journal": False, "label": "w:majority, j:false", "desc": "Replica set majority, memory ack"},
    {"key": "wmaj_j1", "wc": "majority", "journal": True, "label": "w:majority, j:true", "desc": "Replica set majority, journal ack"},
    {"key": "wall_j0", "wc": "all", "journal": False, "label": "w:all, j:false", "desc": "Every data-bearing member (numeric w = member count), memory ack"},
    {"key": "wall_j1", "wc": "all", "journal": True, "label": "w:all, j:true", "desc": "Every data-bearing member (numeric w = member count), journal ack"},
]

KEY_TAKEAWAYS = [
    "w:1 waits for acknowledgement from the primary only.",
    "w:majority requires acknowledgement from a majority (2 of 3 members).",
    "j:true requests on-disk journal durability acknowledgement.",
    "Higher durability (w:majority and j:true) involves network trips and disk fsync, increasing latency.",
]


class ReliabilityError(Exception):
    """Raised for conditions the API layer should report as 503/400 with a readable message."""

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------- formatting

def sanitize(value: Any) -> Any:
    """Convert BSON / non-JSON types into JSON-serialisable values."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Timestamp):
        return {"time": value.time, "inc": value.inc}
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(v) for v in value]
    return value


def format_ms(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f} ms"


def format_uptime(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "N/A"
    seconds = int(seconds)
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m {seconds % 60}s"


def _percentile(sorted_values: list[float], fraction: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * fraction))]


def _stats(values_ms: list[float]) -> dict[str, float]:
    ordered = sorted(values_ms)
    return {
        "avg_ms": round(sum(ordered) / len(ordered), 3),
        "p50_ms": round(ordered[len(ordered) // 2], 3),
        "p99_ms": round(_percentile(ordered, 0.99), 3),
    }


def _display_fields(entry: dict[str, Any]) -> dict[str, Any]:
    entry["operations_display"] = f"{int(entry.get('operation_count') or 0):,}"
    entry["avg_display"] = format_ms(entry.get("avg_ms"))
    entry["p50_display"] = format_ms(entry.get("p50_ms"))
    entry["p99_display"] = format_ms(entry.get("p99_ms"))
    return entry


# ------------------------------------------------------------------- latency

def interval_latencies_ms(docs: list[dict[str, Any]]) -> list[float]:
    """Intervals between consecutive client-side ``time.time_ns()`` stamps, in ms.

    The lab 9.1 script stamps each document just before inserting it, so the gap
    between consecutive stamps approximates per-insert latency.
    """
    stamps = sorted(
        d["value"] for d in docs
        if isinstance(d.get("value"), (int, float)) and not isinstance(d.get("value"), bool)
    )
    return [(b - a) / 1_000_000.0 for a, b in zip(stamps, stamps[1:])]


def _latency_highlights(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {c["key"]: c for c in configs if c.get("avg_ms") is not None}
    highlights: list[dict[str, Any]] = []
    if "w1_j0" in by_key:
        highlights.append({
            "key": "fastest", "title": "Fastest (w:1, j:false)",
            "value": format_ms(by_key["w1_j0"]["avg_ms"]), "detail": "Primary in-memory acknowledgement",
        })
    if "w1_j1" in by_key:
        highlights.append({
            "key": "journaled", "title": "Journaled (w:1, j:true)",
            "value": format_ms(by_key["w1_j1"]["avg_ms"]), "detail": "Local disk fsync latency",
        })
    majority = sorted(by_key[k]["avg_ms"] for k in ("wmaj_j0", "wmaj_j1") if k in by_key)
    if majority:
        low, high = format_ms(majority[0]), format_ms(majority[-1])
        highlights.append({
            "key": "majority", "title": "Majority (w:majority)",
            "value": low if low == high else f"{low} – {high}",
            "detail": "Network round trip to secondaries",
        })
    return highlights


def latency_report(client) -> dict[str, Any]:
    """Historical write-concern benchmark recorded by lab_01_write_concern_latency.py."""
    test_db = client[LATENCY_DATABASE]
    results: list[dict[str, Any]] = []
    has_data = False

    for cfg in LATENCY_CONFIGS:
        collection = f"data_{cfg['key']}"
        entry: dict[str, Any] = {
            "collection": collection, "key": cfg["key"], "wc": cfg["wc"], "journal": cfg["journal"],
            "label": cfg["label"], "desc": cfg["desc"],
            "operation_count": 0, "avg_ms": None, "p50_ms": None, "p99_ms": None, "timestamp": None,
        }
        try:
            total = test_db[collection].count_documents({})
            entry["operation_count"] = total
            if total == 0:
                entry["status"] = "UNAVAILABLE"
            else:
                has_data = True
                docs = list(test_db[collection].find({}, {"_id": 1, "value": 1}).sort("value", 1))
                intervals = interval_latencies_ms(docs)
                if intervals:
                    entry.update(_stats(intervals))
                    entry["status"] = "RECORDED_HISTORICAL"
                    first_id = docs[0].get("_id")
                    if isinstance(first_id, ObjectId):
                        entry["timestamp"] = first_id.generation_time.isoformat()
                else:
                    entry["status"] = "DOCUMENTS_PRESENT_INSUFFICIENT_TIMESTAMPS"
        except PyMongoError as exc:
            logger.warning("Error reading %s.%s: %s", LATENCY_DATABASE, collection, exc)
            entry["status"] = "ERROR"
            entry["error"] = str(exc)
        results.append(_display_fields(entry))

    return {
        "source": "HISTORICAL" if has_data else "UNAVAILABLE",
        "label": "HISTORICAL WRITE CONCERN BENCHMARK",
        "has_data": has_data,
        "database": LATENCY_DATABASE,
        "method": "Interval between consecutive client-side insert timestamps",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "highlights": _latency_highlights(results),
        "configurations": results,
        "saved_runs": wc_core.list_saved_runs(),
        "w_all_note": (
            "w:all is not a valid MongoDB write concern value. It is run as a numeric w equal to the number "
            "of data-bearing members (w:3 for this three-member replica set)."
        ),
        "key_takeaways": KEY_TAKEAWAYS,
    }


def run_live_benchmark(client, docs_per_config: int) -> dict[str, Any]:
    """Run the write-concern benchmark on demand. Caller must have confirmed intent."""
    readiness = replica_set_readiness(client)
    if readiness.get("error") or not readiness.get("detected_primary"):
        raise ReliabilityError(
            "Replica set has no reachable PRIMARY; live benchmark not started"
            + (f" ({readiness['error']})" if readiness.get("error") else "."),
            503,
        )

    doc_count = max(MIN_BENCHMARK_DOCS, min(int(docs_per_config), MAX_BENCHMARK_DOCS))
    run_timestamp = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    try:
        members = wc_core.data_bearing_members(client)
        for cfg in wc_core.build_configurations(members):
            row = wc_core.run_configuration(
                client, cfg, doc_count, database=LATENCY_DATABASE,
                collection=f"live_benchmark_{cfg.key}", wtimeout_ms=5000, drop_first=True,
            )
            results.append(_display_fields({
                "label": cfg.label, "desc": cfg.mapping or "", "wc": cfg.w, "journal": cfg.journal,
                "requested": cfg.requested,
                "operation_count": row["acknowledged"], "documents_attempted": row["documents_attempted"],
                "avg_ms": row["avg_ms"], "p50_ms": row["p50_ms"], "p99_ms": row["p99_ms"],
                "timeouts": row["timeouts"], "errors": row["errors"], "total_ms": row["total_elapsed_ms"],
                "aborted_early": row["aborted_early"], "status": row["status"],
                "error_samples": row["error_samples"], "timestamp": run_timestamp,
            }))
    except PyMongoError as exc:
        logger.warning("Live benchmark failed: %s", exc)
        raise ReliabilityError(f"Live benchmark failed while writing to {LATENCY_DATABASE}: {exc}", 502) from exc

    return {
        "source": "LIVE",
        "label": "LIVE WRITE CONCERN BENCHMARK RESULTS",
        "executed_at": run_timestamp,
        "docs_per_config": doc_count,
        "results": results,
    }


# --------------------------------------------------------- replica-set health

def replica_set_readiness(client) -> dict[str, Any]:
    """Live replica-set topology and health. Never raises; failures go in ``error``."""
    readiness: dict[str, Any] = {
        "source": "LIVE",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "set_name": None,
        "detected_primary": None,
        "detected_secondaries": [],
        "eligible_partition_target": None,
        "healthy": False,
        "members": [],
        "member_count": 0,
        "mongo_net_network": MONGO_NETWORK,
        "warnings": [],
        "error": None,
    }
    try:
        status = client.admin.command("replSetGetStatus", read_preference=ReadPreference.PRIMARY_PREFERRED)
    except PyMongoError as exc:
        logger.warning("replSetGetStatus failed: %s", exc)
        readiness["error"] = str(exc)
        return readiness

    members = []
    for m in status.get("members", []):
        members.append({
            "name": m.get("name"),
            "state_str": m.get("stateStr"),
            "health": m.get("health"),
            "uptime_seconds": m.get("uptime"),
            "uptime_display": format_uptime(m.get("uptime")),
            "sync_source": m.get("syncSourceHost") or None,
            "ping_ms": m.get("pingMs"),
            "is_self": bool(m.get("self", False)),
        })

    primaries = [m["name"] for m in members if m["state_str"] == "PRIMARY"]
    secondaries = [m for m in members if m["state_str"] == "SECONDARY"]
    unhealthy = [m["name"] for m in members if m["health"] != 1]

    readiness.update({
        "set_name": status.get("set"),
        "detected_primary": primaries[0] if primaries else None,
        "detected_secondaries": [m["name"] for m in secondaries],
        "eligible_partition_target": next((m["name"] for m in secondaries if m["health"] == 1), None),
        "members": members,
        "member_count": len(members),
        "healthy": bool(members) and len(primaries) == 1 and not unhealthy,
    })
    if not primaries:
        readiness["warnings"].append("No PRIMARY detected (election may be in progress).")
    if len(primaries) > 1:
        readiness["warnings"].append("Multiple members report PRIMARY.")
    if unhealthy:
        readiness["warnings"].append("Unhealthy members: " + ", ".join(str(n) for n in unhealthy))
    return readiness


# ----------------------------------------------------------------- partition

def _collection_summary(db, name: str) -> dict[str, Any]:
    """Count plus first/last ``createdAt`` for one experiment collection."""
    summary: dict[str, Any] = {"document_count": 0, "first_timestamp": None, "last_timestamp": None, "error": None}
    try:
        summary["document_count"] = db[name].count_documents({})
        if summary["document_count"] > 0:
            first = db[name].find_one({"createdAt": {"$exists": True}}, {"createdAt": 1}, sort=[("createdAt", 1)])
            last = db[name].find_one({"createdAt": {"$exists": True}}, {"createdAt": 1}, sort=[("createdAt", -1)])
            summary["first_timestamp"] = sanitize(first.get("createdAt")) if first else None
            summary["last_timestamp"] = sanitize(last.get("createdAt")) if last else None
    except PyMongoError as exc:
        logger.warning("Error reading %s.%s: %s", PARTITION_DATABASE, name, exc)
        summary["error"] = str(exc)
    return summary


def _load_saved_log() -> dict[str, Any] | None:
    """Newest saved Lab 9.2 report (written by the manual script), or the legacy single-file summary."""
    for candidate in (wc_core.results_dir() / "lab_02_partition_latest.json", PARTITION_RESULTS_FILE):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def simulation_info() -> dict[str, Any]:
    """Describe the manual partition script. Never executes it."""
    info: dict[str, Any] = {
        "automatic_execution": False,
        "script_path": PARTITION_SCRIPT_RELATIVE,
        "script_present": PARTITION_SCRIPT.is_file(),
        "dry_run_command": f"python {PARTITION_SCRIPT_RELATIVE} --dry-run",
        "manual_command": f"python {PARTITION_SCRIPT_RELATIVE}",
        "run_from": "A host shell with Python 3.8+ and the docker CLI. It cannot run inside the backend container.",
        "static_checks": None,
    }
    try:
        text = PARTITION_SCRIPT.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    info["static_checks"] = {
        "method": "Text scan of the script; the script was not executed.",
        "dynamic_primary_detection": "def primary_of" in text and "rs.status()" in text,
        "never_isolates_primary": "is the current PRIMARY" in text,
        "requires_typed_confirmation": "def required_confirmation" in text,
        # a try/finally that reconnects the member also on error, Ctrl+C and SIGTERM
        "network_restore_trap": "def reconnect_all" in text and bool(re.search(r"^\s*finally:", text, re.M)),
        "preserves_network_aliases": "--alias" in text,
        "supports_dry_run": "--dry-run" in text,
    }
    return info


def partition_report(client) -> dict[str, Any]:
    """Historical partition-experiment records plus live replica-set readiness."""
    part_db = client[PARTITION_DATABASE]
    baseline = _collection_summary(part_db, "baseline")
    part_w1 = _collection_summary(part_db, "partition_w1")
    part_maj = _collection_summary(part_db, "partition_majority")

    def status(summary: dict[str, Any]) -> str:
        if summary["error"]:
            return "ERROR"
        return "RECORDS_PRESENT" if summary["document_count"] > 0 else "NO_RECORDS"

    return {
        "source": "HISTORICAL",
        "has_historical_data": baseline["document_count"] > 0 or part_w1["document_count"] > 0,
        "database": PARTITION_DATABASE,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "baseline_writes": {
            "write_concern": "w:1",
            "document_count": baseline["document_count"],
            "status": status(baseline),
            "first_timestamp": baseline["first_timestamp"],
            "last_timestamp": baseline["last_timestamp"],
            "error": baseline["error"],
            "note": "Cumulative across all recorded runs; each run inserts 100 baseline documents before isolation.",
        },
        "partition_w1_writes": {
            "write_concern": "w:1",
            "document_count": part_w1["document_count"],
            "status": status(part_w1),
            "timestamp": part_w1["first_timestamp"],
            "last_timestamp": part_w1["last_timestamp"],
            "error": part_w1["error"],
            "explanation": "w:1 writes succeed immediately on the primary because secondary acknowledgement is not required.",
        },
        "partition_majority_writes": {
            "write_concern": "w:majority",
            "document_count": part_maj["document_count"],
            "status": status(part_maj),
            "timestamp": part_maj["first_timestamp"],
            "last_timestamp": part_maj["last_timestamp"],
            "error": part_maj["error"],
            "explanation": (
                "In a 3-node set, majority is 2. The primary plus the one connected secondary satisfies majority. "
                "If two nodes are isolated, w:majority times out."
            ),
        },
        "cluster_readiness": replica_set_readiness(client),
        "simulation": simulation_info(),
        "saved_experiment_log": _load_saved_log(),
    }
