#!/usr/bin/env python3
"""
Activity 1.1 - Try it yourself
23CSE528 - NoSQL Databases using MongoDB - Chapter 01
Student: Kamlesh Choudhary

Demonstrates one claim per researched product inside a live MongoDB server:
  ScyllaDB - key-value and wide-column access over identical storage
  InfluxDB - a native time series collection vs. a regular collection
  ArangoDB - a document $match and a graph $graphLookup in one pipeline
This file produces the EVIDENCE; the verdict is in the report.
"""

import os
import random
import sys
import datetime as dt

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId, json_util

RANDOM_SEED = 42
BASE_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------
# Helpers (duplicated per activity so each folder is self-contained)
# --------------------------------------------------------------------------

def connect():
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=4000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        sys.exit(f"FATAL: no MongoDB server answered at {uri}: {exc}")
    db = client["nosql_labs"]
    host, port = client.address
    print(f"MongoDB version : {db.command('buildInfo')['version']}")
    print(f"Server address  : {host}:{port}")
    print(f"Database        : {db.name}")
    print("-" * 72)
    return client, db


def _walk(node, acc):
    if isinstance(node, dict):
        for key in ("totalDocsExamined", "totalKeysExamined", "nReturned", "executionTimeMillis"):
            if isinstance(node.get(key), (int, float)):
                acc.setdefault(key, []).append(node[key])
        if isinstance(node.get("stage"), str):
            acc.setdefault("stages", []).append(node["stage"])
        for value in node.values():
            _walk(value, acc)
    elif isinstance(node, list):
        for item in node:
            _walk(item, acc)


def stats_from(explain_doc):
    acc = {}
    _walk(explain_doc, acc)
    stages = list(dict.fromkeys(acc.get("stages", [])))
    return {
        "stage": " -> ".join(stages) if stages else "N/A",
        "totalDocsExamined": sum(acc.get("totalDocsExamined", [])),
        "totalKeysExamined": sum(acc.get("totalKeysExamined", [])),
        "nReturned": max(acc.get("nReturned", [0]), default=0),
        "executionTimeMillis": max(acc.get("executionTimeMillis", [0]), default=0),
    }


def explain_find(db, coll, filter_):
    return stats_from(db.command({"explain": {"find": coll, "filter": filter_},
                                  "verbosity": "executionStats"}))


def explain_agg(db, coll, pipeline):
    return stats_from(db.command({"explain": {"aggregate": coll, "pipeline": pipeline, "cursor": {}},
                                  "verbosity": "executionStats"}))


def print_table(headers, rows):
    cols = [[str(h)] + [str(r[i]) for r in rows] for i, h in enumerate(headers)]
    widths = [min(max(len(c) for c in col) + 2, 40) for col in cols]

    def cell(value, width):
        text = str(value)
        return (text[:width - 4] + "..." if len(text) > width - 1 else text).ljust(width)

    print("".join(cell(h, w) for h, w in zip(headers, widths))[:112])
    print("-" * min(sum(widths), 112))
    for row in rows:
        print("".join(cell(c, w) for c, w in zip(row, widths))[:112])
    print()


def export(db, coll, out_path):
    docs = list(db[coll].find())
    with open(out_path, "w") as fh:
        fh.write(json_util.dumps(docs, indent=2) + "\n")
    print(f"Exported {len(docs)} document(s) from '{coll}' -> {os.path.basename(out_path)}")


# --------------------------------------------------------------------------
# ScyllaDB: one storage engine, two API shapes
# --------------------------------------------------------------------------

def demo_scylladb(db):
    print("ScyllaDB - key-value API and wide-column API over the same collection")
    db.scylla_demo.drop()
    random.seed(RANDOM_SEED)
    docs = []
    for u in range(50):
        for i in range(40):
            ts = (BASE_TIME + dt.timedelta(minutes=i)).isoformat()
            docs.append({"_id": f"user-{u:04d}#{ts}",
                         "event": random.choice(["login", "purchase", "logout", "view"]),
                         "amount": round(random.uniform(0, 500), 2)})
    db.scylla_demo.insert_many(docs)

    kv = explain_find(db, "scylla_demo", {"_id": docs[123]["_id"]})
    user = "user-0010"
    # "~" (0x7E) sorts after every character used in an ISO-8601 timestamp.
    partition = explain_find(db, "scylla_demo",
                             {"_id": {"$gte": f"{user}#", "$lt": f"{user}#~"}})

    print_table(["Access shape", "Stage", "DocsExamined", "Returned", "TimeMs"],
                [[label, s["stage"], s["totalDocsExamined"], s["nReturned"], s["executionTimeMillis"]]
                 for label, s in [("KV: exact _id get", kv),
                                  ("Wide-col: partition range scan", partition)]])
    return kv, partition


# --------------------------------------------------------------------------
# InfluxDB: native time series collection vs. a regular collection
# --------------------------------------------------------------------------

def demo_influxdb(db):
    print("InfluxDB - native MongoDB time series collection vs. a regular collection")
    db.ts_regular.drop()
    db.drop_collection("ts_timeseries")
    db.create_collection("ts_timeseries",
                         timeseries={"timeField": "ts", "metaField": "sensor",
                                     "granularity": "seconds"})

    random.seed(RANDOM_SEED)
    readings = [{"ts": BASE_TIME + dt.timedelta(seconds=i * 30),
                 "sensor": f"sensor-{d:03d}",
                 "temp_c": round(random.uniform(-5, 40), 2)}
                for d in range(100) for i in range(150)]
    db.ts_regular.insert_many([dict(r) for r in readings])
    db.ts_timeseries.insert_many([dict(r) for r in readings])

    # storageSize only reflects the on-disk footprint after a checkpoint.
    db.client.admin.command("fsync")
    regular, timeseries = db.command("collStats", "ts_regular"), db.command("collStats", "ts_timeseries")

    window = {"ts": {"$gte": BASE_TIME + dt.timedelta(seconds=1000),
                     "$lte": BASE_TIME + dt.timedelta(seconds=2000)}}
    regular_plan = explain_find(db, "ts_regular", window)
    ts_plan = explain_find(db, "ts_timeseries", window)

    print_table(["Collection", "storageSize", "logical size", "Docs"],
                [["ts_regular", regular["storageSize"], regular["size"], 15000],
                 ["ts_timeseries", timeseries["storageSize"], timeseries["size"], 15000]])
    print_table(["Collection", "Time-window query plan", "DocsExamined", "Returned", "TimeMs"],
                [[label, s["stage"], s["totalDocsExamined"], s["nReturned"], s["executionTimeMillis"]]
                 for label, s in [("ts_regular", regular_plan), ("ts_timeseries", ts_plan)]])
    return regular, timeseries, regular_plan, ts_plan


# --------------------------------------------------------------------------
# ArangoDB: document access and graph access in one pipeline
# --------------------------------------------------------------------------

def demo_arangodb(db):
    print("ArangoDB - document $match + graph $graphLookup in a single pipeline")
    db.multi_model_items.drop()
    db.multi_model_edges.drop()
    db.multi_model_items.insert_many([
        {"_id": "item-electronics-01", "category": "electronics", "price": 299.0},
        {"_id": "item-electronics-02", "category": "electronics", "price": 149.0},
        {"_id": "item-books-01", "category": "books", "price": 19.0},
    ])
    db.multi_model_edges.insert_many([
        {"_id": ObjectId(), "from": "item-electronics-01", "to": "item-electronics-02"},
        {"_id": ObjectId(), "from": "item-electronics-02", "to": "item-books-01"},
    ])

    pipeline = [
        {"$match": {"category": "electronics"}},              # document access
        {"$graphLookup": {"from": "multi_model_edges", "startWith": "$_id",   # graph access
                          "connectFromField": "to", "connectToField": "from",
                          "as": "related", "maxDepth": 1, "depthField": "depth"}},
    ]
    results = list(db.multi_model_items.aggregate(pipeline))
    stats = explain_agg(db, "multi_model_items", pipeline)

    print_table(["Matched document", "category", "related edges found"],
                [[r["_id"], r["category"], len(r["related"])] for r in results])
    print_table(["Pipeline", "Stage", "DocsExamined", "Returned", "TimeMs"],
                [["$match + $graphLookup", stats["stage"], stats["totalDocsExamined"],
                  stats["nReturned"], stats["executionTimeMillis"]]])
    return stats, results


def persist(db, scylla, influx, arango):
    kv, partition = scylla
    regular, timeseries, regular_plan, ts_plan = influx
    agg_stats, agg_results = arango

    db.multi_model_products.drop()
    db.multi_model_products.insert_many([
        {"_id": ObjectId(), "product": "ScyllaDB",
         "claim": "Wide-column engine with a DynamoDB-compatible key-value API on top.",
         "evidence": {"kv_lookup": kv, "partition_range_scan": partition}},
        {"_id": ObjectId(), "product": "InfluxDB",
         "claim": "Time-series storage is specialised enough to be treated as its own family.",
         "evidence": {"regular_storage_bytes": regular["storageSize"],
                      "timeseries_storage_bytes": timeseries["storageSize"],
                      "regular_logical_bytes": regular["size"],
                      "timeseries_logical_bytes": timeseries["size"],
                      "regular_query_plan": regular_plan, "timeseries_query_plan": ts_plan}},
        {"_id": ObjectId(), "product": "ArangoDB",
         "claim": "Multi-model: document, graph and key-value access in one engine.",
         "evidence": {"combined_pipeline": agg_stats,
                      "matched": [r["_id"] for r in agg_results]}},
    ])
    print("Persisted 3 product document(s) to 'multi_model_products'.\n")


def main():
    _, db = connect()
    scylla = demo_scylladb(db)
    influx = demo_influxdb(db)
    arango = demo_arangodb(db)
    persist(db, scylla, influx, arango)
    export(db, "multi_model_products",
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_model_products.json"))


if __name__ == "__main__":
    main()
