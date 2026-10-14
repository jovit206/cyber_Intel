import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from backend.routes import lab9
from backend.services import reliability


def _doc_values(*offsets_ms):
    return [{"value": int(o * 1_000_000)} for o in offsets_ms]


def _status(*members):
    return {"set": "rs0", "members": [
        {"name": n, "stateStr": s, "health": h, "uptime": 3725, "self": False} for n, s, h in members
    ]}


class LatencyMathTests(unittest.TestCase):
    def test_intervals_are_gaps_between_sorted_timestamps(self):
        docs = _doc_values(30, 0, 10)
        self.assertEqual(reliability.interval_latencies_ms(docs), [10.0, 20.0])

    def test_non_numeric_values_are_ignored(self):
        docs = [{"value": 0}, {"value": None}, {"value": "x"}, {"value": True}, {"value": 2_000_000}]
        self.assertEqual(reliability.interval_latencies_ms(docs), [2.0])

    def test_single_document_yields_no_intervals(self):
        self.assertEqual(reliability.interval_latencies_ms(_doc_values(5)), [])

    def test_format_helpers(self):
        self.assertEqual(reliability.format_ms(None), "N/A")
        self.assertEqual(reliability.format_ms(1.2784), "1.28 ms")
        self.assertEqual(reliability.format_uptime(3725), "1h 2m 5s")
        self.assertEqual(reliability.format_uptime(None), "N/A")


class ReadinessTests(unittest.TestCase):
    def _client(self, status=None, error=None):
        client = MagicMock()
        if error:
            client.admin.command.side_effect = error
        else:
            client.admin.command.return_value = status
        return client

    def test_healthy_set_reports_primary_and_eligible_secondary(self):
        client = self._client(_status(("m1:27017", "SECONDARY", 1), ("m2:27017", "PRIMARY", 1), ("m3:27017", "SECONDARY", 1)))
        r = reliability.replica_set_readiness(client)
        self.assertTrue(r["healthy"])
        self.assertEqual(r["detected_primary"], "m2:27017")
        self.assertEqual(r["eligible_partition_target"], "m1:27017")
        self.assertEqual(r["member_count"], 3)
        self.assertIsNone(r["error"])

    def test_unhealthy_secondary_is_not_eligible_and_degrades_health(self):
        client = self._client(_status(("m1:27017", "SECONDARY", 0), ("m2:27017", "PRIMARY", 1), ("m3:27017", "SECONDARY", 1)))
        r = reliability.replica_set_readiness(client)
        self.assertFalse(r["healthy"])
        self.assertEqual(r["eligible_partition_target"], "m3:27017")
        self.assertTrue(any("m1:27017" in w for w in r["warnings"]))

    def test_no_primary_is_degraded(self):
        client = self._client(_status(("m1:27017", "SECONDARY", 1), ("m2:27017", "SECONDARY", 1)))
        r = reliability.replica_set_readiness(client)
        self.assertFalse(r["healthy"])
        self.assertIsNone(r["detected_primary"])

    def test_status_failure_is_reported_not_raised(self):
        r = reliability.replica_set_readiness(self._client(error=ServerSelectionTimeoutError("down")))
        self.assertFalse(r["healthy"])
        self.assertIn("down", r["error"])


class SimulationInfoTests(unittest.TestCase):
    def test_script_is_described_never_executed(self):
        info = reliability.simulation_info()
        self.assertFalse(info["automatic_execution"])
        self.assertIn("lab_02_simulate_partition", info["script_path"])
        self.assertNotIn("subprocess", vars(reliability))
        self.assertNotIn("os", vars(reliability))


class LiveBenchmarkTests(unittest.TestCase):
    def test_refuses_to_run_without_primary(self):
        client = MagicMock()
        client.admin.command.return_value = _status(("m1:27017", "SECONDARY", 1))
        with self.assertRaises(reliability.ReliabilityError) as ctx:
            reliability.run_live_benchmark(client, 50)
        self.assertEqual(ctx.exception.status_code, 503)
        client.__getitem__.assert_not_called()


class RouteTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(lab9.router)
        self.http = TestClient(app)

    def test_run_latency_requires_confirmation(self):
        res = self.http.post("/api/lab9/run-latency", json={"docs_per_config": 10})
        self.assertEqual(res.status_code, 400)

    def test_partition_without_database_is_503_with_detail(self):
        res = self.http.get("/api/lab9/partition")
        self.assertEqual(res.status_code, 503)
        self.assertIn("detail", res.json())

    def test_partition_route_returns_report(self):
        client = MagicMock()
        collection = MagicMock()
        collection.count_documents.return_value = 0
        client.__getitem__.return_value.__getitem__.return_value = collection
        client.admin.command.return_value = _status(("m1:27017", "PRIMARY", 1))
        with patch.object(lab9, "_client", lambda: client):
            res = self.http.get("/api/lab9/partition")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        for key in ("baseline_writes", "partition_w1_writes", "partition_majority_writes", "cluster_readiness", "simulation"):
            self.assertIn(key, body)
        self.assertEqual(body["baseline_writes"]["status"], "NO_RECORDS")
        self.assertTrue(body["cluster_readiness"]["healthy"])
        self.assertFalse(body["simulation"]["automatic_execution"])


if __name__ == "__main__":
    unittest.main()
