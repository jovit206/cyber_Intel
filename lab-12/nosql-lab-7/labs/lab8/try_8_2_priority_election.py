"""Activity 8.2 (Try it yourself) - Set priorities, predict the next primary, verify.

Run this from this folder:
    docker compose run --rm python try_8_2_priority_election.py

Every MongoDB command this script runs, and the reply it gets back, is saved to
try_8_2_priority_election.json next to this file.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import MongoClient, ReadPreference
from pymongo.errors import AutoReconnect, PyMongoError

TITLE = "Activity 8.2 (Try it yourself) - Priorities and prediction"
RS_URI = "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0"
PRIORITIES = {"mongo1:27017": 3, "mongo2:27017": 2, "mongo3:27017": 1}
OUTPUT_FILE = Path(__file__).with_suffix(".json")

admin = MongoClient(RS_URI).admin
steps = []  # the execution log that ends up in the JSON file


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nMongoDB commands and replies saved to {OUTPUT_FILE.name}")


def members():
    """Current members as {name: {"state": ..., "priority": ..., "arbiter": ...}}."""
    # primaryPreferred, so this still answers during an election
    pref = ReadPreference.PRIMARY_PREFERRED
    config = admin.command("replSetGetConfig", read_preference=pref)["config"]["members"]
    status = admin.command("replSetGetStatus", read_preference=pref)["members"]
    by_host = {m["host"]: m for m in config}
    return {
        m["name"]: {
            "state": m["stateStr"],
            "health": m["health"],
            "priority": by_host[m["name"]]["priority"],
            "arbiter": by_host[m["name"]]["arbiterOnly"],
        }
        for m in status
    }


def primary_of(current):
    return next((name for name, m in current.items() if m["state"] == "PRIMARY"), None)


def one_line(current):
    return "  ".join(f"{name.split(':')[0]}={m['state']}({m['priority']:g})" for name, m in current.items())


def wait_for_primary(step, accept, timeout, show=0):
    """Poll until accept(primary) is true; print the first `show` snapshots."""
    snapshots = []
    started = time.time()
    while time.time() - started < timeout:
        current = members()
        snapshots.append({"at": datetime.now(timezone.utc), "members": current})
        if show > 0:
            print(f"   {datetime.now():%H:%M:%S}  {one_line(current)}")
            show -= 1
        if accept(primary_of(current)) and show <= 0:
            # keep the JSON readable: the first three polls and the one that matched
            record(step, {"replSetGetStatus": 1, "polls": len(snapshots)}, snapshots[:3] + snapshots[3:][-1:])
            return primary_of(current), time.time() - started
        time.sleep(1)
    raise TimeoutError("no matching primary within the timeout")


print(TITLE.upper())
print("Members are shown as name=STATE(priority).")

print("\n1. Make every member eligible (replSetFreeze 0 clears a leftover stepDown freeze)")
for host in PRIORITIES:
    try:
        result = MongoClient(f"mongodb://{host}/?directConnection=true").admin.command("replSetFreeze", 0)
    except PyMongoError as error:
        result = {"skipped": str(error)}  # the primary refuses replSetFreeze; it is not frozen anyway
    record(f"1. Clear any election freeze on {host}", {"replSetFreeze": 0, "directConnection": host}, result)
current = members()
record("1. Members before", {"replSetGetStatus": 1}, current)
print(f"   before: {one_line(current)}")

print("\n2. Set priorities with replSetReconfig: " + ", ".join(f"{h.split(':')[0]}={p}" for h, p in PRIORITIES.items()))
config = admin.command("replSetGetConfig")["config"]
for member in config["members"]:
    if member["host"] in PRIORITIES:
        member["priority"] = PRIORITIES[member["host"]]
config["version"] += 1
record("2. Set the priorities", {"replSetReconfig": config}, admin.command("replSetReconfig", config))

highest = max(PRIORITIES, key=PRIORITIES.get)
print(f"   waiting for the highest-priority member ({highest}) to take over ...")
_, seconds = wait_for_primary("2. Priority takeover", lambda p: p == highest, timeout=90)
print(f"   {highest} is PRIMARY after {seconds:.0f}s, without any stepDown (priority takeover)")
time.sleep(5)  # let the secondaries catch up before the experiment
current = members()
print(f"   now:    {one_line(current)}")

print("\n3. PREDICTION (made before running stepDown)")
old_primary = primary_of(current)
candidates = {n: m["priority"] for n, m in current.items() if n != old_primary and not m["arbiter"]}
predicted = max(candidates, key=candidates.get)
record(
    "3. Prediction, made before stepDown",
    None,
    {"currentPrimary": old_primary, "candidates": candidates, "predictedNewPrimary": predicted,
     "reason": "highest priority among the members that can still be elected"},
)
print(f"   current primary: {old_primary} (priority {current[old_primary]['priority']:g}); it cannot stand again for 60s")
print("   candidates:      " + ", ".join(f"{n} (priority {p:g})" for n, p in candidates.items())
      + "; an arbiter votes but cannot be elected")
print(f"   predicted new primary: {predicted}, the highest-priority candidate")

print(f"\n4. rs.stepDown() on {old_primary}")
try:
    result = admin.command("replSetStepDown", 60)
except AutoReconnect as error:  # some server versions drop the connection
    result = {"note": f"connection closed by the stepDown: {error}"}
record("4. rs.stepDown() on the primary", {"replSetStepDown": 60, "sentTo": old_primary}, result)
elected, _ = wait_for_primary("4. Election after stepDown", lambda p: p not in (None, old_primary), timeout=30, show=3)

print("\n5. VERIFY")
verdict = "CORRECT" if elected == predicted else "WRONG"
record("5. Verify the prediction", None, {"predicted": predicted, "elected": elected, "prediction": verdict})
print(f"   elected: {elected}    predicted: {predicted}    ->  prediction {verdict}")

print(f"\n6. Follow-up: does {old_primary} take the role back when its 60s freeze ends?")
_, seconds = wait_for_primary("6. Old primary takes the role back", lambda p: p == old_primary, timeout=120)
print(f"   yes, {old_primary} is PRIMARY again {seconds:.0f}s after the election")
print(f"   final:  {one_line(members())}")

record("Final rs.status()", {"replSetGetStatus": 1}, admin.command("replSetGetStatus"))
save_output()
