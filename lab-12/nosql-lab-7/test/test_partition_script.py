"""Lab 9.2 orchestration logic against a FAKE docker cluster.

No Docker, no containers and no network are touched: the Docker wrapper is replaced by FakeCluster, which
models connectivity, rs.status() output and write outcomes. The experiment is never run for real here.
"""
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "labs" / "lab9" / "lab_02_simulate_partition.py"
spec = importlib.util.spec_from_file_location("lab02", SCRIPT)
lab = importlib.util.module_from_spec(spec)
sys.modules["lab02"] = lab
spec.loader.exec_module(lab)       # must not run anything at import time

NET = "proj-net"
SERVICES = ("mongo1", "mongo2", "mongo3")


class FakeCluster:
    """Stands in for lab.Docker."""

    def __init__(self, primary="mongo1", ip=None, project="proj", health=None, states=None):
        self.names = {s: f"{project}-{s}-1" for s in SERVICES}
        self.primary = primary
        self.ip = ip
        self.health = health or {}
        self.states = states or {}
        self.connected = {s: True for s in SERVICES}
        self.calls = []
        self.connect_failures = 0
        self.write_result = lambda coll, wc: {"attempted": 1, "acknowledged": 1, "timeouts": 0, "errors": 0,
                                             "errorSamples": [], "elapsedMs": 5}
        self.raise_on_status_after = None
        self._status_calls = 0
        self.daemon_up = True

    # --- helpers
    def service_of(self, name):
        return next(s for s, n in self.names.items() if n == name)

    def _member_lines(self, observer):
        lines = []
        for svc in SERVICES:
            reachable = (self.connected[svc] and self.connected[observer]) or svc == observer
            if observer != svc and not reachable:
                lines.append(f"{svc}:27017|(not reachable/healthy)|0")
                continue
            state = self.states.get(svc) or ("PRIMARY" if svc == self.primary else "SECONDARY")
            lines.append(f"{svc}:27017|{state}|{self.health.get(svc, 1)}")
        return "\n".join(lines)

    # --- lab.Docker interface
    def available(self):
        return self.daemon_up

    def ps_names(self, *filters):
        svc = next((f.split("=")[-1] for f in filters if "compose.service" in f), None)
        project = next((f.split("=")[-1] for f in filters if "compose.project" in f), None)
        if project and not self.names[svc].startswith(project + "-"):
            return []
        return [self.names[svc]] if svc in self.names else []

    def inspect(self, container):
        if container not in self.names.values():
            return None
        svc = self.service_of(container)
        networks = {}
        if self.connected[svc]:
            ipam = {"IPv4Address": self.ip} if self.ip is not None else None
            networks[NET] = {"Aliases": [container, svc, "0123456789ab"], "IPAMConfig": ipam}
        return {"State": {"Running": True}, "NetworkSettings": {"Networks": networks}}

    def mongosh(self, container, js):
        svc = self.service_of(container)
        self.calls.append(("mongosh", container))
        if js == lab.STATUS_JS:
            self._status_calls += 1
            if self.raise_on_status_after is not None and self._status_calls > self.raise_on_status_after:
                raise RuntimeError("simulated crash while polling")
            return lab.Result(0, self._member_lines(svc))
        if "print(rs.status().set)" in js:
            return lab.Result(0, "rs0")
        if "const COLL=" in js:
            coll = js.split("const COLL=")[1].split(",")[0].strip().strip('"')
            wc = json.loads(js.split("WC=")[1].split(", PHASE")[0])
            return lab.Result(0, json.dumps(self.write_result(coll, wc)))
        if "countDocuments" in js:
            return lab.Result(0, "10")
        if "s.members.map" in js:
            return lab.Result(0, "mongo1:27017=(not reachable/healthy) mongo2:27017=SECONDARY")
        return lab.Result(0, "")

    def disconnect(self, network, container):
        self.calls.append(("disconnect", network, container))
        self.connected[self.service_of(container)] = False
        return lab.Result(0, "")

    def connect(self, network, container, aliases=(), ip=None):
        self.calls.append(("connect", network, container, tuple(aliases), ip))
        if self.connect_failures > 0:
            self.connect_failures -= 1
            return lab.Result(1, "", "boom")
        self.connected[self.service_of(container)] = True
        return lab.Result(0, "")

    def count(self, kind):
        return [c for c in self.calls if c[0] == kind]


def run_experiment(cluster, tmp, *, confirm=None, env=None, **overrides):
    settings = lab.Settings(duration=10, poll=5, recovery_timeout=30, results_dir=Path(tmp), **overrides)
    clock = itertools.count(0, 4)
    exp = lab.Experiment(cluster, settings, env=env or {}, sleep=lambda s: None, clock=lambda: next(clock))
    code = None
    try:
        exp.plan()
        if settings.dry_run:
            return exp, 0
        settings.confirm = confirm if confirm is not None else lab.required_confirmation(
            settings.phase, exp.targets, exp.containers)
        exp.confirm()
        code = exp.execute()
    except lab.Refusal as refusal:
        code = refusal.code
    except Exception:       # noqa: BLE001 - mirrors main(): report is still written
        code = "exception"
    finally:
        exp.write_report()
    return exp, code


class ImportTests(unittest.TestCase):
    def test_import_has_no_side_effects_and_cli_defaults_are_safe(self):
        args = lab.parse_args([])
        self.assertFalse(args.dry_run)
        self.assertEqual(args.phase, "single")
        self.assertEqual(args.confirm, "")
        self.assertEqual(lab.SERVICES, ("mongo1", "mongo2", "mongo3"))


class PrecheckTests(unittest.TestCase):
    def refuse(self, cluster=None, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster or FakeCluster(), tmp, dry_run=True, **kw)
            return code, cluster

    def test_healthy_topology_passes_and_changes_nothing(self):
        cluster = FakeCluster(primary="mongo2")
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp, dry_run=True)
            self.assertEqual(code, 0)
            self.assertEqual(exp.primary_before, "mongo2")
            self.assertNotIn(exp.targets[0], ("mongo2",))
            self.assertEqual(len(cluster.count("disconnect")), 0)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_unsafe_topologies_are_refused(self):
        cases = {
            "no primary": dict(states={"mongo1": "SECONDARY", "mongo2": "SECONDARY", "mongo3": "SECONDARY"}),
            "unhealthy member": dict(health={"mongo3": 0}),
            "recovering member": dict(states={"mongo2": "RECOVERING"}),
            "arbiter present": dict(states={"mongo2": "ARBITER"}),
        }
        for name, kwargs in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                cluster = FakeCluster(**kwargs)
                exp, code = run_experiment(cluster, tmp, dry_run=True)
                self.assertEqual(code, lab.EXIT_REFUSED, name)
                self.assertEqual(len(cluster.count("disconnect")), 0)

    def test_primary_can_never_be_the_target(self):
        for requested in ("mongo1", "proj-mongo1-1"):
            with self.subTest(requested), tempfile.TemporaryDirectory() as tmp:
                cluster = FakeCluster(primary="mongo1")
                exp, code = run_experiment(cluster, tmp, dry_run=True, target=requested)
                self.assertEqual(code, lab.EXIT_REFUSED)

    def test_unknown_target_is_refused_and_valid_secondary_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_experiment(FakeCluster(), tmp, dry_run=True, target="nosuch")[1], lab.EXIT_REFUSED)
            exp, code = run_experiment(FakeCluster(), tmp, dry_run=True, target="proj-mongo3-1")
            self.assertEqual((code, exp.targets), (0, ["mongo3"]))

    def test_docker_unavailable_is_an_environment_error(self):
        cluster = FakeCluster()
        cluster.daemon_up = False
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_experiment(cluster, tmp, dry_run=True)[1], lab.EXIT_ENV)

    def test_ambiguous_discovery_is_refused(self):
        class Two(FakeCluster):
            def ps_names(self, *filters):
                return ["a", "b"]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_experiment(Two(), tmp, dry_run=True)[1], lab.EXIT_ENV)


class ConfirmationTests(unittest.TestCase):
    def test_wrong_confirmation_changes_nothing(self):
        cluster = FakeCluster()
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp, confirm="yes")
            self.assertEqual(code, lab.EXIT_CONFIRM)
            self.assertEqual(len(cluster.count("disconnect")), 0)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_single_phase_text_does_not_authorise_majority_loss(self):
        cluster = FakeCluster()
        with tempfile.TemporaryDirectory() as tmp:
            single = "ISOLATE proj-mongo2-1"
            exp, code = run_experiment(cluster, tmp, phase="majority-loss", confirm=single)
            self.assertEqual(code, lab.EXIT_CONFIRM)
            self.assertEqual(len(cluster.count("disconnect")), 0)


class FlowTests(unittest.TestCase):
    def test_full_single_phase_run(self):
        cluster = FakeCluster(primary="mongo1", ip="172.20.0.12")
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp)
            self.assertEqual(code, 0)
            self.assertEqual(exp.status, "COMPLETED")
            disconnects = cluster.count("disconnect")
            self.assertEqual([d[2] for d in disconnects], ["proj-mongo2-1"])      # one secondary, never the primary
            connects = cluster.count("connect")
            self.assertEqual(len(connects), 1)
            _, _, container, aliases, ip = connects[0]
            self.assertEqual(container, "proj-mongo2-1")
            self.assertIn("mongo2", aliases)                                       # compose service alias restored
            self.assertNotIn("0123456789ab", aliases)
            self.assertEqual(ip, "172.20.0.12")
            report = json.loads((Path(tmp) / "lab_02_partition_latest.json").read_text())     # valid JSON
            self.assertTrue(report["recovery_verified"])
            self.assertEqual(report["primary_at_test"], "mongo1")
            self.assertEqual(report["secondary_isolated"], "mongo2")
            labels = [t["label"] for t in report["write_tests"]]
            self.assertEqual(labels, ["baseline w:1 (before isolation)", "w:1 during isolation", "w:majority during isolation"])
            self.assertEqual(report["write_tests"][2]["writeConcern"], {"w": "majority", "wtimeout": 5000})
            self.assertEqual(len(report["status_polls"]), 2)                      # duration 10 / poll 5
            self.assertTrue(report["isolation_started"] and report["isolation_ended"])
            # an isolated member really was reported unreachable while isolated
            self.assertTrue(any("mongo2=(not reachable/healthy)" in p["topology"] for p in report["status_polls"]))

    def test_invalid_ip_text_from_docker_is_never_passed_to_connect(self):
        cluster = FakeCluster(ip="invalid IP")
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp)
            self.assertEqual(code, 0)
            self.assertIsNone(cluster.count("connect")[0][4])

    def test_write_outcomes_are_recorded_honestly(self):
        def behaviour(coll, wc):
            if wc.get("w") == "majority":
                return {"attempted": 3, "acknowledged": 0, "timeouts": 3, "errors": 0,
                        "errorSamples": ["64: waiting for replication timed out"], "elapsedMs": 15000}
            return {"attempted": 10, "acknowledged": 10, "timeouts": 0, "errors": 0, "errorSamples": [], "elapsedMs": 40}
        cluster = FakeCluster()
        cluster.write_result = behaviour
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp)
            report = json.loads((Path(tmp) / "lab_02_partition_latest.json").read_text())
            majority = next(t for t in report["write_tests"] if t["label"].startswith("w:majority"))
            self.assertEqual((majority["result"]["acknowledged"], majority["result"]["timeouts"]), (0, 3))

    def test_cleanup_reconnects_after_a_crash(self):
        cluster = FakeCluster()
        cluster.raise_on_status_after = 2      # blows up while polling during isolation
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp)
            self.assertEqual(code, "exception")
            self.assertEqual(len(cluster.count("connect")), 1)
            self.assertTrue(all(cluster.connected.values()))
            report = json.loads((Path(tmp) / "lab_02_partition_latest.json").read_text())
            self.assertEqual(report["status"], "INTERRUPTED_RECOVERY_UNVERIFIED")
            self.assertFalse(report["recovery_verified"])

    def test_reconnect_falls_back_when_options_are_rejected(self):
        cluster = FakeCluster(ip="172.20.0.12")
        cluster.connect_failures = 2           # with-ip fails, aliases-only fails, plain succeeds
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp)
            attempts = cluster.count("connect")
            self.assertEqual([(len(a[3]) > 0, a[4]) for a in attempts], [(True, "172.20.0.12"), (True, None), (False, None)])
            self.assertTrue(cluster.connected["mongo2"])
            self.assertTrue(any(e["event"].startswith("reconnected (aliases NOT restored)") for e in exp.rec.events))

    def test_total_reconnect_failure_is_reported_not_hidden(self):
        cluster = FakeCluster()
        cluster.connect_failures = 99
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp)
            self.assertEqual(code, lab.EXIT_REFUSED)
            self.assertFalse(exp.recovery_verified)
            self.assertNotEqual(exp.status, "COMPLETED")

    def test_majority_loss_isolates_both_secondaries_never_the_primary(self):
        cluster = FakeCluster(primary="mongo3")
        with tempfile.TemporaryDirectory() as tmp:
            exp, code = run_experiment(cluster, tmp, phase="majority-loss")
            isolated = sorted(d[2] for d in cluster.count("disconnect"))
            self.assertEqual(isolated, ["proj-mongo1-1", "proj-mongo2-1"])
            self.assertNotIn("proj-mongo3-1", isolated)
            self.assertEqual(len(cluster.count("connect")), 2)


class CommandLineTests(unittest.TestCase):
    """The test seam only works together with --dry-run, so it can never change anything."""

    def run_cli(self, topology, *args, env_extra=None):
        env = {**os.environ, "LAB9_TEST_TOPOLOGY": topology}
        env.update(env_extra or {})
        done = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", *args], capture_output=True, text=True, env=env, timeout=60)
        return done.returncode, done.stdout

    def test_seam_accepts_a_healthy_set_and_rejects_unsafe_ones(self):
        ok = "mongo1:27017|SECONDARY|1;mongo2:27017|PRIMARY|1;mongo3:27017|SECONDARY|1"
        self.assertEqual(self.run_cli(ok)[0], 0)
        for bad in ("mongo1:27017|SECONDARY|1;mongo2:27017|SECONDARY|1;mongo3:27017|SECONDARY|1",
                    "mongo1:27017|SECONDARY|1;mongo2:27017|PRIMARY|1",
                    "mongo1:27017|SECONDARY|1;mongo2:27017|PRIMARY|1;mongo3:27017|(not reachable/healthy)|0"):
            code, out = self.run_cli(bad)
            self.assertEqual(code, lab.EXIT_REFUSED, out)
            self.assertIn("REFUSING", out)

    def test_non_interactive_without_confirm_is_refused_before_touching_anything(self):
        env = {**os.environ}
        env.pop("LAB9_TEST_TOPOLOGY", None)
        done = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--phase", "bogus"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 2)       # argparse rejects the invalid phase


if __name__ == "__main__":
    unittest.main()
