import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import admin as admin_routes
from backend.routes import subscriptions as subscription_routes
from backend.services.entitlements import describe_entitlements, effective_plan, feature_limit
from backend.services.subscriptions import password_hash


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(admin_routes.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def test_every_admin_endpoint_rejects_anonymous_requests(self):
        requests = [
            ("GET", "/api/admin/dashboard", None), ("GET", "/api/admin/users", None),
            ("GET", "/api/admin/users/u-1", None), ("GET", "/api/admin/subscriptions", None),
            ("GET", "/api/admin/wallets/summary", None), ("GET", "/api/admin/transactions", None),
            ("GET", "/api/admin/transactions/t-1", None), ("GET", "/api/admin/audit", None),
            ("PATCH", "/api/admin/users/u-1/status", {"status": "disabled"}),
        ]
        for method, path, body in requests:
            response = self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 401, path)

    @patch("backend.routes.admin.get_database")
    def test_normal_user_is_denied_before_any_global_data_query(self, get_database):
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "ordinary-1"}
        db.subscription_users.find_one.return_value = {"user_id": "ordinary-1", "role": "user"}
        get_database.return_value = db
        response = self.client.get("/api/admin/dashboard", cookies={"aegis_session": "private-session"})
        self.assertEqual(response.status_code, 403)
        db.wallet_transactions.aggregate.assert_not_called()
        db.subscription_users.count_documents.assert_not_called()

    @patch("backend.routes.admin.add_money")
    @patch("backend.routes.admin.get_database")
    def test_admin_credit_uses_audited_wallet_service_and_manager_is_denied(self, get_database, add_money):
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "admin"}
        db.subscription_users.find_one.return_value = {"user_id": "admin", "role": "admin"}
        get_database.return_value = db
        add_money.return_value = {"transaction_id": "WLT-TEST", "wallet_after": 50}
        payload = {"user_id": "u-1", "amount": 50, "reason": "Support adjustment", "idempotency_key": "credit-test-1"}
        response = self.client.post("/api/admin/wallets/credit", json=payload,
                                    cookies={"aegis_session": "admin-session"})
        self.assertEqual(response.status_code, 200)
        add_money.assert_called_once_with(db, "u-1", 50, "credit-test-1",
                                          administrator_id="admin", reason="Support adjustment")

        db.subscription_sessions.find_one.return_value = {"user_id": "manager"}
        db.subscription_users.find_one.return_value = {"user_id": "manager", "role": "manager"}
        response = self.client.post("/api/admin/wallets/credit", json=payload,
                                    cookies={"aegis_session": "manager-session"})
        self.assertEqual(response.status_code, 403)
        add_money.assert_called_once()

    @patch("backend.routes.admin.get_database")
    def test_dashboard_uses_actual_grouped_records_and_30_day_login_activity(self, get_database):
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "admin"}
        db.subscription_users.find_one.return_value = {"user_id": "admin", "role": "admin"}
        db.subscription_users.count_documents.side_effect = [4, 2, 0, 2, 0, 0, 0, 0]
        db.wallets.aggregate.return_value = [{"_id": None, "total": 800}]
        db.wallet_transactions.count_documents.return_value = 6
        db.wallet_transactions.aggregate.return_value = [
            {"_id": {"type": "wallet_top_up", "status": "committed"}, "amount": 250, "count": 2},
            {"_id": {"type": "subscription_purchase", "status": "committed"}, "amount": 299, "count": 1},
            {"_id": {"type": "wallet_refund", "status": "refunded"}, "amount": 20, "count": 1},
            {"_id": {"type": "wallet_top_up", "status": "failed"}, "amount": 50, "count": 1},
        ]
        db.subscription_receipts.aggregate.return_value = []
        db.wallets.aggregate.return_value = [{"_id": None, "total": 800}]
        get_database.return_value = db

        response = self.client.get("/api/admin/dashboard", cookies={"aegis_session": "admin-session"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["registered_users"], 4)
        self.assertEqual(payload["active_users"], 2)
        self.assertEqual(payload["lifetime"]["total_transactions"], 6)
        self.assertEqual(payload["lifetime"]["total_deposits"], 250)
        self.assertEqual(payload["lifetime"]["total_subscription_purchases"], 299)
        self.assertEqual(payload["lifetime"]["total_refunds"], 20)
        self.assertEqual(payload["total_wallet_balances"], 800)
        self.assertEqual(payload["activity_period"]["days"], 30)
        self.assertNotIn("password_hash", payload)

    def test_admin_role_has_unlimited_feature_entitlements(self):
        admin = {"user_id": "admin", "role": "admin", "plan": "free"}
        db = MagicMock()
        db.feature_usage.find_one.return_value = {"count": 99}
        self.assertEqual(effective_plan(admin), "admin")
        self.assertIsNone(feature_limit("admin", "search"))
        entitlements = describe_entitlements(admin, db)
        self.assertEqual(entitlements["plan"], "admin")
        self.assertTrue(all(feature["allowed"] for feature in entitlements["features"].values()))
        self.assertTrue(all(feature["limit"] is None for feature in entitlements["features"].values()))

    @patch("backend.routes.subscriptions.ensure_indexes")
    @patch("backend.routes.subscriptions.get_database")
    def test_admin_id_login_uses_existing_password_hash_and_updates_activity(self, get_database, ensure_indexes):
        salt, digest = password_hash("12345")
        user = {"user_id": "admin", "email": "admin@aegis.local", "role": "admin",
                "plan": "free", "password_salt": salt, "password_hash": digest}
        db = MagicMock()
        db.subscription_users.find_one.return_value = user
        db.auth_login_attempts.find_one.return_value = None
        get_database.return_value = db
        from starlette.requests import Request
        from fastapi import Response
        request = Request({"type": "http", "method": "POST", "path": "/api/subscriptions/login",
                           "headers": [], "query_string": b"", "scheme": "http",
                           "server": ("testserver", 80), "client": ("127.0.0.1", 12345)})
        response = Response()
        result = subscription_routes.login(
            subscription_routes.Credentials(email="admin", password="12345"), request, response)
        self.assertEqual(result["user"]["user_id"], "admin")
        self.assertEqual(result["user"]["role"], "admin")
        self.assertIn("aegis_session=", response.headers["set-cookie"])
        db.subscription_users.update_one.assert_called_once()
        db.subscription_sessions.insert_one.assert_called_once()
        db.auth_login_attempts.delete_one.assert_called_once()


if __name__ == "__main__":
    unittest.main()
