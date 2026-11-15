"""Transactional wallet, subscription and session primitives for the Lab 10 extension."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from backend.services.entitlements import FREE_WALLET_LIMIT, effective_plan

PLANS: dict[str, dict[str, int]] = {
    "free": {"price": 0, "monthly_credits": 100},
    "plus": {"price": 299, "monthly_credits": 1000},
    "pro": {"price": 799, "monthly_credits": 5000},
}
MAX_TOP_UP = 100_000
PASSWORD_ITERATIONS = 310_000
SESSION_DAYS = 7


class BillingError(Exception):
    def __init__(self, message: str, status_code: int = 400, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data or {}


def password_hash(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(32)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return salt, derived


def password_matches(password: str, salt: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(password_hash(password, salt)[1], expected)


def ensure_indexes(db) -> None:
    db.subscription_users.create_index("email", unique=True, name="subscription_email_unique")
    db.subscription_users.create_index("user_id", unique=True, name="subscription_user_id_unique")
    db.subscription_sessions.create_index("token_hash", unique=True, name="session_token_unique")
    db.subscription_sessions.create_index("expires_at", expireAfterSeconds=0, name="session_expiry_ttl")
    db.wallets.create_index("user_id", unique=True, name="wallet_user_unique")
    db.wallet_transactions.create_index([("user_id", 1), ("idempotency_key", 1)],
                                       unique=True, name="wallet_transaction_idempotency_unique",
                                       partialFilterExpression={"idempotency_key": {"$type": "string"}})
    db.wallet_transactions.create_index([("user_id", 1), ("created_at", -1)])
    db.wallet_transactions.create_index([("transaction_id", 1), ("user_id", 1)], unique=True,
                                       name="financial_transaction_user_unique",
                                       partialFilterExpression={"transaction_id": {"$type": "string"},
                                                               "user_id": {"$type": "string"}})
    db.wallet_transactions.create_index([("user_id", 1), ("type", 1), ("created_at", -1)],
                                       name="financial_history_type_date")
    db.wallet_transactions.create_index([("user_id", 1), ("status", 1), ("created_at", -1)],
                                       name="financial_history_status_date")
    db.wallet_operations.create_index("idempotency_key", unique=True, name="wallet_idempotency_unique")
    db.subscription_receipts.create_index("receipt_id", unique=True, name="receipt_id_unique")
    db.subscription_receipts.create_index([("user_id", 1), ("created_at", -1)])
    db.subscription_receipts.create_index([("user_id", 1), ("idempotency_key", 1)], unique=True,
        name="failed_receipt_idempotency_unique",
        partialFilterExpression={"status": "rolled_back", "idempotency_key": {"$type": "string"}})
    db.subscription_history.create_index([("user_id", 1), ("changed_at", -1)])
    db.subscription_users.create_index("last_login_at", name="subscription_last_login")
    db.auth_login_attempts.create_index("expires_at", expireAfterSeconds=0, name="auth_attempt_expiry_ttl")
    db.admin_audit_log.create_index([("created_at", -1)], name="admin_audit_created_at")


def create_account(db, *, email: str, password: str, role: str = "user", plan: str = "free",
                   opening_balance: int = 500) -> dict[str, Any]:
    email = email.strip().lower()
    if "@" not in email or len(email) > 254:
        raise BillingError("Enter a valid email address.")
    password_checks = (any(ch.islower() for ch in password), any(ch.isupper() for ch in password),
                       any(ch.isdigit() for ch in password), any(not ch.isalnum() for ch in password))
    if len(password) < 12 or not all(password_checks):
        raise BillingError("Use at least 12 characters with upper and lowercase letters, a number, and a symbol.")
    if role not in {"user", "admin", "manager"} or plan not in PLANS or opening_balance < 0:
        raise BillingError("Invalid account setup.")
    if plan == "free" and opening_balance > FREE_WALLET_LIMIT:
        raise BillingError(f"Free-plan opening wallet balance cannot exceed ₹{FREE_WALLET_LIMIT:,}.")
    salt, digest = password_hash(password)
    now = datetime.now(timezone.utc)
    user_id = secrets.token_urlsafe(18)
    user = {"user_id": user_id, "email": email, "password_salt": salt, "password_hash": digest,
            "role": role, "plan": plan, "created_at": now, "renewal_at": now + timedelta(days=30),
            "credits_allocated": PLANS[plan]["monthly_credits"], "credits_used": 0}
    try:
        with db.client.start_session() as session:
            with session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"),
                                           read_preference=ReadPreference.PRIMARY):
                db.subscription_users.insert_one(user, session=session)
                # Opening funds are explicitly recorded as demo allocations in the same transaction.
                db.wallets.insert_one({"user_id": user_id, "balance": opening_balance, "currency": "INR", "updated_at": now}, session=session)
                if opening_balance:
                    opening_transaction_id = f"WLT-{secrets.token_hex(8).upper()}"
                    opening_ledger_id = secrets.token_urlsafe(18)
                    db.wallet_ledger.insert_one({"entry_id": opening_ledger_id, "user_id": user_id,
                        "transaction_id": opening_transaction_id, "delta": opening_balance,
                        "kind": "demo_opening_balance", "created_at": now}, session=session)
                    record_financial_transaction(db, session, user_id=user_id,
                        transaction_type="wallet_opening_balance", description="Simulated demonstration opening balance",
                        amount=opening_balance, direction="credit", balance_before=0,
                        balance_after=opening_balance, status="committed", created_at=now,
                        transaction_id=opening_transaction_id, ledger_id=opening_ledger_id,
                        metadata={"source": "account_registration", "simulated": True})
                db.credit_ledger.insert_one({"entry_id": secrets.token_urlsafe(18), "user_id": user_id,
                    "delta": PLANS[plan]["monthly_credits"], "kind": "plan_allocation", "feature": None, "created_at": now}, session=session)
    except DuplicateKeyError as error:
        raise BillingError("An account with this email already exists.", 409) from error
    return public_user(user)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"user_id": user["user_id"], "email": user["email"], "role": user.get("role", "user"),
            "plan": user.get("plan", "free"), "renewal_at": user.get("renewal_at"),
            "credits_allocated": user.get("credits_allocated", 0), "credits_used": user.get("credits_used", 0)}


def record_financial_transaction(db, session, *, user_id: str, transaction_type: str,
                                 description: str, amount: int | float = 0,
                                 currency: str = "INR", direction: str = "none",
                                 status: str = "committed", transaction_id: str | None = None,
                                 created_at: datetime | None = None, **details) -> dict[str, Any]:
    """Write the canonical per-user financial activity row inside its business transaction.

    Callers pass the active MongoDB session so the wallet/credit change and its history row
    commit or abort together. Flexible metadata keeps future operation types in one history.
    """
    allowed_statuses = {"committed", "pending", "failed", "rolled_back", "refunded"}
    if status not in allowed_statuses:
        raise BillingError("Unsupported financial transaction status.")
    row = {
        "transaction_id": transaction_id or f"WLT-{secrets.token_hex(8).upper()}",
        "user_id": user_id,
        "type": transaction_type,
        "description": description,
        "amount": amount,
        "currency": currency,
        "direction": direction,
        "status": status,
        "created_at": created_at or datetime.now(timezone.utc),
    }
    row.update({key: value for key, value in details.items() if value is not None})
    db.wallet_transactions.insert_one(row, session=session)
    return row


def start_transaction(session):
    session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"),
                              read_preference=ReadPreference.PRIMARY)


def transaction_receipt(db, user_id: str, operation: str, idempotency_key: str,
                        callback, request_data: dict[str, Any] | None = None) -> dict[str, Any]:
    if not 8 <= len(idempotency_key) <= 128:
        raise BillingError("Provide a valid idempotency key.")
    prior = db.wallet_operations.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
    if prior:
        if (prior["user_id"] != user_id or prior["operation"] != operation
                or prior.get("request_data", {}) != (request_data or {})):
            raise BillingError("This request key belongs to a different operation.", 409)
        return {**prior["result"], "duplicate_request": True}
    client = db.client
    delays: list[float] = []
    for attempt in range(1, 6):
        with client.start_session() as session:
            try:
                start_transaction(session)
                result = callback(session, attempt)
                now = datetime.now(timezone.utc)
                receipt_id = f"RCT-{secrets.token_hex(8).upper()}"
                receipt = {"receipt_id": receipt_id, "user_id": user_id, "operation": operation,
                           "idempotency_key": idempotency_key, "result": result, "attempts": attempt,
                           "retry_delays_seconds": delays, "status": "committed", "created_at": now}
                db.subscription_receipts.insert_one(receipt, session=session)
                transaction_id = result.get("transaction_id")
                if transaction_id:
                    db.wallet_transactions.update_one({"transaction_id": transaction_id, "user_id": user_id},
                        {"$set": {"receipt_id": receipt_id}}, session=session)
                response = {**result, "receipt_id": receipt_id, "attempts": attempt,
                            "retry_delays_seconds": delays, "status": "committed"}
                db.wallet_operations.insert_one({"idempotency_key": idempotency_key, "user_id": user_id,
                    "operation": operation, "request_data": request_data or {}, "result": response}, session=session)
                session.commit_transaction()
                return response
            except BillingError as error:
                if session.in_transaction:
                    session.abort_transaction()
                try:
                    db.subscription_receipts.insert_one({"receipt_id": f"RCT-{secrets.token_hex(8).upper()}",
                        "user_id": user_id, "operation": operation, "idempotency_key": idempotency_key,
                        "request_data": request_data or {},
                        "result": {"message": str(error), "details": error.data, "rolled_back": True},
                        "attempts": attempt, "retry_delays_seconds": delays, "status": "rolled_back",
                        "created_at": datetime.now(timezone.utc)})
                except PyMongoError:
                    pass
                raise
            except DuplicateKeyError as error:
                if session.in_transaction:
                    session.abort_transaction()
                prior = db.wallet_operations.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
                if (prior and prior.get("user_id") == user_id and prior.get("operation") == operation
                        and prior.get("request_data", {}) == (request_data or {})):
                    return {**prior["result"], "duplicate_request": True}
                raise BillingError("This request key was already used by another operation.", 409) from error
            except PyMongoError as error:
                if error.has_error_label("UnknownTransactionCommitResult"):
                    try:
                        session.commit_transaction()
                        return response
                    except PyMongoError:
                        prior = db.wallet_operations.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
                        if prior:
                            return prior["result"]
                if session.in_transaction:
                    session.abort_transaction()
                if not error.has_error_label("TransientTransactionError") or attempt == 5:
                    raise BillingError("The operation could not be safely committed. No success was reported.", 503) from error
        # Only transient transaction errors retry. Delays use bounded exponential jitter.
        import random
        import time
        delay = min(1.0, 0.05 * (2 ** (attempt - 1))) * random.uniform(0.75, 1.25)
        delays.append(round(delay, 4))
        time.sleep(delay)
    raise BillingError("Retry limit reached; no operation was confirmed.", 503)


def purchase_plan(db, user_id: str, plan: str, idempotency_key: str) -> dict[str, Any]:
    if plan not in PLANS:
        raise BillingError("Choose an available subscription plan.")
    pricing = PLANS[plan]
    def commit(session, attempt):
        user = db.subscription_users.find_one({"user_id": user_id}, session=session)
        wallet = db.wallets.find_one({"user_id": user_id}, session=session)
        if not user or not wallet:
            raise BillingError("Account wallet was not found.", 404)
        cost = pricing["price"]
        renewal_at_current = user.get("renewal_at")
        if renewal_at_current and renewal_at_current.tzinfo is None:
            renewal_at_current = renewal_at_current.replace(tzinfo=timezone.utc)
        if user.get("plan", "free") == plan and renewal_at_current and renewal_at_current > datetime.now(timezone.utc):
            raise BillingError("This plan is already active. You can renew it after the current term ends.", 409)
        if wallet["balance"] < cost:
            raise BillingError("Insufficient wallet balance.", 409, {
                "wallet_balance": wallet["balance"], "amount_required": cost,
                "shortfall": cost - wallet["balance"],
            })
        transaction_id = f"WLT-{secrets.token_hex(8).upper()}"
        payment_id = secrets.token_urlsafe(18)
        user_ledger_id = None
        platform_ledger_id = None
        if cost:
            debit = db.wallets.update_one({"user_id": user_id, "balance": {"$gte": cost}},
                {"$inc": {"balance": -cost}, "$set": {"updated_at": datetime.now(timezone.utc)}}, session=session)
            if debit.modified_count != 1:
                raise BillingError("Wallet balance changed. Please retry.", 409)
            platform_credit = db.wallets.update_one({"user_id": "platform"}, {"$inc": {"balance": cost}},
                upsert=True, session=session)
            if platform_credit.modified_count != 1 and platform_credit.upserted_id is None:
                raise BillingError("Platform wallet could not be credited; the purchase was rolled back.", 503)
            user_ledger_id = secrets.token_urlsafe(18)
            platform_ledger_id = secrets.token_urlsafe(18)
            db.wallet_ledger.insert_many([
                {"entry_id": user_ledger_id, "transaction_id": transaction_id, "user_id": user_id,
                 "delta": -cost, "kind": "subscription_payment", "created_at": datetime.now(timezone.utc)},
                {"entry_id": platform_ledger_id, "transaction_id": transaction_id, "user_id": "platform",
                 "delta": cost, "kind": "subscription_revenue", "created_at": datetime.now(timezone.utc)},
            ], session=session)
        db.subscription_payments.insert_one({"payment_id": payment_id, "transaction_id": transaction_id, "user_id": user_id,
            "plan": plan, "amount": cost, "currency": "INR", "status": "paid_demo",
            "idempotency_key": idempotency_key, "created_at": datetime.now(timezone.utc)}, session=session)
        old_plan = user.get("plan", "free")
        old_credit_balance = max(0, user.get("credits_allocated", 0) - user.get("credits_used", 0))
        if old_credit_balance:
            db.credit_ledger.insert_one({"entry_id": secrets.token_urlsafe(18), "user_id": user_id,
                "transaction_id": transaction_id, "delta": -old_credit_balance,
                "kind": "expired_plan_credits", "feature": None,
                "created_at": datetime.now(timezone.utc)}, session=session)
        renewal_at = datetime.now(timezone.utc) + timedelta(days=30)
        db.subscription_users.update_one({"user_id": user_id}, {"$set": {"plan": plan,
            "renewal_at": renewal_at, "credits_allocated": pricing["monthly_credits"],
            "credits_used": 0}}, session=session)
        db.credit_ledger.insert_one({"entry_id": secrets.token_urlsafe(18), "user_id": user_id,
            "transaction_id": transaction_id, "delta": pricing["monthly_credits"],
            "kind": "plan_allocation", "feature": None,
            "created_at": datetime.now(timezone.utc)}, session=session)
        db.subscription_history.insert_one({"history_id": secrets.token_urlsafe(18), "transaction_id": transaction_id,
            "payment_id": payment_id, "user_id": user_id,
            "from_plan": old_plan, "to_plan": plan, "price": cost,
            "credits_allocated": pricing["monthly_credits"], "renewal_at": renewal_at,
            "changed_at": datetime.now(timezone.utc)}, session=session)
        record_financial_transaction(db, session, user_id=user_id, transaction_id=transaction_id,
            idempotency_key=idempotency_key, transaction_type="subscription_purchase",
            description=(f"Purchased {plan.title()} subscription (simulated)" if old_plan == "free"
                         else f"Changed subscription from {old_plan.title()} to {plan.title()} (simulated)"),
            amount=cost, currency="INR", direction="debit" if cost else "none", status="committed",
            balance_before=wallet["balance"], balance_after=wallet["balance"] - cost,
            plan_before=old_plan, plan_after=plan, payment_id=payment_id,
            ledger_id=user_ledger_id, platform_ledger_id=platform_ledger_id,
            credits_allocated=pricing["monthly_credits"],
            metadata={"simulated": True, "renewal_at": renewal_at})
        return {"transaction_id": transaction_id, "payment_id": payment_id,
                "plan_before": old_plan, "plan_after": plan, "plan": plan, "price": cost,
                "credits_allocated": pricing["monthly_credits"], "wallet_before": wallet["balance"],
                "wallet_after": wallet["balance"] - cost}
    return transaction_receipt(db, user_id, "subscription_purchase", idempotency_key, commit,
                               {"plan": plan, "amount": pricing["price"]})


def add_money(db, user_id: str, amount: int, idempotency_key: str, *,
              administrator_id: str | None = None, reason: str | None = None) -> dict[str, Any]:
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1 or amount > MAX_TOP_UP:
        raise BillingError(f"Enter a top-up amount from ₹1 to ₹{MAX_TOP_UP:,}.", 422,
                           {"amount_minimum": 1, "amount_maximum": MAX_TOP_UP})

    if administrator_id and (not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200):
        raise BillingError("Provide a reason of 1 to 200 characters for an administrator credit.", 422)
    operation = "wallet_admin_credit" if administrator_id else "wallet_top_up"
    request_data = {"amount": amount}
    if administrator_id:
        request_data.update({"target_user_id": user_id, "administrator_id": administrator_id,
                             "reason": reason.strip()})

    def commit(session, attempt):
        wallet = db.wallets.find_one({"user_id": user_id}, session=session)
        user = db.subscription_users.find_one({"user_id": user_id}, session=session)
        if not wallet:
            raise BillingError("Account wallet was not found.", 404)
        if not user:
            raise BillingError("Account was not found.", 404)
        plan = effective_plan(user)
        limit = FREE_WALLET_LIMIT if plan == "free" else 10_000_000
        permitted = max(0, limit - int(wallet.get("balance", 0)))
        if amount > permitted:
            raise BillingError(f"This {plan.title()} plan permits a wallet balance up to ₹{limit:,}. You can add up to ₹{permitted:,}.", 409,
                               {"wallet_balance": wallet.get("balance", 0), "wallet_limit": limit,
                                "permitted_amount": permitted, "amount_requested": amount})
        changed = db.wallets.update_one({"user_id": user_id,
                                        "balance": {"$eq": wallet["balance"], "$lte": limit - amount}},
            {"$inc": {"balance": amount}, "$set": {"updated_at": datetime.now(timezone.utc)}}, session=session)
        if changed.modified_count != 1:
            raise BillingError("The wallet balance limit would be exceeded.", 409)
        transaction_id = f"WLT-{secrets.token_hex(8).upper()}"
        now = datetime.now(timezone.utc)
        ledger_id = secrets.token_urlsafe(18)
        ledger_kind = "administrator_wallet_credit" if administrator_id else "simulated_wallet_top_up"
        ledger = {"entry_id": ledger_id, "transaction_id": transaction_id, "user_id": user_id,
                  "delta": amount, "kind": ledger_kind, "created_at": now}
        if administrator_id:
            ledger.update({"administrator_id": administrator_id, "reason": reason.strip()})
        db.wallet_ledger.insert_one(ledger, session=session)
        record_financial_transaction(db, session, user_id=user_id, transaction_id=transaction_id,
            idempotency_key=idempotency_key, transaction_type=operation,
            description=(f"Administrator wallet credit: {reason.strip()}" if administrator_id else
                         "Simulated demonstration wallet top-up; no payment gateway was used."),
            direction="credit", amount=amount, currency="INR", status="committed",
            balance_before=wallet["balance"], balance_after=wallet["balance"] + amount,
            ledger_id=ledger_id, metadata={"simulated": not bool(administrator_id),
                **({"administrator_id": administrator_id, "reason": reason.strip()} if administrator_id else {})})
        if administrator_id:
            db.admin_audit_log.insert_one({"administrator_id": administrator_id,
                "actor_role": "admin", "action": "wallet_credit", "target_record": user_id,
                "created_at": now, "outcome": "committed", "amount": amount,
                "reason": reason.strip(), "transaction_id": transaction_id}, session=session)
        return {"transaction_id": transaction_id, "amount": amount, "wallet_before": wallet["balance"],
                "wallet_after": wallet["balance"] + amount, "simulated": not bool(administrator_id)}

    return transaction_receipt(db, user_id, operation, idempotency_key, commit, request_data)


def transfer(db, user_id: str, recipient_email: str, amount: int, idempotency_key: str) -> dict[str, Any]:
    if amount <= 0 or amount > 1_000_000:
        raise BillingError("Transfer amount must be between ₹1 and ₹1,000,000.")
    recipient_email = recipient_email.strip().lower()
    def commit(session, attempt):
        recipient = db.subscription_users.find_one({"email": recipient_email},
            {"user_id": 1, "plan": 1, "renewal_at": 1}, session=session)
        if not recipient:
            raise BillingError("Recipient account was not found.", 404)
        if recipient["user_id"] == user_id:
            raise BillingError("You cannot transfer to your own wallet.")
        sender_wallet = db.wallets.find_one({"user_id": user_id}, session=session)
        recipient_wallet = db.wallets.find_one({"user_id": recipient["user_id"]}, session=session)
        if not sender_wallet or not recipient_wallet:
            raise BillingError("A transfer wallet was not found.", 404)
        debit = db.wallets.update_one({"user_id": user_id, "balance": {"$gte": amount}},
            {"$inc": {"balance": -amount}, "$set": {"updated_at": datetime.now(timezone.utc)}}, session=session)
        if debit.modified_count != 1:
            raise BillingError("Insufficient wallet balance.", 409)
        recipient_limit = FREE_WALLET_LIMIT if effective_plan(recipient) == "free" else 10_000_000
        credit = db.wallets.update_one({"user_id": recipient["user_id"],
                                        "balance": {"$lte": recipient_limit - amount}},
            {"$inc": {"balance": amount}, "$set": {"updated_at": datetime.now(timezone.utc)}}, session=session)
        if credit.modified_count != 1:
            raise BillingError("Recipient wallet changed. Transfer rolled back.", 409)
        now = datetime.now(timezone.utc)
        transaction_id = f"WLT-{secrets.token_hex(8).upper()}"
        sender_ledger_id = secrets.token_urlsafe(18)
        recipient_ledger_id = secrets.token_urlsafe(18)
        db.wallet_ledger.insert_many([
            {"entry_id": sender_ledger_id, "transaction_id": transaction_id, "user_id": user_id,
             "delta": -amount, "kind": "transfer_out", "created_at": now},
            {"entry_id": recipient_ledger_id, "transaction_id": transaction_id, "user_id": recipient["user_id"],
             "delta": amount, "kind": "transfer_in", "created_at": now},
        ], session=session)
        record_financial_transaction(db, session, user_id=user_id, transaction_id=transaction_id,
            idempotency_key=idempotency_key, transaction_type="wallet_transfer",
            direction="debit", amount=amount, currency="INR", status="committed",
            description=f"Transfer to {recipient_email}",
            balance_before=sender_wallet["balance"], balance_after=sender_wallet["balance"] - amount,
            counterparty_user_id=recipient["user_id"], ledger_id=sender_ledger_id, metadata={"simulated": True})
        record_financial_transaction(db, session, user_id=recipient["user_id"], transaction_id=transaction_id,
            transaction_type="wallet_transfer",
            direction="credit", amount=amount, currency="INR", status="committed",
            description=f"Transfer from account {user_id[:6]}",
            balance_before=recipient_wallet["balance"], balance_after=recipient_wallet["balance"] + amount,
            counterparty_user_id=user_id, ledger_id=recipient_ledger_id, metadata={"simulated": True})
        return {"transaction_id": transaction_id, "amount": amount, "currency": "INR",
                "wallet_before": sender_wallet["balance"], "wallet_after": sender_wallet["balance"] - amount,
                "recipient": recipient_email}
    return transaction_receipt(db, user_id, "wallet_transfer", idempotency_key, commit,
                               {"recipient_email": recipient_email, "amount": amount})


def charge_feature_credits(db, user_id: str, feature: str, credits: int,
                           idempotency_key: str) -> dict[str, Any]:
    """Atomically charge plan credits and write the same canonical financial activity row.

    Existing threat features are not metered yet; future paid endpoints should call this
    service only when they actually deduct credits.
    """
    feature = feature.strip()[:120]
    if not feature or not isinstance(credits, int) or isinstance(credits, bool) or not 1 <= credits <= 100_000:
        raise BillingError("Provide a valid feature name and credit amount.", 422)

    def commit(session, attempt):
        user = db.subscription_users.find_one({"user_id": user_id}, session=session)
        if not user:
            raise BillingError("Account was not found.", 404)
        allocated = int(user.get("credits_allocated", 0))
        used = int(user.get("credits_used", 0))
        available = max(0, allocated - used)
        if credits > available:
            raise BillingError("Insufficient monthly credits.", 409,
                               {"credits_available": available, "credits_required": credits})
        changed = db.subscription_users.update_one(
            {"user_id": user_id, "credits_used": used, "credits_allocated": {"$gte": used + credits}},
            {"$inc": {"credits_used": credits}}, session=session)
        if changed.modified_count != 1:
            raise BillingError("Credit balance changed. Please retry.", 409)
        transaction_id = f"CRD-{secrets.token_hex(8).upper()}"
        now = datetime.now(timezone.utc)
        ledger_id = secrets.token_urlsafe(18)
        db.credit_ledger.insert_one({"entry_id": ledger_id, "transaction_id": transaction_id,
            "user_id": user_id, "delta": -credits, "kind": "feature_usage", "feature": feature,
            "created_at": now}, session=session)
        wallet = db.wallets.find_one({"user_id": user_id}, {"balance": 1}, session=session) or {}
        record_financial_transaction(db, session, user_id=user_id, transaction_id=transaction_id,
            idempotency_key=idempotency_key, transaction_type="feature_usage",
            description=f"Used {feature} ({credits} credits)", amount=0, currency="INR",
            direction="none", status="committed", created_at=now,
            balance_before=wallet.get("balance"), balance_after=wallet.get("balance"),
            credits_delta=-credits, feature=feature, ledger_id=ledger_id,
            metadata={"credits_before": available, "credits_after": available - credits})
        return {"transaction_id": transaction_id, "feature": feature, "credits_charged": credits,
                "credits_before": available, "credits_after": available - credits}

    return transaction_receipt(db, user_id, "feature_credit_charge", idempotency_key, commit,
                               {"feature": feature, "credits": credits})
