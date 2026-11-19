"""Live, read-only status for the isolated synthetic transaction shard comparison."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter
from pymongo import MongoClient

router = APIRouter(prefix="/api/chapter11", tags=["Synthetic shard comparison"])
DATABASE = "chapter11"
COLLECTIONS = {
    "transactions_by_order_id": {"order_id": 1},
    "transactions_by_customer_id": {"customer_id": 1},
    "transactions_by_order_date": {"order_date": 1},
    "transactions_by_hashed_customer_id": {"customer_id": "hashed"},
}
SOURCE = "synthetic-demo-v1"


def _targets(value: Any, shards: set[str]) -> list[str]:
    found: set[str] = set()
    def walk(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"shardName", "shard"} and item in shards if isinstance(item, str) else False:
                    found.add(item)
                walk(item)
        elif isinstance(node, list):
            for item in node: walk(item)
    walk(value)
    return sorted(found)


@router.get("/demo-transactions")
def demo_transaction_shard_status() -> dict:
    uri = os.getenv("CHAPTER11_MONGO_URI", "mongodb://host.docker.internal:37317").strip()
    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2200, connectTimeoutMS=1800,
                             appname="aegis-demo-transaction-shards")
        hello = client.admin.command("hello")
        if hello.get("msg") != "isdbgrid": raise RuntimeError("Chapter 11 endpoint is not mongos.")
        shards = {item["_id"] for item in client.admin.command("listShards").get("shards", [])}
        db = client[DATABASE]
        result = {"available": True, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                  "mongodb_version": client.server_info()["version"], "shards": sorted(shards),
                  "requested_documents_per_collection": 1400, "source": SOURCE, "collections": {}}
        for name, expected_key in COLLECTIONS.items():
            namespace = f"{DATABASE}.{name}"
            metadata = client.config.collections.find_one({"_id": namespace})
            if not metadata:
                result["collections"][name] = {"ready": False, "total_documents": 0,
                    "expected_shard_key": expected_key, "per_shard": [], "queries": {}}
                continue
            coll = db[name]
            owned = {}
            for doc in client.admin.aggregate([{"$shardedDataDistribution": {}}, {"$match": {"ns": namespace}}]):
                owned = {row["shardName"]: row.get("numOwnedDocuments", 0) for row in doc.get("shards", [])}
            collstats = list(coll.aggregate([{"$collStats": {"count": {}, "storageStats": {}}}]))
            per_shard = [{"shard": row.get("shard"), "owned_documents": owned.get(row.get("shard"), row.get("count", 0)),
                          "storage_size_bytes": row.get("storageStats", {}).get("storageSize", 0),
                          "logical_size_bytes": row.get("storageStats", {}).get("size", 0)} for row in collstats]
            query_specs = {"customer_equality": {"customer_id": "DEMO-CUSTOMER-037"},
                           "date_range": {"order_date": {"$gte": datetime(2025, 1, 2), "$lt": datetime(2025, 1, 10)}}}
            query_results = {}
            for label, query in query_specs.items():
                plan = coll.database.command({"explain": {"find": name, "filter": query}, "verbosity": "executionStats"})
                targets = _targets(plan.get("queryPlanner", {}).get("winningPlan", {}), shards)
                query_results[label] = {"targeted_shards": targets, "targeted_shards_known": bool(targets),
                    "scatter_gather": len(targets) > 1 if targets else None,
                    "execution_millis": plan.get("executionStats", {}).get("executionTimeMillis"),
                    "n_returned": plan.get("executionStats", {}).get("nReturned"),
                    "docs_examined": plan.get("executionStats", {}).get("totalDocsExamined")}
            count = coll.count_documents({"source": SOURCE, "is_synthetic": True})
            result["collections"][name] = {"ready": metadata.get("key") == expected_key and count == 1400,
                "shard_key": metadata.get("key"), "total_documents": count, "per_shard": per_shard,
                "queries": query_results}
        result["ready"] = all(item.get("ready") for item in result["collections"].values())
        return result
    except Exception as error:
        return {"available": False, "ready": False, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "message": "Chapter 11 synthetic transaction comparison is temporarily unavailable.",
                "detail": str(error)[:240], "collections": {}}
    finally:
        if client is not None: client.close()
