"""Activity 8.1 (Try it yourself) - Add an arbiter to the replica set.

Run these from this folder (the first one starts the arbiter container):
    docker compose --profile arbiter up -d
    docker compose run --rm python try_8_1_add_arbiter.py

Every MongoDB command this script runs, and the reply it gets back, is saved to
try_8_1_add_arbiter.json next to this file.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import OperationFailure

TITLE = "Activity 8.1 (Try it yourself) - Add an arbiter"
RS_URI = "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0"
ARBITER = "mongo-arbiter:27017"
OUTPUT_FILE = Path(__file__).with_suffix(".json")

admin = MongoClient(RS_URI).admin
steps = []  # the execution log that ends up in the JSON file


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nMongoDB commands and replies saved to {OUTPUT_FILE.name}")


def current_config():
    return admin.command("replSetGetConfig")["config"]


def voting_summary(config):
    voters = sum(m.get("votes", 1) for m in config["members"])
    majority = voters // 2 + 1
    return f"{voters} voting members, majority needed = {majority}, failures tolerated = {voters - majority}"


def add_arbiter(step):
    """Same as rs.addArb(): append an arbiterOnly member and reconfigure."""
    config = current_config()
    config["members"].append({
        "_id": max(m["_id"] for m in config["members"]) + 1,
        "host": ARBITER,
        "arbiterOnly": True,
    })
    config["version"] += 1
    try:
        result = admin.command("replSetReconfig", config)
    except OperationFailure as error:
        record(step, {"replSetReconfig": config}, error.details)
        raise
    record(step, {"replSetReconfig": config}, result)


print(TITLE.upper())
config = current_config()
record("Configuration before", {"replSetGetConfig": 1}, config)
print(f"\nBefore: {voting_summary(config)}")

if any(m.get("arbiterOnly") for m in config["members"]):
    print("\nThe replica set already has an arbiter.")
else:
    print(f"\nAdding {ARBITER} as an arbiter (same as rs.addArb) ...")
    try:
        add_arbiter("First attempt to add the arbiter")
    except OperationFailure as error:
        # MongoDB 5.0+ refuses the reconfig until the default write concern is explicit.
        print(f"  rejected: {error.details['errmsg']}")
        print("\nSetting an explicit cluster-wide default write concern, then retrying ...")
        command = {"setDefaultRWConcern": 1, "defaultWriteConcern": {"w": "majority"}}
        result = admin.command(command)
        record("Set the default write concern", command, result)
        print(f"  defaultWriteConcern = {result['defaultWriteConcern']}")
        add_arbiter("Second attempt to add the arbiter")
    print("  replSetReconfig -> ok")

print("\nWaiting for the new member to report ARBITER ...", end=" ")
for _ in range(60):
    status = admin.command("replSetGetStatus")
    if any(m["name"] == ARBITER and m["stateStr"] == "ARBITER" for m in status["members"]):
        break
    time.sleep(1)
print("done")
record("rs.status() after adding the arbiter", {"replSetGetStatus": 1}, status)

config = current_config()
record("Configuration after", {"replSetGetConfig": 1}, config)
by_host = {m["host"]: m for m in config["members"]}
print(f"\n  {'member':<21} {'state':<10} {'health':<7} {'arbiterOnly':<12} {'priority':<9} votes")
for m in status["members"]:
    c = by_host[m["name"]]
    print(f"  {m['name']:<21} {m['stateStr']:<10} {int(m['health']):<7} "
          f"{str(c['arbiterOnly']):<12} {c['priority']:<9} {c['votes']}")

print(f"\nAfter:  {voting_summary(config)}")

hello = MongoClient(f"mongodb://{ARBITER}/?directConnection=true").admin.command("hello")
record(f"hello on {ARBITER}", {"hello": 1, "directConnection": ARBITER}, hello)
print(f"\nThe arbiter itself reports: arbiterOnly={hello.get('arbiterOnly')}, "
      f"isWritablePrimary={hello['isWritablePrimary']}, secondary={hello['secondary']}")
print("It votes in elections, but it stores no data and can never become primary.")

save_output()
