"""Activity 10.1 (Try it yourself) - Bank server failover while payments keep coming.

Run it with the bank servers up:
    docker compose run --rm app failover

The payment app (payment_app.py) sends a payment every 0.5 s through the
replica-set connection string, with retry and backoff. Meanwhile this script
uses Docker to:
  1. crash the main bank server         docker kill bank-primary
     bank-backup is elected and payments continue there
  2. bring it back                      docker start bank-primary
     it catches up and takes the primary role back (priority 2 > 1)
  3. take away the majority             docker kill bank-backup bank-dr
     bank-primary steps down and the bank stops taking payments
     then                               docker start bank-backup bank-dr
At the end every acknowledged payment is checked to be in the history exactly
once, and the total money in the bank to be unchanged.

Every command this script runs, and the reply it gets back, is saved to
failover_experiment.json next to this file.
"""

import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util

import payment_app as app

TITLE = "Activity 10.1 - Bank Server Failover During Payments"
OUTPUT_FILE = Path(__file__).with_suffix(".json")
PAYMENT_INTERVAL = 0.5

steps = []
milestones = []
results = []
stop = threading.Event()
t_start = time.time()


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nCommands and replies saved to {OUTPUT_FILE.name}")


def log(message):
    print(f"  {datetime.now():%H:%M:%S}  {message}", flush=True)


def milestone(text):
    milestones.append({"at": datetime.now(timezone.utc), "seconds": time.time() - t_start, "event": text})
    with print_lock:
        ticker.flush()
        log(f"== {text}")


def docker(*args):
    command = ["docker", *args]
    with print_lock:
        ticker.flush()
        log(f"$ {' '.join(command)}")
    done = subprocess.run(command, capture_output=True, text=True)
    record("docker", {"shell": " ".join(command)}, {"exitCode": done.returncode,
                                                     "output": (done.stdout + done.stderr).strip().splitlines()})
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed: {done.stderr.strip()}")


def wait_for(description, condition, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for: {description}")


def primary():
    return app.short(app.primary_of(app.replica_status()))


def last_server():
    committed = [r for r in results if r.outcome in ("committed", "already paid")]
    return app.short(committed[-1].server) if committed else None


class Ticker:
    """Prints runs of plain payments as one line; payments that needed retries get their own line."""

    def __init__(self):
        self.run = []

    def flush(self):
        if self.run:
            first, last = self.run[0], self.run[-1]
            numbers = f"payment #{first.number:03d}" if first is last                 else f"payments #{first.number:03d}-#{last.number:03d}"
            log(f"{numbers} committed on {app.short(first.server)}")
            self.run = []

    def add(self, payment):
        plain = payment.outcome == "committed" and payment.attempts == 1
        if plain and (not self.run or self.run[0].server == payment.server):
            self.run.append(payment)
            return
        self.flush()
        if plain:
            self.run.append(payment)
            return
        where = f" on {app.short(payment.server)}" if payment.server else ""
        attempts = f"{payment.attempts} attempt{'s' if payment.attempts > 1 else ''}"
        log(f"payment #{payment.number:03d} {payment.outcome.upper()}{where} after {attempts}, "
            f"{payment.seconds:.1f} s  [{app.summarize_errors(payment.errors)}]")


ticker = Ticker()
print_lock = threading.Lock()


def section(title):
    with print_lock:
        ticker.flush()
        print(f"\n{title}")


def payments():
    number = 0
    while not stop.is_set():
        number += 1
        payment = app.send_payment(number)
        results.append(payment)
        with print_lock:
            ticker.add(payment)
        stop.wait(PAYMENT_INTERVAL)
    with print_lock:
        ticker.flush()


def observe():
    last = None
    while not stop.is_set():
        line = app.one_line(app.replica_status())
        if line != last:
            with print_lock:
                ticker.flush()
                log(f"[observer] {line}")
            last = line
        stop.wait(1)


print(TITLE.upper())

if subprocess.run(["docker", "ps"], capture_output=True).returncode != 0:
    print("\nThis experiment controls the bank servers through Docker, but cannot reach it.")
    print("Run it with docker compose (the app service mounts /var/run/docker.sock):")
    print("  docker compose run --rm app failover")
    sys.exit(1)

print("\nSetup: 4 accounts of 100,000; one payment of 10 every 0.5 s around the ring")
app.setup()
BANK_TOTAL = sum(a["balance"] for a in app.accounts.find())
config = app.client.admin.command("replSetGetConfig")["config"]
priorities = {m["host"]: m["priority"] for m in config["members"]}
for host, priority in priorities.items():
    print(f"  {app.short(host):<13} priority {priority:g}")
record("Setup", {"replSetGetConfig": 1}, {"priorities": priorities, "accounts": app.RING, "total": BANK_TOTAL})

threads = [threading.Thread(target=observe, daemon=True), threading.Thread(target=payments, daemon=True)]
for t in threads:
    t.start()
time.sleep(5)

section("Step 1: the main bank server crashes")
docker("kill", "bank-primary")
milestone("bank-primary crashed (docker kill)")
wait_for("bank-backup to become primary", lambda: primary() == "bank-backup")
milestone("bank-backup elected primary")
wait_for("a payment on bank-backup", lambda: last_server() == "bank-backup")
milestone("payments continue on bank-backup")
time.sleep(5)

section("Step 2: the main bank server comes back")
docker("start", "bank-primary")
milestone("bank-primary restarted (docker start)")
wait_for("bank-primary to take the primary role back", lambda: primary() == "bank-primary")
milestone("bank-primary is primary again (priority 2 > 1)")
wait_for("a payment on bank-primary", lambda: last_server() == "bank-primary")
milestone("payments are back on bank-primary")
time.sleep(4)

section("Step 3: bank-backup and bank-dr go down together - no majority")
refused_before = sum(r.outcome in ("refused", "unknown") for r in results)
docker("kill", "bank-backup", "bank-dr")
milestone("bank-backup and bank-dr crashed (docker kill)")
wait_for("bank-primary to step down", lambda: primary() == "none")
milestone("bank-primary stepped down: no majority, no primary")
wait_for("a payment that fails", lambda: sum(r.outcome in ("refused", "unknown") for r in results) > refused_before,
         timeout=90)
milestone(f"payment #{results[-1].number:03d} gives up: {results[-1].outcome}")
docker("start", "bank-backup", "bank-dr")
milestone("bank-backup and bank-dr restarted (docker start)")
wait_for("a primary again", lambda: primary() == "bank-primary")
milestone("bank-primary is primary again")
wait_for("a payment after the outage", lambda: results and results[-1].outcome == "committed")
milestone("payments resume")
time.sleep(3)

stop.set()
for t in threads:
    t.join()

print("\nChecks")
acknowledged = [r for r in results if r.outcome in ("committed", "already paid")]
refused = [r for r in results if r.outcome == "refused"]
unknown = [r for r in results if r.outcome == "unknown"]
in_history = {d["idempotency_key"]: d["server"] for d in app.transfers.find({}, {"idempotency_key": 1, "server": 1})}
missing = [r.key for r in acknowledged if r.key not in in_history]
refused_but_applied = [r.key for r in refused if r.key in in_history]
unknown_applied = [r.key for r in unknown if r.key in in_history]
unknown_not_applied = [r.key for r in unknown if r.key not in in_history]
bank_total = sum(a["balance"] for a in app.accounts.find())
expected_total_moves = len(in_history)
print(f"  payments sent:               {len(results)}")
print(f"  acknowledged to the client:  {len(acknowledged)}  "
      f"({sum(r.outcome == 'already paid' for r in acknowledged)} found already paid on a retry)")
print(f"  refused to the client:       {len(refused)}")
print(f"  result unknown to the client: {len(unknown)}  -> reconciled with the history: "
      f"{len(unknown_applied)} applied {unknown_applied or ''}, {len(unknown_not_applied)} not applied "
      f"{unknown_not_applied or ''}")
print(f"  in the history:              {expected_total_moves}  (unique index: no payment can appear twice)")
print(f"  acknowledged but missing:    {len(missing)}")
print(f"  refused but applied anyway:  {len(refused_but_applied)} {refused_but_applied if refused_but_applied else ''}")
print(f"  total money:                 {bank_total:,} {'(unchanged)' if bank_total == BANK_TOTAL else '(CHANGED)'}")
per_server = {}
for r in acknowledged:
    per_server[app.short(r.server)] = per_server.get(app.short(r.server), 0) + 1
print("  payments per server:         " + ", ".join(f"{s} {n}" for s, n in per_server.items()))
record("Checks", {"compare": "client results with bank_failover.transfers"}, {
    "sent": len(results), "acknowledged": len(acknowledged), "refused": len(refused),
    "inHistory": expected_total_moves, "acknowledgedButMissing": missing,
    "refusedButApplied": refused_but_applied, "unknownApplied": unknown_applied,
    "unknownNotApplied": unknown_not_applied, "totalMoney": bank_total, "perServer": per_server,
    "payments": [r.__dict__ for r in results],
})

print("\nTimeline")
for m in milestones:
    print(f"  {m['at']:%H:%M:%S}  T+{m['seconds']:>5.1f} s  {m['event']}")
record("Timeline", {"note": "key moments logged by the script"}, milestones)

save_output()
