import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from backend.services.entitlements import (
    FEATURE_LABELS, PLUS_LIMITS, billing_period, describe_entitlements,
    effective_plan, feature_for_path, feature_limit,
)


class EntitlementTests(unittest.TestCase):
    def test_free_plus_and_pro_policy(self):
        self.assertEqual(feature_limit("free", "search"), 0)
        self.assertEqual(feature_limit("plus", "search"), 3)
        self.assertEqual(feature_limit("pro", "search"), None)
        self.assertEqual(PLUS_LIMITS["search"], 3)
        self.assertIn("inventory", FEATURE_LABELS)

    def test_utc_month_boundary_and_expired_plan_downgrade(self):
        before = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
        after = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(billing_period(before), "2026-10")
        self.assertEqual(billing_period(after), "2026-11")
        expired = {"plan": "plus", "renewal_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        self.assertEqual(effective_plan(expired), "free")

    def test_feature_route_mapping_covers_direct_api_calls(self):
        cases = {
            "/api/reports": "reports", "/api/search": "search",
            "/api/virustotal/scan": "search", "/api/cross-source/summary": "cross_source",
            "/api/metrics": "working_set", "/api/lab9/partition": "network_reliability",
            "/api/chapter11/status": "sharding", "/api/lab10/inventory/products": "inventory",
        }
        for path, feature in cases.items():
            with self.subTest(path=path):
                self.assertEqual(feature_for_path(path), feature)
        self.assertIsNone(feature_for_path("/api/events"))
        self.assertIsNone(feature_for_path("/api/search", "DELETE"))

    def test_user_payload_has_usage_counts_and_reset_period(self):
        db = MagicMock()
        db.feature_usage.find_one.side_effect = lambda query: {"count": 2} if query["_id"].endswith(":search:2026-10") else None
        user = {"user_id": "u-1", "plan": "plus", "renewal_at": datetime.now(timezone.utc) + timedelta(days=1)}
        result = describe_entitlements(user, db, datetime(2026, 10, 30, tzinfo=timezone.utc))
        self.assertEqual(result["features"]["search"]["remaining"], 1)
        self.assertEqual(result["features"]["search"]["reset_period"], "monthly")
        self.assertTrue(result["features"]["search"]["allowed"])

    def test_direct_search_api_call_is_denied_for_free_plan(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "free-user"}
        db.subscription_users.find_one.return_value = {"user_id": "free-user", "plan": "free"}
        db.feature_usage.find_one.return_value = None
        app.state.database = db
        client = TestClient(app)
        try:
            response = client.get("/api/search?q=malware", cookies={"aegis_session": "test-session-token"})
        finally:
            client.close()
        self.assertEqual(response.status_code, 402)
        detail = response.json()["detail"]
        self.assertEqual(detail["feature"], "search")
        self.assertEqual(detail["current_plan"], "free")
        self.assertEqual(detail["required_plan"], "plus")
        db.feature_usage.update_one.assert_not_called()

    def test_plus_api_operation_is_metered_only_when_successful(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from backend.main import app
        import backend.routes.api as api_routes
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "plus-user"}
        db.subscription_users.find_one.return_value = {
            "user_id": "plus-user", "plan": "plus",
            "renewal_at": datetime.now(timezone.utc) + timedelta(days=10),
        }
        db.feature_usage.update_one.return_value.modified_count = 1
        app.state.database = db
        client = TestClient(app, raise_server_exceptions=False)
        try:
            with patch.object(api_routes, "get_database", return_value=MagicMock()):
                success = client.get("/api/reports", cookies={"aegis_session": "plus-session-token"})
            self.assertEqual(success.status_code, 200)
            self.assertEqual(db.feature_usage.update_one.call_count, 1)
            db.feature_usage.update_one.reset_mock()
            with patch.object(api_routes, "get_database", side_effect=RuntimeError("offline")):
                failed = client.get("/api/reports", cookies={"aegis_session": "plus-session-token"})
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(db.feature_usage.update_one.call_count, 2)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
