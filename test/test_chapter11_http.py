"""End-to-end Chapter 11 API checks with a temporary in-memory session and the live lab cluster."""
from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.routes import chapter11


class _SessionCollection:
    def find_one(self, query, projection=None):
        if "token_hash" in query:
            return {"user_id": "chapter11-test-user"}
        if "user_id" in query:
            return {"user_id": query["user_id"], "role": "admin", "status": "active", "plan": "admin"}
        return None


class _FeatureUsageCollection:
    def find_one(self, query, projection=None):
        return None


class _SessionDatabase:
    subscription_sessions = _SessionCollection()
    subscription_users = _SessionCollection()
    feature_usage = _FeatureUsageCollection()


@asynccontextmanager
async def _test_lifespan(application):
    yield


class Chapter11HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_lifespan = app.router.lifespan_context
        cls.previous_database = getattr(app.state, "database", None)
        app.router.lifespan_context = _test_lifespan
        app.state.database = _SessionDatabase()
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        cls.client.cookies.set("aegis_session", "temporary-test-session")

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        app.router.lifespan_context = cls.previous_lifespan
        if cls.previous_database is None:
            delattr(app.state, "database")
        else:
            app.state.database = cls.previous_database

    def test_dashboard_route_status_advisor_and_run_controls(self):
        with patch.object(chapter11, "_session_user", return_value={"role": "admin"}):
            page = self.client.get("/?view=lab11")
            self.assertEqual(page.status_code, 200)
            self.assertIn('data-view="lab11"', page.text)
            self.assertIn('id="view-lab11"', page.text)
            self.assertIn("Shard Key Evaluation", page.text)
            self.assertIn("SHARD ADVISOR", page.text)
            self.assertRegex(page.text, r'src="lab11\.js\?v=[0-9a-f]{10}"')
            self.assertTrue(self.client.get("/api/chapter11/capabilities").json()["can_run_experiments"])

            status = self.client.get("/api/chapter11/status")
            self.assertEqual(status.status_code, 200)
            live = status.json()
            self.assertTrue(live["mongos_connected"])
            self.assertEqual(live["shard_count"], 2)
            self.assertTrue(live["config_replica_set"]["healthy"])
            self.assertEqual(live["collections"]["orders_by_timestamp"]["documents"], 10_000)
            self.assertEqual(live["collections"]["orders_by_hashed_user"]["documents"], 10_000)

            matrix = self.client.get("/api/chapter11/reports/decision-matrix")
            measurements = self.client.get("/api/chapter11/reports/measurements")
            evidence = self.client.get("/api/chapter11/reports/measurements?download=true")
            self.assertEqual(matrix.status_code, 200)
            self.assertEqual(len(matrix.json()["matrix"]), 4)
            self.assertEqual(measurements.status_code, 200)
            self.assertEqual(evidence.status_code, 200)
            self.assertIn("attachment", evidence.headers.get("content-disposition", ""))
            self.assertTrue(measurements.json()["collections"]["orders_by_timestamp"]["queries"][0]["raw_explain"])

            profiles = [
                {"profile": "ecommerce_orders", "query_patterns": ["customer_lookup", "order_lookup", "date_range"],
                 "write_frequency": "moderate", "range_queries_important": True, "recent_write_concentration": False,
                 "entity_scoped_queries": True, "compare_a": "range_customer_id", "compare_b": "range_order_id"},
                {"profile": "time_series", "query_patterns": ["date_range", "time_range"], "write_frequency": "high",
                 "range_queries_important": True, "recent_write_concentration": True, "entity_scoped_queries": False,
                 "compare_a": "range_event_time", "compare_b": "hashed_event_time"},
                {"profile": "threat_intelligence", "query_patterns": ["indicator_lookup", "date_range"],
                 "write_frequency": "high", "range_queries_important": False, "recent_write_concentration": True,
                 "entity_scoped_queries": False, "compare_a": "hashed_indicator", "compare_b": "range_event_time"},
            ]
            for body in profiles:
                body["expected_documents"] = 1_000_000
                response = self.client.post("/api/chapter11/advisor", json=body)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()["comparison"]), 2)

            # Verify each authenticated control can enqueue work without executing
            # experiments against the persistent Chapter 11 database in this test.
            for lab in ("lab11_1", "lab11_2"):
                with patch.object(chapter11._job_executor, "submit"):
                    queued = self.client.post("/api/chapter11/runs", json={"lab": lab})
                self.assertEqual(queued.status_code, 202, queued.text)
                job_id = queued.json()["job_id"]
                self.assertEqual(queued.json()["status"], "queued")
                result = self.client.get(f"/api/chapter11/runs/{job_id}")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["lab"], lab)
                self.assertEqual(result.json()["status"], "queued")
                with chapter11._job_lock:
                    chapter11._active_job_id = None
                    chapter11._jobs.pop(job_id, None)

    def test_non_admin_run_is_forbidden(self):
        with patch.object(chapter11, "_session_user", return_value={"role": "member"}):
            response = self.client.post("/api/chapter11/runs", json={"lab": "lab11_1"})
            self.assertEqual(response.status_code, 403)

    def test_duplicate_active_run_is_rejected(self):
        with chapter11._job_lock:
            old_active = chapter11._active_job_id
            old_job = chapter11._jobs.get("busy-test-job")
            chapter11._active_job_id = "busy-test-job"
            chapter11._jobs["busy-test-job"] = {"job_id": "busy-test-job", "status": "running"}
        try:
            with patch.object(chapter11, "_session_user", return_value={"role": "admin"}):
                response = self.client.post("/api/chapter11/runs", json={"lab": "lab11_1"})
            self.assertEqual(response.status_code, 409)
        finally:
            with chapter11._job_lock:
                chapter11._active_job_id = old_active
                if old_job is None:
                    chapter11._jobs.pop("busy-test-job", None)
                else:
                    chapter11._jobs["busy-test-job"] = old_job


if __name__ == "__main__":
    unittest.main()
