"""Create and measure timestamp-range and hashed-user sharding through mongos."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bson import json_util
from bson.min_key import MinKey
from bson.max_key import MaxKey
from bson.int64 import Int64
from pymongo import MongoClient, ReplaceOne
from pymongo.errors import OperationFailure


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DATABASE = "chapter11"
TIMESTAMP_COLLECTION = "orders_by_timestamp"
HASHED_COLLECTION = "orders_by_hashed_user"
SHARD_IDS = ("c11-shard1-rs", "c11-shard2-rs")
DOCUMENTS = 10_000
BASE_TIME = datetime(2025, 1, 1)


def command_ok(result: dict[str, Any], label: str) -> None:
    if result.get("ok") != 1:
        raise RuntimeError(f"{label} failed: {result}")


def verify_router(client: MongoClient) -> dict[str, Any]:
    hello = client.admin.command("hello")
    if hello.get("msg") != "isdbgrid":
        raise RuntimeError("CHAPTER11_MONGO_URI must point to mongos; direct mongod connections are refused.")
    version = client.server_info()["version"]
    shards = client.admin.command("listShards").get("shards", [])
    names = {shard["_id"] for shard in shards}
    if not set(SHARD_IDS).issubset(names):
        raise RuntimeError(f"Expected isolated Chapter 11 shards {SHARD_IDS}; found {sorted(names)}")
    if tuple(sorted(names)) != tuple(sorted(SHARD_IDS)):
        raise RuntimeError(f"Unexpected shard membership in Chapter 11 cluster: {sorted(names)}")
    return {"version": version, "shards": shards, "router": hello.get("msg")}


def ensure_range_collection(client: MongoClient) -> None:
    db = client[DATABASE]
    name = TIMESTAMP_COLLECTION
    if name not in db.list_collection_names():
        db.create_collection(name)
    coll = db[name]
    coll.create_index([("event_at", 1)], name="event_at_shard_key")
    namespace = f"{DATABASE}.{name}"
    metadata = client.config.collections.find_one({"_id": namespace})
    if metadata is None:
        command_ok(client.admin.command({"shardCollection": namespace, "key": {"event_at": 1}}),
                   f"shardCollection {namespace}")
    elif metadata.get("key") != {"event_at": 1}:
        raise RuntimeError(f"Existing {namespace} has incompatible shard key {metadata.get('key')}; refusing to alter it.")

    # Split the time range into historical and recent ranges on the first run,
    # then place the recent range on shard 2. This gives the non-shard-key user
    # lookup a real multi-shard scatter target while later monotonic writes still
    # converge on the newest range. Actual document placement is measured later.
    split_at = BASE_TIME + timedelta(seconds=DOCUMENTS // 2)
    metadata = client.config.collections.find_one({"_id": namespace})
    chunks = list(client.config.chunks.find({"uuid": metadata["uuid"]}))
    if not any(chunk.get("min", {}).get("event_at") == split_at for chunk in chunks):
        command_ok(client.admin.command({"split": namespace, "middle": {"event_at": split_at}}),
                   f"split {namespace} at the midpoint timestamp")
        chunks = list(client.config.chunks.find({"uuid": metadata["uuid"]}))
    recent_chunks = [chunk for chunk in chunks
                     if chunk.get("min", {}).get("event_at", MinKey()) == split_at]
    if recent_chunks and recent_chunks[0].get("shard") != SHARD_IDS[1]:
        command_ok(client.admin.command({"moveChunk": namespace,
                                         "find": {"event_at": split_at},
                                         "to": SHARD_IDS[1],
                                         "_waitForDelete": True}),
                   f"move recent timestamp range to {SHARD_IDS[1]}")


def ensure_hashed_collection(client: MongoClient) -> None:
    db = client[DATABASE]
    name = HASHED_COLLECTION
    if name not in db.list_collection_names():
        db.create_collection(name)
    coll = db[name]
    coll.create_index([("user_id", "hashed")], name="user_id_hashed_shard_key")
    namespace = f"{DATABASE}.{name}"
    # Idempotently remove the abandoned two-zone layout from an interrupted
    # development run; these names are exclusively owned by this Chapter 11 lab.
    old_ranges = ((MinKey(), Int64(0), "chapter11-hash-low"),
                  (Int64(0), MaxKey(), "chapter11-hash-high"))
    for low, high, old_zone in old_ranges:
        client.admin.command({"updateZoneKeyRange": namespace,
                              "min": {"user_id": low}, "max": {"user_id": high}, "zone": None})
    for shard_id, old_zone in ((SHARD_IDS[0], "chapter11-hash-low"),
                               (SHARD_IDS[1], "chapter11-hash-high")):
        command_ok(client.admin.command({"removeShardFromZone": shard_id, "zone": old_zone}),
                   f"remove old zone {old_zone} from {shard_id}")
    metadata = client.config.collections.find_one({"_id": namespace})
    if metadata is not None:
        if metadata.get("key") != {"user_id": "hashed"}:
            raise RuntimeError(f"Existing {namespace} has incompatible shard key {metadata.get('key')}; refusing to alter it.")
        return

    # A hashed-prefix collection on MongoDB 7 presplits with one full-keyspace
    # zone. Both shards are eligible for that zone. Remove any ranges left by
    # an interrupted prior run of this lab before installing the single range.
    zone = "chapter11-hash-all"
    for shard_id in SHARD_IDS:
        command_ok(client.admin.command({"addShardToZone": shard_id, "zone": zone}),
                   f"assign zone {zone} to {shard_id}")
    command_ok(client.admin.command({"updateZoneKeyRange": namespace,
                                     "min": {"user_id": MinKey()}, "max": {"user_id": MaxKey()},
                                     "zone": zone}), "define full hashed zone range")
    command_ok(client.admin.command({"shardCollection": namespace,
                                     "key": {"user_id": "hashed"},
                                     "presplitHashedZones": True}),
               f"shardCollection {namespace}")


def dataset() -> list[dict[str, Any]]:
    rows = []
    for number in range(DOCUMENTS):
        rows.append({
            "_id": f"lab11-{number:05d}",
            "order_id": f"order-{number:05d}",
            "user_id": f"user-{number:05d}",
            "event_at": BASE_TIME + timedelta(seconds=number),
            "amount": number % 1000,
            "experiment": "chapter11-sharding",
        })
    return rows


def upsert_rows(coll, rows: list[dict[str, Any]]) -> float:
    start = time.perf_counter()
    for offset in range(0, len(rows), 1000):
        batch = rows[offset:offset + 1000]
        shard_field = "event_at" if coll.name == TIMESTAMP_COLLECTION else "user_id"
        operations = [ReplaceOne({"_id": row["_id"], shard_field: row[shard_field]}, row, upsert=True)
                      for row in batch]
        coll.bulk_write(operations, ordered=False)
    return round(time.perf_counter() - start, 3)


def shard_counts(client: MongoClient, coll) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    count_stats = list(coll.aggregate([{"$collStats": {"count": {}, "storageStats": {}}}]))
    distribution_docs = list(client.admin.aggregate([
        {"$shardedDataDistribution": {}}, {"$match": {"ns": f"{DATABASE}.{coll.name}"}}
    ]))
    owned = {}
    if distribution_docs:
        owned = {item["shardName"]: item.get("numOwnedDocuments", 0)
                 for item in distribution_docs[0].get("shards", [])}
    result = []
    for item in count_stats:
        storage = item.get("storageStats", {})
        shard = item.get("shard", "unknown")
        result.append({
            "shard": shard,
            "owned_documents": int(owned.get(shard, item.get("count", storage.get("count", 0)))),
            "collstats_count": int(item.get("count", storage.get("count", 0))),
            "storage_size_bytes": int(storage.get("storageSize", 0)),
            "logical_size_bytes": int(storage.get("size", 0)),
            "total_index_size_bytes": int(storage.get("totalIndexSize", 0)),
            "host": item.get("host"),
        })
    return result, distribution_docs


def targeted_shards(explain: dict[str, Any], known_shards: set[str]) -> list[str]:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"shardName", "shard"} and isinstance(item, str) and item in known_shards:
                    found.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(explain.get("queryPlanner", {}).get("winningPlan", {}))
    return sorted(found)


def explain_query(client: MongoClient, coll, label: str, query: dict[str, Any], known_shards: set[str]) -> dict[str, Any]:
    result = coll.database.command({"explain": {"find": coll.name, "filter": query},
                                    "verbosity": "executionStats"})
    targets = targeted_shards(result, known_shards)
    return {
        "label": label,
        "filter": json.loads(json_util.dumps(query)),
        "targeted_shards": targets,
        "targeted_shards_known": bool(targets),
        "scatter_gather": len(targets) > 1 if targets else None,
        "execution_millis": result.get("executionStats", {}).get("executionTimeMillis"),
        "n_returned": result.get("executionStats", {}).get("nReturned"),
        "docs_examined": result.get("executionStats", {}).get("totalDocsExamined"),
        "winning_plan": result.get("queryPlanner", {}).get("winningPlan"),
        "raw_explain": result,
    }


def main() -> None:
    uri = os.environ.get("CHAPTER11_MONGO_URI", "mongodb://localhost:37317")
    REPORTS.mkdir(exist_ok=True)
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="chapter11-lab-runner")
    try:
        cluster = verify_router(client)
        ensure_range_collection(client)
        ensure_hashed_collection(client)
        rows = dataset()
        db = client[DATABASE]
        write_seconds = {
            TIMESTAMP_COLLECTION: upsert_rows(db[TIMESTAMP_COLLECTION], rows),
            HASHED_COLLECTION: upsert_rows(db[HASHED_COLLECTION], rows),
        }

        total_counts = {name: db[name].count_documents({}) for name in (TIMESTAMP_COLLECTION, HASHED_COLLECTION)}
        if total_counts != {TIMESTAMP_COLLECTION: DOCUMENTS, HASHED_COLLECTION: DOCUMENTS}:
            raise RuntimeError(f"Expected {DOCUMENTS} rows in each lab collection; got {total_counts}.")

        shard_names = {row["_id"] for row in cluster["shards"]}
        recent_user = "user-09999"
        date_start = BASE_TIME + timedelta(seconds=DOCUMENTS - 100)
        date_end = BASE_TIME + timedelta(seconds=DOCUMENTS)
        queries = {
            TIMESTAMP_COLLECTION: [
                explain_query(client, db[TIMESTAMP_COLLECTION], "exact user lookup (no timestamp shard key)",
                              {"user_id": recent_user}, shard_names),
                explain_query(client, db[TIMESTAMP_COLLECTION], "recent time range (timestamp shard key)",
                              {"event_at": {"$gte": date_start, "$lt": date_end}}, shard_names),
            ],
            HASHED_COLLECTION: [
                explain_query(client, db[HASHED_COLLECTION], "exact user lookup (hashed shard key)",
                              {"user_id": recent_user}, shard_names),
                explain_query(client, db[HASHED_COLLECTION], "recent time range (not a shard key)",
                              {"event_at": {"$gte": date_start, "$lt": date_end}}, shard_names),
            ],
        }
        collection_results = {}
        for name in (TIMESTAMP_COLLECTION, HASHED_COLLECTION):
            coll = db[name]
            per_shard, distribution = shard_counts(client, coll)
            collection_results[name] = {
                "shard_key": client.config.collections.find_one({"_id": f"{DATABASE}.{name}"}).get("key"),
                "total_documents": total_counts[name],
                "distinct_users": len(coll.distinct("user_id")),
                "distinct_event_timestamps": len(coll.distinct("event_at")),
                "write_seconds": write_seconds[name],
                "per_shard": per_shard,
                "distribution_details": distribution,
                "queries": queries[name],
            }

        result = {
            "experiment": "Lab 11.2 — Shard Key Hotspot Detection",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "mongo_uri_target": "mongos router (secret-free; URI value omitted)",
            "mongodb_version": cluster["version"],
            "shards": [{"name": row["_id"], "host": row["host"]} for row in cluster["shards"]],
            "requested_documents_per_collection": DOCUMENTS,
            "collections": collection_results,
            "interpretation": {
                "timestamp_writes": "Range-based monotonically increasing timestamps route the newest inserts to the chunk that owns the latest time range. Actual owning shard and counts are measured below.",
                "hashed_user_writes": "Hashed customer/user keys distribute distinct user values across hash ranges. Actual chunk ownership and counts are measured below.",
                "measurement_note": "Counts and plans are read from the running cluster. Distribution is not asserted to a predetermined ratio.",
            },
        }
        json_path = REPORTS / "lab11_2_measurements.json"
        json_path.write_text(json_util.dumps(result, indent=2) + "\n", encoding="utf-8")
        markdown_path = REPORTS / "lab11_2_measurements.md"
        lines = [
            "# Lab 11.2 — Shard Key Hotspot Detection",
            "",
            f"- Captured at (UTC): `{result['captured_at_utc']}`",
            f"- MongoDB version: `{cluster['version']}`",
            f"- Router: `mongos` at the configured Chapter 11 endpoint",
            f"- Shards: {', '.join(row['_id'] for row in cluster['shards'])}",
            f"- Requested insert volume: {DOCUMENTS:,} documents per collection",
            "- Distribution is measured from MongoDB; no target ratio is assumed.",
            "",
        ]
        for name, details in collection_results.items():
            lines += [f"## `{name}`", "", f"Shard key: `{details['shard_key']}`",
                      f"Stored documents: **{details['total_documents']:,}** · distinct users: **{details['distinct_users']:,}** · distinct timestamps: **{details['distinct_event_timestamps']:,}**",
                      f"Insert/upsert elapsed time: **{details['write_seconds']} s**", "",
                      "### Actual per-shard counts and storage", "",
                      "| Shard | Owned documents | $collStats count | Storage bytes | Logical bytes | Index bytes |",
                      "|---|---:|---:|---:|---:|---:|"]
            for shard in details["per_shard"]:
                lines.append(f"| {shard['shard']} | {shard['owned_documents']} | {shard['collstats_count']} | {shard['storage_size_bytes']} | {shard['logical_size_bytes']} | {shard['total_index_size_bytes']} |")
            lines += ["", "### Query plans", "", "| Query | Targeted shards | Scatter/gather | Returned | Examined | Time ms |",
                      "|---|---|---|---:|---:|---:|"]
            for query_result in details["queries"]:
                target = ", ".join(query_result["targeted_shards"]) if query_result["targeted_shards_known"] else "not reported by explain"
                scatter = "yes" if query_result["scatter_gather"] is True else "no" if query_result["scatter_gather"] is False else "unknown"
                lines.append(f"| {query_result['label']} | {target} | {scatter} | {query_result['n_returned']} | {query_result['docs_examined']} | {query_result['execution_millis']} |")
            lines.append("")
        lines += ["## Interpretation", "",
                  "An exact equality query on the hashed user shard key is targetable to one shard. A user query against the timestamp-sharded collection does not include its shard key and is broadcast. The timestamp range query is routed by its time range; the hashed collection cannot use its hash key to target that date range and scatters it.",
                  "",
                  "Increasing timestamps put the active insert edge on one range/chunk at a time. Hashed user values avoid a monotonic insert edge when there are many distinct users, though a single hot user remains one shard-key value. The raw explain plans and per-shard distribution records are in the adjacent JSON report.", ""]
        markdown_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote actual cluster measurements: {markdown_path}")
        print(f"Wrote raw statistics and explain output: {json_path}")
        print(f"MongoDB {cluster['version']} through mongos; counts={total_counts}")
        for name, details in collection_results.items():
            print(name, "per-shard:", [(row["shard"], row["owned_documents"]) for row in details["per_shard"]])
            for query in details["queries"]:
                print(" ", query["label"], "targets=", query["targeted_shards"], "scatter=", query["scatter_gather"])
    finally:
        client.close()


if __name__ == "__main__":
    main()
