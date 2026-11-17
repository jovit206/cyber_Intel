"""Administrator API for Chapter 13 deployment telemetry and lab controls."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pymongo.errors import AutoReconnect

from backend.database import get_database
from backend.routes.admin import _admin_user, _audit
from backend.services import chapter13_ops as service

logger = logging.getLogger("aegis.chapter13.routes")
ops_router = APIRouter(prefix="/api/ops", tags=["Chapter 13 Operations"])
services_router = APIRouter(prefix="/api/services", tags=["Chapter 13 Operations"])
_ADMIN = [Depends(_admin_user)]


class FailoverRequest(BaseModel):
    confirm: bool = False
    seconds: int = Field(default=60, ge=5, le=120)


class KubernetesStartRequest(BaseModel):
    confirm: bool = False


@ops_router.get("/metrics/deployment", dependencies=_ADMIN)
def deployment_metrics(minutes: int = Query(default=30, ge=1, le=30)) -> dict[str, Any]:
    try:
        return service.metric_window(get_database(), minutes)
    except Exception as error:
        logger.exception("Chapter 13 deployment metrics query failed")
        raise HTTPException(status_code=503, detail="Deployment metrics are temporarily unavailable.") from error


@ops_router.get("/overview", dependencies=_ADMIN)
def deployment_overview(request: Request) -> dict[str, Any]:
    database = get_database()
    try:
        snapshot = service.metric_window(database, 1)
        latest = snapshot.get("latest")
        kubernetes = service.kubernetes_status()
        job = getattr(request.app.state, "chapter13_kubernetes_job", None)
        return {
            "source": "live",
            "scope": "application_configured_replica_set",
            "note": "Replica-set telemetry is from the MongoDB cluster configured for this application. The separate Chapter 13 Compose, Kubernetes, and Atlas environments are not inferred from it.",
            "replica_set": latest,
            "elections": snapshot.get("elections", []),
            "kubernetes": {**kubernetes, "deployment_job": job},
            "prometheus": service.integration_link_status("PROMETHEUS"),
            "grafana": service.integration_link_status("GRAFANA"),
            "atlas": service.atlas_configuration_status(),
            "sample_count_last_minute": snapshot.get("sample_count", 0),
        }
    except Exception as error:
        logger.exception("Chapter 13 deployment overview query failed")
        raise HTTPException(status_code=503, detail="Deployment overview is temporarily unavailable.") from error


@ops_router.post("/lab13/failover")
def run_failover(payload: FailoverRequest, actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before requesting primary stepdown.")
    database = get_database()
    try:
        before = database.client.admin.command("replSetGetStatus")
    except Exception as error:
        raise HTTPException(status_code=503, detail="The configured MongoDB replica set status is unavailable.") from error
    previous_primary = next((
        member.get("name") for member in before.get("members", [])
        if member.get("stateStr") == "PRIMARY"
    ), None)
    if not previous_primary:
        raise HTTPException(status_code=409, detail="No PRIMARY is currently reported; failover was not requested.")

    details = {"previous_primary": previous_primary, "stepdown_seconds": payload.seconds}
    try:
        # Persist intent before the disruptive operation. If audit storage is unavailable,
        # do not send the stepdown command.
        _audit(database, actor["user_id"], "ops.failover", "application-configured-replica-set", "started", details)
        _audit(database, actor["user_id"], "lab.run", "chapter13-failover", "started", details)
    except Exception as error:
        logger.exception("Chapter 13 failover audit could not be recorded")
        raise HTTPException(status_code=503, detail="Failover was not started because its audit record could not be stored.") from error

    try:
        try:
            database.client.admin.command("replSetStepDown", payload.seconds)
        except AutoReconnect:
            # Expected when the driver connection is closed during primary stepdown.
            pass

        deadline = time.monotonic() + 45
        new_primary = None
        after: dict[str, Any] = {}
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                after = database.client.admin.command("replSetGetStatus")
                new_primary = next((
                    member.get("name") for member in after.get("members", [])
                    if member.get("stateStr") == "PRIMARY"
                ), None)
                if new_primary and new_primary != previous_primary:
                    break
            except Exception:
                continue
        if not new_primary or new_primary == previous_primary:
            raise TimeoutError("A different PRIMARY was not observed within 45 seconds.")

        marker = service.mark_election(database, previous_primary, new_primary)
        outcome = {
            **details,
            "new_primary": new_primary,
            "term": after.get("term"),
            "election_marker_at": marker["timestamp"],
        }
        _audit(database, actor["user_id"], "ops.failover", "application-configured-replica-set", "succeeded", outcome)
        _audit(database, actor["user_id"], "lab.run", "chapter13-failover", "succeeded", outcome)
        return {
            "status": "verified",
            "previous_primary": previous_primary,
            "new_primary": new_primary,
            "term": after.get("term"),
            "election_marker": marker,
            "message": "A different PRIMARY was observed after the requested stepdown.",
        }
    except Exception as error:
        logger.warning("Chapter 13 failover did not complete verification (%s)", type(error).__name__)
        try:
            failure = {**details, "error_type": type(error).__name__}
            _audit(database, actor["user_id"], "ops.failover", "application-configured-replica-set", "failed", failure)
            _audit(database, actor["user_id"], "lab.run", "chapter13-failover", "failed", failure)
        except Exception:
            logger.exception("Chapter 13 failover failure audit could not be recorded")
        raise HTTPException(status_code=504, detail="Failover was requested but a new primary could not be verified. Check the live replica-set status before retrying.") from error


@services_router.post("/lab13/kubernetes")
def start_kubernetes_lab(payload: KubernetesStartRequest, request: Request, actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before applying the Chapter 13 Kubernetes manifests.")
    database = get_database()
    try:
        result = service.launch_kubernetes_deployment(request.app.state, actor["user_id"], database)
    except Exception as error:
        logger.exception("Chapter 13 Kubernetes deployment could not be started")
        raise HTTPException(status_code=503, detail="Kubernetes deployment could not be started.") from error
    if not result.get("accepted"):
        try:
            _audit(database, actor["user_id"], "lab.start", "chapter13-kubernetes", result.get("status", "rejected"), {"reason": result.get("detail", "")})
        except Exception:
            logger.exception("Chapter 13 Kubernetes rejection audit could not be recorded")
        code = 409 if result.get("status") == "starting" else 503
        raise HTTPException(status_code=code, detail=result.get("detail", "Kubernetes deployment is unavailable."))
    return result


router = APIRouter()
router.include_router(ops_router)
router.include_router(services_router)

