from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Request
from pymongo.database import Database
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("aegis.operations")
RETENTION_DAYS = 30
RETENTION_SECONDS = RETENTION_DAYS * 24 * 60 * 60

_active_requests = 0
_active_requests_lock = threading.Lock()
_UNTRACKED_PATHS = {
    "/api/subscriptions/login",
    "/api/subscriptions/register",
    "/api/subscriptions/logout",
    "/api/subscriptions/me",
    "/api/subscriptions/entitlements",
    "/api/subscriptions/plans",
}


def ensure_indexes(database: Database) -> None:
    database["system_operations"].create_index(
        [("timestamp", 1)],
        name="system_operations_timestamp_ttl",
        expireAfterSeconds=RETENTION_SECONDS,
    )


def should_track_request(request: Request) -> bool:
    path = request.url.path
    project_event_routes = ("/api/rag", "/api/vector-benchmark")
    return (
        path.startswith("/api/")
        and not path.startswith("/api/operations")
        and path not in _UNTRACKED_PATHS
        and not any(path == prefix or path.startswith(prefix + "/") for prefix in project_event_routes)
    )


def active_request_count() -> int:
    with _active_requests_lock:
        return _active_requests


def _component_for(route_path: str) -> str:
    if route_path.startswith("/api/ops/lab14/") or route_path.startswith("/api/ops/metrics/monitoring") or route_path.startswith("/api/moderation/alerts"):
        return "Chapter 14 monitoring and backup"
    if route_path.startswith("/api/ops/") or route_path.startswith("/api/services/lab13/"):
        return "Chapter 13 infrastructure operations"
    if route_path.startswith("/api/lab8"):
        return "MongoDB replica-set monitor"
    if route_path.startswith("/api/lab9"):
        return "Reliability experiments"
    if route_path.startswith("/api/lab10"):
        return "Banking and inventory services"
    if route_path.startswith("/api/lab11"):
        return "Sharding workflows"
    if route_path.startswith("/api/cross-source"):
        return "Cross-source analysis"
    if route_path.startswith("/api/admin"):
        return "Administration API"
    if route_path.startswith("/api/security"):
        return "Security Center API"
    if route_path.startswith("/api/manager"):
        return "Management API"
    if route_path.startswith("/api/subscriptions"):
        return "Account and subscription API"
    return "FastAPI API"


def build_operation_record(
    *,
    event_id: str,
    correlation_id: str,
    method: str,
    route_path: str,
    timestamp: datetime,
    completed_at: datetime,
    duration_ms: float,
    http_status_code: int,
    error_type: str | None = None,
) -> dict:
    if error_type:
        status = "failed"
        final_outcome = "failed"
    elif http_status_code >= 500:
        status = "failed"
        final_outcome = "failed"
        error_type = f"HTTP_{http_status_code}"
    elif http_status_code >= 400:
        status = "warning"
        final_outcome = "rejected"
        error_type = f"HTTP_{http_status_code}"
    else:
        status = "success"
        final_outcome = "succeeded"

    return {
        "_id": event_id,
        "event_id": event_id,
        "correlation_id": correlation_id,
        "timestamp": timestamp,
        "completed_at": completed_at,
        "operation": f"{method.upper()} {route_path}",
        "component": _component_for(route_path),
        "location": "FastAPI application container",
        "status": status,
        "duration_ms": round(max(0.0, duration_ms), 2),
        "http_status_code": http_status_code,
        "error_type": error_type,
        "error_message": None,
        "fallback_action": None,
        "alternative_service_used": None,
        "recovery_attempts": None,
        "recovery_status": "not_recorded",
        "final_outcome": final_outcome,
        "source": "http_request",
    }


async def track_api_request(request: Request, call_next):
    global _active_requests

    if not should_track_request(request):
        return await call_next(request)

    started_at = datetime.now(timezone.utc)
    started_clock = time.perf_counter()
    event_id = str(uuid4())
    correlation_id = event_id
    with _active_requests_lock:
        _active_requests += 1

    response = None
    error_type = None
    try:
        response = await call_next(request)
        return response
    except Exception as error:
        error_type = type(error).__name__
        raise
    finally:
        completed_at = datetime.now(timezone.utc)
        duration_ms = (time.perf_counter() - started_clock) * 1000
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        if not isinstance(route_path, str) or not route_path.startswith("/api/"):
            route_path = "/api/unmatched"
        status_code = response.status_code if response is not None else 500
        record = build_operation_record(
            event_id=event_id,
            correlation_id=correlation_id,
            method=request.method,
            route_path=route_path,
            timestamp=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            http_status_code=status_code,
            error_type=error_type,
        )
        if response is not None:
            response.headers["X-Correlation-ID"] = correlation_id
        try:
            database = getattr(request.app.state, "database", None)
            if database is None:
                request.app.state.operations_tracking_enabled = False
                logger.error("System operation telemetry is unavailable because the database is not initialized")
            else:
                try:
                    collection = database["system_operations"]
                    insert_one = getattr(collection, "insert_one", None)
                    if not callable(insert_one):
                        raise TypeError("system_operations collection has no insert_one method")
                    await run_in_threadpool(insert_one, record)
                    request.app.state.operations_tracking_enabled = True
                except PyMongoError:
                    request.app.state.operations_tracking_enabled = False
                    logger.exception("Could not persist system operation telemetry")
                except (AttributeError, KeyError, TypeError) as error:
                    request.app.state.operations_tracking_enabled = False
                    logger.error("System operation telemetry has an incompatible database handle: %s", error)
        finally:
            with _active_requests_lock:
                _active_requests -= 1
