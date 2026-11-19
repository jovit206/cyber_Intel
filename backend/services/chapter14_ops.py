"""Persistent Chapter 14 monitoring, alerts, and backup job records."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

CHAPTER14_DIR = Path(__file__).resolve().parents[2] / "lab-14"
if str(CHAPTER14_DIR) not in sys.path:
    sys.path.insert(0, str(CHAPTER14_DIR))
from monitoring_dashboard import build_alerts, retrieve_replica_status, retrieve_server_metrics

logger = logging.getLogger("aegis.chapter14")
METRICS = "chapter14_monitoring_samples"
ALERTS = "chapter14_monitoring_alerts"
BACKUP_JOBS = "chapter14_backup_jobs"
BACKUP_RUNS = "chapter14_backup_runs"
WORKER_STATUS = "chapter14_backup_worker_status"


def ensure_indexes(database: Database) -> None:
    database[METRICS].create_index([("timestamp", -1)], name="chapter14_metrics_timestamp")
    database[ALERTS].create_index([("created_at", -1)], name="chapter14_alerts_created")
    database[ALERTS].create_index(
        [("active_key", 1)], unique=True, sparse=True, name="chapter14_one_open_alert_per_metric",
    )
    database[BACKUP_JOBS].create_index([("status", 1), ("created_at", 1)], name="chapter14_backup_queue")
    database[BACKUP_JOBS].create_index([("job_id", 1)], unique=True, name="chapter14_backup_job_id")
    database[BACKUP_RUNS].create_index([("created_at", -1)], name="chapter14_backup_runs_created")
    database[WORKER_STATUS].create_index([("worker_id", 1)], unique=True, name="chapter14_worker_id")


def _member_summary(replica: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for member in replica.get("members", []):
        rows.append({
            "name": member.get("name"),
            "state": member.get("stateStr", "UNKNOWN"),
            "health": member.get("health"),
            "ping_ms": member.get("pingMs"),
        })
    return rows


def collect_sample(database: Database) -> dict[str, Any]:
    """Read actual serverStatus/replSetGetStatus data and persist the normalized sample."""
    client = database.client
    metrics = retrieve_server_metrics(client)
    replica = retrieve_replica_status(client)
    timestamp = datetime.now(timezone.utc)
    sample = {
        "timestamp": timestamp,
        "source": "live_mongodb_server_status",
        "scope": "application_configured_replica_set",
        "cache_hit_ratio_percent": metrics.get("cache_hit_ratio_percent"),
        "connections_current": metrics.get("connections_current"),
        "connections_available": metrics.get("connections_available"),
        "opcounters": metrics.get("opcounters", {}),
        "cache_bytes_current": metrics.get("cache_bytes_current"),
        "cache_bytes_read": metrics.get("cache_bytes_read"),
        "replica_set_applicable": replica.get("applicable", False),
        "primary": replica.get("primary"),
        "members": _member_summary(replica),
        "replication_lags": replica.get("lags", []),
    }
    database[METRICS].insert_one(sample)
    sync_threshold_alerts(database, metrics, replica, timestamp)
    return sample


def sync_threshold_alerts(
    database: Database,
    metrics: dict[str, Any],
    replica: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Open alerts on threshold crossings and resolve them after measured recovery."""
    timestamp = now or datetime.now(timezone.utc)
    current = {alert.metric: alert for alert in build_alerts(metrics, replica)}
    for alert in current.values():
        key = alert.metric
        document = {
            "alert_id": uuid4().hex,
            "active_key": key,
            "source": "chapter14_threshold",
            "metric": alert.metric,
            "current_value": alert.current_value,
            "threshold": alert.threshold,
            "status": "active",
            "created_at": timestamp,
            "last_seen_at": timestamp,
            "acknowledged_at": None,
            "acknowledged_by": None,
        }
        try:
            database[ALERTS].insert_one(document)
        except DuplicateKeyError:
            database[ALERTS].update_one(
                {"active_key": key},
                {"$set": {"current_value": alert.current_value, "last_seen_at": timestamp}},
            )
    recovered_keys = database[ALERTS].distinct("active_key", {"active_key": {"$exists": True}})
    for key in recovered_keys:
        if key not in current:
            database[ALERTS].update_one(
                {"active_key": key},
                {"$set": {"status": "resolved", "resolved_at": timestamp}, "$unset": {"active_key": ""}},
            )


def metric_window(database: Database, minutes: int) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    history = list(database[METRICS].find(
        {"timestamp": {"$gte": since}}, {"_id": 0}, sort=[("timestamp", 1)], limit=5000,
    ))
    open_alerts = database[ALERTS].count_documents({"status": {"$in": ["active", "acknowledged"]}})
    backups = database[BACKUP_RUNS].find_one({"status": "success"}, sort=[("created_at", -1)])
    worker = database[WORKER_STATUS].find_one({"worker_id": "chapter14-backup-worker"}, {"_id": 0})
    for point in history:
        if isinstance(point.get("timestamp"), datetime):
            point["timestamp"] = point["timestamp"].isoformat()
    prior_total = None
    prior_time = None
    for point in history:
        total = sum(value for value in (point.get("opcounters") or {}).values() if isinstance(value, (int, float)))
        try:
            instant = datetime.fromisoformat(point["timestamp"])
        except (KeyError, TypeError, ValueError):
            instant = None
        elapsed = (instant - prior_time).total_seconds() if instant and prior_time else 0
        point["operations_per_second"] = (
            round((total - prior_total) / elapsed, 3)
            if prior_total is not None and elapsed > 0 and total >= prior_total else None
        )
        prior_total = total
        prior_time = instant
    latest = history[-1] if history else None
    return {
        "source": "live_mongodb_server_status",
        "scope": "application_configured_replica_set",
        "minutes": minutes,
        "sample_interval_seconds": 30,
        "sample_count": len(history),
        "history": history,
        "latest": latest,
        "alert_count": open_alerts,
        "latest_backup": _public_backup(backups),
        "latest_verification": _public_backup(database[BACKUP_RUNS].find_one(
            {"kind": "verify", "status": "success"}, sort=[("created_at", -1)],
        )),
        "worker": _public_worker(worker),
        "available": bool(history),
    }


def list_alerts(database: Database, status: str | None, limit: int) -> list[dict[str, Any]]:
    query = {"status": status} if status else {}
    rows = list(database[ALERTS].find(query, {"_id": 0, "active_key": 0}, sort=[("created_at", -1)], limit=limit))
    for row in rows:
        for key in ("created_at", "last_seen_at", "acknowledged_at", "resolved_at"):
            if isinstance(row.get(key), datetime):
                row[key] = row[key].isoformat()
    return rows


def list_backups(database: Database, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(database[BACKUP_RUNS].find({}, {"_id": 0, "backup_path": 0}, sort=[("created_at", -1)], limit=limit))
    for row in rows:
        for key in ("created_at", "completed_at"):
            if isinstance(row.get(key), datetime):
                row[key] = row[key].isoformat()
    return rows


def _public_backup(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = {key: value for key, value in row.items() if key not in {"_id", "backup_path"}}
    for key in ("created_at", "completed_at"):
        if isinstance(result.get(key), datetime):
            result[key] = result[key].isoformat()
    return result


def _public_worker(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"status": "not_configured", "last_seen_at": None}
    seen = row.get("last_seen_at")
    age = (datetime.now(timezone.utc) - seen.replace(tzinfo=timezone.utc) if seen and not seen.tzinfo
           else datetime.now(timezone.utc) - seen if seen else None)
    status = row.get("status", "unknown") if age is None or age.total_seconds() <= 90 else "unavailable"
    return {"status": status, "last_seen_at": seen.isoformat() if seen else None}


def enqueue_job(database: Database, job_type: str, actor_id: str, document_count: int = 1000) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    job = {
        "job_id": uuid4().hex,
        "job_type": job_type,
        "status": "queued",
        "actor_user_id": actor_id,
        "document_count": document_count,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "result": None,
        "error_type": None,
    }
    database[BACKUP_JOBS].insert_one(job)
    return {"job_id": job["job_id"], "job_type": job_type, "status": "queued", "created_at": now.isoformat()}


def sample_monitoring_metrics(database: Database) -> dict[str, Any]:
    """Compatibility name used by the application scheduler for live sampling."""
    return collect_sample(database)
