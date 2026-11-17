"""Admin-protected APIs for Chapter 14 monitoring, alerts, and recovery jobs."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.database import get_database
from backend.routes.admin import _admin_user, _audit
from backend.services import chapter14_ops as service

logger = logging.getLogger("aegis.chapter14.routes")
router = APIRouter(tags=["Chapter 14 Monitoring and Backup"])


class ConfirmRequest(BaseModel):
    confirm: bool = False


class AlertAckRequest(BaseModel):
    alert_id: str
    reason: str = Field(default="", max_length=500)


@router.get("/api/ops/metrics/monitoring")
def monitoring_metrics(
    minutes: int = Query(default=60, ge=30, le=720),
    actor: dict = Depends(_admin_user),
) -> dict[str, Any]:
    try:
        return service.metric_window(get_database(), minutes)
    except Exception as error:
        logger.exception("Chapter 14 monitoring metrics query failed")
        raise HTTPException(status_code=503, detail="Monitoring history is temporarily unavailable.") from error


@router.get("/api/ops/lab14/backups")
def backup_history(limit: int = Query(default=50, ge=1, le=200), actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    try:
        database = get_database()
        worker = database[service.WORKER_STATUS].find_one({"worker_id": "chapter14-backup-worker"}, {"_id": 0})
        return {"backups": service.list_backups(database, limit), "worker": service._public_worker(worker)}
    except Exception as error:
        logger.exception("Chapter 14 backup history query failed")
        raise HTTPException(status_code=503, detail="Backup history is temporarily unavailable.") from error


@router.get("/api/ops/lab14/jobs/{job_id}")
def backup_job(job_id: str, actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    row = get_database()[service.BACKUP_JOBS].find_one({"job_id": job_id}, {"_id": 0, "actor_user_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail="Backup job was not found.")
    for key in ("created_at", "started_at", "completed_at"):
        value = row.get(key)
        if value is not None:
            row[key] = value.isoformat()
    return row


def _enqueue_operation(database, actor: dict, job_type: str) -> dict[str, Any]:
    worker = database[service.WORKER_STATUS].find_one({"worker_id": "chapter14-backup-worker"})
    state = service._public_worker(worker)
    if state["status"] != "healthy":
        raise HTTPException(status_code=503, detail="The Chapter 14 backup worker is unavailable; no job was queued.")
    action = "ops.backup" if job_type == "backup" else "ops.verify"
    target = "chapter14-full-replica-set-with-oplog" if job_type == "backup" else "chapter14-isolated-restore-test"
    try:
        _audit(database, actor["user_id"], action, target, "queued", {"job_type": job_type})
        return service.enqueue_job(database, job_type, actor["user_id"])
    except Exception as error:
        logger.exception("Chapter 14 %s job could not be queued", job_type)
        raise HTTPException(status_code=503, detail="The operation could not be safely queued.") from error


@router.post("/api/ops/lab14/backup", status_code=202)
def create_backup(payload: ConfirmRequest, actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before creating a database backup.")
    return _enqueue_operation(get_database(), actor, "backup")


@router.post("/api/ops/lab14/verify", status_code=202)
def verify_restore(payload: ConfirmRequest, actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before running the isolated restore verification.")
    return _enqueue_operation(get_database(), actor, "verify")


@router.get("/api/moderation/alerts")
def alerts(
    status: Literal["active", "acknowledged", "resolved"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    actor: dict = Depends(_admin_user),
) -> dict[str, Any]:
    try:
        database = get_database()
        return {
            "alerts": service.list_alerts(database, status, limit),
            "counts": {
                "active": database[service.ALERTS].count_documents({"status": "active"}),
                "acknowledged": database[service.ALERTS].count_documents({"status": "acknowledged"}),
                "resolved": database[service.ALERTS].count_documents({"status": "resolved"}),
            },
        }
    except Exception as error:
        logger.exception("Chapter 14 alerts query failed")
        raise HTTPException(status_code=503, detail="Alert data is temporarily unavailable.") from error


@router.post("/api/moderation/alerts/ack")
def acknowledge_alert(payload: AlertAckRequest, actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    database = get_database()
    alert = database[service.ALERTS].find_one({"alert_id": payload.alert_id})
    if not alert:
        raise HTTPException(status_code=404, detail="Alert was not found.")
    if alert.get("status") == "resolved":
        raise HTTPException(status_code=409, detail="Resolved alerts cannot be acknowledged.")
    now = datetime.now(timezone.utc)
    try:
        _audit(database, actor["user_id"], "alert.ack", payload.alert_id, "started", {"reason": payload.reason[:500]})
    except Exception as error:
        logger.exception("Chapter 14 alert acknowledgement could not be audited")
        raise HTTPException(status_code=503, detail="Alert acknowledgement was not changed because its audit record could not be stored.") from error
    result = database[service.ALERTS].update_one(
        {"alert_id": payload.alert_id, "status": {"$in": ["active", "acknowledged"]}},
        {"$set": {"status": "acknowledged", "acknowledged_at": now,
                  "acknowledged_by": actor["user_id"], "acknowledgement_reason": payload.reason[:500]}},
    )
    if getattr(result, "matched_count", 1) == 0:
        raise HTTPException(status_code=409, detail="The alert changed before it could be acknowledged.")
    try:
        _audit(database, actor["user_id"], "alert.ack", payload.alert_id, "succeeded", {"reason": payload.reason})
    except Exception:
        logger.exception("Chapter 14 acknowledgement completion audit could not be recorded")
    return {"alert_id": payload.alert_id, "status": "acknowledged", "acknowledged_at": now.isoformat()}


@router.post("/api/moderation/alerts/webhook", include_in_schema=False)
async def receive_alert_webhook(request: Request) -> dict[str, Any]:
    """Accept only HMAC-signed bounded events; no unauthenticated alert ingestion."""
    secret = os.getenv("CH14_ALERT_WEBHOOK_SECRET", "").encode()
    if not secret:
        raise HTTPException(status_code=503, detail="Signed alert ingestion is not configured.")
    body = await request.body()
    if len(body) > 16_384:
        raise HTTPException(status_code=413, detail="Alert event is too large.")
    supplied = request.headers.get("X-Chapter14-Signature", "").strip()
    if supplied.startswith("sha256="):
        supplied = supplied[7:]
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Alert signature is invalid.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="Alert payload must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Alert payload must be an object.")
    metric = str(payload.get("metric", "external_event"))[:120]
    source = str(payload.get("source", "external_webhook"))[:80]
    alert_id = hashlib.sha256(f"{source}:{metric}".encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc)
    database = request.app.state.database if hasattr(request.app.state, "database") else get_database()
    database[service.ALERTS].update_one(
        {"active_key": f"external:{alert_id}"},
        {"$set": {"current_value": payload.get("current_value"), "last_seen_at": now},
         "$setOnInsert": {
             "alert_id": alert_id, "active_key": f"external:{alert_id}", "source": source,
             "metric": metric, "threshold": payload.get("threshold"),
             "severity": str(payload.get("severity", "warning"))[:24], "status": "active",
             "description": str(payload.get("description", ""))[:500], "created_at": now,
             "acknowledged_at": None, "acknowledged_by": None,
         }},
        upsert=True,
    )
    return {"accepted": True, "alert_id": alert_id}

