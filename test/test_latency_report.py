"""GET /api/lab9/latency: data_* collections completed by the newest saved run.

A fake MongoDB client and a temporary results directory are used; no database is contacted and no
benchmark is executed.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import lab9
from backend.services import reliability

KEYS = ["w1_j0", "w1_j1", "wmaj_j0", "wmaj_j1", "wall_j0", "wall_j1"]
LABELS = {"w1_j0": "w:1, j:false", "w1_j1": "w:1, j:true", "wmaj_j0": "w:majority, j:false",
          "wmaj_j1": "w:majority, j:true", "wall_j0": "w:all (w:3), j:false", "wall_j1": "w:all (w:3), j:true"}


def stamped_docs(count, step_ms):
    """Documents shaped like the real data_* collections: {_id: ObjectId, value: Int64 ns, label}."""
    base = 1_791_953_100_000_000_000
    return [{"_id": ObjectId(), "value": base + i * int(step_ms * 1_000_000), "label": "x"} for i in range(count)]


class FakeCursor(list):
    def sort(self, key, direction=1):
        return FakeCursor(sorted(self, key=lambda d: d.get(key, 0), reverse=direction < 0))


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def count_documents(self, _filter):
        return len(self.docs)

    def find(self, _filter=None, _projection=None):
        return FakeCursor(self.docs)


class FakeDatabase:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return FakeCollection(self.collections.get(name, []))

    def list_collection_names(self):
        return list(self.collections)


class FakeClient:
    def __init__(self, collections=None):
        self.db = FakeDatabase(collections or {})

    def __getitem__(self, _name):
        return self.db


def run_json(run_id, rows, finished="2026-10-14T19:32:00.000+00:00"):
    return {
        "experiment": "lab-9.1-write-concern-latency", "run_id": run_id, "finished_at": finished,
        "docs_per_configuration": 1000, "storage_mode": "new_run_collections",
        "configurations": [
            {"key": k, "label": LABELS[k], "acknowledged": 1000, "documents_attempted": 1000, "timeouts": 0,
             "errors": 0, "total_elapsed_ms": 1000.0 * v, "avg_ms": v, "p50_ms": v * 0.9, "p99_ms": v * 3,
             "status": "COMPLETED", **extra}
            for k, v, extra in rows
        ],
        "interpretation": ["measured"],
    }


class LatencyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        patcher = patch.dict(os.environ, {"LAB9_RESULTS_DIR": str(self.dir)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def save(self, name, payload):
        (self.dir / name).write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")

    def report(self, collections=None):
        return reliability.latency_report(FakeClient(collections))

    def by_key(self, report):
        return {c["key"]: c for c in report["configurations"]}

    # -------------------------------------------------------------- collections only
    def test_collections_are_read_from_the_real_schema(self):
        report = self.report({f"data_{k}": stamped_docs(1000, 2.0) for k in KEYS[:4]})
        rows = self.by_key(report)
        self.assertEqual(report["source"], "HISTORICAL")
        self.assertTrue(report["has_data"] and report["has_collection_data"])
        self.assertFalse(report["saved_run_fallback_used"])
        for key in KEYS[:4]:
            self.assertEqual(rows[key]["operation_count"], 1000)
            self.assertAlmostEqual(rows[key]["avg_ms"], 2.0, places=2)
            self.assertEqual(rows[key]["status"], "RECORDED_HISTORICAL")
            self.assertEqual(rows[key]["origin"], "collection")
        self.assertEqual(rows["wall_j0"]["status"], "UNAVAILABLE")
        self.assertEqual(rows["wall_j0"]["origin"], "none")

    def test_run_and_live_collections_are_mapped_from_their_names_and_values(self):
        data = {
            "run_20261014T193141Z_w1_j0": stamped_docs(1000, 2.0),
            "live_benchmark_wall_j1": stamped_docs(50, 5.0),
        }
        rows = self.by_key(self.report(data))
        self.assertEqual(rows["w1_j0"]["operation_count"], 1000)
        self.assertAlmostEqual(rows["w1_j0"]["avg_ms"], 2.0, places=2)
        self.assertEqual(rows["w1_j0"]["collections"], ["run_20261014T193141Z_w1_j0"])
        self.assertEqual(rows["wall_j1"]["operation_count"], 50)
        self.assertAlmostEqual(rows["wall_j1"]["avg_ms"], 5.0, places=2)
        self.assertEqual(rows["wall_j1"]["collections"], ["live_benchmark_wall_j1"])

    # ------------------------------------------------------------ saved run only
    def test_saved_run_populates_the_historical_table_when_collections_are_absent(self):
        self.save("lab_01_20261014T193141Z.json",
                  run_json("20261014T193141Z", [(k, 1.0 + i, {}) for i, k in enumerate(KEYS)]))
        report = self.report()                                   # no data_* collections at all
        rows = self.by_key(report)
        self.assertEqual(report["source"], "SAVED_RUN")
        self.assertTrue(report["has_data"])
        self.assertFalse(report["has_collection_data"])
        self.assertTrue(report["saved_run_fallback_used"])
        self.assertNotIn("HISTORICAL", report["label"])
        for i, key in enumerate(KEYS):
            row = rows[key]
            self.assertEqual(row["status"], "SAVED_RUN")                     # never labelled as a collection
            self.assertEqual(row["origin"], "saved_run")
            self.assertEqual(row["saved_run_file"], "lab_01_20261014T193141Z.json")
            self.assertEqual(row["operation_count"], 1000)                   # acknowledged operations
            self.assertEqual(row["avg_ms"], 1.0 + i)
            self.assertEqual(row["timeouts"], 0)
            self.assertEqual(row["errors"], 0)
            self.assertEqual(row["avg_display"], f"{1.0 + i:.2f} ms")
        self.assertEqual(rows["wall_j1"]["p99_ms"], 18.0)
        self.assertEqual(len(report["saved_runs"]), 1)                       # still listed separately

    # ------------------------------------------------------------------- mixed
    def test_collection_values_win_and_the_saved_run_only_fills_gaps(self):
        self.save("lab_01_20261014T193141Z.json",
                  run_json("20261014T193141Z", [(k, 9.0, {}) for k in KEYS]))
        report = self.report({f"data_{k}": stamped_docs(1000, 2.0) for k in KEYS[:4]})
        rows = self.by_key(report)
        for key in KEYS[:4]:
            self.assertAlmostEqual(rows[key]["avg_ms"], 2.0, places=2)       # NOT overwritten by the saved 9.0
            self.assertEqual(rows[key]["origin"], "collection")
        for key in KEYS[4:]:
            self.assertEqual(rows[key]["avg_ms"], 9.0)
            self.assertEqual(rows[key]["origin"], "saved_run")
        self.assertEqual(report["source"], "HISTORICAL")

    # ----------------------------------------------------------- missing data
    def test_nothing_available_reports_unavailable_without_inventing_numbers(self):
        report = self.report()
        self.assertEqual(report["source"], "UNAVAILABLE")
        self.assertFalse(report["has_data"])
        self.assertEqual(report["saved_runs"], [])
        self.assertEqual(report["highlights"], [])
        for row in report["configurations"]:
            self.assertEqual((row["operation_count"], row["avg_ms"], row["p50_ms"], row["p99_ms"]), (0, None, None, None))
            self.assertEqual(row["avg_display"], "N/A")

    def test_corrupt_files_are_skipped_not_fatal(self):
        self.save("lab_01_20261014T200000Z.json", "{ not json")
        self.save("lab_01_20261014T100000Z.json", run_json("20261014T100000Z", [("w1_j0", 3.0, {})]))
        report = self.report()
        self.assertEqual(self.by_key(report)["w1_j0"]["avg_ms"], 3.0)
        self.assertEqual([r["run_id"] for r in report["saved_runs"]], ["20261014T100000Z"])

    def test_rows_without_acknowledged_writes_are_not_used(self):
        failed = [("w1_j0", 1.0, {"acknowledged": 0, "avg_ms": None, "p50_ms": None, "p99_ms": None, "status": "FAILED"})]
        self.save("lab_01_20261014T193141Z.json", run_json("20261014T193141Z", failed))
        row = self.by_key(self.report())["w1_j0"]
        self.assertEqual((row["status"], row["avg_ms"], row["origin"]), ("UNAVAILABLE", None, "none"))

    def test_newest_run_wins_and_older_runs_fill_what_it_lacks(self):
        self.save("lab_01_20261014T100000Z.json",
                  run_json("20261014T100000Z", [("w1_j0", 5.0, {}), ("wall_j0", 8.0, {})], finished="2026-10-14T10:00:00+00:00"))
        self.save("lab_01_20261014T190000Z.json",
                  run_json("20261014T190000Z", [("w1_j0", 1.5, {})], finished="2026-10-14T19:00:00+00:00"))
        rows = self.by_key(self.report())
        self.assertEqual(rows["w1_j0"]["avg_ms"], 1.5)
        self.assertEqual(rows["w1_j0"]["saved_run_file"], "lab_01_20261014T190000Z.json")
        self.assertEqual(rows["wall_j0"]["avg_ms"], 8.0)
        self.assertEqual(rows["wall_j0"]["saved_run_file"], "lab_01_20261014T100000Z.json")

    def test_older_saved_files_that_only_have_labels_are_mapped(self):
        payload = run_json("20261014T193141Z", [("wall_j1", 4.0, {})])
        del payload["configurations"][0]["key"]
        self.save("lab_01_20261014T193141Z.json", payload)
        self.assertEqual(self.by_key(self.report())["wall_j1"]["avg_ms"], 4.0)


class LatencyApiTests(unittest.TestCase):
    def test_response_keeps_every_existing_field(self):
        app = FastAPI()
        app.include_router(lab9.router)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LAB9_RESULTS_DIR": tmp}):
            (Path(tmp) / "lab_01_20261014T193141Z.json").write_text(
                json.dumps(run_json("20261014T193141Z", [(k, 2.0, {}) for k in KEYS])), encoding="utf-8")
            with patch.object(lab9, "_client", lambda: FakeClient({"data_w1_j0": stamped_docs(50, 1.0)})):
                res = TestClient(app).get("/api/lab9/latency")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        for field in ("source", "label", "has_data", "database", "method", "retrieved_at", "highlights",
                      "configurations", "saved_runs", "w_all_note", "key_takeaways"):
            self.assertIn(field, body)
        self.assertEqual(len(body["configurations"]), 6)
        for row in body["configurations"]:
            for field in ("collection", "key", "wc", "journal", "label", "desc", "operation_count", "avg_ms",
                          "p50_ms", "p99_ms", "timestamp", "status", "operations_display", "avg_display",
                          "p50_display", "p99_display"):
                self.assertIn(field, row)
        origins = {r["key"]: r["origin"] for r in body["configurations"]}
        self.assertEqual(origins["w1_j0"], "collection")
        self.assertEqual(origins["wmaj_j0"], "saved_run")


if __name__ == "__main__":
    unittest.main()
