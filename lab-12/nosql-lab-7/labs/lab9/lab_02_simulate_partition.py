#!/usr/bin/env python3
"""Lab 9.2 - Simulate a network partition (manual, confirmed, reversible).

What it does: disconnects ONE SECONDARY from the Docker network that carries the replica set, observes
rs.status() and the outcome of w:1 / w:majority writes while it is isolated, reconnects it, and verifies
recovery. Everything observed is written to a timestamped report; nothing is assumed or filled in.

It never runs automatically. From the project root (host shell with the docker CLI and Python 3.8+):

    python labs/lab9/lab_02_simulate_partition.py --dry-run     # prechecks + plan, changes nothing
    python labs/lab9/lab_02_simulate_partition.py               # asks you to type a confirmation
    bash   labs/lab9/lab_02_simulate_partition.sh               # same thing, via the launcher

Safety: the PRIMARY is never selected. No container, volume, document or replica-set configuration is
removed or changed. Isolating two members (loss of majority) is a separate phase (--phase majority-loss)
with its own, different confirmation text.

Standard library only; it drives the ``docker`` CLI (and ``mongosh`` inside the MongoDB containers).
Environment: MONGO_CONTAINERS="a b c"  override discovery   COMPOSE_PROJECT_NAME  restrict discovery
             MONGO_NETWORK             force the network    RS_NAME               replica-set name (rs0)
             LAB9_RESULTS_DIR          report directory
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

SERVICES = ("mongo1", "mongo2", "mongo3")
STATUS_JS = 'rs.status().members.forEach(m => print(m.name + "|" + m.stateStr + "|" + m.health))'
DEFAULT_RESULTS = Path(__file__).resolve().parent / "results"
IPV4 = re.compile(r"^(\d{1,3})(\.\d{1,3}){3}$")
SHORT_ID = re.compile(r"^[0-9a-f]{12}$")

EXIT_REFUSED = 1
EXIT_ENV = 2
EXIT_CONFIRM = 3
EXIT_DISCONNECT = 4
EXIT_USAGE = 64


class Refusal(Exception):
    """A precheck or confirmation failed. Nothing has been changed."""

    def __init__(self, message: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- docker
@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str = ""


Runner = Callable[[list], Result]


def subprocess_runner(args: list, timeout: int = 90) -> Result:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return Result(127, "", f"{args[0]}: command not found")
    except subprocess.TimeoutExpired:
        return Result(124, "", f"timed out after {timeout}s: {' '.join(map(str, args[:4]))}")
    return Result(done.returncode, done.stdout, done.stderr)


class Docker:
    """Thin wrapper over the docker CLI. The runner is injectable so the logic is testable without Docker."""

    def __init__(self, runner: Runner = subprocess_runner) -> None:
        self.run = runner

    def available(self) -> bool:
        return self.run(["docker", "info"]).returncode == 0

    def ps_names(self, *filters: str) -> list:
        args = ["docker", "ps", "--format", "{{.Names}}"]
        for f in filters:
            args += ["--filter", f]
        out = self.run(args)
        return [line.strip() for line in out.stdout.splitlines() if line.strip()] if out.returncode == 0 else []

    def inspect(self, container: str) -> Optional[dict]:
        out = self.run(["docker", "inspect", container])
        if out.returncode != 0:
            return None
        try:
            data = json.loads(out.stdout)
        except ValueError:
            return None
        return data[0] if data else None

    def mongosh(self, container: str, js: str) -> Result:
        return self.run(["docker", "exec", container, "mongosh", "--quiet", "--eval", js])

    def disconnect(self, network: str, container: str) -> Result:
        return self.run(["docker", "network", "disconnect", network, container])

    def connect(self, network: str, container: str, aliases=(), ip: Optional[str] = None) -> Result:
        args = ["docker", "network", "connect"]
        for alias in aliases:
            args += ["--alias", alias]
        if ip:
            args += ["--ip", ip]
        return self.run(args + [network, container])


# --------------------------------------------------------------- topology
@dataclass(frozen=True)
class Member:
    state: str
    health: str


def parse_topology(text: str) -> dict:
    """'mongo1:27017|SECONDARY|1' lines -> {service: Member}."""
    members: dict = {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[0]:
            members[parts[0].split(":")[0]] = Member(parts[1], parts[2])
    return members


def describe(topology: dict) -> str:
    return " ".join(f"{svc}={m.state}(health {m.health})" for svc, m in topology.items())


def primary_of(topology: dict) -> str:
    return next((s for s, m in topology.items() if m.state == "PRIMARY"), "")


def secondaries_of(topology: dict) -> list:
    return [s for s, m in topology.items() if m.state == "SECONDARY"]


def validate_topology(topology: dict, services=SERVICES) -> None:
    """Refuse anything other than: 3 members, 1 PRIMARY, 2 SECONDARY, all healthy."""
    if len(topology) != 3:
        raise Refusal(f"expected 3 replica-set members, found {len(topology)}")
    if not primary_of(topology):
        raise Refusal("no PRIMARY at the moment (an election may be running). Wait and retry.")
    if len(secondaries_of(topology)) != 2:
        raise Refusal(f"expected exactly 2 SECONDARY members, found {len(secondaries_of(topology))} "
                      f"(states: {describe(topology)})")
    for svc, member in topology.items():
        if member.health != "1":
            raise Refusal(f"member {svc} is not healthy (health={member.health}); restore the cluster first")
    for svc in services:
        if svc not in topology:
            raise Refusal(f"replica-set member '{svc}' is not part of the discovered topology")


def choose_targets(phase: str, topology: dict, containers: dict, requested: str = "") -> list:
    primary = primary_of(topology)
    if phase == "majority-loss":
        return secondaries_of(topology)
    if not requested:
        return [secondaries_of(topology)[0]]
    chosen = next((s for s, c in containers.items() if requested in (c, s)), "")
    if not chosen:
        raise Refusal(f"--target '{requested}' is not one of the replica-set containers")
    if chosen == primary:
        raise Refusal(f"'{requested}' is the current PRIMARY; only a secondary may be isolated")
    return [chosen]


def required_confirmation(phase: str, targets: list, containers: dict) -> str:
    if phase == "single":
        return f"ISOLATE {containers[targets[0]]}"
    return f"ISOLATE BOTH SECONDARIES {containers[targets[0]]} {containers[targets[1]]}"


# -------------------------------------------------------------- experiment
@dataclass
class Settings:
    phase: str = "single"
    dry_run: bool = False
    target: str = ""
    confirm: str = ""
    duration: int = 30
    poll: int = 5
    wtimeout_ms: int = 5000
    baseline_docs: int = 20
    w1_docs: int = 10
    majority_docs: int = 3
    recovery_timeout: int = 120
    results_dir: Path = DEFAULT_RESULTS
    rs_name: str = "rs0"


@dataclass
class Recorder:
    run_id: str
    started_at: str
    events: list = field(default_factory=list)
    polls: list = field(default_factory=list)
    write_tests: list = field(default_factory=list)
    log_path: Optional[Path] = None

    def log(self, message: str) -> None:
        line = f"[{utc_now()}] {message}"
        print(line, flush=True)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def event(self, name: str, detail: str = "") -> None:
        self.events.append({"t": utc_now(), "event": name, "detail": detail})
        self.log(f"{name}{' - ' + detail if detail else ''}")


class Experiment:
    def __init__(self, docker: Docker, settings: Settings, env: Optional[dict] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.docker, self.s = docker, settings
        self.env = os.environ if env is None else env
        self.sleep, self.clock = sleep, clock
        self.containers: dict = {}
        self.network = ""
        self.topology: dict = {}
        self.rec = Recorder(run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), started_at=utc_now())
        self.isolated: list = []
        self.aliases: dict = {}
        self.ipv4: dict = {}
        self.restored = False
        self.isolation_start = ""
        self.isolation_end = ""
        self.recovery_verified = False
        self.recovery_seconds: Optional[int] = None
        self.replication_check = ""
        self.final_topology = ""
        self.status = "ABORTED_BEFORE_ISOLATION"
        self.primary_before = ""
        self.initial_topology = ""
        self.test_mode = False

    # ---- discovery & prechecks -----------------------------------------------------------
    def discover(self) -> None:
        override = (self.env.get("MONGO_CONTAINERS") or "").split()
        if override:
            if len(override) != 3:
                raise Refusal(f"MONGO_CONTAINERS must name exactly 3 containers (got {len(override)})", EXIT_ENV)
            self.containers = dict(zip(SERVICES, override))
            return
        project = self.env.get("COMPOSE_PROJECT_NAME")
        for svc in SERVICES:
            filters = [f"label=com.docker.compose.service={svc}"]
            if project:
                filters.append(f"label=com.docker.compose.project={project}")
            names = self.docker.ps_names(*filters)
            if not names and self.docker.inspect(svc):
                names = [svc]
            if len(names) != 1:
                raise Refusal(f"could not uniquely identify the '{svc}' container (found {len(names)}). "
                              "Set COMPOSE_PROJECT_NAME or MONGO_CONTAINERS.", EXIT_ENV)
            self.containers[svc] = names[0]

    def check_running_and_network(self) -> None:
        networks: Optional[set] = None
        for svc, name in self.containers.items():
            info = self.docker.inspect(name)
            if not info or not (info.get("State") or {}).get("Running"):
                raise Refusal(f"container {name} ({svc}) is not running")
            nets = {n for n in (info.get("NetworkSettings") or {}).get("Networks", {}) if n not in ("bridge", "host", "none")}
            networks = nets if networks is None else networks & nets
        forced = self.env.get("MONGO_NETWORK")
        if forced:
            self.network = forced
        elif networks:
            self.network = sorted(networks)[0]
        else:
            raise Refusal("the three MongoDB containers do not share a user-defined docker network")

    def load_topology(self) -> bool:
        if self.test_mode:
            raw = (self.env.get("LAB9_TEST_TOPOLOGY") or "").replace(";", "\n")
        else:
            raw = ""
            for name in self.containers.values():
                if name in self.isolated_names():
                    continue
                out = self.docker.mongosh(name, STATUS_JS)
                if out.returncode == 0 and "|" in out.stdout:
                    raw = out.stdout
                    break
        topology = parse_topology(raw)
        if not topology:
            return False
        self.topology = topology
        return True

    def isolated_names(self) -> set:
        return {self.containers[s] for s in self.isolated if s in self.containers}

    # ---- helpers --------------------------------------------------------------------------
    def snapshot_network_settings(self, svc: str) -> None:
        info = self.docker.inspect(self.containers[svc]) or {}
        net = ((info.get("NetworkSettings") or {}).get("Networks") or {}).get(self.network) or {}
        aliases = [a for a in (net.get("Aliases") or []) if a and not SHORT_ID.match(a)]
        self.aliases[svc] = aliases
        ip = ((net.get("IPAMConfig") or {}).get("IPv4Address") or "")
        if IPV4.match(ip) and ip != "0.0.0.0":
            self.ipv4[svc] = ip

    def reconnect_all(self) -> None:
        for svc in list(self.isolated):
            name = self.containers[svc]
            info = self.docker.inspect(name) or {}
            if self.network in (info.get("NetworkSettings") or {}).get("Networks", {}):
                continue   # already connected
            aliases, ip = self.aliases.get(svc, []), self.ipv4.get(svc)
            label = ", ".join(aliases) or "none"
            attempts = []
            if ip:
                attempts.append(("reconnected", aliases, ip, f"aliases: {label}, ip {ip}"))
            attempts.append(("reconnected", aliases, None,
                             f"aliases: {label}" + ("; original IP not reused" if ip else "")))
            if aliases:
                attempts.append(("reconnected (aliases NOT restored)", [], None,
                                 "member hostnames may not resolve until you reconnect with --alias"))
            for event_name, als, address, note in attempts:
                if self.docker.connect(self.network, name, als, address).returncode == 0:
                    self.rec.event(event_name, f"{name} to {self.network} ({note})")
                    break
            else:
                flags = " ".join(f"--alias {a}" for a in aliases)
                self.rec.log(f"WARNING: could not reconnect {name}. Run manually: docker network connect {flags} {self.network} {name}")
        self.restored = True

    def run_writes(self, container: str, collection: str, count: int, write_concern: dict, label: str) -> None:
        js = f"""
const COLL={json.dumps(collection)}, N={count}, WC={json.dumps(write_concern)}, PHASE={json.dumps(self.s.phase)};
const res = {{attempted:N, acknowledged:0, timeouts:0, errors:0, errorSamples:[]}};
const t0 = Date.now();
for (let i=0;i<N;i++) {{
  try {{
    const r = db.getSiblingDB('lab9_partition')[COLL].insertOne({{test:COLL, phase:PHASE, i:i, createdAt:new Date()}}, {{writeConcern: WC}});
    if (r.acknowledged) res.acknowledged++;
  }} catch (e) {{
    const msg = String(e.message || e);
    if (e.code === 64 || /timed out|wtimeout/i.test(msg)) res.timeouts++; else res.errors++;
    if (res.errorSamples.length < 2) res.errorSamples.push(String(e.code) + ': ' + msg.slice(0,160));
  }}
}}
res.elapsedMs = Date.now() - t0;
print(JSON.stringify(res));
"""
        out = self.docker.mongosh(container, js)
        last = (out.stdout.strip().splitlines() or [""])[-1].strip()
        try:
            result = json.loads(last)
        except ValueError:
            result = {"attempted": count, "error": (out.stdout + out.stderr).strip()[-300:] or "no output"}
        self.rec.write_tests.append({"t": utc_now(), "label": label, "container": container, "collection": collection,
                                     "writeConcern": write_concern, "result": result})
        self.rec.event(f"write test: {label}", json.dumps(result))

    def poll_status(self) -> None:
        if self.load_topology():
            line = describe(self.topology)
            self.rec.polls.append({"t": utc_now(), "topology": line, "primary": primary_of(self.topology)})
        else:
            line = "rs.status() unavailable"
            self.rec.polls.append({"t": utc_now(), "topology": "unavailable", "primary": ""})
        self.rec.log(f"rs.status(): {line}")

    # ---- report -------------------------------------------------------------------------
    def report(self) -> dict:
        return {
            "experiment": "lab-9.2-network-partition",
            "run_id": self.rec.run_id,
            "executedAt": self.rec.started_at,
            "finished_at": utc_now(),
            "phase": self.s.phase,
            "status": self.status,
            "network": self.network,
            "replica_set": self.s.rs_name,
            "initial_topology": self.initial_topology,
            "primary_at_test": self.primary_before,
            "secondary_isolated": " ".join(self.isolated),
            "isolation_started": self.isolation_start,
            "isolation_ended": self.isolation_end,
            "recovery_verified": self.recovery_verified,
            "recovery_seconds": "" if self.recovery_seconds is None else str(self.recovery_seconds),
            "replication_check": self.replication_check,
            "final_topology": self.final_topology,
            "status_polls": self.rec.polls,
            "write_tests": self.rec.write_tests,
            "events": self.rec.events,
        }

    def write_report(self) -> Optional[Path]:
        if not self.isolation_start:      # refused / aborted attempts leave no artifact
            return None
        directory = self.s.results_dir
        path = directory / f"lab_02_partition_{self.rec.run_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.report(), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        (directory / "lab_02_partition_latest.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        self.rec.log(f"report written: {path}")
        return path

    # ---- main flow --------------------------------------------------------------------------
    def plan(self) -> None:
        s = self.s
        if self.env.get("LAB9_TEST_TOPOLOGY") and s.dry_run:
            self.test_mode = True
            self.containers = {svc: svc for svc in SERVICES}
            self.network = self.env.get("MONGO_NETWORK", "mongo-net")
            print("[TEST MODE] using LAB9_TEST_TOPOLOGY instead of a live cluster (dry-run only)")
        else:
            if not self.docker.available():
                raise Refusal("the docker CLI is unavailable or the daemon is not reachable "
                              "(is Docker Desktop / the engine running?)", EXIT_ENV)
            self.discover()
            self.check_running_and_network()
        if not self.load_topology():
            raise Refusal("could not read rs.status() from any MongoDB container")
        validate_topology(self.topology)
        if not self.test_mode:
            out = self.docker.mongosh(self.containers[primary_of(self.topology)], "print(rs.status().set)")
            actual = (out.stdout.strip().splitlines() or [""])[-1].strip()
            if actual != s.rs_name:
                raise Refusal(f"replica set is '{actual}', expected '{s.rs_name}'")
        self.targets = choose_targets(s.phase, self.topology, self.containers, s.target)
        self.primary_before = primary_of(self.topology)
        self.initial_topology = describe(self.topology)

    def print_plan(self) -> None:
        s = self.s
        effect = ("Expected effect: with 3 voting members, isolating one secondary leaves the primary and one secondary,\n"
                  "a majority of 2, so both w:1 and w:majority writes are normally still acknowledged. The isolated member\n"
                  "reports the others unreachable. The experiment records what actually happens.")
        if s.phase == "majority-loss":
            effect = ("Expected effect: isolating BOTH secondaries leaves the primary with 1 of 3 votes. It normally steps down\n"
                      "to SECONDARY, so w:1 writes fail with a 'not primary' style error and w:majority writes cannot be\n"
                      "acknowledged. The experiment records what actually happens.")
        print("\n================ LAB 9.2: NETWORK PARTITION SIMULATION ================")
        print(f"Replica set : {s.rs_name}   Network: {self.network}")
        print(f"Topology    : {describe(self.topology)}")
        print(f"Primary     : {self.primary_before} ({self.containers[self.primary_before]})   - will NOT be isolated")
        for svc in self.targets:
            print(f"Isolate     : {svc} ({self.containers[svc]})")
        print(f"Phase       : {s.phase}   observe {s.duration}s, poll {s.poll}s, wtimeout {s.wtimeout_ms} ms")
        print(effect)
        print("Restores    : the member is reconnected automatically (also on error / Ctrl+C) and recovery is verified.")
        print("Untouched   : containers, volumes, replica-set configuration and existing documents.")
        print("========================================================================")

    def confirm(self) -> None:
        required = required_confirmation(self.s.phase, self.targets, self.containers)
        if self.s.confirm:
            reply = self.s.confirm
        elif sys.stdin is not None and sys.stdin.isatty():
            print(f"Type exactly:  {required}")
            try:
                reply = input("> ")
            except EOFError:
                raise Refusal("no confirmation was entered; nothing was changed", EXIT_CONFIRM) from None
        else:
            raise Refusal("no terminal available and no --confirm supplied; nothing was changed", EXIT_CONFIRM)
        if reply != required:
            raise Refusal("confirmation did not match; nothing was changed", EXIT_CONFIRM)

    def execute(self) -> int:
        s, rec = self.s, self.rec
        s.results_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(s.results_dir, os.W_OK):
            raise Refusal(f"results directory '{s.results_dir}' is not writable")
        rec.log_path = s.results_dir / f"lab_02_partition_{rec.run_id}.log"
        rec.log_path.write_text("", encoding="utf-8")
        rec.event("start", f"run {rec.run_id} phase={s.phase} primary={self.primary_before} targets={' '.join(self.targets)}")
        try:
            self.status = "FAILED_BEFORE_ISOLATION"
            self.run_writes(self.containers[self.primary_before], "baseline", s.baseline_docs,
                            {"w": 1, "wtimeout": s.wtimeout_ms}, "baseline w:1 (before isolation)")

            for svc in self.targets:
                self.snapshot_network_settings(svc)
            self.isolation_start = utc_now()
            for svc in self.targets:
                result = self.docker.disconnect(self.network, self.containers[svc])
                if result.returncode != 0:
                    raise Refusal(f"could not disconnect {self.containers[svc]}: {result.stderr.strip()}", EXIT_DISCONNECT)
                self.isolated.append(svc)
                rec.event("isolated", f"{self.containers[svc]} disconnected from {self.network}")
            self.status = "ISOLATION_RUNNING"

            elapsed = 0
            while elapsed < s.duration:
                self.sleep(s.poll)
                elapsed += s.poll
                self.poll_status()

            observed = primary_of(self.topology) or self.primary_before
            writer = self.containers[observed]
            rec.event("write tests start", f"against container {writer} (primary according to rs.status: {primary_of(self.topology) or 'none'})")
            self.run_writes(writer, "partition_w1", s.w1_docs, {"w": 1, "wtimeout": s.wtimeout_ms}, "w:1 during isolation")
            self.run_writes(writer, "partition_majority", s.majority_docs,
                            {"w": "majority", "wtimeout": s.wtimeout_ms}, "w:majority during isolation")
            for svc in self.targets:
                out = self.docker.mongosh(self.containers[svc],
                                          'const s=rs.status(); print(s.members.map(m=>m.name+"="+m.stateStr).join(" "))')
                own = (out.stdout.strip().splitlines() or ["unavailable"])[-1]
                rec.event("isolated member's own view", f"{self.containers[svc]}: {own}")
        except BaseException:
            if self.isolated:
                self.status = "INTERRUPTED_RECOVERY_UNVERIFIED"
            raise
        finally:
            # runs on success, on error and on Ctrl+C / SIGTERM
            if self.isolated and not self.restored:
                rec.log("cleanup: restoring network connectivity ...")
                self.reconnect_all()
                self.isolation_end = self.isolation_end or utc_now()

        self.isolation_end = utc_now()
        self.verify_recovery()
        self.print_result()
        return 0 if self.recovery_verified else EXIT_REFUSED

    def verify_recovery(self) -> None:
        s, rec = self.s, self.rec
        self.status = "RECOVERY_UNVERIFIED"
        rec.event("waiting for recovery", f"up to {s.recovery_timeout}s")
        begin = self.clock()
        healthy = False
        saved, self.isolated = self.isolated, []        # all members may be queried again
        try:
            while self.clock() - begin < s.recovery_timeout:
                if self.load_topology() and len(self.topology) == 3 and primary_of(self.topology) \
                        and len(secondaries_of(self.topology)) == 2 \
                        and all(m.health == "1" for m in self.topology.values()):
                    healthy = True
                    break
                self.sleep(3)
            self.recovery_seconds = int(self.clock() - begin)
            self.final_topology = describe(self.topology)
            if not healthy:
                rec.event("recovery NOT verified",
                          f"cluster did not return to 3 healthy members within {s.recovery_timeout}s: {self.final_topology}")
                return
            rec.event("cluster healthy again", f"{self.final_topology} after {self.recovery_seconds}s")
            target = self.containers[self.targets[0]]
            primary = self.containers[primary_of(self.topology)]
            js = ("db.getMongo().setReadPref('secondaryPreferred'); "
                  "print(db.getSiblingDB('lab9_partition').partition_w1.countDocuments({}))")
            primary_count = target_count = ""
            for _ in range(10):
                primary_count = (self.docker.mongosh(primary, js).stdout.strip().splitlines() or [""])[-1].strip()
                target_count = (self.docker.mongosh(target, js).stdout.strip().splitlines() or [""])[-1].strip()
                if primary_count.isdigit() and primary_count == target_count:
                    self.replication_check = f"partition_w1 has {primary_count} documents on both the primary and {target}"
                    self.recovery_verified, self.status = True, "COMPLETED"
                    break
                self.sleep(3)
            else:
                self.replication_check = (f"partition_w1 counts differ: primary={primary_count} {target}={target_count} "
                                          "(replication not yet caught up)")
            rec.event("replication check", self.replication_check)
        finally:
            self.isolated = saved

    def print_result(self) -> None:
        path = self.s.results_dir / f"lab_02_partition_{self.rec.run_id}.json"
        print("\n================================ RESULT ================================")
        print(f"Status           : {self.status}")
        print(f"Isolation window : {self.isolation_start}  ->  {self.isolation_end}")
        print(f"Recovery verified: {str(self.recovery_verified).lower()} ({self.replication_check})")
        print(f"Final topology   : {self.final_topology}")
        print(f"Report           : {path}")
        print("========================================================================")


# ------------------------------------------------------------------- CLI
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Lab 9.2 network partition simulation (manual, confirmed, reversible)")
    p.add_argument("--dry-run", action="store_true", help="run every precheck and print the plan; change nothing")
    p.add_argument("--target", default="", help="secondary container to isolate (default: first healthy secondary)")
    p.add_argument("--phase", choices=("single", "majority-loss"), default="single",
                   help="single: isolate one secondary. majority-loss: isolate BOTH secondaries")
    p.add_argument("--duration", type=int, default=30, help="seconds to observe while isolated")
    p.add_argument("--poll", type=int, default=5, help="rs.status() polling interval in seconds")
    p.add_argument("--wtimeout-ms", type=int, default=5000, help="write-concern timeout for the test writes")
    p.add_argument("--results-dir", default=os.getenv("LAB9_RESULTS_DIR") or str(DEFAULT_RESULTS))
    p.add_argument("--confirm", default="", help="confirmation text, for non-interactive use (must match exactly)")
    args = p.parse_args(argv)
    if args.poll < 1 or args.duration < 0 or args.wtimeout_ms < 100:
        p.error("--poll must be >= 1, --duration >= 0 and --wtimeout-ms >= 100")
    return args


def main(argv=None, docker: Optional[Docker] = None) -> int:
    args = parse_args(argv)
    settings = Settings(phase=args.phase, dry_run=args.dry_run, target=args.target, confirm=args.confirm,
                        duration=args.duration, poll=args.poll, wtimeout_ms=args.wtimeout_ms,
                        results_dir=Path(args.results_dir), rs_name=os.getenv("RS_NAME", "rs0"))
    experiment = Experiment(docker or Docker(), settings)

    def _terminate(signum, frame):           # make SIGTERM run the cleanup in `finally`
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _terminate)
    except (ValueError, OSError):
        pass
    try:
        experiment.plan()
        experiment.print_plan()
        if settings.dry_run:
            print("[dry-run] All prechecks passed. Nothing was changed.")
            return 0
        experiment.confirm()
        return experiment.execute()
    except Refusal as refusal:
        print(f"[{utc_now()}] REFUSING: {refusal}")
        return refusal.code
    except KeyboardInterrupt:
        print(f"[{utc_now()}] interrupted; network connectivity has been restored where it was changed")
        return 130
    finally:
        if experiment.isolation_start:
            experiment.write_report()


if __name__ == "__main__":
    raise SystemExit(main())
