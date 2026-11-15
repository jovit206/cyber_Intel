from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend.routes import chapter11
from backend.services.shard_advisor import STRATEGIES, recommend


def payload(profile: str, patterns: list[str], **overrides):
    value = {
        "profile": profile,
        "expected_documents": 1_000_000,
        "write_frequency": "moderate",
        "query_patterns": patterns,
        "range_queries_important": True,
        "recent_write_concentration": False,
        "entity_scoped_queries": False,
        "compare_a": "range_customer_id",
        "compare_b": "hashed_customer_id",
    }
    value.update(overrides)
    return value


def request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/chapter11/runs",
                    "headers": [], "query_string": b"", "server": ("testserver", 80),
                    "client": ("testclient", 1234), "scheme": "http"})


class Chapter11AdvisorTests(unittest.TestCase):
    def test_advisor_profiles_use_documented_workload_rules(self):
        cases = [
            ("ecommerce_orders", ["customer_lookup", "order_lookup", "date_range"], "range_customer_id"),
            ("time_series", ["time_range", "date_range"], "hashed_event_time"),
            ("threat_intelligence", ["indicator_lookup", "date_range"], "hashed_indicator"),
        ]
        for profile, patterns, expected in cases:
            with self.subTest(profile=profile):
                result = recommend(payload(profile, patterns, write_frequency="high", recent_write_concentration=True,
                                          range_queries_important=profile != "threat_intelligence"))
                self.assertEqual(result["recommended"]["id"], expected)
                self.assertEqual(len(result["comparison"]), 2)
                self.assertNotEqual(result["comparison"][0]["id"], result["comparison"][1]["id"])
                self.assertTrue(all("rule_breakdown" in strategy for strategy in result["strategies"]))
                self.assertIn("not benchmark results", result["educational_notice"])

    def test_advisor_covers_customer_range_and_hotspot_tradeoffs(self):
        result = recommend(payload(
            "customer_transactions", ["customer_lookup", "date_range"], write_frequency="high",
            recent_write_concentration=True, entity_scoped_queries=True,
        ))
        by_id = {item["id"]: item for item in result["strategies"]}
        self.assertIn("Poor", by_id["hashed_customer_id"]["range_suitability"])
        self.assertIn("upper range", by_id["range_order_id"]["hotspot_risk"])
        self.assertGreater(by_id["hashed_customer_id"]["fit_score"], 0)

    def test_advisor_validation_requires_distinct_comparison(self):
        body = payload("custom", ["order_lookup"], compare_a="range_order_id", compare_b="range_order_id")
        with self.assertRaises(ValidationError):
            chapter11.AdvisorRequest.model_validate(body)
        too_many = payload("custom", ["order_lookup"], expected_documents=100_000_000_001)
        with self.assertRaises(ValidationError):
            chapter11.AdvisorRequest.model_validate(too_many)

    def test_experiment_runs_require_admin(self):
        with patch.object(chapter11, "_session_user", lambda ignored: {"role": "member"}):
            with self.assertRaises(HTTPException) as error:
                chapter11.start_run(chapter11.RunRequest(lab="lab11_2"), request())
        self.assertEqual(error.exception.status_code, 403)

    def test_non_admin_can_use_advisor_but_cannot_run(self):
        with patch.object(chapter11, "_session_user", lambda ignored: {"role": "member"}):
            self.assertEqual(chapter11.capabilities(request()), {"can_run_experiments": False})
        self.assertTrue(chapter11.advise(chapter11.AdvisorRequest.model_validate(payload("custom", ["order_lookup"]))))

    def test_unavailable_cluster_returns_a_safe_unavailable_state(self):
        from unittest.mock import patch

        with patch.object(chapter11, "MongoClient", side_effect=RuntimeError("private connection detail")):
            status = chapter11.cluster_status()
        self.assertFalse(status["available"])
        self.assertFalse(status["mongos_connected"])
        self.assertIn("temporarily unavailable", status["message"])
        self.assertNotIn("private connection detail", str(status))

    def test_experiment_scripts_measure_real_distribution_without_fixed_ratios(self):
        self.assertTrue({"range_customer_id", "hashed_customer_id", "range_order_id", "range_event_time",
                         "hashed_event_time", "hashed_indicator"}.issubset(STRATEGIES))
        script = (chapter11.LAB_ROOT / "lab11_2_hotspot.py").read_text(encoding="utf-8")
        self.assertIn("$shardedDataDistribution", script)
        self.assertIn("$collStats", script)
        self.assertIn("scatter_gather", script)
        self.assertNotIn("9500", script)
        self.assertNotIn("5000/5000", script)
        self.assertNotIn("5014/4986", script)

    def test_frontend_adds_one_chapter11_page_and_keeps_financial_summary_unique(self):
        html = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('data-view="lab11"'), 1)
        self.assertEqual(html.count('id="view-lab11"'), 1)
        self.assertEqual(html.count('id="financial-summary"'), 1)
        for label in ("Refresh shard-key evaluation", "Refresh hotspot measurements", "Refresh measurements",
                      "View raw explain output", "SHARD ADVISOR", "Download raw JSON evidence"):
            self.assertIn(label, html)
        for label in ("LAB NOTEBOOK", "Lab 11", "Chapter 11", "Experiment 11"):
            self.assertNotIn(label.lower(), html.lower())
        self.assertIn('src="lab11.js"', html)

    def test_new_routes_are_registered_on_the_application(self):
        from backend.main import app

        paths = set(app.openapi()["paths"])
        for path in ("/api/chapter11/status", "/api/chapter11/capabilities", "/api/chapter11/advisor",
                     "/api/chapter11/runs", "/api/chapter11/runs/{job_id}", "/api/chapter11/reports/{report_name}"):
            self.assertIn(path, paths)


if __name__ == "__main__":
    unittest.main()
