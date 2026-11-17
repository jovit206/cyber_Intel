"""Faculty-compatible Chapter 16 endpoints over the existing read-only probes."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.database import get_database
from backend.routes.admin import _admin_user, _audit
from backend.services.chapter16 import fraud_review

router = APIRouter(tags=["Chapter 16 operations"])


@router.get("/api/ops/audit")
def audit_events(
    request: Request,
    actor: dict = Depends(_admin_user),
    limit: int = Query(100, ge=1, le=500),
    day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    actor_filter: str | None = Query(None, alias="actor", max_length=128),
):
    del actor
    database = request.app.state.database
    # This is the application's MongoDB audit log, explicitly identified as such;
    # Chapter 16 Cassandra delivery is reported separately as unavailable.
    clauses = []
    if day:
        try:
            start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as error:
            raise HTTPException(422, "day must be a valid UTC date.") from error
        clauses.append({"created_at": {"$gte": start, "$lt": start + timedelta(days=1)}})
    if actor_filter:
        clauses.append({"$or": [{"administrator_id": actor_filter}, {"target_record": actor_filter}]})
    query = {"$and": clauses} if clauses else {}
    rows = list(database.admin_audit_log.find(query, {"_id": 0}).sort("created_at", -1).limit(limit))
    for row in rows:
        value = row.get("created_at")
        if isinstance(value, datetime):
            row["created_at"] = (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()
    return {"events": rows, "source": "MongoDB admin_audit_log", "chapter16_cassandra": "not_configured"}


@router.get("/api/ops/audit/summary")
def audit_summary(request: Request, actor: dict = Depends(_admin_user), days: int = Query(7, ge=1, le=30)):
    del actor
    database = request.app.state.database
    start = datetime.now(timezone.utc) - timedelta(days=days)
    match = {"created_at": {"$gte": start}}
    by_action = list(database.admin_audit_log.aggregate([
        {"$match": match},
        {"$group": {"_id": {"$ifNull": ["$action", "unknown"]}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    daily = list(database.admin_audit_log.aggregate([
        {"$match": match},
        {"$group": {"_id": {"day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}, "kind": {"$ifNull": ["$action", "unknown"]}}, "count": {"$sum": 1}}},
        {"$sort": {"_id.day": 1}},
    ]))
    return {"mongodb_audit": "available", "cassandra_delivery": "not_configured", "outbox_pending": None,
            "days": days, "total": database.admin_audit_log.count_documents(match),
            "by_action": [{"kind": row["_id"], "count": row["count"]} for row in by_action],
            "daily": [{"day": row["_id"]["day"], "kind": row["_id"]["kind"], "count": row["count"]} for row in daily],
            "note": "Counts come from MongoDB admin_audit_log. Cassandra outbox delivery is not integrated into this deployment."}


@router.get("/api/moderation/fraud")
def fraud_candidates(request: Request, actor: dict = Depends(_admin_user), account: str = Query(..., min_length=1, max_length=128)):
    del request
    try:
        return fraud_review(account, "account-review", 3)
    except Exception as error:
        raise HTTPException(503, f"Fraud evidence query unavailable ({type(error).__name__}).") from error


@router.get("/api/moderation/fraud/{account}")
def fraud_account(
    account: str,
    txn_id: str | None = Query(None, max_length=128),
    max_hops: int = Query(3, ge=1, le=3),
    actor: dict = Depends(_admin_user),
):
    del actor
    try:
        return fraud_review(account, txn_id or "account-review", max_hops)
    except Exception as error:
        raise HTTPException(503, f"Fraud evidence query unavailable ({type(error).__name__}).") from error


@router.post("/api/services/lab16/start")
def start_lab16(actor: dict = Depends(_admin_user)):
    _audit(get_database(), actor["user_id"], "lab.start", "chapter16-services", "rejected",
           {"reason": "No safe service orchestrator is configured; no service was started."})
    raise HTTPException(409, detail="Chapter 16 service control is unavailable: no safe orchestrator is configured.")


@router.post("/api/services/lab16/stop")
def stop_lab16(actor: dict = Depends(_admin_user)):
    _audit(get_database(), actor["user_id"], "lab.stop", "chapter16-services", "rejected",
           {"reason": "No safe service orchestrator is configured; no service was stopped."})
    raise HTTPException(409, detail="Chapter 16 service control is unavailable: no safe orchestrator is configured.")
