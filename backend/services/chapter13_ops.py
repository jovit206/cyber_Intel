"""Live Chapter 13 telemetry and fixed Kubernetes lab controls."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from pymongo.database import Database
from pymongo.errors import OperationFailure

logger = logging.getLogger("aegis.chapter13")
_standalone_warned = False
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAPTER13_DIR = PROJECT_ROOT / "chapter-13"
METRICS_COLLECTION = "deployment_metric_samples"
ELECTIONS_COLLECTION = "deployment_election_events"
METRIC_RETENTION_DAYS = 30
SAMPLE_INTERVAL_SECONDS = 10
_job_lock = threading.Lock()


def ensure_indexes(database: Database) -> None:
    database[METRICS_COLLECTION].create_index(
        [("timestamp", 1)], name="deployment_metrics_timestamp_ttl",
        expireAfterSeconds=METRIC_RETENTION_DAYS * 24 * 60 * 60,
    )
    database[ELECTIONS_COLLECTION].create_index(
        [("timestamp", 1)], name="deployment_elections_timestamp_ttl",
        expireAfterSeconds=METRIC_RETENTION_DAYS * 24 * 60 * 60,
    )


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    parsed = _as_datetime(value)
    return parsed.isoformat() if parsed else None


def _member_rows(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "id": member.get("_id"),
        "name": member.get("name"),
        "state": member.get("stateStr", "UNKNOWN"),
        "health": member.get("health"),
        "uptime_seconds": member.get("uptime"),
        "ping_ms": member.get("pingMs"),
        "sync_source": member.get("syncSourceHost"),
        "optime_at": _iso(member.get("optimeDate")),
        "last_heartbeat_at": _iso(member.get("lastHeartbeat")),
    } for member in status.get("members", [])]


def build_deployment_sample(
    repl_status: dict[str, Any],
    server_status: dict[str, Any],
    previous: dict[str, Any] | None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build a BSON-safe sample from actual replSetGetStatus/serverStatus responses."""
    now = timestamp or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    members = _member_rows(repl_status)
    primary = next((member["name"] for member in members if member["state"] == "PRIMARY"), None)
    primary_member = next((member for member in repl_status.get("members", []) if member.get("stateStr") == "PRIMARY"), None)
    primary_optime = _as_datetime((primary_member or {}).get("optimeDate"))
    replication_lag = {}
    if primary_optime:
        for member in members:
            member_optime = _as_datetime(member.get("optime_at"))
            if member["state"] == "SECONDARY" and member_optime:
                replication_lag[member["name"]] = max(0, int((primary_optime - member_optime).total_seconds() * 1000))

    counters = server_status.get("opcounters", {})
    totals = sum(value for value in counters.values() if isinstance(value, (int, float)))
    elapsed = None
    if previous:
        previous_time = _as_datetime(previous.get("timestamp"))
        if previous_time:
            elapsed = (now - previous_time).total_seconds()
    operations_per_second = None
    if previous and elapsed and elapsed > 0:
        old_total = previous.get("opcounter_total")
        if isinstance(old_total, (int, float)) and totals >= old_total:
            operations_per_second = round((totals - old_total) / elapsed, 3)

    cache = (server_status.get("wiredTiger") or {}).get("cache") or {}
    requests = cache.get("pages requested from the cache")
    reads = cache.get("pages read into cache")
    previous_cache = (previous or {}).get("cache_counters") or {}
    hit_ratio = None
    if isinstance(requests, (int, float)) and isinstance(reads, (int, float)):
        delta_requests = requests - previous_cache.get("requests", requests)
        delta_reads = reads - previous_cache.get("reads", reads)
        if delta_requests > 0 and 0 <= delta_reads <= delta_requests:
            hit_ratio = round(1 - delta_reads / delta_requests, 5)

    connections = server_status.get("connections") or {}
    old_primary = (previous or {}).get("primary")
    election = None
    if old_primary and primary and old_primary != primary:
        election = {"previous_primary": old_primary, "new_primary": primary, "timestamp": now}

    return {
        "timestamp": now,
        "source": "live_mongodb_server_status",
        "scope": "application_configured_replica_set",
        "set_name": repl_status.get("set"),
        "term": repl_status.get("term"),
        "primary": primary,
        "healthy": bool(primary) and bool(members) and all(member["health"] == 1 for member in members),
        "member_count": len(members),
        "members": members,
        "replication_lag_ms": replication_lag,
        "operations_per_second": operations_per_second,
        "opcounter_total": totals,
        "connections": {
            "current": connections.get("current"),
            "available": connections.get("available"),
            "total_created": connections.get("totalCreated"),
        },
        "cache": {
            "bytes_in_cache": cache.get("bytes currently in the cache"),
            "max_bytes": cache.get("maximum bytes configured"),
            "dirty_bytes": cache.get("tracked dirty bytes in the cache"),
            "pages_read": reads,
            "pages_evicted": cache.get("pages evicted by application threads"),
            "pages_requested": requests,
            "hit_ratio_interval": hit_ratio,
        },
        "cache_counters": {"requests": requests, "reads": reads},
        "election": election,
    }


def sample_deployment_metrics(database: Database) -> dict[str, Any] | None:
    """Persist one live sample; errors are logged without leaking connection details."""
    collection = database[METRICS_COLLECTION]
    try:
        previous = collection.find_one({}, sort=[("timestamp", -1)])
        repl_status = database.client.admin.command("replSetGetStatus")
        server_status = database.client.admin.command({"serverStatus": 1})
        sample = build_deployment_sample(repl_status, server_status, previous)
        collection.insert_one(sample)
        marker = sample.get("election")
        if marker:
            database[ELECTIONS_COLLECTION].insert_one({**marker, "source": "observed_primary_change"})
        return sample
    except OperationFailure as error:
        if error.code == 76:  # NoReplicationEnabled: standalone server, no replica-set telemetry exists
            global _standalone_warned
            if not _standalone_warned:
                logger.warning("Chapter 13 telemetry disabled: the configured MongoDB is not a replica set member; "
                               "no synthetic sample was written")
                _standalone_warned = True
            return None
        logger.warning("Chapter 13 telemetry sample failed; no synthetic sample was written", exc_info=True)
        return None
    except Exception:
        logger.warning("Chapter 13 telemetry sample failed; no synthetic sample was written", exc_info=True)
        return None


def metric_window(database: Database, minutes: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes)
    points = list(database[METRICS_COLLECTION].find(
        {"timestamp": {"$gte": start}}, {"_id": 0}, sort=[("timestamp", 1)], limit=2000,
    ))
    elections = list(database[ELECTIONS_COLLECTION].find(
        {"timestamp": {"$gte": start}}, {"_id": 0}, sort=[("timestamp", 1)], limit=500,
    ))
    for point in points:
        point["timestamp"] = _iso(point.get("timestamp"))
        point.pop("cache_counters", None)
        point.pop("opcounter_total", None)
    for event in elections:
        event["timestamp"] = _iso(event.get("timestamp") or event.get("detected_at"))
        event.pop("detected_at", None)
    return {
        "source": "live_mongodb_server_status",
        "scope": "application_configured_replica_set",
        "minutes": minutes,
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "sample_count": len(points),
        "history": points,
        "elections": elections,
        "latest": points[-1] if points else None,
        "available": bool(points),
    }


def mark_election(database: Database, previous_primary: str, new_primary: str) -> dict[str, Any]:
    event = {
        "timestamp": datetime.now(timezone.utc),
        "previous_primary": previous_primary,
        "new_primary": new_primary,
        "source": "confirmed_failover_operation",
    }
    database[ELECTIONS_COLLECTION].insert_one(event)
    event["timestamp"] = event["timestamp"].isoformat()
    return event


def kubernetes_preflight() -> tuple[bool, str]:
    if not shutil.which("kubectl"):
        return False, "kubectl is not installed in the application environment."
    if not shutil.which("bash"):
        return False, "Bash is not available to run the existing deployment script."
    if not (CHAPTER13_DIR / ".env").is_file():
        return False, "Chapter 13 local credentials are not configured in this environment."
    keyfile = CHAPTER13_DIR / "secrets" / "mongodb-keyfile"
    if not keyfile.is_file() or keyfile.stat().st_size == 0:
        return False, "The Chapter 13 replica-set keyfile is not configured."
    try:
        result = subprocess.run(
            ["kubectl", "config", "current-context"], capture_output=True,
            text=True, timeout=5, check=True,
        )
        if not result.stdout.strip():
            return False, "No Kubernetes context is selected."
    except (OSError, subprocess.SubprocessError):
        return False, "A usable Kubernetes context is not available."
    return True, "Kubernetes deployment prerequisites are available."


def kubernetes_status() -> dict[str, Any]:
    if not shutil.which("kubectl"):
        return {"status": "not_configured", "configured": False, "reason": "kubectl is unavailable to the application."}
    try:
        result = subprocess.run(
            ["kubectl", "--namespace", "mongo-ha", "get", "pods", "-o", "json"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        payload = json.loads(result.stdout)
        pods = []
        for item in payload.get("items", []):
            status = item.get("status") or {}
            pods.append({
                "name": item.get("metadata", {}).get("name"),
                "phase": status.get("phase", "Unknown"),
                "ready": bool(status.get("containerStatuses")) and all(
                    container.get("ready", False) for container in status.get("containerStatuses", [])
                ),
                "restarts": sum(container.get("restartCount", 0) for container in status.get("containerStatuses", [])),
                "node": item.get("spec", {}).get("nodeName"),
            })
        all_ready = bool(pods) and all(pod["phase"] == "Running" and pod["ready"] for pod in pods)
        return {
            "status": "ready" if all_ready else "degraded" if pods else "stopped",
            "configured": True,
            "namespace": "mongo-ha",
            "pods": pods,
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"status": "unavailable", "configured": False, "reason": "The Kubernetes API could not be queried from this application."}


def start_kubernetes_deployment(app_state: Any, actor_id: str, database: Database, job_id: str) -> None:
    """Run only the repository's reviewed Chapter 13 deploy script; never accept commands from the caller."""
    script = CHAPTER13_DIR / "scripts" / "deploy-kubernetes.sh"
    try:
        result = subprocess.run(
            ["bash", str(script)], cwd=CHAPTER13_DIR, capture_output=True,
            text=True, timeout=1200, check=False,
        )
        job = {
            "job_id": job_id,
            "status": "ready" if result.returncode == 0 else "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "message": "Chapter 13 Kubernetes deployment completed." if result.returncode == 0 else "The existing Chapter 13 deployment script failed. Inspect the host deployment logs.",
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        job = {"job_id": job_id, "status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "message": "Kubernetes deployment exceeded its 20-minute limit."}
    except OSError:
        job = {"job_id": job_id, "status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "message": "The existing Kubernetes deployment script could not be started."}
    with _job_lock:
        app_state.chapter13_kubernetes_job = job
    try:
        from backend.routes.admin import _audit
        _audit(database, actor_id, "lab.start", "chapter13-kubernetes", job["status"], {"job_id": job_id, "exit_code": job.get("exit_code")})
    except Exception:
        logger.exception("Chapter 13 Kubernetes completion audit could not be recorded")


def launch_kubernetes_deployment(app_state: Any, actor_id: str, database: Database) -> dict[str, Any]:
    ready, reason = kubernetes_preflight()
    if not ready:
        return {"accepted": False, "status": "not_configured", "detail": reason}
    with _job_lock:
        existing = getattr(app_state, "chapter13_kubernetes_job", None)
        if existing and existing.get("status") == "starting":
            return {"accepted": False, "status": "starting", "job_id": existing.get("job_id"), "detail": "A Chapter 13 Kubernetes deployment is already running."}
        job_id = uuid4().hex
        job = {"job_id": job_id, "status": "starting", "started_at": datetime.now(timezone.utc).isoformat(), "message": "Running the reviewed Chapter 13 Kubernetes deployment script."}
        app_state.chapter13_kubernetes_job = job
    from backend.routes.admin import _audit
    _audit(database, actor_id, "lab.start", "chapter13-kubernetes", "started", {"job_id": job_id})
    threading.Thread(
        target=start_kubernetes_deployment,
        args=(app_state, actor_id, database, job_id),
        name="chapter13-kubernetes-deploy", daemon=True,
    ).start()
    return {"accepted": True, **job}


def integration_link_status(kind: str) -> dict[str, Any]:
    """Probe configured monitoring URLs without returning credentials or raw errors."""
    internal_url = os.getenv(f"LAB13_{kind}_URL", "").strip().rstrip("/")
    public_url = os.getenv(f"LAB13_{kind}_PUBLIC_URL", "").strip().rstrip("/")
    safe_public_url = _safe_public_url(public_url)
    if not internal_url:
        return {"status": "not_configured", "url": safe_public_url}
    suffix = "/-/healthy" if kind == "PROMETHEUS" else "/api/health"
    try:
        request = Request(internal_url + suffix, headers={"Accept": "application/json"})
        with urlopen(request, timeout=2) as response:
            healthy = 200 <= response.status < 300
        return {"status": "healthy" if healthy else "degraded", "url": safe_public_url}
    except (OSError, URLError, TimeoutError, ValueError):
        return {"status": "unavailable", "url": safe_public_url}


def _safe_public_url(value: str) -> str | None:
    """Return a browser-safe HTTP(S) link with user info and query secrets removed."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))
    except ValueError:
        return None


def atlas_configuration_status() -> dict[str, Any]:
    configured = (
        (CHAPTER13_DIR / "terraform" / "terraform.tfvars").is_file()
        and bool(os.getenv("MONGODB_ATLAS_PUBLIC_KEY", "").strip())
        and bool(os.getenv("MONGODB_ATLAS_PRIVATE_KEY", "").strip())
    )
    return {
        "status": "configuration_present" if configured else "not_configured",
        "configured": configured,
        "detail": "Terraform and Atlas credentials are configured; live Atlas state is not queried by this application." if configured else "Optional Atlas M0 Terraform is documented, but its configuration is not available to this application.",
    }

