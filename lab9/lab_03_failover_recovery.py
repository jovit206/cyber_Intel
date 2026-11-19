#!/usr/bin/env python3
"""Lab 9.3 - Replica-set failover and recovery tests on an ISOLATED three-member replica set.

Runs against the separate cluster defined in lab9/failover-cluster/docker-compose.yml (containers
netrel-mongo1/2/3, replica set "rsfail", network netrel-net). It never touches the application's replica
set: every container it acts on must carry the label aegis.purpose=lab9-isolated-failover-tests.

Steps, each recorded with timestamps and MongoDB's own answers:
  1. initialise the replica set if needed and verify primary election
  2. verify two healthy secondaries replicating from the primary
  3. insert a real test document with w:majority
  4. confirm the document is readable on each secondary (replication delay measured)
  5. controlled primary step-down (replSetStepDown)
  6. verify that a new primary is elected (election time measured)
  7. continuous writes and reads through the replica-set connection string during the failover
  8. restart one member (docker restart)
  9. verify that it rejoins as a healthy SECONDARY and catches up
 10. confirm full cluster recovery: one primary, two secondaries, equal document counts

Standard library only; drives the docker CLI and mongosh inside the containers.

    python lab9/lab_03_failover_recovery.py --dry-run   # prechecks only, changes nothing
    python lab9/lab_03_failover_recovery.py             # asks for typed confirmation
    python lab9/lab_03_failover_recovery.py --yes       # non-interactive

Results: lab9/results/lab_03_failover_<timestamp>.json and lab_03_failover_latest.json,
shown on the Network Reliability page.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONTAINERS = ("netrel-mongo1", "netrel-mongo2", "netrel-mongo3")
LABEL_KEY, LABEL_VALUE = "aegis.purpose", "lab9-isolated-failover-tests"
RS_NAME = "rsfail"
DATABASE = "failover_lab"
DEFAULT_RESULTS = Path(__file__).resolve().parent / "results"
RS_URI = f"mongodb://{','.join(c + ':27017' for c in CONTAINERS)}/?replicaSet={RS_NAME}&retryWrites=true&serverSelectionTimeoutMS=15000"

STATUS_JS = ("try { const s = rs.status(); print(JSON.stringify({ok: 1, set: s.set, term: s.term, members: s.members.map(m => "
             "({name: m.name, state: m.stateStr, health: m.health, sync: m.syncSourceHost || null, optime: m.optimeDate}))})) }"
             " catch (e) { print(JSON.stringify({ok: 0, code: e.code, codeName: e.codeName, error: e.message})) }")


class TestFailure(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def run(args: list[str], timeout: int = 60, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise TestFailure(f"{' '.join(args[:4])} failed: {(result.stderr or result.stdout).strip()[:400]}")
    return result.stdout.strip()


def mongosh(container: str, js: str, timeout: int = 60, uri: str | None = None) -> dict:
    args = ["docker", "exec", container, "mongosh", "--quiet"] + ([uri] if uri else []) + ["--eval", js]
    out = run(args, timeout=timeout, check=False)
    line = next((l for l in reversed(out.splitlines()) if l.strip().startswith("{")), "")
    try:
        return json.loads(line)
    except ValueError:
        return {"ok": 0, "error": out[-400:]}


def status(container: str) -> dict:
    return mongosh(container, STATUS_JS, timeout=30)


def topology(prefer: str | None = None) -> dict:
    """rs.status() from the first member that answers."""
    for container in ([prefer] if prefer else []) + [c for c in CONTAINERS if c != prefer]:
        try:
            s = status(container)
        except (TestFailure, subprocess.TimeoutExpired):
            continue
        if s.get("ok"):
            return s
    return {"ok": 0}


def primary_of(s: dict) -> str | None:
    names = [m["name"].split(":")[0] for m in s.get("members", []) if m["state"] == "PRIMARY"]
    return names[0] if len(names) == 1 else None


def healthy_secondaries(s: dict) -> list[str]:
    return [m["name"].split(":")[0] for m in s.get("members", []) if m["state"] == "SECONDARY" and m["health"] == 1]


def wait_for(predicate, timeout: float, interval: float = 0.5, prefer: str | None = None):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = topology(prefer)
        if last.get("ok") and predicate(last):
            return last
        time.sleep(interval)
    raise TestFailure(f"timed out after {timeout:.0f}s; last topology: {json.dumps(last)[:400]}")


def precheck() -> None:
    try:
        run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=20)
    except (TestFailure, FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise TestFailure(f"Docker is not reachable: {error}")
    for container in CONTAINERS:
        inspect = run(["docker", "inspect", "--format",
                       "{{.State.Running}}|{{index .Config.Labels \"" + LABEL_KEY + "\"}}", container], check=False)
        if "|" not in inspect:
            raise TestFailure(f"container {container} does not exist; start lab9/failover-cluster first")
        running, label = inspect.split("|", 1)
        if label != LABEL_VALUE:
            raise TestFailure(f"REFUSING: {container} is not labelled {LABEL_KEY}={LABEL_VALUE}; it is not the isolated cluster")
        if running != "true":
            raise TestFailure(f"container {container} is not running")


def initialise(report: dict) -> None:
    s = status(CONTAINERS[0])
    if s.get("ok"):
        report["initialisation"] = {"performed": False, "note": "replica set already initialised"}
        return
    if s.get("codeName") not in {"NotYetInitialized"}:
        raise TestFailure(f"unexpected rs.status() answer: {s}")
    config = {"_id": RS_NAME, "members": [{"_id": i, "host": f"{c}:27017"} for i, c in enumerate(CONTAINERS)]}
    out = mongosh(CONTAINERS[0], f"print(JSON.stringify(rs.initiate({json.dumps(config)})))")
    if not out.get("ok"):
        raise TestFailure(f"rs.initiate failed: {out}")
    report["initialisation"] = {"performed": True, "config": config, "at": now()}
    log(f"replica set {RS_NAME} initiated")


def member_count(container: str, run_id: str) -> int:
    out = mongosh(container, f"db.getMongo().setReadPref('secondaryPreferred'); print(JSON.stringify({{ok: 1, n: "
                             f"db.getSiblingDB('{DATABASE}').events.countDocuments({{run_id: '{run_id}'}})}}))", timeout=30)
    return int(out.get("n", -1)) if out.get("ok") else -1


def traffic_js(run_id: str, seconds: int) -> str:
    """Writes and reads every 200 ms through the replica-set URI; prints one JSON summary at the end."""
    return f"""
const coll = db.getSiblingDB('{DATABASE}').events;
const end = Date.now() + {seconds * 1000};
const writes = [], reads = [];
let seq = 0;
while (Date.now() < end) {{
  const t0 = Date.now();
  try {{
    coll.insertOne({{run_id: '{run_id}', kind: 'failover_traffic', seq: seq++, at: new Date()}}, {{writeConcern: {{w: 'majority', wtimeout: 5000}}}});
    let p = null;
    try {{ p = db.hello().primary; }} catch (e) {{ p = null; }}
    writes.push({{t: t0, ok: true, ms: Date.now() - t0, p: p}});
  }} catch (e) {{ writes.push({{t: t0, ok: false, ms: Date.now() - t0, error: e.codeName || e.message}}); }}
  const t1 = Date.now();
  try {{ coll.findOne({{run_id: '{run_id}'}}); reads.push({{t: t1, ok: true, ms: Date.now() - t1}}); }}
  catch (e) {{ reads.push({{t: t1, ok: false, ms: Date.now() - t1, error: e.codeName || e.message}}); }}
  sleep(200);
}}
print(JSON.stringify({{ok: 1, writes: writes, reads: reads}}));
"""


def summarise(ops: list[dict]) -> dict:
    ok = [o for o in ops if o["ok"]]
    failed = [o for o in ops if not o["ok"]]
    gap = 0
    previous = None
    for o in ops:
        if o["ok"]:
            if previous is not None:
                gap = max(gap, o["t"] - previous)
            previous = o["t"] + o["ms"]
    slow = max((o["ms"] for o in ok), default=0)
    summary = {"attempted": len(ops), "succeeded": len(ok), "failed": len(failed),
               "errors": sorted({o.get("error", "") for o in failed}), "slowest_success_ms": slow,
               "longest_gap_between_successes_ms": gap}
    # For writes: which member the client saw as primary, in order, with the time of each change.
    sequence = []
    for o in ok:
        if "p" in o and (not sequence or sequence[-1]["primary"] != o["p"]):
            sequence.append({"primary": o["p"], "first_seen": datetime.fromtimestamp(o["t"] / 1000, timezone.utc).isoformat(timespec="milliseconds")})
    if sequence:
        summary["primary_as_seen_by_client"] = sequence
    return summary


def run_tests(results_dir: Path, assume_yes: bool) -> dict:
    run_id = f"failover-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    report: dict = {"experiment": "lab_03_failover_recovery", "run_id": run_id, "replica_set": RS_NAME,
                    "containers": list(CONTAINERS), "network": "netrel-net", "isolated_cluster": True,
                    "started_at": now(), "steps": [], "status": "running"}

    def step(name: str, **data):
        entry = {"step": len(report["steps"]) + 1, "name": name, "at": now(), **data}
        report["steps"].append(entry)
        log(f"step {entry['step']}: {name} -> {json.dumps(data, default=str)[:300]}")
        return entry

    try:
        initialise(report)
        # 1. primary election
        t0 = time.monotonic()
        s = wait_for(lambda s: primary_of(s) and len(healthy_secondaries(s)) == 2, 90)
        primary = primary_of(s)
        step("primary elected", passed=True, primary=primary, term=s.get("term"),
             wait_seconds=round(time.monotonic() - t0, 2), members=s["members"])
        # 2. secondaries
        secondaries = healthy_secondaries(s)
        step("secondaries healthy and replicating", passed=len(secondaries) == 2, secondaries=secondaries,
             sync_sources={m["name"]: m["sync"] for m in s["members"] if m["state"] == "SECONDARY"})
        # 3. insert test document
        marker = {"run_id": run_id, "kind": "replication_probe", "note": "Lab 9.3 replication test document"}
        out = mongosh(primary, f"const r = db.getSiblingDB('{DATABASE}').events.insertOne(Object.assign({json.dumps(marker)}, "
                               f"{{at: new Date()}}), {{writeConcern: {{w: 'majority', wtimeout: 10000}}}}); "
                               f"print(JSON.stringify({{ok: 1, id: r.insertedId.toString(), acknowledged: r.acknowledged}}))")
        if not out.get("ok") or not out.get("acknowledged"):
            raise TestFailure(f"test insert failed: {out}")
        inserted_at = time.monotonic()
        step("test document inserted with w:majority", passed=True, primary=primary, inserted_id=out["id"])
        # 4. replication to each secondary
        seen = {}
        for secondary in secondaries:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                found = mongosh(secondary, f"db.getMongo().setReadPref('secondaryPreferred'); print(JSON.stringify({{ok: 1, "
                                           f"found: db.getSiblingDB('{DATABASE}').events.countDocuments({{run_id: '{run_id}', kind: 'replication_probe'}})}}))")
                if found.get("found") == 1:
                    seen[secondary] = round((time.monotonic() - inserted_at) * 1000)
                    break
                time.sleep(0.2)
        step("document replicated to secondaries", passed=len(seen) == len(secondaries),
             visible_after_ms=seen, note="measured from the acknowledged insert to the first successful read on each secondary, "
                                         "including docker exec overhead")
        # 5-7. step-down with concurrent traffic
        observer = secondaries[0]
        traffic_seconds = 25
        traffic_result: dict = {}

        def traffic():
            traffic_result.update(mongosh(observer, traffic_js(run_id, traffic_seconds), timeout=traffic_seconds + 60, uri=RS_URI))

        worker = threading.Thread(target=traffic, daemon=True)
        worker.start()
        time.sleep(4)  # baseline traffic before the step-down
        stepdown_started = time.monotonic()
        stepdown_at = now()
        sd = mongosh(primary, "try { const r = db.adminCommand({replSetStepDown: 30, secondaryCatchUpPeriodSecs: 10}); "
                              "print(JSON.stringify({ok: r.ok})) } catch (e) { print(JSON.stringify({ok: 1, note: 'connection closed by step-down: ' + e.message})) }")
        step("controlled primary step-down", passed=bool(sd.get("ok")), stepped_down=primary, at_time=stepdown_at, response=sd)
        s = wait_for(lambda s: primary_of(s) not in (None, primary), 60, interval=0.25, prefer=observer)
        new_primary = primary_of(s)
        election_seconds = round(time.monotonic() - stepdown_started, 2)
        step("new primary elected", passed=new_primary not in (None, primary), old_primary=primary,
             new_primary=new_primary, term=s.get("term"), election_seconds=election_seconds)
        worker.join(traffic_seconds + 90)
        writes, reads = summarise(traffic_result.get("writes", [])), summarise(traffic_result.get("reads", []))
        step("reads and writes during failover", passed=writes["succeeded"] > 0 and reads["succeeded"] > 0,
             writes=writes, reads=reads, connection="replica-set URI with retryWrites=true",
             note="w:majority writes and primary reads every 200 ms for 25 s, spanning the step-down")
        # 8-9. restart one secondary and verify it rejoins
        s = topology(new_primary)
        restart_target = next(n for n in healthy_secondaries(s))
        restart_started = time.monotonic()
        run(["docker", "restart", "--time", "10", restart_target], timeout=90)
        step("member restarted", passed=True, container=restart_target)
        s = wait_for(lambda s: restart_target in healthy_secondaries(s) and primary_of(s) is not None, 120, prefer=new_primary)
        rejoin_seconds = round(time.monotonic() - restart_started, 2)
        primary_now = primary_of(s)
        expected = member_count(primary_now, run_id)
        deadline = time.monotonic() + 30
        restarted_count = -1
        while time.monotonic() < deadline:
            restarted_count = member_count(restart_target, run_id)
            if restarted_count == expected:
                break
            time.sleep(0.5)
        step("restarted member rejoined and caught up", passed=restarted_count == expected and expected > 0,
             container=restart_target, rejoin_seconds=rejoin_seconds, documents_on_primary=expected,
             documents_on_restarted_member=restarted_count)
        # 10. full recovery
        s = wait_for(lambda s: primary_of(s) and len(healthy_secondaries(s)) == 2, 60)
        counts = {c: member_count(c, run_id) for c in CONTAINERS}
        recovered = len(set(counts.values())) == 1 and -1 not in counts.values()
        step("cluster recovered", passed=bool(primary_of(s)) and len(healthy_secondaries(s)) == 2 and recovered,
             primary=primary_of(s), secondaries=healthy_secondaries(s), documents_per_member=counts,
             final_members=s["members"])
        report["status"] = "passed" if all(st.get("passed") for st in report["steps"]) else "failed"
    except (TestFailure, subprocess.TimeoutExpired) as error:
        report["status"] = "error"
        report["error"] = str(error)
        log(f"ERROR: {error}")
    report["finished_at"] = now()
    report["summary"] = {
        "steps_passed": sum(1 for st in report["steps"] if st.get("passed")),
        "steps_total": len(report["steps"]),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    text = json.dumps(report, indent=2, default=str)
    (results_dir / f"lab_03_failover_{stamp}.json").write_text(text, encoding="utf-8")
    (results_dir / "lab_03_failover_latest.json").write_text(text, encoding="utf-8")
    log(f"report written: {results_dir / f'lab_03_failover_{stamp}.json'} (status {report['status']})")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="run prechecks and show the plan; change nothing")
    parser.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    parser.add_argument("--results-dir", default=os.getenv("LAB9_RESULTS_DIR") or str(DEFAULT_RESULTS))
    args = parser.parse_args()
    try:
        precheck()
    except TestFailure as error:
        log(str(error))
        return 2
    s = topology()
    log(f"isolated cluster reachable; replica set: {s.get('set') or 'not initialised'}; "
        f"members: {[(m['name'], m['state']) for m in s.get('members', [])]}")
    if args.dry_run:
        log("[dry-run] prechecks passed. Plan: initialise if needed, verify election and replication, insert a test "
            "document, step down the primary with traffic running, restart one secondary, verify recovery. Nothing changed.")
        return 0
    if not args.yes:
        answer = input("Type FAILOVER TEST to step down and restart members of the ISOLATED cluster: ").strip()
        if answer != "FAILOVER TEST":
            log("not confirmed; nothing changed")
            return 1
    report = run_tests(Path(args.results_dir), args.yes)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
