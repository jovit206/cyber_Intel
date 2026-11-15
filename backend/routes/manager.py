"""Read-only management views. Every route checks the manager role independently."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from backend.database import get_database

router = APIRouter(prefix="/api/manager", tags=["Manager (read only)"])
USER_FIELDS = {"_id": 0, "user_id": 1, "email": 1, "name": 1, "role": 1, "created_at": 1,
               "plan": 1, "renewal_at": 1, "status": 1, "account_status": 1,
               "last_login_at": 1, "credits_allocated": 1, "credits_used": 1}
TRANSACTION_FIELDS = {"_id": 0, "transaction_id": 1, "user_id": 1, "created_at": 1, "type": 1,
                      "amount": 1, "currency": 1, "direction": 1, "status": 1,
                      "description": 1, "balance_after": 1, "failure_reason": 1, "receipt_id": 1}


def _manager(request: Request):
    token = request.cookies.get("aegis_session", "")
    if not token:
        raise HTTPException(401, "Please sign in as a manager.")
    db = get_database()
    session = db.subscription_sessions.find_one({
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": {"$gt": datetime.now(timezone.utc)},
    })
    user = db.subscription_users.find_one({"user_id": (session or {}).get("user_id")},
                                         {"user_id": 1, "role": 1, "status": 1, "account_status": 1})
    if not user:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    if user.get("status", user.get("account_status", "active")) in {"banned", "disabled", "suspended", "inactive"}:
        raise HTTPException(403, "This account is not active.")
    if user.get("role") != "manager":
        raise HTTPException(403, "Manager access is required.")
    return db


@router.get("/dashboard")
def dashboard(request: Request):
    db = _manager(request)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    total = db.wallets.aggregate([
        {"$match": {"user_id": {"$ne": "platform"}}},
        {"$lookup": {"from": "subscription_users", "localField": "user_id", "foreignField": "user_id", "as": "account"}},
        {"$match": {"account.0": {"$exists": True}}},
        {"$group": {"_id": None, "balance": {"$sum": {"$ifNull": ["$balance", 0]}}}},
    ])
    row = next(iter(total), {})
    return {"registered_users": db.subscription_users.count_documents({}),
            "active_users": db.subscription_users.count_documents({"last_login_at": {"$gte": since}}),
            "wallet_balance_total": row.get("balance", 0),
            "wallet_transactions": db.wallet_transactions.count_documents({}),
            "wallet_operations": db.wallet_operations.count_documents({}),
            "subscriptions": {plan: db.subscription_users.count_documents({"plan": plan})
                              for plan in ("free", "plus", "pro")}}


@router.get("/users")
def users(request: Request, q: str = Query("", max_length=100), limit: int = Query(100, ge=1, le=500)):
    db = _manager(request)
    query = {}
    if q.strip():
        import re
        pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
        query["$or"] = [{key: pattern} for key in ("user_id", "email", "name")]
    records = list(db.subscription_users.find(query, USER_FIELDS)
                   .sort("created_at", -1).limit(limit))
    ids = [user["user_id"] for user in records]
    balances = {item["user_id"]: item.get("balance", 0) for item in db.wallets.find(
        {"user_id": {"$in": ids}}, {"user_id": 1, "balance": 1})} if ids else {}
    for user in records:
        user["wallet_balance"] = balances.get(user["user_id"])
    return {"items": records, "total": len(records)}


@router.get("/transactions")
def transactions(request: Request, limit: int = Query(100, ge=1, le=500)):
    db = _manager(request)
    return {"items": list(db.wallet_transactions.find({}, TRANSACTION_FIELDS).sort("created_at", -1).limit(limit))}


@router.get("/transactions/{transaction_id}")
def transaction_detail(request: Request, transaction_id: str):
    db = _manager(request)
    row = db.wallet_transactions.find_one({"transaction_id": transaction_id}, TRANSACTION_FIELDS)
    if not row:
        raise HTTPException(404, "Transaction not found.")
    return row


@router.get("/subscriptions")
def subscriptions(request: Request, plan: str | None = None, limit: int = Query(100, ge=1, le=500)):
    db = _manager(request)
    query = {"plan": plan} if plan else {}
    if plan and plan not in {"free", "plus", "pro"}:
        raise HTTPException(422, "Unsupported subscription plan.")
    rows = list(db.subscription_users.find(query, {"_id": 0, "user_id": 1, "email": 1, "plan": 1,
        "renewal_at": 1, "status": 1, "account_status": 1}).sort("created_at", -1).limit(limit))
    ids = [row["user_id"] for row in rows]
    balances = {wallet["user_id"]: wallet.get("balance", 0) for wallet in db.wallets.find(
        {"user_id": {"$in": ids}}, {"user_id": 1, "balance": 1})} if ids else {}
    for row in rows:
        row["wallet_balance"] = balances.get(row["user_id"])
    return {"items": rows, "total": len(rows)}


@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: str):
    db = _manager(request)
    user = db.subscription_users.find_one({"user_id": user_id}, USER_FIELDS)
    if not user:
        raise HTTPException(404, "User not found.")
    return {"user": user,
            "wallet": db.wallets.find_one({"user_id": user_id}, {"_id": 0}),
            "transactions": list(db.wallet_transactions.find({"user_id": user_id}, TRANSACTION_FIELDS).sort("created_at", -1).limit(50)),
            "subscription_history": list(db.subscription_history.find({"user_id": user_id}, {"_id": 0}).sort("changed_at", -1).limit(50))}


@router.get("/audit")
def audit(request: Request, limit: int = Query(100, ge=1, le=500)):
    db = _manager(request)
    return {"items": list(db.admin_audit_log.find({}, {"_id": 0}).sort("created_at", -1).limit(limit))}
