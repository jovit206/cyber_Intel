"""Lab 9.1 core + CLI: no MongoDB, no network, no benchmark is run."""
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pymongo.errors import OperationFailure, WTimeoutError

from backend.services import write_concern as core

ROOT = Path(__file__).resolve().parent.parent


class FakeCollection:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0

    def insert_one(self, doc):
        self.calls += 1
        return self.behaviour(self.calls)


class FakeDb:
    def __init__(self, behaviour):
        self.col = FakeCollection(behaviour)
        self.dropped = []
        self.write_concern = None

    def get_collection(self, name, write_concern=None):
        self.write_concern = write_concern
        return self.col

    def drop_collection(self, name):
        self.dropped.append(name)


class FakeClient:
    def __init__(self, behaviour):
        self.db = FakeDb(behaviour)

    def __getitem__(self, name):
        return self.db


class ConfigurationTests(unittest.TestCase):
    def test_matrix_has_w1_majority_all_each_with_and_without_journal(self):
        configs = core.build_configurations(3)
        self.assertEqual(len(configs), 6)
        self.assertEqual({(c.requested, c.journal) for c in configs},
                         {(r, j) for r in ("w:1", "w:majority", "w:all") for j in (False, True)})

    def test_w_all_is_expressed_as_the_member_count_not_the_string_all(self):
        for members in (3, 5):
            alls = [c for c in core.build_configurations(members) if c.requested == "w:all"]
            self.assertTrue(all(c.w == members and isinstance(c.w, int) for c in alls))
            self.assertTrue(all("not a valid MongoDB write concern" in c.mapping for c in alls))
        self.assertNotIn("all", [c.w for c in core.build_configurations(3)])

    def test_data_bearing_members_excludes_arbiters(self):
        client = MagicMock()
        client.admin.command.return_value = {"config": {"members": [
            {"host": "a"}, {"host": "b"}, {"host": "c", "arbiterOnly": True}]}}
        self.assertEqual(core.data_bearing_members(client), 2)


class MeasurementTests(unittest.TestCase):
    cfg = core.build_configurations(3)[0]

    def test_counts_and_percentiles_for_a_clean_run(self):
        client = FakeClient(lambda n: None)
        row = core.run_configuration(client, self.cfg, 50, database="d", collection="c")
        self.assertEqual((row["acknowledged"], row["timeouts"], row["errors"]), (50, 0, 0))
        self.assertEqual(row["status"], "COMPLETED")
        self.assertLessEqual(row["p50_ms"], row["p99_ms"])
        self.assertIsNotNone(row["avg_ms_per_attempt"])

    def test_write_concern_timeouts_and_errors_are_counted_separately(self):
        def behaviour(n):
            if n % 10 == 0:
                raise WTimeoutError("waiting for replication timed out", 64, {})
            if n % 10 == 5:
                raise OperationFailure("Not enough data-bearing nodes", 100)
        row = core.run_configuration(FakeClient(behaviour), self.cfg, 40, database="d", collection="c")
        self.assertEqual(row["timeouts"], 4)
        self.assertEqual(row["errors"], 4)
        self.assertEqual(row["acknowledged"], 32)
        self.assertEqual(row["status"], "PARTIAL")
        self.assertTrue(row["error_samples"])

    def test_configuration_aborts_early_when_nothing_can_be_acknowledged(self):
        def behaviour(n):
            raise WTimeoutError("timed out", 64, {})
        client = FakeClient(behaviour)
        row = core.run_configuration(client, self.cfg, 1000, database="d", collection="c")
        self.assertTrue(row["aborted_early"])
        self.assertEqual(client.db.col.calls, core.MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(row["status"], "ABORTED")
        self.assertIsNone(row["avg_ms"])

    def test_write_concern_is_applied_with_a_finite_timeout(self):
        client = FakeClient(lambda n: None)
        core.run_configuration(client, self.cfg, 3, database="d", collection="c", wtimeout_ms=2500)
        document = client.db.write_concern.document
        self.assertEqual(document["wtimeout"], 2500)
        self.assertIn("w", document)

    def test_historical_collections_are_only_dropped_when_explicitly_requested(self):
        client = FakeClient(lambda n: None)
        core.run_configuration(client, self.cfg, 2, database="d", collection="data_w1_j0")
        self.assertEqual(client.db.dropped, [])
        core.run_configuration(client, self.cfg, 2, database="d", collection="data_w1_j0", drop_first=True)
        self.assertEqual(client.db.dropped, ["data_w1_j0"])


class ExperimentSafetyTests(unittest.TestCase):
    def _client(self, members, primary=True):
        client = MagicMock()

        def command(name, *args, **kwargs):
            if name == "replSetGetStatus":
                return {"set": "rs0", "members": members}
            if name == "replSetGetConfig":
                return {"config": {"members": [{"host": m["name"]} for m in members]}}
            return {"ok": 1}
        client.admin.command.side_effect = command
        return client

    def test_refuses_without_a_primary(self):
        members = [{"name": "a:27017", "stateStr": "SECONDARY", "health": 1},
                   {"name": "b:27017", "stateStr": "SECONDARY", "health": 1}]
        with self.assertRaises(core.ExperimentError):
            core.run_experiment(self._client(members))

    def test_refuses_when_a_member_is_unhealthy(self):
        members = [{"name": "a:27017", "stateStr": "PRIMARY", "health": 1},
                   {"name": "b:27017", "stateStr": "(not reachable/healthy)", "health": 0}]
        with self.assertRaises(core.ExperimentError) as ctx:
            core.run_experiment(self._client(members))
        self.assertIn("b:27017", str(ctx.exception))


class InterpretationAndArtifactTests(unittest.TestCase):
    ROWS = [
        {"key": "w1_j0", "label": "w:1, j:false", "avg_ms": 1.0, "timeouts": 0, "errors": 0, "aborted_early": False},
        {"key": "wmaj_j0", "label": "w:majority, j:false", "avg_ms": 4.0, "timeouts": 0, "errors": 0, "aborted_early": False},
        {"key": "wall_j0", "label": "w:all (w:3), j:false", "avg_ms": 2.5, "timeouts": 2, "errors": 0, "aborted_early": False},
    ]

    def test_interpretation_is_derived_from_measurements_not_assumed(self):
        text = " ".join(core.interpret(self.ROWS))
        self.assertIn("w:1, j:false (1.00 ms) < w:all (w:3), j:false (2.50 ms) < w:majority, j:false (4.00 ms)", text)
        self.assertIn("4.00x", text)
        self.assertIn("Not every write was acknowledged for: w:all", text)

    def test_ranking_follows_the_data_even_when_w_all_is_faster_than_majority(self):
        order = [r["label"] for r in sorted(self.ROWS, key=lambda r: r["avg_ms"])]
        self.assertEqual(order[-1], "w:majority, j:false")

    def test_artifacts_are_written_and_listed(self):
        result = {
            "experiment": "x", "run_id": "20260101T000000Z", "finished_at": "t", "docs_per_configuration": 5,
            "storage_mode": "new_run_collections", "w_all_mapping": "m", "interpretation": ["a"],
            "configurations": [{"label": "w:1, j:false", "requested": "w:1", "w": 1, "journal": False,
                                "documents_attempted": 5, "acknowledged": 5, "timeouts": 0, "errors": 0,
                                "aborted_early": False, "total_elapsed_ms": 10.0, "avg_ms_per_attempt": 2.0,
                                "avg_ms": 2.0, "p50_ms": 2.0, "p99_ms": 3.0, "status": "COMPLETED", "collection": "c"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = core.save_artifacts(result, Path(tmp))
            self.assertEqual(json.loads(Path(paths["json"]).read_text())["run_id"], "20260101T000000Z")
            rows = list(csv.DictReader(Path(paths["csv"]).open()))
            self.assertEqual(rows[0]["label"], "w:1, j:false")
            self.assertEqual(json.loads(Path(paths["latest"]).read_text())["run_id"], "20260101T000000Z")
            runs = core.list_saved_runs(directory=Path(tmp))
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["configurations"][0]["avg_ms"], 2.0)


class ScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("lab01", ROOT / "labs" / "lab9" / "lab_01_write_concern_latency.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)   # importing must not run anything or touch the network

    def test_defaults_match_the_assignment(self):
        args = self.module.parse_args([])
        self.assertEqual(args.docs, 1000)
        self.assertFalse(args.replace_historical)
        self.assertFalse(args.dry_run)

    def test_invalid_arguments_are_rejected(self):
        for argv in (["--docs", "0"], ["--docs", "100001"], ["--wtimeout-ms", "5"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.module.parse_args(argv)


if __name__ == "__main__":
    unittest.main()
