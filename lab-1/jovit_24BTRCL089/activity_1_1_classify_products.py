"""Activity 1.1 - Classify database products into NoSQL families.

Author: Jovit Chawla (24BTRCL089)

Products: Amazon S3, Memcached, Couchbase, Apache HBase, Amazon Neptune.
Each product gets a small campus-themed workload stored in the shape its family uses,
and the access pattern that family is built for is measured on a live MongoDB server:

  s3_objects          key -> opaque blob (object store)            Key-Value
  memcache            key -> cached value with TTL (cache-aside)   Key-Value (in-memory)
  course_catalog      nested JSON documents + multikey index       Document
  hbase_sensor_rows   sorted row key + sparse column families      Wide-Column
  campus_people/edges nodes + edges, multi-hop traversal           Graph

All data is synthetic and generated with a fixed seed; it is clearly lab data, not real records.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import time

from bson import Binary
from pymongo import ASCENDING

from common import Recorder, connect, explain_aggregate, explain_find, fresh, time_ms

SEED = 89
START = dt.datetime(2026, 3, 2, tzinfo=dt.timezone.utc)


def s3_object_store(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Amazon S3 -> key-value object store")
    coll = fresh(db, "s3_objects")
    buckets = {"lecture-notes": 1200, "assignments": 1200, "id-photos": 600}
    docs = []
    for bucket, count in buckets.items():
        for i in range(count):
            body = rng.randbytes(rng.randint(1024, 3072))
            year = 2025 + (i % 2)
            key = f"{bucket}/{year}/sem{1 + i % 2}/file-{i:05d}.bin"
            docs.append({"_id": key, "body": Binary(body), "size": len(body),
                         "etag": hashlib.md5(body).hexdigest(), "content_type": "application/octet-stream"})
    marker = b"JOVIT-MARKER"
    target = docs[777]
    target["body"] = Binary(bytes(target["body"])[:500] + marker + bytes(target["body"])[500:])
    coll.insert_many(docs)
    rec.log(f"  stored {len(docs)} objects in {len(buckets)} buckets ({sum(d['size'] for d in docs) // 1024} KiB of blobs)")

    key = target["_id"]
    get = rec.plan("GET object by full key", explain_find(coll, {"_id": key}))
    prefix = rec.plan("LIST keys with prefix 'assignments/2026/'",
                      explain_find(coll, {"_id": {"$regex": "^assignments/2026/"}}, projection={"_id": 1}))
    # S3 cannot search inside object bodies; neither can a key-value lookup: every blob must be fetched and scanned.
    started = time.perf_counter()
    fetched = hits = 0
    for doc in coll.find({}, {"body": 1}):
        fetched += 1
        hits += marker in bytes(doc["body"])
    scan_ms = round((time.perf_counter() - started) * 1000, 2)
    rec.log(f"  search INSIDE blob bodies for a byte marker: fetched {fetched} objects client-side, "
            f"found {hits}, {scan_ms} ms (no server-side index can help)")
    rec.data["s3"] = {"objects": len(docs), "get_by_key": get, "prefix_list": prefix,
                      "content_search": {"objects_fetched": fetched, "hits": hits, "ms": scan_ms}}


def memcached_cache(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Memcached -> in-memory key-value cache (cache-aside with TTL)")
    grades = fresh(db, "grades")
    courses = [f"CSE{200 + i}" for i in range(40)]
    grades.insert_many([{"student": f"S{n:05d}", "course": rng.choice(courses), "score": rng.randint(35, 100)}
                        for n in range(30000)])
    cache = fresh(db, "memcache")
    cache.create_index("expires_at", expireAfterSeconds=0)
    ttl_index = cache.index_information()["expires_at_1"]
    rec.log(f"  TTL index: {{expires_at: 1}} expireAfterSeconds={ttl_index['expireAfterSeconds']} (mongod deletes expired keys)")

    pipeline = [{"$group": {"_id": "$course", "avg": {"$avg": "$score"}, "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]

    def compute():
        return list(grades.aggregate(pipeline))

    def cache_get_or_compute(key="report:course-averages", ttl=300):
        hit = cache.find_one({"_id": key, "expires_at": {"$gt": dt.datetime.now(dt.timezone.utc)}})
        if hit:
            return hit["value"], True
        value = compute()
        cache.replace_one({"_id": key}, {"value": value, "expires_at": dt.datetime.now(dt.timezone.utc)
                                         + dt.timedelta(seconds=ttl)}, upsert=True)
        return value, False

    started = time.perf_counter()
    _, was_hit = cache_get_or_compute()
    miss_ms = round((time.perf_counter() - started) * 1000, 3)
    hit_ms = time_ms(lambda: cache_get_or_compute(), repeat=20)
    plan = rec.plan("cache GET by key", explain_find(cache, {"_id": "report:course-averages"}))
    agg = rec.plan("recompute aggregation over grades", explain_aggregate(grades, pipeline))
    rec.log(f"  cache miss (aggregate {grades.estimated_document_count()} grades + SET): {miss_ms} ms, first call hit={was_hit}")
    rec.log(f"  cache hit (GET by key, median of 20): {hit_ms} ms -> {round(miss_ms / hit_ms, 1)}x faster")
    rec.data["memcached"] = {"miss_ms": miss_ms, "hit_ms_median": hit_ms, "get_plan": plan, "recompute_plan": agg,
                             "ttl_seconds": ttl_index["expireAfterSeconds"]}


def couchbase_documents(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Couchbase -> document store (nested JSON, flexible schema)")
    coll = fresh(db, "course_catalog")
    days, rooms = ["Mon", "Tue", "Wed", "Thu", "Fri"], ["A101", "A204", "B110", "LAB-3", "LAB-7"]
    docs = []
    for i in range(2500):
        course = {"_id": f"C{i:04d}", "title": f"Course {i}", "credits": rng.choice([2, 3, 4]),
                  "instructors": [{"name": f"Prof {rng.randint(1, 120)}", "dept": rng.choice(["CSE", "AIML", "ECE"])}],
                  "schedule": [{"day": d, "room": rng.choice(rooms), "slot": rng.randint(1, 7)}
                               for d in rng.sample(days, rng.randint(1, 3))]}
        if i % 3 == 0:                      # lab courses carry extra fields theory courses do not have
            course["lab"] = {"equipment": rng.sample(["GPU", "Arduino", "Oscilloscope", "VR kit"], 2), "batch_size": 20}
        if i % 5 == 0:
            course["prerequisites"] = [f"C{rng.randint(0, 2499):04d}"]
        docs.append(course)
    coll.insert_many(docs)
    coll.create_index([("schedule.day", ASCENDING), ("schedule.room", ASCENDING)])
    shapes = len({tuple(sorted(d)) for d in docs})
    rec.log(f"  {len(docs)} course documents with {shapes} different top-level shapes in ONE collection")
    one = rec.plan("fetch whole course (nested arrays included) by key", explain_find(coll, {"_id": "C0042"}))
    nested = rec.plan("query INSIDE nested array: Wed classes in LAB-3",
                      explain_find(coll, {"schedule": {"$elemMatch": {"day": "Wed", "room": "LAB-3"}}}))
    labs = rec.plan("query an optional field: lab courses with a GPU", explain_find(coll, {"lab.equipment": "GPU"}))
    result = coll.update_one({"_id": "C0042"}, {"$push": {"instructors": {"name": "Prof Guest", "dept": "AIML"}}})
    rec.log(f"  atomic in-place update of a nested array ($push): modified={result.modified_count}")
    rec.data["couchbase"] = {"documents": len(docs), "distinct_shapes": shapes, "by_key": one,
                             "nested_query": nested, "optional_field_query": labs}


def hbase_wide_column(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Apache HBase -> wide-column store (sorted row key, sparse column families)")
    coll = fresh(db, "hbase_sensor_rows")
    rows = []
    for building in range(1, 11):
        for step in range(7 * 96):          # one week of 15-minute readings
            ts = START + dt.timedelta(minutes=15 * step)
            row = {"_id": f"bldg-{building:02d}#{ts:%Y-%m-%dT%H:%M}",
                   "env": {"temp_c": round(rng.uniform(19, 34), 1), "humidity": rng.randint(30, 80)}}
            if building <= 4:               # only metered buildings have the 'power' column family
                row["power"] = {"kw": round(rng.uniform(5, 60), 2)}
            if rng.random() < 0.02:         # very sparse 'alarm' family
                row["alarm"] = {"code": rng.choice(["SMOKE", "DOOR", "HVAC"])}
            rows.append(row)
    rng.shuffle(rows)                       # insertion order is irrelevant: the row-key index keeps rows sorted
    coll.insert_many(rows)
    rec.log(f"  {len(rows)} rows; families present: env={coll.count_documents({'env': {'$exists': True}})}, "
            f"power={coll.count_documents({'power': {'$exists': True}})}, alarm={coll.count_documents({'alarm': {'$exists': True}})}")
    day = {"_id": {"$gte": "bldg-03#2026-03-04", "$lt": "bldg-03#2026-03-05"}}
    scan = rec.plan("row-key RANGE scan: building 3 on 2026-03-04", explain_find(coll, day, sort={"_id": 1}))
    first = coll.find(day).sort("_id", 1).limit(1)[0]["_id"]
    rec.log(f"  rows come back in row-key order, first row key: {first}")
    nonkey = rec.plan("filter on a NON-key column (env.temp_c > 33)", explain_find(coll, {"env.temp_c": {"$gt": 33}}))
    rec.data["hbase"] = {"rows": len(rows), "rowkey_range": scan, "non_key_filter": nonkey}


def neptune_graph(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Amazon Neptune -> graph database (multi-hop relationship traversal)")
    people, edges = fresh(db, "campus_people"), fresh(db, "campus_edges")
    people.insert_many([{"_id": f"P{i:03d}", "role": rng.choice(["student", "mentor", "alumni"])} for i in range(400)])
    edge_docs = []
    for i in range(400):
        for j in rng.sample(range(400), 4):
            if j != i:
                edge_docs.append({"src": f"P{i:03d}", "dst": f"P{j:03d}", "type": "knows"})
    edges.insert_many(edge_docs)
    edges.create_index("src")
    pipeline = [{"$match": {"_id": "P007"}},
                {"$graphLookup": {"from": "campus_edges", "startWith": "$_id", "connectFromField": "dst",
                                  "connectToField": "src", "as": "paths", "maxDepth": 2, "depthField": "hop"}}]
    graph_ms = time_ms(lambda: list(people.aggregate(pipeline)), repeat=5)
    result = list(people.aggregate(pipeline))[0]["paths"]
    reached = {}
    for edge in result:
        reached.setdefault(edge["dst"], edge["hop"])
    per_hop = {h: sum(1 for v in reached.values() if v == h) for h in sorted(set(reached.values()))}

    def app_side_bfs():
        frontier, seen, trips = {"P007"}, {"P007"}, 0
        for _ in range(3):
            trips += 1
            nxt = {e["dst"] for e in edges.find({"src": {"$in": list(frontier)}}, {"dst": 1})} - seen
            seen |= nxt
            frontier = nxt
        return seen, trips
    bfs_ms = time_ms(app_side_bfs, repeat=5)
    seen, trips = app_side_bfs()
    rec.log(f"  {len(edge_docs)} 'knows' edges between 400 people")
    rec.log(f"  $graphLookup from P007, 3 hops in ONE server-side query: {len(reached)} people reached "
            f"(new per hop {per_hop}), median {graph_ms} ms")
    rec.log(f"  same traversal done hop-by-hop by the application: {len(seen) - 1} people, {trips} round trips, "
            f"median {bfs_ms} ms")
    rec.data["neptune"] = {"edges": len(edge_docs), "reached": len(reached), "new_per_hop": per_hop,
                           "graphlookup_ms": graph_ms, "app_bfs_ms": bfs_ms, "app_round_trips": trips}


CLASSIFICATION = [
    ("Amazon S3", "Key-Value (object store)", "s3"),
    ("Memcached", "Key-Value (in-memory cache)", "memcached"),
    ("Couchbase", "Document", "couchbase"),
    ("Apache HBase", "Wide-Column (column-family)", "hbase"),
    ("Amazon Neptune", "Graph", "neptune"),
]


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_1_1", "Activity 1.1 - Classify Database Products", client)
    rng = random.Random(SEED)
    for step in (s3_object_store, memcached_cache, couchbase_documents, hbase_wide_column, neptune_graph):
        step(db, rec, rng)
    d = rec.data
    evidence = {
        "s3": f"key GET examined {d['s3']['get_by_key']['docsExamined']} doc; body search had to fetch all "
              f"{d['s3']['content_search']['objects_fetched']} objects",
        "memcached": f"hit {d['memcached']['hit_ms_median']} ms vs recompute {d['memcached']['miss_ms']} ms; TTL expiry",
        "couchbase": f"{d['couchbase']['distinct_shapes']} document shapes in one collection; nested query used "
                     f"{d['couchbase']['nested_query']['plan']}",
        "hbase": f"row-key range examined {d['hbase']['rowkey_range']['keysExamined']} keys for "
                 f"{d['hbase']['rowkey_range']['nReturned']} rows; non-key filter examined "
                 f"{d['hbase']['non_key_filter']['docsExamined']} docs",
        "neptune": f"{d['neptune']['reached']} people within 3 hops in 1 query vs "
                   f"{d['neptune']['app_round_trips']} app round trips",
    }
    rec.section("Classification (each backed by the measurements above)")
    for product, family, key in CLASSIFICATION:
        rec.log(f"  {product:<15} {family:<30} evidence: {evidence[key]}")
    d["classification"] = [{"product": p, "family": f, "evidence": evidence[k]} for p, f, k in CLASSIFICATION]
    rec.save()


if __name__ == "__main__":
    main()
