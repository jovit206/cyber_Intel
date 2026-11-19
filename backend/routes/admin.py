"""Administrator-only read views over existing account and financial records."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.database import get_database
from backend.services.subscriptions import BillingError, add_money

router = APIRouter(prefix="/api/admin", tags=["Administrator"])
COOKIE_NAME = "aegis_session"
VALID_STATUSES = {"committed", "pending", "failed", "rolled_back", "refunded"}
SAFE_USER_FIELDS = {
    "_id": 0, "user_id": 1, "email": 1, "name": 1, "role": 1, "created_at": 1, "plan": 1,
    "renewal_at": 1, "status": 1, "account_status": 1, "last_login_at": 1,
}
SAFE_TRANSACTION_FIELDS = {
    "_id": 0, "transaction_id": 1, "user_id": 1, "created_at": 1, "type": 1,
    "amount": 1, "currency": 1, "direction": 1, "status": 1,
    "description": 1, "balance_after": 1, "failure_reason": 1,
    "receipt_id": 1,
}


def _admin_user(request: Request) -> dict:
    """Independently authenticate and authorize every endpoint in this router."""
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Please sign in as an administrator.")
    db = get_database()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    session = db.subscription_sessions.find_one({
        "token_hash": token_hash, "expires_at": {"$gt": datetime.now(timezone.utc)},
    })
    if not session:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    user = db.subscription_users.find_one({"user_id": session.get("user_id")},
        {"user_id": 1, "role": 1, "status": 1, "account_status": 1})
    if not user:
        raise HTTPException(status_code=401, detail="Account is unavailable.")
    if user.get("status", user.get("account_status", "active")) in {"disabled", "suspended", "inactive", "banned"}:
        raise HTTPException(status_code=403, detail="This account is not active.")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def _audit(db, actor: str, action: str, target: str, outcome: str, details: dict | None = None, session=None) -> None:
    db.admin_audit_log.insert_one({
        "administrator_id": actor, "actor_role": "admin", "action": action, "target_record": target,
        "created_at": datetime.now(timezone.utc), "outcome": outcome,
        "details": details or {},
    }, **({"session": session} if session is not None else {}))


def _date_filter(date_from: datetime | None, date_to: datetime | None) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="Start date must be before end date.")
    value: dict = {}
    if date_from:
        value["$gte"] = date_from if date_from.tzinfo else date_from.replace(tzinfo=timezone.utc)
    if date_to:
        value["$lte"] = date_to if date_to.tzinfo else date_to.replace(tzinfo=timezone.utc)
    return value


def _failed_receipt_pipeline(query: dict) -> list[dict]:
    return [
        {"$match": query},
        {"$sort": {"created_at": -1, "receipt_id": 1}},
        {"$group": {"_id": {"$ifNull": ["$idempotency_key", "$receipt_id"]}, "row": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$row"}},
        {"$lookup": {"from": "wallet_operations", "let": {"key": "$idempotency_key"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$ne": ["$$key", None]}, {"$eq": ["$idempotency_key", "$$key"]},
            ]}}}, {"$project": {"_id": 1}}], "as": "successful_operations"}},
        {"$match": {"successful_operations": {"$size": 0}}},
    ]


def _failure_view(row: dict) -> dict:
    request = row.get("request_data") or {}
    result = row.get("result") or {}
    details = result.get("details") or {}
    return {
        "transaction_id": row.get("receipt_id"), "receipt_id": row.get("receipt_id"),
        "user_id": row.get("user_id"), "created_at": row.get("created_at"),
        "type": row.get("operation", "unknown"), "amount": request.get("amount", 0),
        "currency": "INR", "direction": "none", "status": row.get("status", "rolled_back"),
        "description": result.get("message", "Operation rolled back."),
        "failure_reason": result.get("message"),
        "balance_after": details.get("wallet_balance"),
    }


def _transaction_filters(user_id: str | None, transaction_type: str | None, status: str | None,
                         q: str, date_from: datetime | None, date_to: datetime | None):
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported transaction status.")
    if transaction_type and len(transaction_type) > 64:
        raise HTTPException(status_code=422, detail="Transaction type is too long.")
    if len(q) > 100:
        raise HTTPException(status_code=422, detail="Search text must be 100 characters or fewer.")
    query: dict = {}
    if user_id:
        query["user_id"] = user_id
    if transaction_type:
        query["type"] = transaction_type
    if status:
        query["status"] = status
    date_range = _date_filter(date_from, date_to)
    if date_range:
        query["created_at"] = date_range
    if q.strip():
        pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
        query["$or"] = [{field: pattern} for field in ("transaction_id", "receipt_id", "user_id", "description", "type")]
    return query


@router.get("/dashboard")
def dashboard(request: Request):
    _admin_user(request)
    db = get_database()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    transaction_groups = list(db.wallet_transactions.aggregate([
        {"$group": {"_id": {"type": "$type", "status": "$status"},
                    "count": {"$sum": 1}, "amount": {"$sum": {"$ifNull": ["$amount", 0]}}}},
    ]))
    failed_query = {"status": {"$in": ["failed", "rolled_back"]}}
    failed_count_pipeline = _failed_receipt_pipeline(failed_query) + [{"$count": "total"}]
    failed_receipts = list(db.subscription_receipts.aggregate(failed_count_pipeline))
    failed_receipt_count = failed_receipts[0]["total"] if failed_receipts else 0
    totals = {"total_transactions": db.wallet_transactions.count_documents({}) + failed_receipt_count,
              "successful_transactions": 0, "failed_transactions": failed_receipt_count,
              "total_deposits": 0, "total_subscription_purchases": 0, "total_refunds": 0}
    for row in transaction_groups:
        kind, state = row["_id"].get("type"), row["_id"].get("status")
        amount, count = row["amount"], row["count"]
        if state in {"committed", "refunded"}:
            totals["successful_transactions"] += count
        if state in {"failed", "rolled_back"}:
            totals["failed_transactions"] += count
        if kind == "wallet_top_up" and state == "committed":
            totals["total_deposits"] += amount
        if kind == "subscription_purchase" and state == "committed":
            totals["total_subscription_purchases"] += amount
        if (kind in {"wallet_refund", "refund"} and state in {"committed", "refunded"}) or state == "refunded":
            totals["total_refunds"] += amount
    wallet_total = list(db.wallets.aggregate([
        {"$match": {"user_id": {"$ne": "platform"}}},
        {"$lookup": {"from": "subscription_users", "localField": "user_id", "foreignField": "user_id", "as": "account"}},
        {"$match": {"account.0": {"$exists": True}}},
        {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$balance", 0]}}}},
    ]))
    return {
        "generated_at": now,
        "activity_period": {"days": 30, "since": cutoff, "based_on": "successful login timestamps; earlier activity is not inferred"},
        "registered_users": db.subscription_users.count_documents({}),
        "active_users": db.subscription_users.count_documents({"last_login_at": {"$gte": cutoff}}),
        "banned_users": db.subscription_users.count_documents({"$or": [{"status": "banned"}, {"account_status": "banned"}]}),
        "total_wallet_balances": wallet_total[0]["total"] if wallet_total else 0,
        "currency": "INR", "lifetime": totals,
        "wallet_transaction_count": db.wallet_transactions.count_documents({}),
        "wallet_operation_count": db.wallet_operations.count_documents({}),
        "subscription_payment_count": db.subscription_payments.count_documents({}),
        "subscriptions": {
            "free": db.subscription_users.count_documents({"$or": [{"plan": "free"}, {"plan": {"$exists": False}}]}),
            "active_paid": db.subscription_users.count_documents({"plan": {"$in": ["plus", "pro"]}, "renewal_at": {"$gt": now}}),
            "expired_paid": db.subscription_users.count_documents({"plan": {"$in": ["plus", "pro"]}, "renewal_at": {"$lte": now}}),
            "plus": db.subscription_users.count_documents({"plan": "plus"}),
            "pro": db.subscription_users.count_documents({"plan": "pro"}),
        },
        "recent_users": list(db.subscription_users.find({}, SAFE_USER_FIELDS).sort("created_at", -1).limit(5)),
        "recent_transactions": list(db.wallet_transactions.find({}, SAFE_TRANSACTION_FIELDS).sort("created_at", -1).limit(5)),
        "recent_admin_actions": list(db.admin_audit_log.find({}, {"_id": 0}).sort("created_at", -1).limit(10)),
    }


@router.get("/users")
def users(request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
          q: str = Query("", max_length=100), plan: str | None = None,
          status: str | None = None, sort_by: Literal["user_id", "email", "name", "created_at", "plan", "last_login_at"] = "created_at",
          sort_dir: Literal["asc", "desc"] = "desc"):
    _admin_user(request)
    db = get_database()
    query: dict = {}
    if plan:
        if plan not in {"free", "plus", "pro"}:
            raise HTTPException(status_code=422, detail="Unsupported plan filter.")
        query["plan"] = plan
    if status:
        if status not in {"active", "suspended", "disabled", "inactive", "banned"}:
            raise HTTPException(status_code=422, detail="Unsupported account status filter.")
        query["$or"] = [{"status": status}, {"account_status": status}]
    if q.strip():
        pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
        query["$and"] = [{"$or": [{"user_id": pattern}, {"email": pattern}, {"name": pattern}]}]
    total = db.subscription_users.count_documents(query)
    direction = -1 if sort_dir == "desc" else 1
    rows = list(db.subscription_users.find(query, SAFE_USER_FIELDS).sort(sort_by, direction)
                .skip((page - 1) * page_size).limit(page_size))
    ids = [row["user_id"] for row in rows]
    wallets = {row["user_id"]: row.get("balance", 0) for row in db.wallets.find(
        {"user_id": {"$in": ids}}, {"user_id": 1, "balance": 1})} if ids else {}
    transaction_counts = {row["_id"]: row["count"] for row in db.wallet_transactions.aggregate([
        {"$match": {"user_id": {"$in": ids}}}, {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
    ])} if ids else {}
    items = []
    for row in rows:
        item = {key: row[key] for key in ("user_id", "email", "name", "created_at", "plan", "renewal_at", "last_login_at") if key in row}
        item["account_status"] = row.get("status", row.get("account_status"))
        item["wallet_balance"] = wallets.get(row["user_id"])
        item["transaction_count"] = transaction_counts.get(row["user_id"], 0)
        items.append(item)
    return {"items": items, "page": page, "page_size": page_size, "total": total,
            "pages": (total + page_size - 1) // page_size}


@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: str):
    _admin_user(request)
    db = get_database()
    user = db.subscription_users.find_one({"user_id": user_id}, SAFE_USER_FIELDS)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    wallet = db.wallets.find_one({"user_id": user_id}, {"_id": 0, "user_id": 0})
    transactions = list(db.wallet_transactions.find({"user_id": user_id}, SAFE_TRANSACTION_FIELDS)
                        .sort("created_at", -1).limit(50))
    history = list(db.subscription_history.find({"user_id": user_id}, {"_id": 0})
                   .sort("changed_at", -1).limit(50))
    failed_query = {"user_id": user_id, "status": {"$in": ["failed", "rolled_back"]}}
    failed = [_failure_view(row) for row in db.subscription_receipts.aggregate(
        _failed_receipt_pipeline(failed_query) + [{"$limit": 50}])]
    financial_history = sorted(transactions + failed,
        key=lambda row: row.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:50]
    return {"user": user, "wallet": wallet, "transactions": financial_history,
            "subscription_history": history}


@router.get("/subscriptions")
def subscriptions(request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                  plan: str | None = None):
    _admin_user(request)
    db = get_database()
    query = {"plan": plan} if plan else {}
    if plan and plan not in {"free", "plus", "pro"}:
        raise HTTPException(status_code=422, detail="Unsupported plan filter.")
    total = db.subscription_users.count_documents(query)
    users_page = list(db.subscription_users.find(query, SAFE_USER_FIELDS).sort("created_at", -1)
                      .skip((page - 1) * page_size).limit(page_size))
    ids = [user["user_id"] for user in users_page]
    wallets = {row["user_id"]: row.get("balance", 0) for row in db.wallets.find(
        {"user_id": {"$in": ids}}, {"user_id": 1, "balance": 1})} if ids else {}
    return {"items": [{"user_id": user["user_id"], "email": user.get("email"),
                        "plan": user.get("plan", "free"), "renewal_at": user.get("renewal_at"),
                        "wallet_balance": wallets.get(user["user_id"])} for user in users_page],
            "page": page, "page_size": page_size, "total": total}


@router.get("/wallets/summary")
def wallet_summary(request: Request):
    _admin_user(request)
    db = get_database()
    rows = list(db.wallets.aggregate([
        {"$match": {"user_id": {"$ne": "platform"}}},
        {"$lookup": {"from": "subscription_users", "localField": "user_id", "foreignField": "user_id", "as": "account"}},
        {"$match": {"account.0": {"$exists": True}}},
        {"$group": {"_id": None, "total_balance": {"$sum": {"$ifNull": ["$balance", 0]}},
                    "wallet_count": {"$sum": 1}}},
    ]))
    return rows[0] if rows else {"total_balance": 0, "wallet_count": 0}


@router.get("/transactions")
def transactions(request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                 q: str = Query("", max_length=100), user_id: str | None = None,
                 transaction_type: str | None = None, status: str | None = None,
                 date_from: datetime | None = None, date_to: datetime | None = None,
                 sort_by: Literal["created_at", "amount", "type", "status", "transaction_id", "user_id"] = "created_at",
                 sort_dir: Literal["asc", "desc"] = "desc"):
    _admin_user(request)
    db = get_database()
    query = _transaction_filters(user_id, transaction_type, status, q, date_from, date_to)
    direction = -1 if sort_dir == "desc" else 1
    offset = (page - 1) * page_size
    fetch_count = offset + page_size
    rows = list(db.wallet_transactions.find(query, SAFE_TRANSACTION_FIELDS)
                .sort(sort_by, direction).limit(fetch_count))
    failures = []
    if not status or status in {"failed", "rolled_back"}:
        failed_query: dict = {"status": {"$in": ["failed", "rolled_back"]}}
        if user_id:
            failed_query["user_id"] = user_id
        if transaction_type:
            failed_query["operation"] = transaction_type
        if q.strip():
            pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
            failed_query["$or"] = [{field: pattern} for field in ("receipt_id", "user_id", "operation", "result.message")]
        date_range = _date_filter(date_from, date_to)
        if date_range:
            failed_query["created_at"] = date_range
        pipeline = _failed_receipt_pipeline(failed_query) + [{"$sort": {sort_by if sort_by != "transaction_id" else "receipt_id": direction}},
                                                              {"$limit": fetch_count}]
        failures = [_failure_view(row) for row in db.subscription_receipts.aggregate(pipeline)]
    combined = sorted(rows + failures, key=lambda row: row.get(sort_by, row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=direction < 0)
    # Stable, server-side sort with the requested field; filter composition above occurs in MongoDB.
    transaction_total = db.wallet_transactions.count_documents(query)
    if not status or status in {"failed", "rolled_back"}:
        count_rows = list(db.subscription_receipts.aggregate(_failed_receipt_pipeline(failed_query) + [{"$count": "total"}]))
        transaction_total += count_rows[0]["total"] if count_rows else 0
    page_rows = combined[offset:offset + page_size]
    user_ids = list({row["user_id"] for row in page_rows if row.get("user_id")})
    user_info = {row["user_id"]: row for row in db.subscription_users.find(
        {"user_id": {"$in": user_ids}}, {"user_id": 1, "email": 1, "name": 1})} if user_ids else {}
    for row in page_rows:
        account = user_info.get(row.get("user_id"), {})
        row["user"] = {key: account[key] for key in ("user_id", "email", "name") if key in account}
    return {"items": page_rows, "page": page, "page_size": page_size, "total": transaction_total,
            "pages": (transaction_total + page_size - 1) // page_size,
            "summary": _transaction_summary(db)}


def _transaction_summary(db) -> dict:
    result = {"deposits": 0, "subscription_charges": 0, "refunds": 0, "failed": 0}
    for row in db.wallet_transactions.aggregate([
        {"$group": {"_id": {"type": "$type", "status": "$status"},
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}}, "count": {"$sum": 1}}},
    ]):
        kind, state = row["_id"].get("type"), row["_id"].get("status")
        if kind == "wallet_top_up" and state == "committed": result["deposits"] += row["amount"]
        if kind == "subscription_purchase" and state == "committed": result["subscription_charges"] += row["amount"]
        if state in {"failed", "rolled_back"}: result["failed"] += row["count"]
        if state == "refunded" or kind in {"wallet_refund", "refund"}: result["refunds"] += row["amount"]
    failed = list(db.subscription_receipts.aggregate(_failed_receipt_pipeline(
        {"status": {"$in": ["failed", "rolled_back"]}}) + [{"$count": "total"}]))
    result["failed"] += failed[0]["total"] if failed else 0
    return result


@router.get("/transactions/{transaction_id}")
def transaction_detail(request: Request, transaction_id: str):
    _admin_user(request)
    db = get_database()
    row = db.wallet_transactions.find_one({"transaction_id": transaction_id}, SAFE_TRANSACTION_FIELDS)
    if row:
        user = db.subscription_users.find_one({"user_id": row.get("user_id")}, {"user_id": 1, "email": 1, "name": 1})
        row["user"] = user or {"user_id": row.get("user_id")}
        return row
    receipt = db.subscription_receipts.aggregate(_failed_receipt_pipeline({
        "receipt_id": transaction_id, "status": {"$in": ["failed", "rolled_back"]},
    }) + [{"$limit": 1}])
    found = next(iter(receipt), None)
    if not found:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    result = _failure_view(found)
    account = db.subscription_users.find_one({"user_id": result["user_id"]}, {"user_id": 1, "email": 1, "name": 1})
    result["user"] = account or {"user_id": result["user_id"]}
    return result


class StatusChange(BaseModel):
    status: Literal["active", "suspended", "disabled", "banned"]


@router.patch("/users/{user_id}/status")
def change_user_status(user_id: str, payload: StatusChange, request: Request):
    actor = _admin_user(request)
    db = get_database()
    target = db.subscription_users.find_one({"user_id": user_id}, {"status": 1, "account_status": 1, "user_id": 1, "role": 1})
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.get("role") == "admin" or user_id == actor["user_id"]:
        raise HTTPException(status_code=409, detail="Administrator accounts cannot be banned or disabled here.")
    field = "status" if "status" in target else "account_status"
    previous = target.get(field, "active")
    action = "account_banned" if payload.status == "banned" else "account_unbanned"
    with db.client.start_session() as session:
        with session.start_transaction():
            db.subscription_users.update_one({"user_id": user_id}, {"$set": {field: payload.status}}, session=session)
            _audit(db, actor["user_id"], action, user_id, "updated",
                   {"field": field, "previous": previous, "new": payload.status}, session=session)
    return {"user_id": user_id, "status": payload.status}


class WalletCredit(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    amount: int = Field(gt=0, le=100_000, strict=True)  # a JSON integer; "100" is not coerced
    reason: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.post("/wallets/credit")
def wallet_credit(payload: WalletCredit, request: Request):
    actor = _admin_user(request)
    try:
        return add_money(get_database(), payload.user_id, payload.amount, payload.idempotency_key,
                         administrator_id=actor["user_id"], reason=payload.reason.strip())
    except BillingError as error:
        raise HTTPException(status_code=error.status_code,
                            detail={"message": str(error), **error.data}) from error


@router.get("/audit")
def audit_log(request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    _admin_user(request)
    db = get_database()
    collection = db.admin_audit_log
    total = collection.count_documents({})
    items = list(collection.find({}, {"_id": 0}).sort("created_at", -1)
                 .skip((page - 1) * page_size).limit(page_size))
    return {"items": items, "page": page, "page_size": page_size, "total": total}
