from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pymongo.database import Database
from pymongo.errors import PyMongoError

from backend.config import settings
from backend.routes.admin import _admin_user
from backend.routes.api import metrics_history
from backend.services import operations as operation_service
from backend.services import status as source_status

router = APIRouter(prefix="/api/operations", tags=["System Operations"])
_AUTH = [Depends(_admin_user)]
_EVENT_FIELDS = {
    "_id": 1,
    "event_id": 1,
    "correlation_id": 1,
    "project_name": 1,
    "timestamp": 1,
    "completed_at": 1,
    "operation": 1,
    "component": 1,
    "location": 1,
    "status": 1,
    "duration_ms": 1,
    "http_status_code": 1,
    "error_type": 1,
    "error_message": 1,
    "fallback_action": 1,
    "recovery_action": 1,
    "alternative_service_used": 1,
    "recovery_attempts": 1,
    "recovery_status": 1,
    "final_outcome": 1,
    "source": 1,
}
_AUDIT_FIELDS = {
    "_id": 1,
    "action": 1,
    "outcome": 1,
    "created_at": 1,
    "actor_role": 1,
}
_EVENT_FILTERS = {"all", "failed", "recovered", "unresolved", "warnings", "fallback"}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _event_view(document: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in document.items() if key in _EVENT_FIELDS}
    result["event_id"] = str(document.get("event_id", document.get("_id", "")))
    result["correlation_id"] = str(document.get("correlation_id", ""))
    result["timestamp"] = _iso(document.get("timestamp"))
    result["completed_at"] = _iso(document.get("completed_at"))
    result.pop("_id", None)
    return result


def _audit_event_view(document: dict[str, Any]) -> dict[str, Any]:
    outcome = str(document.get("outcome", "unknown"))
    status = "success" if outcome in {"success", "created", "completed"} else (
        "failed" if outcome in {"failure", "failed"} else "warning"
    )
    return {
        "event_id": f"audit:{document.get('_id', '')}",
        "correlation_id": None,
        "timestamp": _iso(document.get("created_at")),
        "completed_at": None,
        "operation": f"ADMIN {document.get('action', 'recorded action')}",
        "component": "Admin audit log",
        "location": "Administrator audit log",
        "status": status,
        "duration_ms": None,
        "http_status_code": None,
        "error_type": None,
        "error_message": None,
        "fallback_action": None,
        "alternative_service_used": None,
        "recovery_attempts": None,
        "recovery_status": "not_recorded",
        "final_outcome": outcome,
        "source": "admin_audit",
    }


def _retained_filter() -> dict[str, Any]:
    return {"timestamp": {"$gte": datetime.now(timezone.utc) - timedelta(days=operation_service.RETENTION_DAYS)}}


def _health_components(database: Database, request: Request) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checked_at = datetime.now(timezone.utc)
    mongo_started = time.perf_counter()
    mongo_topology: dict[str, Any] = {"available": False, "members": [], "primary": None, "replica_set": None}
    try:
        database.client.admin.command({"ping": 1})
        response_ms = round((time.perf_counter() - mongo_started) * 1000, 2)
        mongo_status = "healthy"
        mongo_error = None
        try:
            hello = database.client.admin.command({"hello": 1})
            members = hello.get("hosts", [])
            mongo_topology = {
                "available": True,
                "replica_set": hello.get("setName"),
                "primary": hello.get("primary"),
                "members": members,
            }
        except PyMongoError:
            mongo_topology["error"] = "Topology details are unavailable."
    except PyMongoError as error:
        response_ms = None
        mongo_status = "unavailable"
        mongo_error = type(error).__name__

    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_running = bool(scheduler and scheduler.running)
    jobs = []
    if scheduler:
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run_at": _iso(job.next_run_time),
                "trigger": str(job.trigger),
            })

    components: list[dict[str, Any]] = [
        {
            "id": "backend",
            "name": "FastAPI backend",
            "status": "healthy",
            "location": "Application container",
            "last_health_check": _iso(checked_at),
            "response_time_ms": None,
            "dependencies": ["MongoDB", "Session middleware"],
            "recent_errors": [],
            "detail": "This authenticated health request was served by the backend.",
        },
        {
            "id": "operation-tracking",
            "name": "Operation telemetry",
            "status": "healthy" if getattr(request.app.state, "operations_tracking_enabled", False) else "degraded",
            "location": "MongoDB system_operations collection",
            "last_health_check": _iso(checked_at),
            "response_time_ms": None,
            "dependencies": ["MongoDB"],
            "recent_errors": [],
            "detail": "Successful writes are confirmed by the request tracker; stored records expire after 30 days.",
        },
        {
            "id": "mongodb",
            "name": "MongoDB",
            "status": mongo_status,
            "location": settings.mongo_database,
            "last_health_check": _iso(checked_at),
            "response_time_ms": response_ms,
            "dependencies": [],
            "recent_errors": [mongo_error] if mongo_error else [],
            "detail": "Status is based on a live MongoDB ping; topology is reported only when available.",
        },
        {
            "id": "scheduler",
            "name": "Background scheduler",
            "status": "healthy" if scheduler_running else "unavailable",
            "location": "Application process",
            "last_health_check": _iso(checked_at),
            "response_time_ms": None,
            "dependencies": ["FastAPI backend"],
            "recent_errors": [],
            "detail": f"{len(jobs)} scheduled jobs are registered." if scheduler_running else "Scheduler is not running.",
        },
        {
            "id": "docker-peers",
            "name": "Other Docker containers",
            "status": "not_monitored",
            "location": "Docker host",
            "last_health_check": None,
            "response_time_ms": None,
            "dependencies": [],
            "recent_errors": [],
            "detail": "The app container has no Docker socket mount, so peer-container health cannot be checked here.",
        },
        {
            "id": "backups",
            "name": "Backup service",
            "status": "not_monitored",
            "location": "External to this application",
            "last_health_check": None,
            "response_time_ms": None,
            "dependencies": [],
            "recent_errors": [],
            "detail": "No backup service health endpoint is configured for this application.",
        },
    ]

    for name, state in source_status.snapshot().items():
        if not state["configured"]:
            status = "not_monitored"
            detail = "No credentials/configuration are active for this source."
        elif state["last_error"]:
            status = "degraded"
            detail = "The latest fetch failed; sensitive provider response text is not exposed."
        elif state["last_successful_fetch"]:
            status = "healthy"
            detail = "A successful fetch has been recorded by the current process."
        else:
            status = "unknown"
            detail = "No successful fetch has been recorded by the current process."
        components.append({
            "id": f"collector-{name}",
            "name": f"{name.title()} collector",
            "status": status,
            "location": "Background scheduler",
            "last_health_check": state["last_successful_fetch"],
            "response_time_ms": None,
            "dependencies": ["MongoDB"],
            "recent_errors": ["Fetch error recorded"] if state["last_error"] else [],
            "detail": detail,
            "records_received": state["records_received"],
            "records_inserted": state["records_inserted"],
            "duplicates_skipped": state["duplicates_skipped"],
            "next_scheduled_fetch": state["next_scheduled_fetch"],
        })

    return components, {
        **mongo_topology,
        "database": settings.mongo_database,
        "response_time_ms": response_ms,
        "scheduler_running": scheduler_running,
        "jobs": jobs,
        "app_container_detected": os.path.exists("/.dockerenv"),
        "docker_daemon_status": "not_monitored",
    }


def _aggregate(collection, match: dict[str, Any], group: dict[str, Any]) -> list[dict[str, Any]]:
    return list(collection.aggregate([
        {"$match": match},
        {"$group": group},
        {"$sort": {"_id": 1}},
    ], maxTimeMS=5000))


@router.get("/overview", dependencies=_AUTH)
def get_operations_overview(request: Request) -> dict[str, Any]:
    database = request.app.state.database
    collection = database["system_operations"]
    retained = _retained_filter()
    try:
        status_rows = _aggregate(collection, retained, {
            "_id": "$status",
            "count": {"$sum": 1},
        })
        daily_rows = _aggregate(collection, retained, {
            "_id": {
                "day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp", "timezone": "UTC"}},
                "status": "$status",
            },
            "count": {"$sum": 1},
        })
        failure_rows = _aggregate(collection, {**retained, "status": "failed"}, {
            "_id": "$error_type",
            "count": {"$sum": 1},
        })
        project_rows = list(collection.aggregate([
            {"$match": {**retained, "source": "project_execution"}},
            {"$group": {
                "_id": "$project_name",
                "count": {"$sum": 1},
                "failed_count": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
                "average_duration_ms": {"$avg": "$duration_ms"},
            }},
            {"$sort": {"_id": 1}},
        ], maxTimeMS=5000))
        component_rows = list(collection.aggregate([
            {"$match": retained},
            {"$group": {
                "_id": "$component",
                "count": {"$sum": 1},
                "average_duration_ms": {"$avg": "$duration_ms"},
            }},
            {"$sort": {"_id": 1}},
        ], maxTimeMS=5000))
        latest_success = collection.find_one(
            {**retained, "status": "success"},
            projection=_EVENT_FIELDS,
            sort=[("timestamp", -1)],
        )
        latest_failure = collection.find_one(
            {**retained, "status": "failed"},
            projection=_EVENT_FIELDS,
            sort=[("timestamp", -1)],
        )
        first_event = collection.find_one(
            retained,
            projection={"timestamp": 1},
            sort=[("timestamp", 1)],
        )
        audit_filter = {
            "created_at": {
                "$gte": datetime.now(timezone.utc) - timedelta(days=operation_service.RETENTION_DAYS)
            }
        }
        audit_count = database["admin_audit_log"].count_documents(audit_filter)
        latest_audit = database["admin_audit_log"].find_one(
            audit_filter,
            projection=_AUDIT_FIELDS,
            sort=[("created_at", -1)],
        )
        duration_events = list(
            collection.find(retained, projection={
                "timestamp": 1, "duration_ms": 1, "status": 1, "component": 1,
            }).sort("timestamp", -1).limit(200)
        )
        total = sum(row["count"] for row in status_rows)
        counts = {row["_id"]: row["count"] for row in status_rows}
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="Operations telemetry is temporarily unavailable.") from error

    components, infrastructure = _health_components(database, request)
    failed_components = [item for item in components if item["status"] in {"degraded", "unavailable"}]
    overall_health = "degraded" if failed_components else "healthy"
    process_started = getattr(
        request.app.state, "operations_tracking_started_at", datetime.now(timezone.utc)
    )

    days: dict[str, dict[str, int]] = {}
    for row in daily_rows:
        day = row["_id"]["day"]
        days.setdefault(day, {"success": 0, "failed": 0, "warning": 0})
        days[day][row["_id"]["status"]] = row["count"]

    cache_points = [
        {
            "timestamp": point["timestamp"],
            "hit_ratio": point.get("hit_ratio_interval"),
            "bytes_in_cache": point.get("bytes_in_cache"),
        }
        for point in list(metrics_history)
    ]
    def latest(document: dict[str, Any] | None) -> dict[str, Any] | None:
        return _event_view(document) if document else None

    durations = [
        {
            "timestamp": _iso(item.get("timestamp")),
            "duration_ms": item.get("duration_ms"),
            "status": item.get("status"),
            "component": item.get("component"),
        }
        for item in reversed(duration_events)
    ]
    uptime_seconds = max(0, int((datetime.now(timezone.utc) - process_started).total_seconds()))

    return {
        "tracking": {
            "retention_days": operation_service.RETENTION_DAYS,
            "earliest_retained_event_at": _iso(first_event.get("timestamp")) if first_event else None,
            "process_tracking_started_at": _iso(process_started),
            "tracking_enabled": bool(getattr(request.app.state, "operations_tracking_enabled", False)),
            "historical_data_available": total > 0,
            "historical_note": "Only post-instrumentation operations are recorded; request history before this rollout is unavailable.",
        },
        "summary": {
            "total_operations": total,
            "successful_operations": counts.get("success", 0),
            "failed_operations": counts.get("failed", 0),
            "warning_operations": counts.get("warning", 0),
            "running_operations": operation_service.active_request_count(),
            "recovered_operations": None,
            "total_recovery_attempts": None,
            "successful_recovery_attempts": None,
            "failed_recovery_attempts": None,
            "average_recovery_duration_ms": None,
            "recovery_data_available": False,
            "overall_health": overall_health,
            "last_successful_operation": latest(latest_success),
            "last_failure": latest(latest_failure),
            "last_recovery_action": None,
            "admin_audit_entries": audit_count,
            "last_admin_audit_action": (
                _audit_event_view(latest_audit) if latest_audit else None
            ),
            "uptime_seconds": uptime_seconds,
        },
        "operations_over_time": [
            {"day": day, **values} for day, values in sorted(days.items())
        ],
        "failure_categories": [
            {"name": row["_id"] or "Unclassified server error", "count": row["count"]}
            for row in failure_rows
        ],
        "project_performance": [
            {
                "project_name": row["_id"] or "Unlabeled project",
                "operation_count": row["count"],
                "failed_count": row["failed_count"],
                "average_duration_ms": round(row["average_duration_ms"] or 0, 2),
            }
            for row in project_rows
        ],
        "component_performance": [
            {
                "component": row["_id"],
                "operation_count": row["count"],
                "average_duration_ms": round(row["average_duration_ms"] or 0, 2),
            }
            for row in component_rows
        ],
        "duration_series": durations,
        "status_distribution": {
            "success": counts.get("success", 0),
            "failed": counts.get("failed", 0),
            "warning": counts.get("warning", 0),
            "running": counts.get("running", 0),
        },
        "components": components,
        "infrastructure": infrastructure,
        "execution_flow": [
            {"id": "browser", "label": "Admin browser", "detail": "Authenticated dashboard request."},
            {"id": "backend", "label": "FastAPI application", "detail": "Serves UI and API routes."},
            {"id": "session", "label": "Session middleware", "detail": "Validates the application session before protected APIs."},
            {"id": "api", "label": "API routers", "detail": "Routes requests to the relevant application service."},
            {"id": "mongodb", "label": "MongoDB", "detail": "Stores application and telemetry records."},
            {"id": "scheduler", "label": "Background scheduler", "detail": "Runs configured collectors and maintenance jobs."},
            {"id": "sources", "label": "Configured CTI sources", "detail": "External sources are shown only when configured and monitored."},
        ],
        "cache_metrics": {
            "available": bool(cache_points),
            "history": cache_points,
            "note": "WiredTiger samples are held in process memory and reset on backend restart.",
        },
        "data_sources": {
            "operation_metrics": "system_operations collection; API request route, status, duration and timestamps only.",
            "component_health": "Live MongoDB ping, scheduler state and in-process collector status.",
            "cache_metrics": "Existing 10-second WiredTiger sampler; last 60 in-process samples.",
            "recovery_metrics": "Unavailable: no structured recovery-attempt history is currently recorded.",
            "administrative_audit": "Existing admin_audit_log action/outcome/timestamp fields; identities and details are excluded.",
        },
    }


@router.get("/events", dependencies=_AUTH)
def get_operation_events(
    request: Request,
    page: int = Query(1, ge=1, le=100),
    limit: int = Query(50, ge=1, le=100),
    q: str = Query("", max_length=120),
    status: str | None = None,
    event_filter: str = Query("all", alias="filter"),
    component: str | None = Query(None, max_length=120),
    from_datetime: datetime | None = Query(None, alias="from"),
    to_datetime: datetime | None = Query(None, alias="to"),
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
) -> dict[str, Any]:
    if status not in {None, "success", "failed", "warning"}:
        raise HTTPException(status_code=422, detail="Unsupported operation status.")
    if event_filter not in _EVENT_FILTERS:
        raise HTTPException(status_code=422, detail="Unsupported event filter.")
    if from_datetime and to_datetime and from_datetime > to_datetime:
        raise HTTPException(status_code=422, detail="Start time must be before end time.")

    filters: dict[str, Any] = _retained_filter()
    timestamp_filter = dict(filters["timestamp"])
    if from_datetime:
        lower_bound = from_datetime if from_datetime.tzinfo else from_datetime.replace(tzinfo=timezone.utc)
        timestamp_filter["$gte"] = max(timestamp_filter["$gte"], lower_bound)
    if to_datetime:
        timestamp_filter["$lte"] = to_datetime if to_datetime.tzinfo else to_datetime.replace(tzinfo=timezone.utc)
    filters["timestamp"] = timestamp_filter
    audit_filters: dict[str, Any] = {
        "created_at": {
            "$gte": timestamp_filter["$gte"],
            **({"$lte": timestamp_filter["$lte"]} if "$lte" in timestamp_filter else {}),
        }
    }
    request_conditions: list[dict[str, Any]] = []
    audit_conditions: list[dict[str, Any]] = []
    if status:
        request_conditions.append({"status": status})
        outcome_filters = {
            "success": {"$in": ["success", "created", "completed"]},
            "failed": {"$in": ["failure", "failed"]},
            "warning": "warning",
        }
        audit_conditions.append({"outcome": outcome_filters[status]})
    filter_map = {
        "failed": {"status": "failed"},
        "recovered": {"recovery_status": "successful"},
        "unresolved": {"recovery_status": {"$in": ["failed", "unresolved"]}},
        "warnings": {"status": "warning"},
        "fallback": {"fallback_action": {"$nin": [None, ""]}},
    }
    if event_filter != "all":
        request_conditions.append(filter_map[event_filter])
        audit_filter_map = {
            "failed": {"outcome": {"$in": ["failure", "failed"]}},
            "recovered": None,
            "unresolved": None,
            "warnings": {"outcome": "warning"},
            "fallback": None,
        }
        if audit_filter_map[event_filter] is None:
            audit_conditions.append({"_id": {"$exists": False}})
        else:
            audit_conditions.append(audit_filter_map[event_filter])
    if component:
        request_conditions.append({"component": component})
        if component != "Admin audit log":
            audit_conditions.append({"_id": {"$exists": False}})
    if q:
        expression = re.escape(q.strip())
        request_conditions.append({"$or": [
            {"event_id": {"$regex": expression, "$options": "i"}},
            {"correlation_id": {"$regex": expression, "$options": "i"}},
            {"operation": {"$regex": expression, "$options": "i"}},
            {"component": {"$regex": expression, "$options": "i"}},
            {"project_name": {"$regex": expression, "$options": "i"}},
            {"error_type": {"$regex": expression, "$options": "i"}},
            {"fallback_action": {"$regex": expression, "$options": "i"}},
        ]})
        audit_search = [
            {"action": {"$regex": expression, "$options": "i"}},
            {"outcome": {"$regex": expression, "$options": "i"}},
            {"actor_role": {"$regex": expression, "$options": "i"}},
        ]
        if q.startswith("audit:"):
            try:
                audit_search.append({"_id": ObjectId(q[6:])})
            except (InvalidId, TypeError):
                pass
        audit_conditions.append({"$or": audit_search})
    if request_conditions:
        filters["$and"] = request_conditions
    if audit_conditions:
        audit_filters["$and"] = audit_conditions

    collection = request.app.state.database["system_operations"]
    audit_collection = request.app.state.database["admin_audit_log"]
    direction = -1 if sort == "newest" else 1
    try:
        request_total = collection.count_documents(filters)
        audit_total = audit_collection.count_documents(audit_filters)
        fetch_limit = page * limit
        request_records = list(
            collection.find(filters, projection=_EVENT_FIELDS)
            .sort("timestamp", direction)
            .limit(fetch_limit)
        )
        audit_records = list(
            audit_collection.find(audit_filters, projection=_AUDIT_FIELDS)
            .sort("created_at", direction)
            .limit(fetch_limit)
        )
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="Operation history is temporarily unavailable.") from error
    records = [_event_view(record) for record in request_records]
    records.extend(_audit_event_view(record) for record in audit_records)
    records.sort(
        key=lambda item: datetime.fromisoformat(item["timestamp"]),
        reverse=direction < 0,
    )
    total = request_total + audit_total
    records = records[(page - 1) * limit:page * limit]
    return {
        "events": records,
        "page": page,
        "limit": limit,
        "total": total,
        "retention_days": operation_service.RETENTION_DAYS,
        "timezone": "UTC (displayed in the browser's local timezone)",
    }


@router.get("/events/{event_id}", dependencies=_AUTH)
def get_operation_event(event_id: str, request: Request) -> dict[str, Any]:
    if event_id.startswith("audit:"):
        try:
            audit_id = ObjectId(event_id[6:])
        except (InvalidId, TypeError) as error:
            raise HTTPException(status_code=404, detail="Operation event not found.") from error
        try:
            audit_event = request.app.state.database["admin_audit_log"].find_one(
                {
                    "_id": audit_id,
                    "created_at": {
                        "$gte": datetime.now(timezone.utc) - timedelta(days=operation_service.RETENTION_DAYS)
                    },
                },
                projection=_AUDIT_FIELDS,
            )
        except PyMongoError as error:
            raise HTTPException(status_code=503, detail="Audit event details are temporarily unavailable.") from error
        if not audit_event:
            raise HTTPException(status_code=404, detail="Operation event not found.")
        event = _audit_event_view(audit_event)
        return {
            "event": event,
            "related_events": [],
            "simple_explanation": f"An administrator audit action was recorded with outcome {event['final_outcome']}.",
            "technical_details_available": False,
            "technical_logs_available": False,
            "privacy_note": "Administrator identity, target record and audit details are not exposed in this view.",
        }

    collection = request.app.state.database["system_operations"]
    try:
        event = collection.find_one(
            {"_id": event_id, **_retained_filter()},
            projection=_EVENT_FIELDS,
        )
        if not event:
            raise HTTPException(status_code=404, detail="Operation event not found.")
        related = list(
            collection.find(
                {"correlation_id": event.get("correlation_id")},
                projection=_EVENT_FIELDS,
            ).sort("timestamp", 1).limit(50)
        )
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="Operation event details are temporarily unavailable.") from error
    return {
        "event": _event_view(event),
        "related_events": [_event_view(item) for item in related],
        "simple_explanation": (
            f"{event.get('operation', 'The project operation')} was recorded for "
            f"{event.get('project_name', 'the project')} with status {event.get('status', 'unknown')}."
            if event.get("source") == "project_execution"
            else (
                "The API request completed successfully."
                if event.get("status") == "success"
                else "The API request did not complete successfully; see the recorded status and safe error type."
            )
        ),
        "technical_details_available": bool(event.get("error_type")),
        "technical_logs_available": False,
        "privacy_note": "Request bodies, query values, cookies, tokens, user identifiers and raw exception messages are not stored.",
    }
