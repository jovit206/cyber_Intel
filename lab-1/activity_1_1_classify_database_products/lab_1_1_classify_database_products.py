#!/usr/bin/env python3
"""
Activity 1.1 - Classify Database Products
23CSE528 - NoSQL Databases using MongoDB - Chapter 01
Student: Kamlesh Choudhary

Models one 20,000-row IoT sensor dataset four ways in a live MongoDB server
(key-value, document, wide-column, graph) and measures each shape with
explain("executionStats"). This file produces the EVIDENCE; the written
classification and verdict are in the report.
"""

import json
import os
import random
import sys
import datetime as dt

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId, json_util

RANDOM_SEED = 42
BASE_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
DEVICE_COUNT = 200
READINGS_PER_DEVICE = 100          # 200 * 100 = 20,000 readings
ZONE_COUNT = 25
METRICS = ["temperature", "humidity", "pressure", "battery"]


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


def explain_find(db, coll, filter_, sort=None):
    inner = {"find": coll, "filter": filter_}
    if sort:
        inner["sort"] = dict(sort) if isinstance(sort, list) else sort
    return stats_from(db.command({"explain": inner, "verbosity": "executionStats"}))


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
# One canonical dataset, reshaped four ways
# --------------------------------------------------------------------------

def generate_readings():
    random.seed(RANDOM_SEED)
    readings = []
    for d in range(DEVICE_COUNT):
        for r in range(READINGS_PER_DEVICE):
            metric = METRICS[len(readings) % len(METRICS)]
            ranges = {"temperature": (-10, 45), "humidity": (10, 100),
                      "pressure": (950, 1050), "battery": (0, 100)}
            readings.append({
                "device_id": f"dev-{d:04d}",
                "zone": f"zone-{d % ZONE_COUNT:02d}",
                "ts": BASE_TIME + dt.timedelta(seconds=r * 60),
                "metric": metric,
                "value": round(random.uniform(*ranges[metric]), 2),
                "status": random.choices(["ok", "warn", "critical"], weights=[90, 8, 2])[0],
            })
    return readings


def build_kv_shape(db, readings):
    """Key-value: opaque blob addressed only by its key (models S3 / Memcached)."""
    db.kv_shape.drop()
    docs = [{"_id": f"reading#{i:06d}",
             "blob": json.dumps({**r, "ts": r["ts"].isoformat()})}
            for i, r in enumerate(readings)]
    db.kv_shape.insert_many(docs)
    key_lookup = explain_find(db, "kv_shape", {"_id": docs[len(docs) // 2]["_id"]})
    content_query = explain_find(db, "kv_shape", {"blob": {"$regex": '"status": "critical"'}})
    return key_lookup, content_query


def build_doc_shape(db, readings):
    """Document: nested fields the engine can index and query (models Couchbase)."""
    db.doc_shape.drop()
    db.doc_shape.insert_many([{
        "_id": ObjectId(),
        "device": {"id": r["device_id"], "zone": r["zone"]},
        "reading": {"metric": r["metric"], "value": r["value"], "ts": r["ts"]},
        "status": r["status"],
    } for r in readings])
    db.doc_shape.create_index("device.zone")
    return explain_find(db, "doc_shape", {"device.zone": "zone-07"})


def build_wide_column_shape(db, readings):
    """Wide-column: sorted composite row key = the index (models HBase)."""
    db.wide_column_shape.drop()
    db.wide_column_shape.insert_many([{
        "_id": f"{r['device_id']}#{r['ts'].isoformat()}",
        "metric": r["metric"], "value": r["value"], "status": r["status"],
    } for r in readings])
    device = "dev-0050"
    lo = (BASE_TIME + dt.timedelta(minutes=20)).isoformat()
    hi = (BASE_TIME + dt.timedelta(minutes=40)).isoformat()
    return explain_find(db, "wide_column_shape",
                        {"_id": {"$gte": f"{device}#{lo}", "$lte": f"{device}#{hi}"}})


def build_graph_shape(db):
    """Graph: multi-hop traversal over an edge list (models Neptune)."""
    db.graph_nodes.drop()
    db.graph_edges.drop()
    db.graph_nodes.insert_many([{"_id": f"dev-{d:04d}"} for d in range(DEVICE_COUNT)])
    # Each device reports to a parent 5 positions back, so 3 hops from a leaf
    # reaches its regional gateway.
    db.graph_edges.insert_many([{"_id": ObjectId(), "from": f"dev-{d:04d}", "to": f"dev-{d - 5:04d}"}
                                for d in range(5, DEVICE_COUNT)])
    db.graph_edges.create_index("from")

    start = "dev-0199"
    pipeline = [
        {"$match": {"_id": start}},
        {"$graphLookup": {"from": "graph_edges", "startWith": "$_id",
                          "connectFromField": "to", "connectToField": "from",
                          "as": "ancestry", "maxDepth": 2, "depthField": "depth"}},
    ]
    raw = db.command({"explain": {"aggregate": "graph_nodes", "pipeline": pipeline, "cursor": {}},
                      "verbosity": "executionStats"})
    # $graphLookup explain reports docs examined only for the seed $match, not
    # for the traversal, so we also time the same walk done manually.
    seed_exec = raw["stages"][0]["$cursor"]["executionStats"]
    graph_stats = {
        "stage": "$graphLookup (seed $match only)",
        "totalDocsExamined": seed_exec["totalDocsExamined"],
        "totalKeysExamined": seed_exec["totalKeysExamined"],
        "nReturned": raw["stages"][1]["nReturned"],
        "executionTimeMillis": raw["stages"][1]["executionTimeMillisEstimate"],
    }

    examined = returned = 0
    current = start
    for _ in range(3):
        hop = explain_find(db, "graph_edges", {"from": current})
        examined += hop["totalDocsExamined"]
        returned += hop["nReturned"]
        row = db.graph_edges.find_one({"from": current})
        if not row:
            break
        current = row["to"]
    manual_stats = {"stage": "3x IXSCAN (manual hops)", "totalDocsExamined": examined,
                    "totalKeysExamined": examined, "nReturned": returned,
                    "executionTimeMillis": 0}

    hops = len(db.graph_nodes.aggregate(pipeline).next()["ancestry"])
    return graph_stats, manual_stats, hops


# --------------------------------------------------------------------------
# Classification persisted with the evidence that supports it
# --------------------------------------------------------------------------

PRODUCTS = [
    ("Amazon S3", "Key-Value", "kv_shape",
     "Large opaque blobs retrieved by a known key at scale",
     "Cannot query by object content without an external index"),
    ("Couchbase", "Document", "doc_shape",
     "Semi-structured records needing nested-field queries",
     "Multi-document ACID and joins are limited vs. relational"),
    ("Apache HBase", "Column-Family", "wide_column_shape",
     "High-volume time-series writes with an engineered row key",
     "Flexibility is tied to row-key design"),
    ("Amazon Neptune", "Graph", "graph_shape",
     "Relationship-heavy queries: fraud rings, recommendations",
     "Not a general-purpose store for flat records"),
    ("Memcached", "Key-Value", "kv_shape",
     "Ephemeral caching of hot precomputed values",
     "No persistence and no secondary access path"),
]


def persist(db, evidence):
    db.database_products.drop()
    db.database_products.insert_many([{
        "_id": ObjectId(), "product": product, "family": family,
        "best_for": best_for, "limitation": limitation,
        "shape_tested": shape, "evidence": evidence[shape],
    } for product, family, shape, best_for, limitation in PRODUCTS])
    print(f"Persisted {len(PRODUCTS)} classification document(s) to 'database_products'.\n")


def main():
    _, db = connect()

    print(f"Generating {DEVICE_COUNT * READINGS_PER_DEVICE} IoT readings (seed={RANDOM_SEED})...")
    readings = generate_readings()

    kv_key, kv_content = build_kv_shape(db, readings)
    doc_stats = build_doc_shape(db, readings)
    wide_stats = build_wide_column_shape(db, readings)
    graph_stats, manual_stats, hops = build_graph_shape(db)
    print("Built kv_shape, doc_shape, wide_column_shape and graph_shape.\n")

    print("MEASURED EXPLAIN STATS (executionStats)")
    print_table(
        ["Shape", "Query", "Stage", "DocsExamined", "Returned", "TimeMs"],
        [[shape, query, s["stage"], s["totalDocsExamined"], s["nReturned"], s["executionTimeMillis"]]
         for shape, query, s in [
             ("kv_shape", "_id lookup", kv_key),
             ("kv_shape", "blob content regex", kv_content),
             ("doc_shape", "nested device.zone", doc_stats),
             ("wide_column_shape", "ts range, 1 device", wide_stats),
             ("graph_shape", f"$graphLookup 3-hop ({hops} found)", graph_stats),
             ("graph_shape", "manual 3x hop", manual_stats),
         ]],
    )
    print("Note: $graphLookup explain reports docs examined for the seed $match only, so the")
    print("      'manual 3x hop' row is the comparable traversal-cost measurement.\n")

    persist(db, {
        "kv_shape": {"key_lookup": kv_key, "content_query": kv_content},
        "doc_shape": {"nested_field_query": doc_stats},
        "wide_column_shape": {"range_query": wide_stats},
        "graph_shape": {"graphlookup": graph_stats, "manual_join": manual_stats, "hops": hops},
    })
    export(db, "database_products",
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "database_products.json"))


if __name__ == "__main__":
    main()
