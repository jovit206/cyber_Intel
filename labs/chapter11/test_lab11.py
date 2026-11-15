"""Automated Chapter 11 topology, sharding, count, and query-plan checks."""

from __future__ import annotations

import os
from pathlib import Path

from pymongo import MongoClient

from lab11_2_hotspot import (DATABASE, DOCUMENTS, HASHED_COLLECTION,
                             SHARD_IDS, TIMESTAMP_COLLECTION, targeted_shards)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main() -> None:
    report_path = Path(__file__).resolve().parent / "reports" / "lab11_2_measurements.json"
    require(report_path.is_file(), "Lab 11.2 actual-results report exists")
    uri = os.environ.get("CHAPTER11_MONGO_URI", "mongodb://localhost:37317")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="chapter11-lab-checks")
    try:
        hello = client.admin.command("hello")
        require(hello.get("msg") == "isdbgrid", "Python test client is connected through mongos")
        version = tuple(int(part) for part in client.server_info()["version"].split(".")[:2])
        require(version >= (5, 0), f"MongoDB version {client.server_info()['version']} supports this lab")

        shards = client.admin.command("listShards").get("shards", [])
        shard_ids = {item["_id"] for item in shards}
        require(set(SHARD_IDS).issubset(shard_ids), "At least two isolated Chapter 11 shards are registered")

        report = __import__("json").loads(report_path.read_text(encoding="utf-8"))
        collections = report.get("collections", {})
        for name, expected_key in ((TIMESTAMP_COLLECTION, {"event_at": 1}),
                                   (HASHED_COLLECTION, {"user_id": "hashed"})):
            namespace = f"{DATABASE}.{name}"
            metadata = client.config.collections.find_one({"_id": namespace})
            require(metadata is not None, f"{namespace} is sharded")
            require(dict(metadata.get("key", {})) == expected_key,
                    f"{namespace} has shard key {expected_key}")
            actual = client[DATABASE][name].count_documents({})
            require(actual == DOCUMENTS, f"{namespace} contains {actual} of {DOCUMENTS} requested documents")
            measured = collections.get(name, {}).get("per_shard", [])
            require(sum(int(item.get("owned_documents", 0)) for item in measured) == DOCUMENTS,
                    f"{namespace} per-shard ownership counts sum to {DOCUMENTS}")
            measured_shards = {item["shard"] for item in measured if int(item.get("owned_documents", 0)) > 0}
            require(measured_shards.issubset(shard_ids), f"{namespace} distribution names only registered shards")

        timestamp_queries = collections[TIMESTAMP_COLLECTION]["queries"]
        hashed_queries = collections[HASHED_COLLECTION]["queries"]
        timestamp_user = timestamp_queries[0]
        hashed_user = hashed_queries[0]
        timestamp_targets = targeted_shards(timestamp_user["raw_explain"], shard_ids)
        hashed_targets = targeted_shards(hashed_user["raw_explain"], shard_ids)
        require(len(timestamp_targets) >= 2,
                f"Timestamp-key user lookup demonstrates scatter/gather across {len(timestamp_targets)} shards")
        require(len(hashed_targets) == 1,
                f"Hashed user equality lookup targets one shard ({', '.join(hashed_targets)})")
        require(timestamp_queries[1]["targeted_shards_known"], "Timestamp range explain reports shard targets")
        require(hashed_queries[1]["targeted_shards_known"], "Hashed collection date-range explain reports shard targets")
        print(f"MongoDB {client.server_info()['version']}; measured distribution is in {report_path}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
