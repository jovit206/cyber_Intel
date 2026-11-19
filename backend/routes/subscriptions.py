"""Account and billing API for the isolated subscription demonstration."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument

from backend.database import get_database
from backend.services.entitlements import FREE_WALLET_LIMIT, describe_entitlements, effective_plan
from backend.services.subscriptions import (BillingError, PLANS, SESSION_DAYS, create_account,
    add_money, ensure_indexes, password_matches, public_user, purchase_plan, transfer)

logger = logging.getLogger("aegis.subscriptions")
router = APIRouter(prefix="/api/subscriptions", tags=["Subscriptions & Wallets"])
COOKIE_NAME = "aegis_session"


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    # Login supports both regular emails and the five-character local Admin ID.
    # Registration has its own stricter password validator below.
    password: str = Field(min_length=1, max_length=256)


class RegistrationCredentials(Credentials):
    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        checks = (any(ch.islower() for ch in value), any(ch.isupper() for ch in value),
                  any(ch.isdigit() for ch in value), any(not ch.isalnum() for ch in value))
        if len(value) < 12 or sum(checks) < 4:
            raise ValueError("Use at least 12 characters with upper and lowercase letters, a number, and a symbol.")
        return value


class Purchase(BaseModel):
    plan: Literal["free", "plus", "pro"]
    idempotency_key: str = Field(min_length=8, max_length=128)


class WalletTransfer(BaseModel):
    recipient_email: str = Field(min_length=5, max_length=254)
    # Money amounts must be JSON integers; "100" or 100.0 is rejected instead of being coerced.
    amount: int = Field(gt=0, le=1_000_000, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=128)


class WalletTopUp(BaseModel):
    amount: int = Field(strict=True)
    idempotency_key: str = Field(min_length=8, max_length=128)


def _problem(error: BillingError):
    raise HTTPException(status_code=error.status_code,
                        detail={"message": str(error), **error.data}) from error


def _session_user(request: Request):
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db = get_database()
    row = db.subscription_sessions.find_one({"token_hash": token_hash, "expires_at": {"$gt": datetime.now(timezone.utc)}})
    if not row:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    user = db.subscription_users.find_one({"user_id": row["user_id"]})
    if not user:
        raise HTTPException(status_code=401, detail="Account is unavailable.")
    return user


LOGIN_FAILURE_LIMIT = 8
LOGIN_WINDOW = timedelta(minutes=15)


def _login_attempt_key(identity: str, request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    material = f"{identity.strip().casefold()}\0{address}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _reserve_login_attempt(db, key: str, now: datetime) -> None:
    """Atomically reserve one of the limited attempts before password work begins."""
    row = db.auth_login_attempts.find_one({"_id": key}, {"locked_until": 1, "expires_at": 1})
    expires = row.get("expires_at") if row else None
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if row and expires and expires <= now:
        db.auth_login_attempts.delete_one({"_id": key, "expires_at": row["expires_at"]})
        row = None
    locked_until = row.get("locked_until") if row else None
    if locked_until and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if row and locked_until and locked_until > now:
        raise HTTPException(status_code=429, detail="Too many unsuccessful sign-in attempts. Try again later.")
    result = db.auth_login_attempts.find_one_and_update(
        {"_id": key},
        {"$inc": {"failures": 1}, "$set": {"updated_at": now, "expires_at": now + LOGIN_WINDOW},
         "$setOnInsert": {"created_at": now}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    reserved = int(result.get("failures", 0)) if result else 0
    if reserved >= LOGIN_FAILURE_LIMIT:
        db.auth_login_attempts.update_one({"_id": key}, {"$set": {"locked_until": now + LOGIN_WINDOW}})
    # Attempt N may still be a valid login; attempts beyond the limit are rejected.
    if reserved > LOGIN_FAILURE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many unsuccessful sign-in attempts. Try again later.")


@router.get("/plans")
def plans():
    descriptions = {
        "free": ["Live Threat Stream", "Top Threats", f"Wallet balance up to ₹{FREE_WALLET_LIMIT:,}"],
        "plus": ["All supported features with monthly usage limits", "Search & Enrich: 3 successful uses per month", "Usage limits and reset periods are shown in your account"],
        "pro": ["Unlimited access to supported platform features", "No monthly feature-use limits", "Subject to security and system-capacity controls"],
    }
    return {"plans": [{"name": key, **value, "features": descriptions[key]} for key, value in PLANS.items()],
            "currency": "INR", "demo_only": True,
            "policy": "Full monthly price is charged on plan change; no proration. Feature usage resets at the start of each UTC calendar month. Expired paid plans return to Free access."}


@router.post("/register", status_code=201)
def register(credentials: RegistrationCredentials):
    db = get_database()
    try:
        ensure_indexes(db)
        user = create_account(db, email=str(credentials.email), password=credentials.password)
    except BillingError as error:
        _problem(error)
    except Exception:
        # Avoid leaking duplicate-index/database details to a public client, but keep the cause in server logs.
        logger.exception("Account registration failed")
        raise HTTPException(status_code=503, detail="Registration is temporarily unavailable.") from None
    return {"user": user, "message": "Account created. Sign in to continue."}


@router.post("/login")
def login(credentials: Credentials, request: Request, response: Response):
    db = get_database()
    try:
        ensure_indexes(db)
        identity = credentials.email.strip()
        attempt_key = _login_attempt_key(identity, request)
        now = datetime.now(timezone.utc)
        _reserve_login_attempt(db, attempt_key, now)
        query = {"email": identity.lower()} if "@" in identity else {"user_id": identity}
        user = db.subscription_users.find_one(query)
        matches = False
        if user and user.get("password_salt") and user.get("password_hash"):
            matches = password_matches(credentials.password, user["password_salt"], user["password_hash"])
        else:
            # Keep unknown-account failures close to the cost of a real password check.
            from backend.services.subscriptions import password_hash
            password_hash(credentials.password, b"\0" * 32)
        if (not user or not matches or user.get("status", user.get("account_status", "active"))
                in {"disabled", "suspended", "inactive", "banned"}):
            if user and user.get("role") in {"admin", "manager"}:
                db.admin_audit_log.insert_one({"administrator_id": user["user_id"],
                    "actor_role": user["role"], "action": "login", "target_record": user["user_id"],
                    "created_at": now, "outcome": "failure"})
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        db.auth_login_attempts.delete_one({"_id": attempt_key})
        db.subscription_users.update_one({"user_id": user["user_id"]}, {"$set": {"last_login_at": now}})
        user["last_login_at"] = now
        if user.get("role") in {"admin", "manager"}:
            db.admin_audit_log.insert_one({"administrator_id": user["user_id"], "actor_role": user["role"],
                "action": "login", "target_record": user["user_id"], "created_at": now,
                "outcome": "success"})
        token = secrets.token_urlsafe(48)
        prior_token = request.cookies.get(COOKIE_NAME, "")
        if prior_token:
            db.subscription_sessions.delete_one({"token_hash": hashlib.sha256(prior_token.encode()).hexdigest()})
        expires = now + timedelta(days=SESSION_DAYS)
        db.subscription_sessions.insert_one({"token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "user_id": user["user_id"], "created_at": now, "expires_at": expires})
        secure_cookie = (os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
                         or request.scope.get("scheme", "http") == "https")
        response.set_cookie(COOKIE_NAME, token, httponly=True, secure=secure_cookie,
                            samesite="lax", max_age=SESSION_DAYS * 86400, path="/")
        return {"user": public_user(user), "expires_at": expires}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sign-in failed")
        raise HTTPException(status_code=503, detail="Sign in is temporarily unavailable.") from None


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        db = get_database()
        digest = hashlib.sha256(token.encode()).hexdigest()
        session = db.subscription_sessions.find_one({"token_hash": digest})
        if session:
            user = db.subscription_users.find_one({"user_id": session.get("user_id")}, {"role": 1})
            if user and user.get("role") in {"admin", "manager"}:
                db.admin_audit_log.insert_one({"administrator_id": session.get("user_id"),
                    "actor_role": user["role"], "action": "logout", "target_record": session.get("user_id"),
                    "created_at": datetime.now(timezone.utc), "outcome": "success"})
        db.subscription_sessions.delete_one({"token_hash": digest})
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=(
        os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true" or request.scope.get("scheme", "http") == "https"), samesite="lax")
    return {"status": "signed_out"}


@router.get("/me")
def me(request: Request):
    user = _session_user(request)
    db = get_database()
    wallet = db.wallets.find_one({"user_id": user["user_id"]}, {"_id": 0})
    entitlements = describe_entitlements(user, db)
    return {"user": {**public_user(user), "plan": entitlements["plan"]}, "wallet": wallet,
            "wallet_limit": entitlements["wallet_limit"], "entitlements": entitlements,
            "available_credits": max(0, user.get("credits_allocated", 0) - user.get("credits_used", 0))}


@router.get("/entitlements")
def entitlements(request: Request):
    user = _session_user(request)
    result = describe_entitlements(user, get_database())
    return {"entitlements": result}


@router.post("/wallet/top-up")
def wallet_top_up(request: Request, payload: WalletTopUp):
    user = _session_user(request)
    try:
        return add_money(get_database(), user["user_id"], payload.amount, payload.idempotency_key)
    except BillingError as error:
        _problem(error)


@router.get("/purchase-quote/{plan}")
def purchase_quote(request: Request, plan: str):
    user = _session_user(request)
    if plan not in PLANS:
        raise HTTPException(status_code=404, detail="Subscription plan not found.")
    wallet = get_database().wallets.find_one({"user_id": user["user_id"]}, {"balance": 1, "_id": 0})
    if not wallet:
        raise HTTPException(status_code=404, detail="Account wallet was not found.")
    price = PLANS[plan]["price"]
    return {"plan": plan, "price": price, "credits_granted": PLANS[plan]["monthly_credits"],
            "wallet_balance": wallet["balance"], "amount_charged": price,
            "wallet_after": wallet["balance"] - price,
            "sufficient_balance": wallet["balance"] >= price,
            "policy": "Full monthly price; no proration. Changing plans replaces the current monthly credit allocation."}


@router.post("/purchase")
def buy_plan(request: Request, payload: Purchase):
    user = _session_user(request)
    try:
        return purchase_plan(get_database(), user["user_id"], payload.plan, payload.idempotency_key)
    except BillingError as error:
        _problem(error)


@router.post("/transfer")
def send_wallet_transfer(request: Request, payload: WalletTransfer):
    user = _session_user(request)
    try:
        return transfer(get_database(), user["user_id"], str(payload.recipient_email), payload.amount, payload.idempotency_key)
    except BillingError as error:
        _problem(error)


@router.get("/history")
def history(request: Request, limit: int = 50):
    user = _session_user(request)
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="Limit must be from 1 to 100.")
    db = get_database()
    user_id = user["user_id"]
    return {
        "receipts": list(db.subscription_receipts.find({"user_id": user_id}, {"_id": 0, "result": 1,
            "receipt_id": 1, "operation": 1, "status": 1, "attempts": 1, "retry_delays_seconds": 1,
            "created_at": 1}).sort("created_at", -1).limit(limit)),
        "payments": list(db.subscription_payments.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit)),
        "subscription_history": list(db.subscription_history.find({"user_id": user_id}, {"_id": 0}).sort("changed_at", -1).limit(limit)),
        "transactions": list(db.wallet_transactions.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit)),
    }


@router.get("/financial-activity")
def financial_activity(request: Request, page: int = 1, page_size: int = 20,
                       transaction_type: str | None = None, status: str | None = None,
                       q: str = "", date_from: datetime | None = None,
                       date_to: datetime | None = None):
    """Personal, searchable history. The session is the only source of user identity."""
    user = _session_user(request)
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(status_code=422, detail="Page must be positive and page size must be 1 to 100.")
    valid_statuses = {"committed", "pending", "failed", "rolled_back", "refunded"}
    if status and status not in valid_statuses:
        raise HTTPException(status_code=422, detail="Unsupported transaction status filter.")
    if transaction_type and len(transaction_type) > 64:
        raise HTTPException(status_code=422, detail="Transaction type filter is too long.")
    if len(q) > 100:
        raise HTTPException(status_code=422, detail="Search text is too long.")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="Start date must be before end date.")

    user_id = user["user_id"]
    db = get_database()
    query: dict = {"user_id": user_id}
    if transaction_type:
        query["type"] = transaction_type
    if status in {"failed", "rolled_back"}:
        query["status"] = {"$in": ["failed", "rolled_back"]}
    elif status:
        query["status"] = status
    created_range = {}
    if date_from:
        created_range["$gte"] = date_from if date_from.tzinfo else date_from.replace(tzinfo=timezone.utc)
    if date_to:
        end = date_to if date_to.tzinfo else date_to.replace(tzinfo=timezone.utc)
        if end.hour == end.minute == end.second == end.microsecond == 0:
            created_range["$lt"] = end + timedelta(days=1)
        else:
            created_range["$lte"] = end
    if created_range:
        query["created_at"] = created_range
    if q.strip():
        pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
        query["$or"] = [{"transaction_id": pattern}, {"description": pattern}, {"type": pattern}]

    include_failed = status in (None, "failed", "rolled_back")
    successful_keys = set()
    failure_rows = []
    if include_failed:
        successful_keys = {row.get("idempotency_key") for row in db.wallet_operations.find(
            {"user_id": user_id}, {"idempotency_key": 1, "_id": 0}) if row.get("idempotency_key")}
        failed_query: dict = {"user_id": user_id, "status": {"$in": ["failed", "rolled_back"]}}
        if successful_keys:
            failed_query["idempotency_key"] = {"$nin": list(successful_keys)}
        if transaction_type:
            failed_query["operation"] = transaction_type
        if created_range:
            failed_query["created_at"] = created_range
        if q.strip():
            failed_query["$or"] = [
                {"receipt_id": pattern}, {"operation": pattern}, {"result.message": pattern},
            ]
        seen_keys = set()
        cursor = db.subscription_receipts.find(failed_query, {"_id": 0}).sort("created_at", -1).limit(10_000)
        for receipt in cursor:
            request_data = receipt.get("request_data") or {}
            idempotency_key = receipt.get("idempotency_key")
            if idempotency_key and idempotency_key in seen_keys:
                continue
            if idempotency_key:
                seen_keys.add(idempotency_key)
            failed_rows = {
                "transaction_id": receipt["receipt_id"], "receipt_id": receipt["receipt_id"],
                "type": receipt.get("operation", "unknown"),
                "description": receipt.get("result", {}).get("message", "Operation rolled back."),
                "amount": request_data.get("amount", 0), "currency": "INR", "direction": "none",
                "status": "rolled_back", "rolled_back": True,
                "failure_reason": receipt.get("result", {}).get("message"),
                "balance_before": receipt.get("result", {}).get("details", {}).get("wallet_balance"),
                "balance_after": receipt.get("result", {}).get("details", {}).get("wallet_balance"),
                "plan_before": None, "plan_after": request_data.get("plan"),
                "credits_delta": None, "created_at": receipt.get("created_at"),
                "request_data": {key: value for key, value in request_data.items()
                                 if key in {"plan", "amount", "feature", "credits"}},
            }
            failure_rows.append(failed_rows)

    offset = (page - 1) * page_size
    fetch_limit = offset + page_size
    tx_total = db.wallet_transactions.count_documents(query)
    committed = list(db.wallet_transactions.find(query, {"_id": 0, "user_id": 0})
                     .sort("created_at", -1).limit(fetch_limit))
    merged = committed + failure_rows
    merged.sort(key=lambda row: row.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    summary_groups = list(db.wallet_transactions.aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": {"type": "$type", "status": "$status"},
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "credits_delta": {"$sum": {"$ifNull": ["$credits_delta", 0]}}}},
    ]))
    total_added = sum(row["amount"] for row in summary_groups
                      if row["_id"].get("type") in {"wallet_top_up", "wallet_opening_balance"}
                      and row["_id"].get("status") == "committed")
    subscription_spending = sum(row["amount"] for row in summary_groups
                                if row["_id"].get("type") == "subscription_purchase"
                                and row["_id"].get("status") == "committed")
    refunds = sum(row["amount"] for row in summary_groups
                  if row["_id"].get("type") == "refund" or row["_id"].get("status") == "refunded")
    credits_consumed = -sum(row["credits_delta"] for row in summary_groups if row["credits_delta"] < 0)
    wallet = db.wallets.find_one({"user_id": user_id}, {"balance": 1, "_id": 0}) or {}
    total_count = tx_total + len(failure_rows)
    return {
        "items": merged[offset:offset + page_size], "page": page, "page_size": page_size,
        "total": total_count, "total_pages": (total_count + page_size - 1) // page_size,
        "available_types": sorted(
            {row["_id"].get("type") for row in summary_groups if row["_id"].get("type")}
            | {row.get("type") for row in failure_rows if row.get("type")}),
        "summary": {"wallet_balance": wallet.get("balance", 0), "total_money_added": total_added,
                    "subscription_spending": subscription_spending, "refunds": refunds,
                    "credits_consumed": credits_consumed},
    }


@router.get("/financial-activity/{transaction_id}")
def financial_activity_details(request: Request, transaction_id: str):
    user = _session_user(request)
    db = get_database()
    user_id = user["user_id"]
    transaction = db.wallet_transactions.find_one(
        {"transaction_id": transaction_id, "user_id": user_id}, {"_id": 0, "user_id": 0})
    if transaction:
        receipt = None
        if transaction.get("receipt_id"):
            receipt = db.subscription_receipts.find_one(
                {"receipt_id": transaction["receipt_id"], "user_id": user_id},
                {"_id": 0, "receipt_id": 1, "operation": 1, "status": 1, "created_at": 1, "result": 1})
        payment_query: dict = {"transaction_id": transaction_id, "user_id": user_id}
        if transaction.get("idempotency_key"):
            payment_query = {"user_id": user_id, "$or": [
                {"transaction_id": transaction_id},
                {"idempotency_key": transaction["idempotency_key"]},
            ]}
        return {"transaction": transaction, "receipt": receipt,
                "payments": list(db.subscription_payments.find(payment_query, {"_id": 0})),
                "wallet_ledger": list(db.wallet_ledger.find(
                    {"transaction_id": transaction_id, "user_id": user_id}, {"_id": 0})),
                "credit_ledger": list(db.credit_ledger.find(
                    {"transaction_id": transaction_id, "user_id": user_id}, {"_id": 0})),
                "subscription_changes": list(db.subscription_history.find(
                    {"transaction_id": transaction_id, "user_id": user_id}, {"_id": 0}))}
    receipt = db.subscription_receipts.find_one(
        {"receipt_id": transaction_id, "user_id": user_id, "status": {"$in": ["failed", "rolled_back"]}},
        {"_id": 0, "idempotency_key": 0})
    if receipt:
        return {"transaction": {"transaction_id": transaction_id, "type": receipt.get("operation"),
                    "status": "rolled_back", "description": receipt.get("result", {}).get("message"),
                    "created_at": receipt.get("created_at")}, "receipt": receipt,
                "payments": [], "wallet_ledger": [], "credit_ledger": [], "subscription_changes": []}
    raise HTTPException(status_code=404, detail="Financial transaction not found.")


@router.get("/receipts/{receipt_id}")
def receipt_details(request: Request, receipt_id: str):
    user = _session_user(request)
    receipt = get_database().subscription_receipts.find_one(
        {"receipt_id": receipt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return receipt


@router.get("/admin/summary")
def admin_summary(request: Request):
    user = _session_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    db = get_database()
    return {
        "users_by_plan": {row["_id"]: row["count"] for row in db.subscription_users.aggregate([
            {"$group": {"_id": "$plan", "count": {"$sum": 1}}}])},
        "revenue_total": list(db.subscription_payments.aggregate([
            {"$match": {"status": "paid_demo"}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]))[0].get("total", 0)
            if db.subscription_payments.count_documents({"status": "paid_demo"}) else 0,
        "successful_operations": db.subscription_receipts.count_documents({"status": "committed"}),
        "platform_wallet_balance": (db.wallets.find_one({"user_id": "platform"}, {"balance": 1, "_id": 0}) or {}).get("balance", 0),
        "negative_wallets": db.wallets.count_documents({"balance": {"$lt": 0}}),
    }


@router.get("/admin/audit")
def admin_audit(request: Request):
    user = _session_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    db = get_database()
    wallets = list(db.wallets.find({}, {"_id": 0, "user_id": 1, "balance": 1}))
    ledger_totals = {row["_id"]: row["total"] for row in db.wallet_ledger.aggregate([
        {"$group": {"_id": "$user_id", "total": {"$sum": "$delta"}}}])}
    wallet_mismatches = [{"user_id": wallet["user_id"], "wallet_balance": wallet.get("balance", 0),
                          "ledger_balance": ledger_totals.get(wallet["user_id"], 0)}
                         for wallet in wallets if wallet.get("balance", 0) != ledger_totals.get(wallet["user_id"], 0)]
    money_ledger = list(db.wallet_ledger.aggregate([
        {"$group": {"_id": "$kind", "total": {"$sum": "$delta"}}}]))
    by_kind = {row["_id"]: row["total"] for row in money_ledger}
    paid = list(db.subscription_payments.aggregate([
        {"$match": {"status": "paid_demo"}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]))
    paid_total = paid[0]["total"] if paid else 0
    payment_ledger_ok = (by_kind.get("subscription_payment", 0) == -paid_total
                         and by_kind.get("subscription_revenue", 0) == paid_total)
    receipt_ids = {row["receipt_id"] for row in db.subscription_receipts.find({}, {"receipt_id": 1})}
    operations = list(db.wallet_operations.find({}, {"result.receipt_id": 1, "idempotency_key": 1, "user_id": 1}))
    operation_receipt_gaps = [row["idempotency_key"] for row in operations
                              if row.get("result", {}).get("receipt_id") not in receipt_ids]
    expected_supply = sum(row.get("delta", 0) for row in db.wallet_ledger.find(
        {"kind": {"$in": ["demo_opening_balance", "simulated_wallet_top_up"]}}, {"delta": 1}))
    total_wallets = sum(wallet.get("balance", 0) for wallet in wallets)
    return {
        "passed": not wallet_mismatches and payment_ledger_ok and not operation_receipt_gaps
                  and db.wallets.count_documents({"balance": {"$lt": 0}}) == 0
                  and total_wallets == expected_supply,
        "money_conservation": {"wallet_total": total_wallets, "explicit_demo_opening_total": expected_supply,
                               "matches": total_wallets == expected_supply},
        "wallet_ledger_matches": not wallet_mismatches,
        "wallet_mismatches": wallet_mismatches,
        "subscription_payments_match_ledger": payment_ledger_ok,
        "negative_wallets": db.wallets.count_documents({"balance": {"$lt": 0}}),
        "operation_receipt_gaps": operation_receipt_gaps,
        "duplicate_idempotency_keys": max(0, db.wallet_operations.count_documents({}) -
            len(db.wallet_operations.distinct("idempotency_key"))),
        "note": "Read-only audit. Discrepancies are reported; no balances or records are corrected.",
    }
