#!/usr/bin/env python3
"""
Activity 1.2 - Map a Real-World Application
23CSE528 - NoSQL Databases using MongoDB - Chapter 01
Student: Kamlesh Choudhary

Builds a ride-sharing dataset (500 drivers, 2,000 riders, 20,000 rides) and
implements all four data domains for real, plus the social-analytics half:
  driver_locations   - 2dsphere + TTL index, $geoNear
  user_profiles      - heterogeneous documents fetched by _id, no join
  ride_history       - compound index vs. the same query unindexed
  driver_rider_graph - $graphLookup 3-hop fraud-ring detection
  raw_posts / realtime_counters - time-partitioned reads and $inc counters
This file produces the EVIDENCE; the verdict is in the report.
"""

import os
import random
import sys
import time
import datetime as dt

from pymongo import MongoClient, ASCENDING, DESCENDING, GEOSPHERE
from pymongo.errors import PyMongoError
from bson import ObjectId, json_util

RANDOM_SEED = 42
BASE_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
DRIVER_COUNT, RIDER_COUNT, RIDE_COUNT = 500, 2000, 20000
BLR_LAT, BLR_LON = 12.9716, 77.5946


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


def explain_find(db, coll, filter_, sort=None, hint=None):
    inner = {"find": coll, "filter": filter_}
    if sort:
        inner["sort"] = dict(sort) if isinstance(sort, list) else sort
    if hint:
        inner["hint"] = hint
    return stats_from(db.command({"explain": inner, "verbosity": "executionStats"}))


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
# Domain 1: driver_locations - 2dsphere + TTL, $geoNear
# --------------------------------------------------------------------------

def build_driver_locations(db):
    db.driver_locations.drop()
    random.seed(RANDOM_SEED)
    now = dt.datetime.now(dt.timezone.utc)   # kept current so TTL does not delete rows mid-run
    db.driver_locations.insert_many([{
        "_id": f"drv-{d:04d}",
        "loc": {"type": "Point", "coordinates": [BLR_LON + random.uniform(-0.15, 0.15),
                                                 BLR_LAT + random.uniform(-0.15, 0.15)]},
        "available": random.random() < 0.6,
        "updated_at": now,
    } for d in range(DRIVER_COUNT)])
    db.driver_locations.create_index([("loc", GEOSPHERE)])
    db.driver_locations.create_index("updated_at", expireAfterSeconds=300)

    ttl_index = db.driver_locations.index_information()["updated_at_1"]
    print(f"TTL index: {ttl_index}")
    print("(MongoDB's TTL monitor sweeps roughly every 60s, so expiry is never instant.)\n")

    pipeline = [
        {"$geoNear": {"near": {"type": "Point", "coordinates": [BLR_LON + 0.02, BLR_LAT - 0.01]},
                      "distanceField": "dist_m", "spherical": True,
                      "query": {"available": True}}},
        {"$limit": 5},
    ]
    stats = explain_agg(db, "driver_locations", pipeline)
    nearest = list(db.driver_locations.aggregate(pipeline))
    print_table(["Nearest available driver", "distance (m)"],
                [[d["_id"], round(d["dist_m"], 1)] for d in nearest])
    return stats, ttl_index, nearest


# --------------------------------------------------------------------------
# Domain 2: user_profiles - one document per user, no join
# --------------------------------------------------------------------------

def build_user_profiles(db):
    db.user_profiles.drop()
    random.seed(RANDOM_SEED)
    riders = [{"_id": f"rdr-{r:04d}", "role": "rider", "name": f"Rider {r}",
               "payment_method": random.choice(["upi", "card", "wallet"]),
               "home_zone": f"zone-{r % 25:02d}"} for r in range(RIDER_COUNT)]
    drivers = [{"_id": f"drv-{d:04d}", "role": "driver", "name": f"Driver {d}",
                "license_no": f"KA-{d:06d}",
                "vehicle": random.choice(["hatchback", "sedan", "auto", "bike"]),
                "payout_account": f"payout-{d:04d}"} for d in range(DRIVER_COUNT)]
    db.user_profiles.insert_many(riders + drivers)

    stats = explain_find(db, "user_profiles", {"_id": "rdr-0001"})
    print_table(["Profile", "Role", "Role-specific fields present"],
                [["rdr-0001", "rider", "payment_method, home_zone"],
                 ["drv-0001", "driver", "license_no, vehicle, payout_account"]])
    return stats


# --------------------------------------------------------------------------
# Domain 3: ride_history - compound index vs. unindexed
# --------------------------------------------------------------------------

def build_ride_history(db):
    db.ride_history.drop()
    random.seed(RANDOM_SEED)
    drivers = [f"drv-{d:04d}" for d in range(DRIVER_COUNT)]
    db.ride_history.insert_many([{
        "_id": ObjectId(),
        "rider_id": f"rdr-{i % RIDER_COUNT:04d}",
        "driver_id": random.choice(drivers),
        "ts": BASE_TIME + dt.timedelta(minutes=i * 3),
        "fare": round(random.uniform(60, 850), 2),
        "status": random.choices(["completed", "cancelled"], weights=[92, 8])[0],
    } for i in range(RIDE_COUNT)])
    db.ride_history.create_index([("rider_id", ASCENDING), ("ts", DESCENDING)])

    query = {"rider_id": "rdr-0005",
             "ts": {"$gte": BASE_TIME, "$lte": BASE_TIME + dt.timedelta(days=10)}}
    indexed = explain_find(db, "ride_history", query, sort=[("ts", -1)])
    unindexed = explain_find(db, "ride_history", query, sort=[("ts", -1)], hint={"$natural": 1})
    return indexed, unindexed


# --------------------------------------------------------------------------
# Domain 4: driver_rider_graph - $graphLookup fraud ring
# --------------------------------------------------------------------------

def build_driver_rider_graph(db):
    db.graph_entities.drop()
    entities = ([f"rdr-{r:04d}" for r in range(RIDER_COUNT)]
                + [f"drv-{d:04d}" for d in range(DRIVER_COUNT)])
    # One seeded ring of four entities sharing devices / payment instruments;
    # every other entity has an empty adjacency list.
    ring = ["rdr-0001", "drv-0001", "rdr-0002", "drv-0002"]
    links = {e: [] for e in entities}
    for a, b in zip(ring, ring[1:] + ring[:1]):
        links[a].append(b)
        links[b].append(a)
    db.graph_entities.insert_many([{"_id": e, "linked_to": links[e]} for e in entities])
    db.graph_entities.create_index("linked_to")

    def traverse(seed):
        pipeline = [
            {"$match": {"_id": seed}},
            {"$graphLookup": {"from": "graph_entities", "startWith": "$linked_to",
                              "connectFromField": "linked_to", "connectToField": "_id",
                              "as": "ring", "maxDepth": 2, "depthField": "depth"}},
        ]
        found = sorted({m["_id"] for m in db.graph_entities.aggregate(pipeline).next()["ring"]})
        return pipeline, found

    pipeline, ring_found = traverse("rdr-0001")
    stats = explain_agg(db, "graph_entities", pipeline)
    _, control_found = traverse("rdr-0500")     # not in the ring

    print_table(["3-hop $graphLookup seed", "Entities reached"],
                [["rdr-0001 (in seeded ring)", ", ".join(ring_found)],
                 ["rdr-0500 (control)", ", ".join(control_found) or "(none)"]])
    return stats, ring_found, control_found


# --------------------------------------------------------------------------
# Social analytics: time-partitioned posts + $inc counters
# --------------------------------------------------------------------------

def build_social_analytics(db):
    db.raw_posts.drop()
    random.seed(RANDOM_SEED)
    posts = [{"_id": ObjectId(),
              "partition_date": (BASE_TIME + dt.timedelta(days=day)).strftime("%Y-%m-%d"),
              "author": f"user-{random.randint(0, 999):04d}",
              "text": f"post #{day}-{i} " + random.choice(
                  ["great day", "product launch", "random thought", "travel update"]),
              "ts": BASE_TIME + dt.timedelta(days=day, seconds=i)}
             for day in range(10) for i in range(500)]
    db.raw_posts.insert_many(posts)
    db.raw_posts.create_index("partition_date")

    one_day = explain_find(db, "raw_posts", {"partition_date": "2026-01-03"})
    keyword = explain_find(db, "raw_posts", {"text": {"$regex": "product launch"}})

    db.realtime_counters.drop()
    post_ids = [p["_id"] for p in posts[:1000]]
    db.realtime_counters.insert_many([{"_id": pid, "likes": 0, "views": 0} for pid in post_ids])
    inc_stats = stats_from(db.command({
        "explain": {"update": "realtime_counters",
                    "updates": [{"q": {"_id": post_ids[0]}, "u": {"$inc": {"views": 1}}}]},
        "verbosity": "executionStats"}))

    started = time.perf_counter()
    for pid in post_ids:
        db.realtime_counters.update_one({"_id": pid}, {"$inc": {"views": 1}})
    wall_ms = (time.perf_counter() - started) * 1000
    return one_day, keyword, inc_stats, wall_ms, len(post_ids)


def persist(db, geo, profile_stats, ride, graph, social):
    geo_stats, ttl_index, nearest = geo
    indexed, unindexed = ride
    graph_stats, ring_found, control_found = graph
    one_day, keyword, inc_stats, wall_ms, n_updates = social

    db.app_domain_mapping.drop()
    db.app_domain_mapping.insert_many([
        {"_id": ObjectId(), "app": "ride-sharing", "domain": "driver_locations",
         "family": "Key-Value (production: Redis geo)",
         "evidence": {"geoNear": geo_stats, "ttl_index": ttl_index,
                      "nearest_sample": [d["_id"] for d in nearest]}},
        {"_id": ObjectId(), "app": "ride-sharing", "domain": "user_profiles",
         "family": "Document (MongoDB)", "evidence": {"fetch_by_id": profile_stats}},
        {"_id": ObjectId(), "app": "ride-sharing", "domain": "ride_history",
         "family": "Document or Column-Family (production: Cassandra at scale)",
         "evidence": {"indexed": indexed, "unindexed_forced": unindexed}},
        {"_id": ObjectId(), "app": "ride-sharing", "domain": "driver_rider_graph",
         "family": "Graph (production: Neo4j / Neptune)",
         "evidence": {"graphlookup": graph_stats, "ring_found": ring_found,
                      "control_found": control_found}},
        {"_id": ObjectId(), "app": "social-media-analytics", "domain": "raw_post_storage",
         "family": "Column-Family (production: Cassandra)",
         "evidence": {"one_day_indexed": one_day, "keyword_scan": keyword}},
        {"_id": ObjectId(), "app": "social-media-analytics", "domain": "real_time_analytics",
         "family": "Key-Value (production: Redis INCR)",
         "evidence": {"inc_explain": inc_stats, "wall_clock_ms": round(wall_ms, 1),
                      "n_updates": n_updates}},
    ])
    print("Persisted 6 domain-mapping document(s) to 'app_domain_mapping'.\n")


def main():
    _, db = connect()
    print(f"Generating {DRIVER_COUNT} drivers, {RIDER_COUNT} riders, {RIDE_COUNT} rides "
          f"(seed={RANDOM_SEED})...\n")

    geo = build_driver_locations(db)
    profile_stats = build_user_profiles(db)
    ride = build_ride_history(db)
    graph = build_driver_rider_graph(db)
    social = build_social_analytics(db)

    geo_stats, _, _ = geo
    indexed, unindexed = ride
    graph_stats, _, _ = graph
    one_day, keyword, inc_stats, wall_ms, n_updates = social

    print("MEASURED EXPLAIN STATS (executionStats)")
    print_table(
        ["Domain / Query", "Stage", "DocsExamined", "Returned", "TimeMs"],
        [[label, s["stage"], s["totalDocsExamined"], s["nReturned"], s["executionTimeMillis"]]
         for label, s in [
             ("driver_locations: $geoNear top-5", geo_stats),
             ("user_profiles: fetch by _id", profile_stats),
             ("ride_history: compound index", indexed),
             ("ride_history: forced $natural", unindexed),
             ("driver_rider_graph: $graphLookup", graph_stats),
             ("raw_posts: one partition_date", one_day),
             ("raw_posts: keyword regex", keyword),
             ("realtime_counters: one $inc", inc_stats),
         ]],
    )
    print(f"Wall-clock for {n_updates} sequential $inc updates (measured in Python, not from")
    print(f"explain): {wall_ms:.1f}ms total, {wall_ms / n_updates:.3f}ms per update.\n")

    persist(db, geo, profile_stats, ride, graph, social)
    export(db, "app_domain_mapping",
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_domain_mapping.json"))


if __name__ == "__main__":
    main()
