"""Lab 8.2 - Force a failover, observe the election, and watch a client reconnect.

Run this from this folder:
    docker compose run --rm python lab_8_2_failover_monitor.py

Steps 1-2 step the primary down and identify the new one. Step 3 reads from the
replica set once a second and logs which member answered; part-way through, a
second connection forces another stepDown so the reconnection can be observed.
Set READ_SECONDS to change how long step 3 reads (default 14).

Every MongoDB command this script runs, and the reply it gets back, is saved to
lab_8_2_failover_monitor.json next to this file.
"""

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import MongoClient, ReadPreference
from pymongo.errors import AutoReconnect, PyMongoError

TITLE = "Lab 8.2 - Observe Failover and Election"
RS_URI = "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0"
READ_SECONDS = int(os.environ.get("READ_SECONDS", "14"))
SECOND_STEPDOWN_AFTER = 5  # seconds into step 3
OUTPUT_FILE = Path(__file__).with_suffix(".json")

admin = MongoClient(RS_URI).admin
steps = []  # the execution log that ends up in the JSON file


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nMongoDB commands and replies saved to {OUTPUT_FILE.name}")


def log(message):
    print(f"  {datetime.now():%H:%M:%S}  {message}", flush=True)


def replica_status():
    # primaryPreferred, so this still answers while there is no primary
    return admin.command("replSetGetStatus", read_preference=ReadPreference.PRIMARY_PREFERRED)


def states(status):
    return {m["name"]: m["stateStr"] for m in status["members"]}


def primary_of(status):
    return next((m["name"] for m in status["members"] if m["stateStr"] == "PRIMARY"), None)


def one_line(status):
    return "  ".join(f"{name.split(':')[0]}={state}" for name, state in states(status).items())


def step_down(step):
    """Same as rs.stepDown(): the primary gives up its role for 60 seconds."""
    target = primary_of(replica_status())
    try:
        result = MongoClient(RS_URI).admin.command("replSetStepDown", 60)
    except AutoReconnect as error:  # some server versions drop the connection
        result = {"note": f"connection closed by the stepDown: {error}"}
    record(step, {"replSetStepDown": 60, "sentTo": target}, result)
    return target


print(TITLE.upper())

print("\nStep 1: rs.stepDown() on the primary, then watch the election")
log(f"before:  {one_line(replica_status())}")
old_primary = step_down("Step 1: rs.stepDown() on the primary")
log(f"replSetStepDown sent to {old_primary}")
watch = []
for _ in range(3):
    status = replica_status()
    log(f"watch:   {one_line(status)}")
    watch.append({"at": datetime.now(timezone.utc), "members": states(status)})
    time.sleep(1)
record("Step 1: member states during the election", {"replSetGetStatus": 1}, watch)

print("\nStep 2: rs.status() after the election - identify the new primary")
status = replica_status()
record("Step 2: rs.status() after the election", {"replSetGetStatus": 1}, status)
for m in status["members"]:
    print(f"  {m['name']:<21} {m['stateStr']:<10} health={int(m['health'])}")
print(f"  old primary: {old_primary}   ->   new primary: {primary_of(status)}")

print(f"\nStep 3: read once a second for {READ_SECONDS}s and log which member answers")
# A short selection timeout makes a "no primary" window visible in the log
# instead of the driver silently waiting 30 seconds.
reader = MongoClient(RS_URI, serverSelectionTimeoutMS=2000)
messages = reader["lab"]["messages"]


def force_second_stepdown():
    target = step_down("Step 3: second rs.stepDown(), sent from a separate connection")
    log(f">>> another connection sent replSetStepDown to {target} <<<")


threading.Timer(SECOND_STEPDOWN_AFTER, force_second_stepdown).start()

connected_to = None
reads = []
failures = 0
started = time.time()
while time.time() - started < READ_SECONDS:
    try:
        cursor = messages.find({}, {"_id": 0, "text": 1}).limit(1)
        doc = next(cursor, None)
        host, port = cursor.address  # the member that actually served this read
        member = f"{host}:{port}"
        if member != connected_to:
            log(f"*** connected to {member} (previously: {connected_to}) ***")
            connected_to = member
        log(f"read ok from {member}  {doc}")
        reads.append({"at": datetime.now(timezone.utc), "servedBy": member, "document": doc})
    except PyMongoError as error:
        failures += 1
        log(f"READ FAILED ({type(error).__name__}): {str(error)[:80]}")
        reads.append({"at": datetime.now(timezone.utc), "servedBy": None, "error": str(error)})
        connected_to = None
    time.sleep(1)

record(
    "Step 3: continuous reads through the replica-set connection",
    {"find": "messages", "$db": "lab", "projection": {"_id": 0, "text": 1}, "limit": 1, "uri": RS_URI},
    reads,
)
served_by = list(dict.fromkeys(r["servedBy"] for r in reads if r["servedBy"]))
print(f"  {len(reads) - failures} successful reads, {failures} failed reads; served by: {' -> '.join(served_by)}")

status = replica_status()
record("Final rs.status()", {"replSetGetStatus": 1}, status)
print(f"  final:   {one_line(status)}")

save_output()
