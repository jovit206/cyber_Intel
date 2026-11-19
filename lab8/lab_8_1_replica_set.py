"""Lab 8.1 - Deploy a local replica set and verify replication.

Run these from this folder:
    docker compose up -d
    docker compose run --rm python lab_8_1_replica_set.py

Every MongoDB command this script runs, and the reply it gets back, is saved to
lab_8_1_replica_set.json next to this file.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from pymongo.write_concern import WriteConcern

TITLE = "Lab 8.1 - Deploy a Local Replica Set"
RS_NAME = "rs0"
MEMBERS = ["mongo1:27017", "mongo2:27017", "mongo3:27017"]
RS_URI = f"mongodb://{','.join(MEMBERS)}/?replicaSet={RS_NAME}"
ALREADY_INITIALIZED = 23
OUTPUT_FILE = Path(__file__).with_suffix(".json")

steps = []  # the execution log that ends up in the JSON file


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nMongoDB commands and replies saved to {OUTPUT_FILE.name}")


# One direct connection per member, so each one can be asked individually.
direct = {
    host: MongoClient(f"mongodb://{host}/?directConnection=true", serverSelectionTimeoutMS=5000)
    for host in MEMBERS
}


def replica_status():
    return direct[MEMBERS[0]].admin.command("replSetGetStatus")


def wait_until_ready(timeout=90):
    """Wait until the set has one PRIMARY and every other member is SECONDARY."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        states = [m["stateStr"] for m in replica_status()["members"]]
        if states.count("PRIMARY") == 1 and states.count("SECONDARY") == len(MEMBERS) - 1:
            return
        time.sleep(1)
    raise TimeoutError("replica set did not become ready")


print(TITLE.upper())

print("\nStep 2: initialise the replica set")
config = {"_id": RS_NAME, "members": [{"_id": i, "host": h} for i, h in enumerate(MEMBERS)]}
try:
    result = direct[MEMBERS[0]].admin.command("replSetInitiate", config)
    print(f"  replSetInitiate -> ok: {result['ok']}")
except OperationFailure as error:
    if error.code != ALREADY_INITIALIZED:
        raise
    result = error.details
    print("  replica set is already initialised")
record("Step 2: initialise the replica set", {"replSetInitiate": config}, result)
print("  waiting for the election ...", end=" ")
wait_until_ready()
print("done")

print("\nStep 3: insert a document into the primary, then read it on every member")
client = MongoClient(RS_URI)  # replica-set connection: writes go to the primary
messages = client["lab"].get_collection("messages", write_concern=WriteConcern(w=3, wtimeout=30000))
document = {"text": "hello from the primary", "lab": "8.1", "insertedAt": datetime.now(timezone.utc)}
inserted_id = messages.insert_one(document).inserted_id
primary = f"{client.primary[0]}:{client.primary[1]}"
print(f"  inserted _id={inserted_id} through primary {primary}")
record(
    "Step 3a: insert a document into the primary",
    {"insert": "messages", "$db": "lab", "documents": [document], "writeConcern": {"w": 3, "wtimeout": 30000}},
    {"acknowledged": True, "insertedId": inserted_id, "primary": primary},
)

found_on_secondaries = []
for host in MEMBERS:
    role = "PRIMARY" if direct[host].admin.command("hello")["isWritablePrimary"] else "SECONDARY"
    found = list(direct[host]["lab"]["messages"].find({"_id": inserted_id}))
    print(f"  {host:<14} {role:<10} text={found[0]['text']!r}" if found else f"  {host:<14} {role:<10} NOT FOUND")
    record(
        f"Step 3b: read the document directly on {host} ({role})",
        {"find": "messages", "$db": "lab", "filter": {"_id": inserted_id}, "directConnection": host},
        found,
    )
    if role == "SECONDARY":
        found_on_secondaries.append(bool(found))
print(f"  document present on both secondaries: {found_on_secondaries == [True, True]}")

print("\nStep 4: rs.status() - primary, secondaries and health")
status = replica_status()
record("Step 4: rs.status()", {"replSetGetStatus": 1}, status)
members = status["members"]
print(f"  {'member':<14} {'state':<10} {'health':<7} uptime")
for m in members:
    print(f"  {m['name']:<14} {m['stateStr']:<10} {int(m['health']):<7} {m['uptime']}s")
print(f"  primary:     {', '.join(m['name'] for m in members if m['stateStr'] == 'PRIMARY')}")
print(f"  secondaries: {', '.join(m['name'] for m in members if m['stateStr'] == 'SECONDARY')}")
print(f"  all members healthy (health = 1): {all(m['health'] == 1 for m in members)}")

save_output()
