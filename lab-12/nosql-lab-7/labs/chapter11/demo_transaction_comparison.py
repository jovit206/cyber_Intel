"""Experiment 11.1: evaluate four keys using the persisted application dataset.

Reads chapter07.demo_financial_activity, then writes only four dedicated Chapter 11.1
collections through mongos. Experiment 11.2 uses separate 10,000-row fixtures.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import json_util
from pymongo import MongoClient, ReplaceOne
from pymongo.errors import BulkWriteError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.services.demo_transactions import COLLECTION as SOURCE_COLLECTION, RECORD_COUNT, SOURCE

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DETAIL_JSON = REPORTS / "demo_transaction_sharding.json"
DETAIL_MD = REPORTS / "demo_transaction_sharding.md"
DECISION_JSON = REPORTS / "lab11_1_decision_matrix.json"
DECISION_MD = REPORTS / "lab11_1_decision_matrix.md"
DATABASE = "chapter11"
EXPECTED_SHARDS = {"c11-shard1-rs", "c11-shard2-rs"}
LAYOUTS = {
    "transactions_by_order_id": ("order_id", 1),
    "transactions_by_customer_id": ("customer_id", 1),
    "transactions_by_order_date": ("order_date", 1),
    "transactions_by_hashed_customer_id": ("customer_id", "hashed"),
}
QUERY_WEIGHTS = {
    "customer_id_equality": 0.45,
    "order_id_equality": 0.30,
    "date_range": 0.15,
    "customer_id_range": 0.10,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_source() -> tuple[MongoClient, list[dict[str, Any]]]:
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000,
                         appname="chapter11-1-transaction-source")
    database = client[settings.mongo_database]
    rows = list(database[SOURCE_COLLECTION].find(
        {"source": SOURCE, "is_synthetic": True},
        {"_id": 1, "transaction_id": 1, "order_id": 1, "customer_id": 1,
         "order_date": 1, "created_at": 1, "source": 1, "is_synthetic": 1},
    ).sort("order_id", 1))
    require(len(rows) == RECORD_COUNT,
            f"11.1 requires exactly {RECORD_COUNT} source rows; found {len(rows)}. No Chapter 11 collections were written.")
    require(len({row.get("transaction_id") for row in rows}) == RECORD_COUNT,
            "The source dataset has duplicate or missing transaction IDs. No Chapter 11 collections were written.")
    for field in ("order_id", "customer_id", "order_date"):
        require(all(row.get(field) is not None for row in rows),
                f"Source dataset is missing {field}; no Chapter 11 collections were written.")
    return client, rows


def verify_router(client: MongoClient) -> list[dict[str, Any]]:
    require(client.admin.command("hello").get("msg") == "isdbgrid",
            "Chapter 11 access must use mongos; refusing a direct mongod connection.")
    shards = client.admin.command("listShards").get("shards", [])
    require({item["_id"] for item in shards} == EXPECTED_SHARDS,
            f"Unexpected Chapter 11 shard membership: {[item['_id'] for item in shards]}")
    version = tuple(int(part) for part in client.server_info()["version"].split(".")[:2])
    require(version >= (5, 0), "MongoDB 5.0+ is required for this experiment.")
    return shards


def ensure_collection(client: MongoClient, name: str, field: str, kind: Any) -> None:
    db = client[DATABASE]
    namespace = f"{DATABASE}.{name}"
    if name not in db.list_collection_names():
        db.create_collection(name)
    index_key = [(field, kind)]
    existing_indexes = db[name].index_information().values()
    if not any(list(index.get("key", [])) == index_key for index in existing_indexes):
        db[name].create_index(index_key, name="transaction_evaluation_shard_key")
    metadata = client.config.collections.find_one({"_id": namespace})
    expected = {field: kind}
    if metadata:
        require(metadata.get("key") == expected,
                f"{namespace} has shard key {metadata.get('key')}, expected {expected}; refusing to alter it.")
    else:
        client.admin.command({"shardCollection": namespace, "key": expected})


def ensure_range_split(client: MongoClient, name: str, field: str, middle: Any) -> None:
    namespace = f"{DATABASE}.{name}"
    metadata = client.config.collections.find_one({"_id": namespace})
    chunks = list(client.config.chunks.find({"uuid": metadata["uuid"]}))
    if not any(chunk.get("min", {}).get(field) == middle for chunk in chunks):
        client.admin.command({"split": namespace, "middle": {field: middle}})
        chunks = list(client.config.chunks.find({"uuid": metadata["uuid"]}))
    upper = next((chunk for chunk in chunks if chunk.get("min", {}).get(field) == middle), None)
    if upper and upper.get("shard") != "c11-shard2-rs":
        client.admin.command({"moveChunk": namespace, "find": {field: middle},
                              "to": "c11-shard2-rs", "_waitForDelete": True})


def targets_in_plan(plan: dict[str, Any], shard_names: set[str]) -> list[str]:
    found: set[str] = set()
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"shardName", "shard"} and isinstance(item, str) and item in shard_names:
                    found.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(plan.get("queryPlanner", {}).get("winningPlan", {}))
    return sorted(found)


def live_stats(client: MongoClient, coll, shard_names: set[str]) -> list[dict[str, Any]]:
    owned: dict[str, int] = {}
    for entry in client.admin.aggregate([
        {"$shardedDataDistribution": {}},
        {"$match": {"ns": f"{DATABASE}.{coll.name}"}},
    ]):
        owned = {row["shardName"]: int(row.get("numOwnedDocuments", 0))
                 for row in entry.get("shards", [])}
    stats = list(coll.aggregate([{"$collStats": {"count": {}, "storageStats": {}}}]))
    return [{
        "shard": item.get("shard"),
        "owned_documents": owned.get(item.get("shard"), int(item.get("count", 0))),
        "storage_size_bytes": int(item.get("storageStats", {}).get("storageSize", 0)),
        "logical_size_bytes": int(item.get("storageStats", {}).get("size", 0)),
        "index_size_bytes": int(item.get("storageStats", {}).get("totalIndexSize", 0)),
    } for item in stats if item.get("shard") in shard_names]


def explain_queries(client: MongoClient, coll, queries: dict[str, dict[str, Any]],
                    shard_names: set[str]) -> dict[str, dict[str, Any]]:
    results = {}
    for label, query in queries.items():
        explain = coll.database.command({"explain": {"find": coll.name, "filter": query},
                                         "verbosity": "executionStats"})
        target_shards = targets_in_plan(explain, shard_names)
        results[label] = {
            "filter": json.loads(json_util.dumps(query)),
            "targeted_shards": target_shards,
            "targeted_shards_known": bool(target_shards),
            "scatter_gather": len(target_shards) > 1 if target_shards else None,
            "n_returned": explain.get("executionStats", {}).get("nReturned"),
            "docs_examined": explain.get("executionStats", {}).get("totalDocsExamined"),
            "execution_millis": explain.get("executionStats", {}).get("executionTimeMillis"),
            "raw_explain": explain,
        }
    return results


def _date_for_mongo(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def main() -> None:
    source_client = None
    shard_client = None
    REPORTS.mkdir(exist_ok=True)
    try:
        source_client, rows = read_source()
        shard_uri = os.getenv("CHAPTER11_MONGO_URI", "mongodb://localhost:37317").strip()
        shard_client = MongoClient(shard_uri, serverSelectionTimeoutMS=5000,
                                   appname="chapter11-1-shard-key-evaluation")
        shards = verify_router(shard_client)
        shard_names = {item["_id"] for item in shards}
        db = shard_client[DATABASE]
        sorted_dates = sorted(row["order_date"] for row in rows)
        unique_customers = sorted({row["customer_id"] for row in rows})
        customer_counts = Counter(row["customer_id"] for row in rows)
        most_common_customer = customer_counts.most_common(1)[0][0]
        order_sample = rows[len(rows) // 2]["order_id"]
        date_start = sorted_dates[len(sorted_dates) // 4]
        date_end = sorted_dates[(len(sorted_dates) * 3) // 4]
        customer_start = unique_customers[len(unique_customers) // 4]
        customer_end = unique_customers[(len(unique_customers) * 3) // 4]
        queries = {
            "customer_id_equality": {"customer_id": most_common_customer},
            "order_id_equality": {"order_id": order_sample},
            "date_range": {"order_date": {"$gte": date_start, "$lte": date_end}},
            "customer_id_range": {"customer_id": {"$gte": customer_start, "$lte": customer_end}},
        }

        collection_results: dict[str, dict[str, Any]] = {}
        for collection_name, (field, kind) in LAYOUTS.items():
            ensure_collection(shard_client, collection_name, field, kind)
            unique_values = sorted({row[field] for row in rows})
            if kind != "hashed":
                split_at = unique_values[len(unique_values) // 2]
                ensure_range_split(shard_client, collection_name, field,
                                   _date_for_mongo(split_at) if isinstance(split_at, datetime) else split_at)
            coll = db[collection_name]
            started = time.perf_counter()
            try:
                coll.bulk_write([
                    ReplaceOne({"_id": row["_id"], field: row[field]}, row, upsert=True)
                    for row in rows
                ], ordered=False)
            except BulkWriteError as error:
                first = (error.details or {}).get("writeErrors", [{}])[0]
                raise RuntimeError(f"11.1 write failed for {collection_name}: {first.get('errmsg', 'MongoDB write error')}") from None
            elapsed = round(time.perf_counter() - started, 4)
            total = coll.count_documents({"source": SOURCE, "is_synthetic": True})
            require(total == RECORD_COUNT,
                    f"{collection_name} contains {total} source rows; expected {RECORD_COUNT}.")
            per_shard = live_stats(shard_client, coll, shard_names)
            counts = [row["owned_documents"] for row in per_shard]
            require(sum(counts) == RECORD_COUNT,
                    f"{collection_name} per-shard ownership does not sum to {RECORD_COUNT}.")
            frequencies = Counter(row[field] for row in rows)
            monotonic = all(a[field] < b[field] for a, b in zip(rows, rows[1:])) if kind != "hashed" else False
            query_results = explain_queries(shard_client, coll, queries, shard_names)
            targeted = [name for name, plan in query_results.items() if plan["scatter_gather"] is False]
            balance = (1 - (max(counts) - min(counts)) / RECORD_COUNT) if counts else 0.0
            target_score = sum(QUERY_WEIGHTS[name] for name in targeted)
            score = 0.70 * target_score + 0.20 * balance + 0.10 * (0 if monotonic else 1)
            date_plan = query_results["date_range"]
            customer_range_plan = query_results["customer_id_range"]
            if monotonic:
                hotspot = "High: input key values increase in insert order; the active upper range is a write hotspot risk."
            elif max(frequencies.values()) / RECORD_COUNT > 0.05:
                hotspot = "Elevated: a small number of key values account for a notable share of rows."
            else:
                hotspot = "Low in this dataset: values are not monotonic and the most frequent key represents " + \
                    f"{max(frequencies.values()) / RECORD_COUNT:.1%} of rows."
            collection_results[collection_name] = {
                "candidate": "hashed customer_id" if kind == "hashed" else field,
                "shard_key": {field: kind},
                "source_collection": f"{settings.mongo_database}.{SOURCE_COLLECTION}",
                "total_documents": total,
                "cardinality": {"distinct_values": len(unique_values),
                                "ratio": len(unique_values) / total,
                                "minimum_rows_per_value": min(frequencies.values()),
                                "maximum_rows_per_value": max(frequencies.values()),
                                "most_frequent_share": max(frequencies.values()) / total},
                "distribution": {"per_shard": per_shard,
                                 "balance_score": round(balance, 4),
                                 "imbalance_percent": round((max(counts) - min(counts)) / total * 100, 2)},
                "monotonic_in_input_order": monotonic,
                "hotspot_risk": hotspot,
                "query_targeting_score": round(target_score, 4),
                "recommendation_score": round(score, 4),
                "query_weights": QUERY_WEIGHTS,
                "range_efficiency": {
                    "date_range_targets": date_plan["targeted_shards"],
                    "date_range_scatter_gather": date_plan["scatter_gather"],
                    "date_range_returned": date_plan["n_returned"],
                    "date_range_examined": date_plan["docs_examined"],
                    "customer_range_targets": customer_range_plan["targeted_shards"],
                    "customer_range_scatter_gather": customer_range_plan["scatter_gather"],
                },
                "per_shard": per_shard,
                "queries": query_results,
                "write_elapsed_seconds": elapsed,
            }

        winner_name, winner = max(collection_results.items(), key=lambda pair: pair[1]["recommendation_score"])
        label = winner["candidate"]
        workload = {
            "name": "customer-centric transaction lookup with order and date access",
            "query_weights": QUERY_WEIGHTS,
            "score_formula": "70% measured weighted single-shard query coverage + 20% measured shard balance + 10% non-monotonic insert-key credit.",
        }
        recommendations = {
            "order_id": "Best when order lookups dominate; customer and date filters scatter, and sequential IDs can make the upper range hot.",
            "customer_id": "Keeps customer equality/range reads local. Check hot-customer skew; this source dataset's frequency counts are measured above.",
            "order_date": "Targets time windows, but increasing dates route new writes to the active upper range.",
            "hashed customer_id": "Can distribute distinct customer keys and targets equality reads, but removes customer/date ordering and scatters range queries.",
        }
        for item in collection_results.values():
            candidate_key = "hashed customer_id" if item["shard_key"].get("customer_id") == "hashed" else next(iter(item["shard_key"]))
            item["fit"] = recommendations[candidate_key]
        report = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "experiment": "11.1 - Shard Key Evaluation",
            "dataset": {"label": "Transaction Dataset", "source_collection": f"{settings.mongo_database}.{SOURCE_COLLECTION}",
                        "record_count": RECORD_COUNT, "filter": {"source": SOURCE, "is_synthetic": True},
                        "unique_transaction_ids": len({row["transaction_id"] for row in rows})},
            "mongodb_version": shard_client.server_info()["version"],
            "router": "mongos",
            "shards": sorted(shard_names),
            "workload": workload,
            "recommendation": label,
            "reason": f"{label} scored highest ({winner['recommendation_score']:.3f}) under the displayed workload weights using measured query plans, shard balance, and key progression. Hashed keys can improve distribution but reduce range locality; here their measured results are shown rather than assumed.",
            "matrix": [],
            "collections": {
                name: {**item, "queries": {
                    query_name: {key: value for key, value in query.items() if key != "raw_explain"}
                    for query_name, query in item["queries"].items()
                }} for name, item in collection_results.items()
            },
            "measurement_note": "Dataset cardinality and value frequencies come from the persisted source collection. Placement, storage, write elapsed time, and query plans come from the live Chapter 11 cluster.",
        }
        for name, item in collection_results.items():
            shards_summary = ", ".join(f"{shard['shard']}: {shard['owned_documents']}" for shard in item["per_shard"])
            query_summary = "; ".join(
                f"{key.replace('_', ' ')}: {len(value['targeted_shards'])} shard(s)" if value["targeted_shards_known"]
                else f"{key.replace('_', ' ')}: target unknown" for key, value in item["queries"].items())
            report["matrix"].append({
                "candidate": item["candidate"],
                "shard_key": json.dumps(item["shard_key"], sort_keys=True),
                "cardinality": f"{item['cardinality']['distinct_values']:,}/{RECORD_COUNT:,} distinct; {item['cardinality']['minimum_rows_per_value']}–{item['cardinality']['maximum_rows_per_value']} rows/value",
                "distribution": shards_summary,
                "query_targeting": query_summary,
                "targeted_query_count": sum(plan["scatter_gather"] is False for plan in item["queries"].values()),
                "hotspot_risk": item["hotspot_risk"],
                "range_efficiency": f"Date range: {'targeted' if item['range_efficiency']['date_range_scatter_gather'] is False else 'scatter/gather' if item['range_efficiency']['date_range_scatter_gather'] else 'unknown'}; customer range: {'targeted' if item['range_efficiency']['customer_range_scatter_gather'] is False else 'scatter/gather' if item['range_efficiency']['customer_range_scatter_gather'] else 'unknown'}",
                "fit": f"Measured score {item['recommendation_score']:.3f}",
            })
        detail = {
            **report,
            "requested_documents_per_collection": RECORD_COUNT,
            "source": SOURCE,
            "collections": collection_results,
            "placement_note": "The range-key collections are split at a value midpoint and the upper range is placed on the second Chapter 11 shard to make routing observable; document placement is then measured, not assumed.",
        }
        detail_bytes = json_util.dumps(detail, indent=2) + "\n"
        decision_bytes = json.dumps(report, indent=2) + "\n"
        DETAIL_JSON.write_text(detail_bytes, encoding="utf-8")
        DECISION_JSON.write_text(decision_bytes, encoding="utf-8")
        write_markdown(report, detail)
        print(f"PASS: read exactly {len(rows)} persisted records from {settings.mongo_database}.{SOURCE_COLLECTION}")
        print(f"PASS: measured all four shard-key strategies through mongos on {len(shards)} shards")
        for name, item in collection_results.items():
            print(f"{name}: cardinality={item['cardinality']['distinct_values']} owned=" +
                  str([(s['shard'], s['owned_documents']) for s in item['per_shard']]) +
                  f" targeted={item['query_targeting_score']:.2f} score={item['recommendation_score']:.3f}")
        print(f"Recommendation from measured profile: {label}")
    finally:
        if source_client is not None:
            source_client.close()
        if shard_client is not None:
            shard_client.close()


def write_markdown(report: dict[str, Any], detail: dict[str, Any]) -> None:
    lines = [
        "# Shard Key Evaluation", "",
        f"- Captured UTC: `{report['captured_at_utc']}`",
        f"- Dataset: `{report['dataset']['label']}` ({report['dataset']['record_count']:,} persisted records from `{report['dataset']['source_collection']}`)",
        f"- MongoDB `{report['mongodb_version']}` through mongos; shards: {', '.join(report['shards'])}",
        f"- Recommendation: **{report['recommendation']}** — {report['reason']}",
        f"- Scoring: {report['workload']['score_formula']}",
        "- Every cardinality, distribution, write, and query value below was measured from the source collection or running cluster.", "",
        "| Candidate | Shard key | Cardinality | Shard distribution | Query routing | Range efficiency | Hotspot assessment | Fit |",
        "|---|---|---|---|---|---|---|---|"]
    for row in report["matrix"]:
        lines.append("| " + " | ".join(str(row[key]).replace("|", "\\|") for key in
                     ("candidate", "shard_key", "cardinality", "distribution", "query_targeting",
                      "range_efficiency", "hotspot_risk", "fit")) + " |")
    lines += ["", "## Query plan details", ""]
    for name, item in detail["collections"].items():
        lines += [f"### {item['candidate']}", "",
                  f"Actual frequency range: {item['cardinality']['minimum_rows_per_value']}–{item['cardinality']['maximum_rows_per_value']} records per distinct key; most frequent key share {item['cardinality']['most_frequent_share']:.2%}.",
                  f"Actual per-shard storage: {[(s['shard'], s['owned_documents'], s['storage_size_bytes']) for s in item['per_shard']]}", "",
                  "| Query | Shards targeted | Scatter/gather | Returned | Examined | Time ms |",
                  "|---|---|---|---:|---:|---:|"]
        for query, plan in item["queries"].items():
            scatter = "unknown" if plan["scatter_gather"] is None else "yes" if plan["scatter_gather"] else "no"
            lines.append(f"| {query} | {', '.join(plan['targeted_shards']) or 'unknown'} | {scatter} | {plan['n_returned']} | {plan['docs_examined']} | {plan['execution_millis']} |")
        lines.append("")
    lines += ["## Trade-offs", "",
              "A hashed key generally spreads different key values across hash ranges, while removing the ordering needed for efficient customer or date range scans. The measured cardinality and shard counts above show how this particular dataset landed; they are not guarantees for other workloads.",
              "", "The four `transactions_by_*` collections are dedicated only to this evaluation. Experiment 11.2 continues to use its independent 10,000-row timestamp and hashed-user collections.", ""]
    DETAIL_MD.write_text("\n".join(lines), encoding="utf-8")
    DECISION_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
