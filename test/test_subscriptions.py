import unittest
import copy
from http.cookies import SimpleCookie
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException, Response
from starlette.requests import Request

from backend.services.subscriptions import (
    BillingError, MAX_TOP_UP, PLANS, PASSWORD_ITERATIONS, add_money, create_account, password_hash,
    password_matches, purchase_plan, transfer, charge_feature_credits,
)
from backend.routes import subscriptions as subscription_routes


class _Result:
    def __init__(self, modified=0, upserted=None):
        self.modified_count = modified
        self.upserted_id = upserted


class _Cursor(list):
    def sort(self, *args, **kwargs):
        if args:
            fields = args[0] if isinstance(args[0], list) else [(args[0], args[1] if len(args) > 1 else 1)]
            for field, direction in reversed(fields):
                self.sort(key=lambda row: row.get(field) is not None and row.get(field), reverse=direction < 0)
        return self

    def limit(self, count):
        return _Cursor(self[:count])

    def skip(self, count):
        return _Cursor(self[count:])


class _Collection:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def create_index(self, *args, **kwargs):
        return None

    @staticmethod
    def _matches(row, query):
        for key, expected in query.items():
            actual = row.get(key)
            if isinstance(expected, dict):
                for operator, value in expected.items():
                    if operator == "$eq" and actual != value: return False
                    if operator == "$gte" and not actual >= value: return False
                    if operator == "$lte" and not actual <= value: return False
                    if operator == "$gt" and not actual > value: return False
                    if operator == "$lt" and not actual < value: return False
                    if operator == "$in" and actual not in value: return False
                    if operator == "$nin" and actual in value: return False
            elif hasattr(expected, "search"):
                if actual is None or not expected.search(str(actual)): return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query, projection=None, **kwargs):
        found = next((row for row in self.rows if self._matches(row, query)), None)
        return copy.deepcopy(found) if found is not None else None

    def find_one_and_update(self, query, update, upsert=False, **kwargs):
        row = next((r for r in self.rows if self._matches(r, query)), None)
        if row is None:
            if not upsert: return None
            row = {k: v for k, v in query.items() if not isinstance(v, dict)}
            row.update(copy.deepcopy(update.get("$setOnInsert", {})))
            self.rows.append(row)
        for key, amount in update.get("$inc", {}).items(): row[key] = row.get(key, 0) + amount
        row.update(copy.deepcopy(update.get("$set", {})))
        return copy.deepcopy(row)

    def find(self, query=None, projection=None, **kwargs):
        return _Cursor(copy.deepcopy([r for r in self.rows if self._matches(r, query or {})]))

    def insert_one(self, row, **kwargs):
        self.rows.append(copy.deepcopy(row))
        return _Result(modified=1)

    def insert_many(self, rows, **kwargs):
        self.rows.extend(copy.deepcopy(list(rows)))
        return _Result(modified=len(rows))

    def update_one(self, query, update, upsert=False, **kwargs):
        row = next((r for r in self.rows if self._matches(r, query)), None)
        if row is None and not upsert:
            return _Result()
        if row is None:
            row = {k: v for k, v in query.items() if not isinstance(v, dict)}
            self.rows.append(row)
        for key, amount in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + amount
        row.update(copy.deepcopy(update.get("$set", {})))
        return _Result(modified=1, upserted="new" if upsert else None)

    def count_documents(self, query):
        return sum(self._matches(row, query) for row in self.rows)

    def aggregate(self, pipeline):
        rows = copy.deepcopy(self.rows)
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if self._matches(row, stage["$match"])]
            if "$group" in stage:
                spec = stage["$group"]
                grouped = {}
                for row in rows:
                    key = {name: row.get(expr[1:]) if isinstance(expr, str) and expr.startswith("$") else expr
                           for name, expr in spec["_id"].items()}
                    target = grouped.setdefault(tuple(sorted(key.items())), {"_id": key})
                    for name, accumulator in spec.items():
                        if name == "_id": continue
                        expr = accumulator["$sum"]
                        if isinstance(expr, dict) and "$ifNull" in expr:
                            field, fallback = expr["$ifNull"]
                            value = row.get(field[1:], fallback)
                        elif isinstance(expr, str) and expr.startswith("$"):
                            value = row.get(expr[1:], 0)
                        else:
                            value = expr
                        target[name] = target.get(name, 0) + (value or 0)
                rows = list(grouped.values())
        return _Cursor(rows)

    def delete_one(self, query):
        found = next((i for i, row in enumerate(self.rows) if self._matches(row, query)), None)
        if found is not None:
            del self.rows[found]
        return _Result(modified=int(found is not None))


class _Session:
    def __init__(self, db):
        self.db = db
        self.in_transaction = False
        self.snapshot = None

    def __enter__(self): return self
    def __exit__(self, kind, value, traceback):
        if self.in_transaction:
            if kind is None: self.commit_transaction()
            else: self.abort_transaction()
        return False
    def start_transaction(self, **kwargs):
        self.db.lock.acquire()
        self.snapshot = {name: copy.deepcopy(collection.rows) for name, collection in self.db.collections.items()}
        self.in_transaction = True
        return self
    def commit_transaction(self):
        self.in_transaction = False
        self.db.lock.release()
    def abort_transaction(self):
        for name, collection in self.db.collections.items():
            collection.rows = self.snapshot.get(name, [])
        self.in_transaction = False
        self.db.lock.release()


class _Client:
    def __init__(self, db): self.db = db
    def start_session(self): return _Session(self.db)


class _Database:
    def __init__(self):
        import threading
        self.lock = threading.RLock()
        self.collections = {}
        self.client = _Client(self)
    def __getitem__(self, name):
        if name not in self.collections: self.collections[name] = _Collection(self)
        return self.collections[name]
    def __getattr__(self, name): return self[name]


def _demo_db(balance=1000, plan="free"):
    db = _Database()
    db.wallets.insert_one({"user_id": "user-1", "balance": balance, "currency": "INR"})
    db.wallets.insert_one({"user_id": "platform", "balance": 0, "currency": "INR"})
    db.subscription_users.insert_one({"user_id": "user-1", "email": "one@example.test", "plan": plan,
        "renewal_at": datetime.now(timezone.utc) + timedelta(days=30), "credits_allocated": 100, "credits_used": 0})
    db.credit_ledger.insert_one({"user_id": "user-1", "delta": 100, "kind": "plan_allocation"})
    db.wallet_ledger.insert_one({"user_id": "user-1", "delta": balance, "kind": "demo_opening_balance"})
    return db


class SubscriptionPrimitiveTests(unittest.TestCase):
    def test_plan_prices_and_allocations(self):
        self.assertEqual(PLANS, {
            "free": {"price": 0, "monthly_credits": 100},
            "plus": {"price": 299, "monthly_credits": 1000},
            "pro": {"price": 799, "monthly_credits": 5000},
        })

    def test_password_salt_is_unique_and_hash_is_verified(self):
        salt_one, digest_one = password_hash("correct horse battery staple")
        salt_two, digest_two = password_hash("correct horse battery staple")
        self.assertEqual(len(salt_one), 32)
        self.assertNotEqual(salt_one, salt_two)
        self.assertNotEqual(digest_one, digest_two)
        self.assertTrue(password_matches("correct horse battery staple", salt_one, digest_one))
        self.assertFalse(password_matches("incorrect password", salt_one, digest_one))

    def test_account_creation_rejects_weak_passwords(self):
        with self.assertRaises(BillingError):
            create_account(_Database(), email="weak@example.test", password="all lowercase letters")
        self.assertEqual(PASSWORD_ITERATIONS, 310_000)

    def test_new_free_account_opening_wallet_respects_balance_cap(self):
        db = _Database()
        create_account(db, email="cap@example.test", password="SecurePassword123!")
        wallet = db.wallets.find_one({"user_id": db.subscription_users.rows[0]["user_id"]})
        self.assertEqual(wallet["balance"], 500)
        self.assertEqual(db.wallet_transactions.rows[0]["balance_after"], 500)
        with self.assertRaisesRegex(BillingError, "cannot exceed"):
            create_account(_Database(), email="too-much@example.test", password="SecurePassword123!", opening_balance=501)

    def test_top_up_commits_wallet_ledger_transaction_and_receipt(self):
        db = _demo_db(200, plan="plus")
        result = add_money(db, "user-1", 500, "top-up-key-0001")
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 700)
        self.assertEqual(db.wallets.find_one({"user_id": "platform"})["balance"], 0)
        self.assertEqual(db.wallet_ledger.rows[-1]["delta"], 500)
        self.assertEqual(db.wallet_transactions.rows[-1]["type"], "wallet_top_up")
        self.assertEqual(db.wallet_transactions.rows[-1]["balance_before"], 200)
        self.assertEqual(db.wallet_transactions.rows[-1]["balance_after"], 700)
        self.assertEqual(db.wallet_transactions.rows[-1]["idempotency_key"], "top-up-key-0001")
        self.assertEqual(db.wallet_transactions.rows[-1]["ledger_id"], db.wallet_ledger.rows[-1]["entry_id"])
        self.assertEqual(db.subscription_receipts.rows[0]["receipt_id"], result["receipt_id"])
        self.assertTrue(result["simulated"])

    def test_administrator_credit_is_atomic_audited_and_idempotent(self):
        db = _demo_db(100, plan="plus")
        db.subscription_users.insert_one({"user_id": "admin", "role": "admin", "plan": "free"})
        first = add_money(db, "user-1", 75, "admin-credit-key-01",
                          administrator_id="admin", reason="Reimbursement")
        duplicate = add_money(db, "user-1", 75, "admin-credit-key-01",
                              administrator_id="admin", reason="Reimbursement")
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 175)
        self.assertEqual(len(db.wallet_transactions.rows), 1)
        self.assertEqual(db.wallet_transactions.rows[0]["type"], "wallet_admin_credit")
        self.assertEqual(db.wallet_ledger.rows[-1]["kind"], "administrator_wallet_credit")
        self.assertEqual(db.admin_audit_log.rows[0]["action"], "wallet_credit")
        self.assertEqual(db.admin_audit_log.rows[0]["reason"], "Reimbursement")
        self.assertEqual(first["transaction_id"], duplicate["transaction_id"])
        self.assertTrue(duplicate["duplicate_request"])

    def test_top_up_rejects_invalid_amounts_without_writing(self):
        db = _demo_db()
        for amount in (0, -1, MAX_TOP_UP + 1, 1.5, True):
            with self.subTest(amount=amount), self.assertRaises(BillingError):
                add_money(db, "user-1", amount, "invalid-topup-1")
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 1000)
        self.assertEqual(db.wallet_transactions.rows, [])

    def test_free_wallet_cap_is_checked_inside_the_atomic_deposit(self):
        db = _demo_db(400)
        result = add_money(db, "user-1", 100, "free-cap-valid-01")
        self.assertEqual(result["wallet_after"], 500)
        with self.assertRaisesRegex(BillingError, "permits a wallet balance up to") as raised:
            add_money(db, "user-1", 1, "free-cap-over-001")
        self.assertEqual(raised.exception.data["permitted_amount"], 0)
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 500)
        self.assertEqual(len(db.wallet_transactions.rows), 1)

    def test_duplicate_top_up_request_does_not_credit_twice(self):
        db = _demo_db(200, plan="plus")
        first = add_money(db, "user-1", 100, "same-topup-key-1")
        duplicate = add_money(db, "user-1", 100, "same-topup-key-1")
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 300)
        self.assertEqual(len(db.wallet_transactions.rows), 1)
        self.assertEqual(first["receipt_id"], duplicate["receipt_id"])
        self.assertTrue(duplicate["duplicate_request"])

    def test_idempotency_key_cannot_be_reused_with_different_amount(self):
        db = _demo_db(200, plan="plus")
        add_money(db, "user-1", 100, "same-topup-key-2")
        with self.assertRaisesRegex(BillingError, "different operation"):
            add_money(db, "user-1", 200, "same-topup-key-2")
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 300)
        self.assertEqual(len(db.wallet_transactions.rows), 1)

    def test_purchase_credits_platform_and_allocates_plan_once(self):
        db = _demo_db(1000)
        result = purchase_plan(db, "user-1", "plus", "purchase-key-0001")
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 701)
        self.assertEqual(db.wallets.find_one({"user_id": "platform"})["balance"], 299)
        self.assertEqual(db.subscription_users.find_one({"user_id": "user-1"})["plan"], "plus")
        self.assertEqual(db.subscription_users.find_one({"user_id": "user-1"})["credits_allocated"], 1000)
        self.assertEqual(db.subscription_payments.rows[0]["amount"], 299)
        self.assertEqual(db.wallet_transactions.rows[0]["balance_after"], 701)
        self.assertEqual(db.wallet_transactions.rows[0]["plan_before"], "free")
        self.assertEqual(db.wallet_transactions.rows[0]["plan_after"], "plus")
        self.assertEqual(db.subscription_payments.rows[0]["transaction_id"], db.wallet_transactions.rows[0]["transaction_id"])
        self.assertEqual(sum(row["delta"] for row in db.credit_ledger.rows), 1000)
        self.assertEqual(result["receipt_id"], db.wallet_transactions.rows[0]["receipt_id"])
        duplicate = purchase_plan(db, "user-1", "plus", "purchase-key-0001")
        self.assertTrue(duplicate["duplicate_request"])
        self.assertEqual(len(db.subscription_payments.rows), 1)
        with self.assertRaisesRegex(BillingError, "already active"):
            purchase_plan(db, "user-1", "plus", "purchase-key-0002")

    def test_insufficient_purchase_rolls_back_all_financial_and_plan_changes(self):
        db = _demo_db(100)
        with self.assertRaisesRegex(BillingError, "Insufficient wallet balance") as raised:
            purchase_plan(db, "user-1", "plus", "poor-purchase-key")
        self.assertEqual(raised.exception.data, {"wallet_balance": 100, "amount_required": 299, "shortfall": 199})
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 100)
        self.assertEqual(db.wallets.find_one({"user_id": "platform"})["balance"], 0)
        self.assertEqual(db.subscription_users.find_one({"user_id": "user-1"})["plan"], "free")
        self.assertEqual(db.subscription_payments.rows, [])
        self.assertEqual(db.wallet_transactions.rows, [])
        self.assertEqual(db.credit_ledger.rows, [{"user_id": "user-1", "delta": 100, "kind": "plan_allocation"}])
        self.assertEqual(db.wallet_operations.rows, [])
        self.assertEqual(db.subscription_receipts.rows[0]["status"], "rolled_back")

    def test_concurrent_plan_purchases_cannot_overdraw_wallet(self):
        db = _demo_db(500)
        def buy(plan, key):
            try:
                return purchase_plan(db, "user-1", plan, key)["status"]
            except BillingError:
                return "declined"
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda i: buy("plus" if i % 2 else "pro", f"concurrent-key-{i:02}"), range(8)))
        balance = db.wallets.find_one({"user_id": "user-1"})["balance"]
        self.assertGreaterEqual(balance, 0)
        self.assertEqual(balance, 201)
        self.assertEqual(db.wallets.find_one({"user_id": "platform"})["balance"], 299)
        self.assertEqual(len(db.subscription_payments.rows), 1)

    def test_feature_credit_charge_is_atomic_idempotent_and_in_central_history(self):
        db = _demo_db(500)
        first = charge_feature_credits(db, "user-1", "advanced search", 12, "feature-charge-001")
        duplicate = charge_feature_credits(db, "user-1", "advanced search", 12, "feature-charge-001")
        self.assertEqual(first["credits_after"], 88)
        self.assertTrue(duplicate["duplicate_request"])
        self.assertEqual(db.subscription_users.find_one({"user_id": "user-1"})["credits_used"], 12)
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 500)
        self.assertEqual(len(db.credit_ledger.rows), 2)
        self.assertEqual(len(db.wallet_transactions.rows), 1)
        self.assertEqual(db.wallet_transactions.rows[0]["type"], "feature_usage")
        self.assertEqual(db.wallet_transactions.rows[0]["credits_delta"], -12)

    def test_transfer_creates_a_private_history_row_for_each_wallet(self):
        db = _demo_db(500)
        db.subscription_users.insert_one({"user_id": "user-2", "email": "two@example.test"})
        db.wallets.insert_one({"user_id": "user-2", "balance": 50})
        db.wallet_transactions.insert_one({"transaction_id": "WLT-RECIPIENT-OLD", "user_id": "user-2",
            "idempotency_key": "transfer-history-01", "type": "wallet_top_up", "status": "committed"})
        result = transfer(db, "user-1", "two@example.test", 40, "transfer-history-01")
        self.assertEqual(result["wallet_after"], 460)
        self.assertEqual(db.wallets.find_one({"user_id": "user-2"})["balance"], 90)
        own_rows = [row for row in db.wallet_transactions.rows if row["user_id"] == "user-1"]
        other_rows = [row for row in db.wallet_transactions.rows if row["user_id"] == "user-2"
                      and row.get("transaction_id") == result["transaction_id"]]
        self.assertEqual(len(own_rows), 1)
        self.assertEqual(len(other_rows), 1)
        self.assertEqual(own_rows[0]["direction"], "debit")
        self.assertEqual(other_rows[0]["direction"], "credit")
        self.assertNotIn("idempotency_key", other_rows[0])
        self.assertEqual(own_rows[0]["transaction_id"], other_rows[0]["transaction_id"])

    def test_transfer_to_missing_user_is_logged_as_rolled_back_without_history_success(self):
        db = _demo_db(500)
        with self.assertRaisesRegex(BillingError, "Recipient account was not found"):
            transfer(db, "user-1", "missing@example.test", 40, "transfer-missing-01")
        self.assertEqual(db.wallets.find_one({"user_id": "user-1"})["balance"], 500)
        self.assertEqual(db.wallet_transactions.rows, [])
        self.assertEqual(db.subscription_receipts.rows[0]["status"], "rolled_back")
        self.assertTrue(db.subscription_receipts.rows[0]["result"]["rolled_back"])

    def test_financial_activity_api_is_private_paginated_and_includes_failures(self):
        db = _Database()
        now = datetime.now(timezone.utc)
        db.wallets.insert_one({"user_id": "user-1", "balance": 900})
        db.wallet_transactions.insert_many([
            {"transaction_id": "WLT-OWN-1", "user_id": "user-1", "type": "wallet_top_up",
             "description": "Simulated top-up", "amount": 100, "direction": "credit", "status": "committed",
             "balance_before": 800, "balance_after": 900, "created_at": now},
            {"transaction_id": "WLT-OTHER-1", "user_id": "user-2", "type": "wallet_top_up",
             "description": "Private other user data", "amount": 999, "direction": "credit",
             "status": "committed", "balance_after": 999, "created_at": now},
        ])
        db.subscription_receipts.insert_one({"receipt_id": "RCT-FAILED-1", "user_id": "user-1",
            "operation": "subscription_purchase", "idempotency_key": "failed-plan-key",
            "request_data": {"plan": "pro", "amount": 799},
            "result": {"message": "Insufficient wallet balance.", "details": {"wallet_balance": 100}, "rolled_back": True},
            "status": "rolled_back", "created_at": now + timedelta(seconds=1)})
        with patch.object(subscription_routes, "get_database", return_value=db), patch.object(
            subscription_routes, "_session_user", return_value={"user_id": "user-1"}
        ):
            first = subscription_routes.financial_activity(Request({"type": "http", "headers": []}), page=1, page_size=1)
            second = subscription_routes.financial_activity(Request({"type": "http", "headers": []}), page=2, page_size=1)
            self.assertEqual(first["total"], 2)
            self.assertEqual(first["items"][0]["status"], "rolled_back")
            self.assertEqual(first["items"][0]["amount"], 799)
            self.assertEqual(second["items"][0]["transaction_id"], "WLT-OWN-1")
            self.assertEqual(first["summary"]["wallet_balance"], 900)
            self.assertEqual(first["summary"]["total_money_added"], 100)
            with self.assertRaises(HTTPException) as other_user:
                subscription_routes.financial_activity_details(Request({"type": "http", "headers": []}), "WLT-OTHER-1")
            self.assertEqual(other_user.exception.status_code, 404)

    def test_register_login_cookie_logout_and_receipt_owner_isolation(self):
        db = _Database()
        credentials = subscription_routes.RegistrationCredentials(email="owner@example.test", password="A long Test password 9!")
        with patch.object(subscription_routes, "get_database", return_value=db):
            registered = subscription_routes.register(credentials)
            self.assertEqual(registered["user"]["email"], "owner@example.test")
            self.assertNotIn("password_hash", registered["user"])
            login_response = Response()
            login_credentials = subscription_routes.Credentials(email=credentials.email, password=credentials.password)
            login_request = Request({"type": "http", "headers": []})
            signed_in = subscription_routes.login(login_credentials, login_request, login_response)
            cookie = SimpleCookie()
            cookie.load(login_response.headers["set-cookie"])
            token = cookie[subscription_routes.COOKIE_NAME].value
            self.assertTrue(cookie[subscription_routes.COOKIE_NAME]["httponly"])
            self.assertEqual(signed_in["user"]["user_id"], registered["user"]["user_id"])
            request = Request({"type": "http", "headers": [(b"cookie", f"{subscription_routes.COOKIE_NAME}={token}".encode())]})
            self.assertEqual(subscription_routes._session_user(request)["email"], "owner@example.test")
            db.subscription_receipts.insert_one({"receipt_id": "other-users-receipt", "user_id": "someone-else"})
            with self.assertRaises(HTTPException) as error:
                subscription_routes.receipt_details(request, "other-users-receipt")
            self.assertEqual(error.exception.status_code, 404)
            subscription_routes.logout(request, Response())
            with self.assertRaises(HTTPException) as expired:
                subscription_routes._session_user(request)
            self.assertEqual(expired.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
