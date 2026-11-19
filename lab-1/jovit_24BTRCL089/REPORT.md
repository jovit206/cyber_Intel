# Lab 1 - Introduction to NoSQL: Families, Products and Data-Domain Mapping

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 1 activities) |
| **Executed on** | 2026-11-19T14:15:15+00:00 on `LAPTOP-DSDFIQOI`, Windows 11 (10.0.26300) |
| **Software** | MongoDB 8.3.7 (standalone), Python 3.12.10, pymongo 4.20.0 |

> **Own work.** All code, datasets and measurements in this folder (`lab-1/jovit_24BTRCL089/`) are my own and were
> produced by running the scripts below on my machine. The files in the parent `lab-1/` folder are a reference
> submission by another student; they were read only to understand what the activities ask for, and nothing from
> them (code, data, results or credentials) is reused here.

## 1. Experiment title

Classifying NoSQL database products into families and mapping application data domains to the right NoSQL model,
with every claim demonstrated on a live MongoDB server.

## 2. Aim

To understand the four classic NoSQL families (key-value, document, wide-column, graph) and the newer specialised
and multi-model systems, by reproducing each family's characteristic access pattern in MongoDB and measuring it.

## 3. Objectives

1. Classify Amazon S3, Memcached, Couchbase, Apache HBase and Amazon Neptune into NoSQL families (Activity 1.1).
2. Decide whether ScyllaDB, InfluxDB and ArangoDB fit one family or span several (Activity 1.1, Try it yourself).
3. Map the data domains of a ride-sharing app and a social-media analytics platform to families (Activity 1.2).
4. Repeat the mapping for an application of my own choice - a food-delivery platform (Activity 1.2, Try it yourself).
5. Justify every answer with measured query plans (`explain("executionStats")`) and timings, not assertions.

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | Windows 11 (10.0.26300) |
| Machine | `LAPTOP-DSDFIQOI` (laptop) |
| Database server | MongoDB Community 8.3.7, standalone, `mongodb://localhost:27017` |
| Language / driver | Python 3.12.10, pymongo 4.20.0 (`pip install -r requirements.txt`) |
| Database used | `jovit_lab01` (only this lab's own collections are dropped and recreated) |
| Optional | MongoDB Compass to browse the collections |

## 5. Theory

**Why NoSQL.** Relational databases store data in fixed-schema tables and scale mainly vertically. NoSQL systems
relax one or more of these constraints - schema, joins, strong consistency - to gain horizontal scale, flexible data
shapes or very low latency for a specific access pattern. They are usually grouped into four families:

- **Key-value stores** (Redis, Memcached, DynamoDB, Amazon S3 as an object store) map an opaque key to a value.
  Reads and writes by key are O(1)-like and extremely fast, but the store cannot look *inside* the value, so any
  query on content requires reading every value. Memcached keeps values only in memory with an expiry (TTL), which
  makes it a cache rather than a system of record.
- **Document stores** (MongoDB, Couchbase) store self-describing JSON/BSON documents. Related data is embedded
  (arrays, sub-documents), so one read returns a whole entity; documents in one collection may have different
  fields; secondary and multikey indexes allow queries on nested fields.
- **Wide-column (column-family) stores** (HBase, Cassandra, ScyllaDB) organise data by a row/partition key kept in
  sorted order, with columns grouped into families that can be sparse. Range scans on the key are cheap; queries on
  non-key columns are not supported efficiently, so tables are designed around the query.
- **Graph databases** (Neo4j, Amazon Neptune) store nodes and edges and traverse relationships directly
  ("index-free adjacency"), which makes multi-hop questions (friends-of-friends, fraud rings, recommendations)
  natural to express.

Beyond the four: **time-series databases** (InfluxDB, TimescaleDB, MongoDB time-series collections) are optimised
for append-only timestamped measurements - columnar compression, time bucketing and downsampling. **Multi-model**
databases (ArangoDB, Cosmos DB) expose several models over one engine. **Search engines** (Elasticsearch, Atlas
Search) rank results by relevance, which none of the four families do.

**How this lab demonstrates the families with one database.** MongoDB can imitate each access pattern: `_id`
lookups behave like a key-value store, nested documents and multikey indexes like a document store, a sorted
string row key with sparse sub-documents like a wide-column table, an edge collection with `$graphLookup` like a
graph, and time-series collections like a TSDB. Comparing the query plans - `EXPRESS_IXSCAN` (direct key lookup),
`IXSCAN` (index range), `COLLSCAN` (full scan) - and the keys/documents examined shows *which access pattern each
data shape makes cheap*, and that is exactly what decides which family a product belongs to.

## 6. Procedure

1. Start the MongoDB service (`Get-Service MongoDB` shows *Running*) or set `MONGO_URI` in a git-ignored `.env`
   created from `.env.example`.
2. Install the driver: `python -m pip install -r requirements.txt`.
3. Run each activity from this folder (or all at once with `run_all.ps1`):
   `python activity_1_1_classify_products.py`, `python activity_1_1_try_it_yourself.py`,
   `python activity_1_2_map_application.py`, `python activity_1_2_try_it_yourself.py`.
4. Each script generates its synthetic dataset (fixed random seed), creates the indexes, runs the queries, calls
   `explain` with `executionStats`, prints the measurements and saves them to `results/<activity>.json` and
   `results/<activity>.output.txt`.
5. Run `python build_report.py` to regenerate this report from the saved results.
6. Optionally open the `jovit_lab01` database in MongoDB Compass to inspect the collections.

## 7. Source code

The complete source of every file used in this lab.

### `common.py`

```python
"""Shared helpers for Jovit Chawla's (24BTRCL089) Chapter 1 experiments.

Every number printed by the activity scripts comes from a live MongoDB server:
query-plan figures from ``explain`` with ``executionStats`` verbosity, timings from
``time.perf_counter`` around real round trips. Nothing here fabricates a measurement.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

from pymongo import MongoClient
from pymongo.errors import PyMongoError

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
STUDENT = {"name": "Jovit Chawla", "enrollment": "24BTRCL089"}
DATABASE = "jovit_lab01"


def load_env() -> None:
    """Read KEY=VALUE pairs from this folder's (git-ignored) .env without overriding real env vars."""
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    """Connect to MONGO_URI (default: the local mongod service). Fails loudly; there is no mock fallback."""
    load_env()
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="jovit-24BTRCL089-lab01")
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        sys.exit(f"Cannot reach MongoDB ({type(exc).__name__}). Start mongod or set MONGO_URI in .env.")
    return client, client[os.environ.get("LAB1_DATABASE", DATABASE)]


def fresh(db, name: str, **options):
    """Drop and recreate one of this lab's own collections so every run starts from the same state."""
    db.drop_collection(name)
    return db.create_collection(name, **options)


def _plan_stages(node: dict | None) -> list[str]:
    stages: list[str] = []
    while isinstance(node, dict):
        if "stage" in node:
            stages.append(node["stage"])
        children = node.get("inputStages") or ([node["inputStage"]] if "inputStage" in node else [])
        node = children[0] if children else None
    return stages


def _find_key(obj: Any, key: str):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def summarise_explain(explain: dict) -> dict:
    winning = _find_key(explain, "winningPlan") or {}
    plan = winning.get("queryPlan", winning)
    stats = _find_key(explain, "executionStats") or {}
    pipeline = [next(iter(stage)) for stage in explain.get("stages", []) if isinstance(stage, dict)]
    return {
        "plan": " <- ".join(_plan_stages(plan)) or "n/a",
        "pipeline": pipeline,
        "nReturned": stats.get("nReturned"),
        "keysExamined": stats.get("totalKeysExamined"),
        "docsExamined": stats.get("totalDocsExamined"),
        "executionTimeMillis": stats.get("executionTimeMillis"),
    }


def explain_find(coll, filter_: dict, *, sort=None, limit: int = 0, projection=None, hint=None) -> dict:
    command: dict[str, Any] = {"find": coll.name, "filter": filter_}
    if sort:
        command["sort"] = sort
    if limit:
        command["limit"] = limit
    if projection:
        command["projection"] = projection
    if hint:
        command["hint"] = hint
    return summarise_explain(coll.database.command("explain", command, verbosity="executionStats"))


def explain_aggregate(coll, pipeline: list[dict]) -> dict:
    command = {"aggregate": coll.name, "pipeline": pipeline, "cursor": {}}
    return summarise_explain(coll.database.command("explain", command, verbosity="executionStats"))


def time_ms(fn: Callable[[], Any], repeat: int = 5) -> float:
    """Median wall-clock milliseconds of ``repeat`` real calls."""
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 3)


class Recorder:
    """Prints results to the console and saves the same lines plus structured data under results/."""

    def __init__(self, activity: str, title: str, client):
        self.activity, self.title = activity, title
        self.lines: list[str] = []
        self.data: dict[str, Any] = {}
        info = client.server_info()
        self.environment = {
            "student": STUDENT,
            "run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "machine": platform.node(),
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "python": platform.python_version(),
            "pymongo": __import__("pymongo").version,
            "mongodb_server": info.get("version"),
            "topology": client.admin.command("hello").get("setName", "standalone"),
        }
        self.log(f"{title}")
        self.log(f"Student: {STUDENT['name']} ({STUDENT['enrollment']})")
        self.log(f"Run at {self.environment['run_at_utc']} on {self.environment['machine']} | MongoDB "
                 f"{self.environment['mongodb_server']} ({self.environment['topology']}) | Python "
                 f"{self.environment['python']} | pymongo {self.environment['pymongo']}")

    def log(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def section(self, text: str) -> None:
        self.log()
        self.log(f"== {text} ==")

    def plan(self, label: str, summary: dict) -> dict:
        self.log(f"  {label}: plan={summary['plan']} pipeline={summary['pipeline'] or '-'} "
                 f"returned={summary['nReturned']} keysExamined={summary['keysExamined']} "
                 f"docsExamined={summary['docsExamined']} serverMs={summary['executionTimeMillis']}")
        return summary

    def save(self) -> None:
        RESULTS_DIR.mkdir(exist_ok=True)
        payload = {"activity": self.activity, "title": self.title, "environment": self.environment,
                   "measurements": self.data}
        (RESULTS_DIR / f"{self.activity}.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        (RESULTS_DIR / f"{self.activity}.output.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        print(f"\nSaved results/{self.activity}.json and results/{self.activity}.output.txt")
```

### `activity_1_1_classify_products.py`

```python
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
```

### `activity_1_1_try_it_yourself.py`

```python
"""Activity 1.1 (Try it yourself) - Do ScyllaDB, InfluxDB and ArangoDB fit one family or several?

Author: Jovit Chawla (24BTRCL089)

  ScyllaDB  - partition key + clustering order (Cassandra data model)  -> ONE family: wide-column
  InfluxDB  - measurement + tags + timestamped fields, downsampling     -> time-series (outside the classic four)
  ArangoDB  - one engine answering key-value, document AND graph queries -> multi-model (spans several)

Each claim is demonstrated on a live MongoDB server. All data is synthetic, generated with a fixed seed.
"""
from __future__ import annotations

import datetime as dt
import random

from pymongo import ASCENDING, DESCENDING

from common import Recorder, connect, explain_aggregate, explain_find, fresh, time_ms

SEED = 2489
START = dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc)


def scylla(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("ScyllaDB -> wide-column: PRIMARY KEY ((channel_id), sent_at DESC)")
    coll = fresh(db, "scylla_messages")
    coll.create_index([("channel_id", ASCENDING), ("sent_at", DESCENDING)])   # partition key, clustering column
    docs = [{"channel_id": f"ch-{rng.randint(1, 200):03d}", "sent_at": START + dt.timedelta(seconds=rng.randint(0, 30 * 86400)),
             "author": f"user{rng.randint(1, 3000)}", "text": "msg"} for _ in range(60000)]
    coll.insert_many(docs)
    partition = rec.plan("latest 20 messages of one partition (ch-042)",
                         explain_find(coll, {"channel_id": "ch-042"}, sort={"sent_at": -1}, limit=20))
    no_key = rec.plan("same question without the partition key (by author)",
                      explain_find(coll, {"author": "user42"}, sort={"sent_at": -1}, limit=20))
    sorted_by_index = "SORT" not in partition["plan"]
    rec.log(f"  partition query needed no SORT stage (clustering order served by the index): {sorted_by_index}")
    rec.data["scylla"] = {"rows": len(docs), "partition_query": partition, "non_partition_query": no_key,
                          "clustering_order_from_index": sorted_by_index}


def influx(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("InfluxDB -> time-series: measurement, tags, fields, timestamp")
    db.drop_collection("influx_cpu_ts")
    ts = db.create_collection("influx_cpu_ts", timeseries={"timeField": "time", "metaField": "tags", "granularity": "minutes"})
    plain = fresh(db, "influx_cpu_plain")
    points = []
    for host in range(20):
        for minute in range(0, 7 * 24 * 60, 2):           # one week, a point every 2 minutes, per host
            points.append({"time": START + dt.timedelta(minutes=minute),
                           "tags": {"host": f"web-{host:02d}", "region": "ap-south-1" if host < 10 else "eu-west-1"},
                           "usage_user": round(rng.uniform(2, 95), 2), "usage_system": round(rng.uniform(1, 30), 2)})
    ts.insert_many([dict(p) for p in points])
    plain.insert_many([dict(p) for p in points])
    db.client.admin.command("fsync")      # checkpoint so storageSize reflects the data just written
    ts_stats = db.command("collStats", "influx_cpu_ts")
    plain_stats = db.command("collStats", "influx_cpu_plain")
    ts_bytes = ts_stats.get("storageSize", 0)
    plain_bytes = plain_stats.get("storageSize", 0)
    buckets = db["system.buckets.influx_cpu_ts"].estimated_document_count() if "system.buckets.influx_cpu_ts" in \
        db.list_collection_names() else ts_stats.get("timeseries", {}).get("bucketCount")
    rec.log(f"  {len(points)} points from 20 hosts | on-disk storage: time-series {ts_bytes // 1024} KiB vs "
            f"plain collection {plain_bytes // 1024} KiB ({round(plain_bytes / max(ts_bytes, 1), 1)}x smaller); "
            f"points packed into {buckets} buckets")
    downsample = [{"$match": {"tags.host": "web-07", "time": {"$gte": START, "$lt": START + dt.timedelta(days=1)}}},
                  {"$group": {"_id": {"$dateTrunc": {"date": "$time", "unit": "hour"}},
                              "mean_user": {"$avg": "$usage_user"}, "max_user": {"$max": "$usage_user"}}},
                  {"$sort": {"_id": 1}}]
    hourly = list(ts.aggregate(downsample))
    rec.plan("1-hour downsample of web-07 for one day", explain_aggregate(ts, downsample))
    rec.log(f"  downsampled 720 raw points into {len(hourly)} hourly rows; first: "
            f"{hourly[0]['_id']:%Y-%m-%d %H:%M} mean={hourly[0]['mean_user']:.2f} max={hourly[0]['max_user']}")
    window = [{"$match": {"tags.host": "web-07"}}, {"$sort": {"time": 1}}, {"$limit": 30},
              {"$setWindowFields": {"sortBy": {"time": 1}, "output": {
                  "moving_avg": {"$avg": "$usage_user", "window": {"documents": [-4, 0]}}}}}]
    moving = list(ts.aggregate(window))
    rec.log(f"  5-point moving average (window function), last value: {moving[-1]['moving_avg']:.2f}")
    rec.data["influx"] = {"points": len(points), "timeseries_storage_bytes": ts_bytes, "plain_storage_bytes": plain_bytes,
                          "hourly_rows": len(hourly)}


def arango(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("ArangoDB -> multi-model: key-value + document + graph in one engine")
    users, follows = fresh(db, "arango_users"), fresh(db, "arango_follows")
    cities = ["Jaipur", "Pune", "Delhi", "Bengaluru", "Kolkata"]
    users.insert_many([{"_id": f"u{i:04d}", "name": f"User {i}", "profile": {"city": rng.choice(cities),
                        "skills": rng.sample(["python", "mongodb", "ml", "cloud", "react"], 2)}} for i in range(1500)])
    users.create_index("profile.city")
    follows.insert_many([{"_from": f"u{i:04d}", "_to": f"u{rng.randrange(1500):04d}"} for i in range(1500) for _ in range(3)])
    follows.create_index("_from")
    kv = rec.plan("KEY-VALUE: get u0089 by key", explain_find(users, {"_id": "u0089"}))
    doc = rec.plan("DOCUMENT: users in Pune who know mongodb",
                   explain_find(users, {"profile.city": "Pune", "profile.skills": "mongodb"}))
    combined = [{"$match": {"_id": "u0089"}},
                {"$graphLookup": {"from": "arango_follows", "startWith": "$_id", "connectFromField": "_to",
                                  "connectToField": "_from", "as": "reach", "maxDepth": 1}},
                {"$lookup": {"from": "arango_users", "localField": "reach._to", "foreignField": "_id", "as": "people"}},
                {"$project": {"same_city": {"$size": {"$filter": {"input": "$people", "cond": {
                    "$eq": ["$$this.profile.city", "Pune"]}}}}, "reached": {"$size": "$people"}}}]
    out = list(users.aggregate(combined))[0]
    ms = time_ms(lambda: list(users.aggregate(combined)))
    rec.log(f"  GRAPH + DOCUMENT in ONE query: u0089 reaches {out['reached']} people within 2 hops, "
            f"{out['same_city']} of them live in Pune (median {ms} ms)")
    rec.data["arango"] = {"kv": kv, "document": doc, "graph_plus_document": {**out, "median_ms": ms}}


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_1_1_try_it_yourself", "Activity 1.1 (Try it yourself) - ScyllaDB, InfluxDB, ArangoDB", client)
    rng = random.Random(SEED)
    scylla(db, rec, rng)
    influx(db, rec, rng)
    arango(db, rec, rng)
    d = rec.data
    verdict = [
        ("ScyllaDB", "Single family: Wide-Column", "partition query examined "
         f"{d['scylla']['partition_query']['keysExamined']} keys, no SORT; without the key: "
         f"{d['scylla']['non_partition_query']['docsExamined']} docs scanned"),
        ("InfluxDB", "Time-series (specialised; not one of the classic four)",
         f"time-series storage {d['influx']['timeseries_storage_bytes'] // 1024} KiB vs "
         f"{d['influx']['plain_storage_bytes'] // 1024} KiB plain; native downsampling"),
        ("ArangoDB", "Multi-model: Key-Value + Document + Graph",
         "the same data answered a key lookup, a nested-document query and a traversal joined to documents"),
    ]
    rec.section("Verdict")
    for product, family, why in verdict:
        rec.log(f"  {product:<9} {family:<55} {why}")
    d["verdict"] = [{"product": p, "classification": f, "evidence": w} for p, f, w in verdict]
    rec.save()


if __name__ == "__main__":
    main()
```

### `activity_1_2_map_application.py`

```python
"""Activity 1.2 - Map a real-world application's data domains to NoSQL families.

Author: Jovit Chawla (24BTRCL089)

Part A - ride-sharing app (city: Pune), four data domains:
  1. live driver positions   -> Key-Value / geo cache (Redis GEO)      2dsphere + $geoNear, TTL on stale pings
  2. rider & driver profiles -> Document (MongoDB)                       heterogeneous docs, one read, no joins
  3. trip history            -> Document at app scale / Wide-Column at   compound index vs. collection scan
                                 very large scale (Cassandra)
  4. fraud relationships     -> Graph (Neo4j / Neptune)                  $graphLookup over shared devices/cards

Part B - social-media analytics platform, two layers:
  raw post stream            -> Wide-Column (Cassandra, time-bucketed partitions)
  real-time counters         -> Key-Value (Redis INCR)

All data is synthetic and generated with a fixed seed; the fraud ring is planted on purpose so the
traversal has something real to find.
"""
from __future__ import annotations

import datetime as dt
import random
import time

from pymongo import ASCENDING, DESCENDING, GEOSPHERE, UpdateOne

from common import Recorder, connect, explain_aggregate, explain_find, fresh, time_ms

SEED = 1289
NOW = dt.datetime(2026, 5, 10, 18, 0, tzinfo=dt.timezone.utc)
PUNE = (73.8567, 18.5204)            # lon, lat
DRIVERS, RIDERS, TRIPS = 800, 3000, 25000


def point(rng: random.Random, spread: float = 0.08) -> dict:
    return {"type": "Point", "coordinates": [round(PUNE[0] + rng.uniform(-spread, spread), 6),
                                             round(PUNE[1] + rng.uniform(-spread, spread), 6)]}


def driver_positions(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("1. Live driver positions -> Key-Value / geospatial cache")
    coll = fresh(db, "ride_driver_positions")
    coll.create_index([("loc", GEOSPHERE)])
    coll.create_index("pinged_at", expireAfterSeconds=120)       # a driver who stops pinging disappears
    coll.insert_many([{"_id": f"D{i:04d}", "loc": point(rng), "available": rng.random() < 0.6, "pinged_at": NOW}
                      for i in range(DRIVERS)])
    ops = [UpdateOne({"_id": f"D{rng.randrange(DRIVERS):04d}"}, {"$set": {"loc": point(rng), "pinged_at": NOW}})
           for _ in range(4000)]
    started = time.perf_counter()
    coll.bulk_write(ops, ordered=False)
    burst_ms = (time.perf_counter() - started) * 1000
    rec.log(f"  4000 position upserts by key in one bulk write: {burst_ms:.1f} ms ({4000 / burst_ms * 1000:,.0f} writes/s)")
    pickup = {"type": "Point", "coordinates": [73.8478, 18.5314]}   # Shivajinagar
    near = [{"$geoNear": {"near": pickup, "distanceField": "metres", "maxDistance": 3000, "query": {"available": True},
                          "spherical": True}}, {"$limit": 5}]
    found = list(coll.aggregate(near))
    summary = rec.plan("5 nearest available drivers within 3 km", explain_aggregate(coll, near))
    rec.log("  nearest: " + ", ".join(f"{d['_id']} {d['metres']:.0f} m" for d in found))
    rec.data["driver_positions"] = {"burst_write_ms": round(burst_ms, 2), "nearest": [(d["_id"], round(d["metres"]))
                                    for d in found], "geo_plan": summary, "ttl_seconds": 120}


def profiles(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("2. Rider and driver profiles -> Document")
    coll = fresh(db, "ride_profiles")
    docs = [{"_id": f"R{i:05d}", "kind": "rider", "name": f"Rider {i}", "phone": f"+91-90{i:08d}",
             "saved_places": [{"label": "home", "loc": point(rng)}, {"label": "work", "loc": point(rng)}],
             "payment": {"type": rng.choice(["UPI", "card", "cash"])}} for i in range(RIDERS)]
    docs += [{"_id": f"D{i:04d}", "kind": "driver", "name": f"Driver {i}", "phone": f"+91-80{i:08d}",
              "vehicle": {"model": rng.choice(["WagonR", "Dzire", "Nexon EV"]), "plate": f"MH12-{i:04d}"},
              "documents": [{"type": t, "verified": rng.random() < 0.95} for t in ("licence", "RC", "insurance")],
              "rating": round(rng.uniform(3.8, 5.0), 2)} for i in range(DRIVERS)]
    coll.insert_many(docs)
    coll.create_index("phone", unique=True)
    rider = rec.plan("whole rider profile by id (places + payment in one doc)", explain_find(coll, {"_id": "R00089"}))
    driver = rec.plan("whole driver profile by id (vehicle + documents)", explain_find(coll, {"_id": "D0089"}))
    phone = rec.plan("login lookup by phone (unique secondary index)", explain_find(coll, {"phone": "+91-9000000089"}))
    rec.data["profiles"] = {"documents": len(docs), "rider_by_id": rider, "driver_by_id": driver, "by_phone": phone}


def trip_history(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("3. Trip history -> Document (time-ordered per rider; wide-column at very large scale)")
    coll = fresh(db, "ride_trips")
    coll.insert_many([{"rider": f"R{rng.randrange(RIDERS):05d}", "driver": f"D{rng.randrange(DRIVERS):04d}",
                       "requested_at": NOW - dt.timedelta(minutes=rng.randint(0, 90 * 24 * 60)),
                       "fare_inr": rng.randint(60, 900), "status": rng.choice(["completed"] * 9 + ["cancelled"])}
                      for _ in range(TRIPS)])
    query = {"rider": "R00089", "requested_at": {"$gte": NOW - dt.timedelta(days=30)}}
    before = rec.plan("rider's last-30-day trips, NO index", explain_find(coll, query, sort={"requested_at": -1}))
    coll.create_index([("rider", ASCENDING), ("requested_at", DESCENDING)])
    after = rec.plan("same query with {rider:1, requested_at:-1}", explain_find(coll, query, sort={"requested_at": -1}))
    rec.data["trip_history"] = {"trips": TRIPS, "without_index": before, "with_index": after}


def fraud_graph(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("4. Fraud relationships -> Graph")
    links = fresh(db, "ride_links")
    edges = []
    for i in range(RIDERS):                               # ordinary accounts: their own device and card
        edges.append({"from": f"R{i:05d}", "to": f"device-{i:05d}"})
        edges.append({"from": f"R{i:05d}", "to": f"card-{i:05d}"})
    ring = ["R00089", "R00412", "R01777", "R02250", "R02901"]  # planted ring sharing devices and cards in a chain
    shared = ["device-RING-A", "card-RING-B", "device-RING-C", "card-RING-D"]
    for k, hub in enumerate(shared):
        edges.append({"from": ring[k], "to": hub})
        edges.append({"from": ring[k + 1], "to": hub})
    # store both directions so a traversal can go account -> identifier -> account
    links.insert_many(edges + [{"from": e["to"], "to": e["from"]} for e in edges])
    links.create_index("from")
    pipeline = [{"$documents": [{"start": "R00089"}]},
                {"$graphLookup": {"from": "ride_links", "startWith": "$start", "connectFromField": "to",
                                  "connectToField": "from", "as": "hits", "maxDepth": 8, "depthField": "depth"}}]
    started = time.perf_counter()
    hits = list(db.aggregate(pipeline))[0]["hits"]
    ms = round((time.perf_counter() - started) * 1000, 2)
    accounts = sorted({h["to"] for h in hits if h["to"].startswith("R")} | {"R00089"})
    rec.log(f"  traversal from R00089 through shared devices/cards: connected accounts {accounts} "
            f"({len(hits)} edges walked, {ms} ms)")
    normal = list(db.aggregate([{"$documents": [{"start": "R00007"}]}, pipeline[1]]))[0]["hits"]
    rec.log(f"  control: an ordinary account (R00007) connects only to {sorted({h['to'] for h in normal})}")
    rec.data["fraud_graph"] = {"edges": len(edges) * 2, "ring_found": accounts, "ring_planted": sorted(ring),
                               "ms": ms, "control_reach": len(normal)}


def social_analytics(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Part B - social-media analytics")
    posts = fresh(db, "social_posts")
    tags = ["#ipl", "#monsoon", "#exam", "#ai", "#pune", "#cricket", "#music", "#startup"]
    start = NOW - dt.timedelta(hours=24)
    docs = []
    for i in range(12000):
        at = start + dt.timedelta(seconds=rng.randint(0, 24 * 3600 - 1))
        docs.append({"bucket": f"{at:%Y-%m-%dT%H}", "posted_at": at, "user": f"user{rng.randint(1, 5000)}",
                     "tags": rng.sample(tags, 2), "text": "post"})
    posts.insert_many(docs)
    posts.create_index([("bucket", ASCENDING), ("posted_at", ASCENDING)])   # partition = hour, clustering = time
    hour = f"{start + dt.timedelta(hours=10):%Y-%m-%dT%H}"
    part = rec.plan(f"raw layer: all posts in partition {hour}", explain_find(posts, {"bucket": hour}, sort={"posted_at": 1}))
    someone = docs[89]["user"]
    scan = rec.plan(f"raw layer: posts by {someone} (not the partition key)", explain_find(posts, {"user": someone}))

    counters = fresh(db, "social_counters")
    ops = [UpdateOne({"_id": f"{tag}:{d['posted_at']:%Y-%m-%dT%H}"}, {"$inc": {"n": 1}}, upsert=True)
           for d in docs for tag in d["tags"]]
    started = time.perf_counter()
    counters.bulk_write(ops, ordered=False)
    inc_ms = (time.perf_counter() - started) * 1000
    one_inc = time_ms(lambda: counters.update_one({"_id": "#ai:live"}, {"$inc": {"n": 1}}, upsert=True), repeat=50)
    counters.create_index([("n", DESCENDING)])
    top = list(counters.find({"_id": {"$regex": f":{hour}$"}}).sort("n", -1).limit(3))
    rec.log(f"  real-time layer: {len(ops)} counter increments ($inc upsert by key) in {inc_ms:.1f} ms; "
            f"single INCR median {one_inc} ms")
    rec.log(f"  trending in {hour}: " + ", ".join(f"{t['_id'].split(':')[0]}={t['n']}" for t in top))
    rec.data["social"] = {"posts": len(docs), "partition_read": part, "non_partition_read": scan,
                          "increments": len(ops), "increments_ms": round(inc_ms, 2), "single_inc_ms": one_inc,
                          "trending": [(t["_id"], t["n"]) for t in top]}


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_1_2", "Activity 1.2 - Map a Real-World Application (ride-sharing + social analytics)", client)
    rng = random.Random(SEED)
    for step in (driver_positions, profiles, trip_history, fraud_graph, social_analytics):
        step(db, rec, rng)
    d = rec.data
    mapping = [
        ("Live driver positions", "Key-Value + geo (Redis GEO)", f"{d['driver_positions']['burst_write_ms']} ms for 4000 key "
         f"writes; geo query examined {d['driver_positions']['geo_plan']['docsExamined']} docs"),
        ("Rider/driver profiles", "Document (MongoDB)", f"profile by id: {d['profiles']['rider_by_id']['plan']}, "
         f"{d['profiles']['rider_by_id']['docsExamined']} doc"),
        ("Trip history", "Document; Wide-Column at very large scale (Cassandra)",
         f"{d['trip_history']['without_index']['docsExamined']} docs scanned without index -> "
         f"{d['trip_history']['with_index']['docsExamined']} with the compound index"),
        ("Fraud relationships", "Graph (Neo4j / Neptune)", f"ring of {len(d['fraud_graph']['ring_found'])} found "
         f"in one traversal; planted ring matched: {d['fraud_graph']['ring_found'] == d['fraud_graph']['ring_planted']}"),
        ("Social raw post stream", "Wide-Column (Cassandra)", f"hour partition read examined "
         f"{d['social']['partition_read']['docsExamined']} docs vs {d['social']['non_partition_read']['docsExamined']}"),
        ("Social real-time counters", "Key-Value (Redis INCR)", f"single increment median {d['social']['single_inc_ms']} ms"),
    ]
    rec.section("Mapping (each row backed by the measurements above)")
    for domain, family, why in mapping:
        rec.log(f"  {domain:<26} {family:<52} {why}")
    d["mapping"] = [{"domain": a, "family": b, "evidence": c} for a, b, c in mapping]
    rec.save()


if __name__ == "__main__":
    main()
```

### `activity_1_2_try_it_yourself.py`

```python
"""Activity 1.2 (Try it yourself) - Map a second application: a food-delivery platform.

Author: Jovit Chawla (24BTRCL089)

Domains and the family each one maps to:
  restaurant menus         -> Document          nested sections / variants / add-ons, read in one go
  live order state         -> Key-Value + TTL   order id -> current status, expires after delivery
  delivery zones           -> Geospatial        is this address inside a restaurant's delivery polygon?
  order analytics          -> Time-series       orders per hour, revenue per kitchen (column-oriented at scale)
  "also ordered" graph     -> Graph             dishes connected through shared orders
  dish search              -> Search engine     relevance-ranked full text (outside the four classic families:
                                                Elasticsearch / Atlas Search)

All data is synthetic, generated with a fixed seed.
"""
from __future__ import annotations

import datetime as dt
import itertools
import random

from pymongo import ASCENDING, GEOSPHERE, TEXT

from common import Recorder, connect, explain_aggregate, explain_find, fresh, time_ms

SEED = 8924
NOW = dt.datetime(2026, 6, 6, 20, 0, tzinfo=dt.timezone.utc)
JAIPUR = (75.7873, 26.9124)
DISHES = ["paneer butter masala", "dal makhani", "veg biryani", "chicken biryani", "masala dosa", "idli sambar",
          "chole bhature", "pav bhaji", "margherita pizza", "veg burger", "hakka noodles", "gulab jamun",
          "rasmalai", "dal baati churma", "kadhai paneer", "butter naan", "jeera rice", "mango lassi"]


def menus(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Restaurant menus -> Document")
    coll = fresh(db, "food_restaurants")
    docs = []
    for i in range(300):
        sections = []
        for name in rng.sample(["Starters", "Mains", "Breads", "Desserts", "Drinks"], 3):
            items = [{"dish": d, "price": rng.randint(80, 450), "veg": "chicken" not in d,
                      "variants": [{"size": s, "extra": e} for s, e in (("half", 0), ("full", rng.randint(40, 120)))]}
                     for d in rng.sample(DISHES, 4)]
            sections.append({"name": name, "items": items})
        docs.append({"_id": f"REST{i:03d}", "name": f"Kitchen {i}", "cuisine": rng.choice(["North Indian", "South Indian",
                     "Rajasthani", "Chinese", "Fast food"]), "menu": sections,
                     "loc": {"type": "Point", "coordinates": [JAIPUR[0] + rng.uniform(-0.06, 0.06),
                                                              JAIPUR[1] + rng.uniform(-0.06, 0.06)]}})
    coll.insert_many(docs)
    coll.create_index("menu.items.dish")
    whole = rec.plan("open a restaurant page (menu with sections, variants) by id", explain_find(coll, {"_id": "REST089"}))
    nested = rec.plan("restaurants serving 'dal baati churma' under 200 INR",
                      explain_find(coll, {"menu.items": {"$elemMatch": {"dish": "dal baati churma", "price": {"$lt": 200}}}}))
    rec.data["menus"] = {"restaurants": len(docs), "by_id": whole, "nested_query": nested}


def order_state(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Live order state -> Key-Value with TTL")
    coll = fresh(db, "food_order_state")
    coll.create_index("expire_at", expireAfterSeconds=0)
    statuses = ["placed", "accepted", "cooking", "picked_up", "delivered"]
    coll.insert_many([{"_id": f"ORD{i:06d}", "status": rng.choice(statuses), "courier": f"C{rng.randint(1, 400):03d}",
                       "expire_at": NOW + dt.timedelta(hours=2)} for i in range(20000)])
    get = rec.plan("customer app polls ORD000089 status", explain_find(coll, {"_id": "ORD000089"}))
    poll_ms = time_ms(lambda: coll.find_one({"_id": "ORD000089"}, {"status": 1}), repeat=50)
    update_ms = time_ms(lambda: coll.update_one({"_id": "ORD000089"}, {"$set": {"status": "picked_up"}}), repeat=50)
    rec.log(f"  status read median {poll_ms} ms, status write median {update_ms} ms; entries expire 2 h after creation (TTL)")
    rec.data["order_state"] = {"orders": 20000, "get": get, "poll_ms": poll_ms, "update_ms": update_ms}


def delivery_zones(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Delivery zones -> Geospatial")
    zones = fresh(db, "food_zones")
    zones.create_index([("area", GEOSPHERE)])
    lon, lat = JAIPUR
    for i, (dx, dy) in enumerate(itertools.product((-0.04, 0.0, 0.04), repeat=2)):
        cx, cy, r = lon + dx, lat + dy, 0.02
        ring = [[cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r], [cx - r, cy - r]]
        zones.insert_one({"_id": f"ZONE-{i}", "area": {"type": "Polygon", "coordinates": [ring]}, "fee": 20 + 5 * i})
    address = {"type": "Point", "coordinates": [lon + 0.041, lat - 0.035]}
    query = {"area": {"$geoIntersects": {"$geometry": address}}}
    zone = zones.find_one(query)
    plan = rec.plan("which delivery zone contains this address?", explain_find(zones, query))
    rec.log(f"  address falls in {zone['_id'] if zone else 'no zone'} (fee {zone['fee'] if zone else '-'} INR)")
    rests = db["food_restaurants"]
    rests.create_index([("loc", GEOSPHERE)])
    near = rests.count_documents({"loc": {"$geoWithin": {"$centerSphere": [address["coordinates"], 3 / 6378.1]}}})
    rec.log(f"  restaurants within 3 km of the address: {near}")
    rec.data["zones"] = {"zone": zone["_id"] if zone else None, "plan": plan, "restaurants_within_3km": near}


def analytics(db, rec: Recorder, rng: random.Random) -> list[list[str]]:
    rec.section("Order analytics -> Time-series")
    db.drop_collection("food_orders_ts")
    ts = db.create_collection("food_orders_ts", timeseries={"timeField": "at", "metaField": "kitchen", "granularity": "minutes"})
    baskets, orders = [], []
    for _ in range(30000):
        at = NOW - dt.timedelta(minutes=rng.randint(0, 7 * 24 * 60))
        hour = at.hour
        weight = 3 if 12 <= hour <= 14 or 19 <= hour <= 22 else 1          # lunch and dinner peaks
        if rng.random() > weight / 3:
            continue
        basket = rng.sample(DISHES[:8], rng.randint(1, 3)) if rng.random() < 0.7 else rng.sample(DISHES, 2)
        baskets.append(basket)
        orders.append({"at": at, "kitchen": f"REST{rng.randrange(300):03d}", "amount": rng.randint(150, 1400),
                       "items": len(basket)})
    ts.insert_many(orders)
    per_hour = [{"$group": {"_id": {"$hour": "$at"}, "orders": {"$sum": 1}, "revenue": {"$sum": "$amount"}}},
                {"$sort": {"orders": -1}}, {"$limit": 3}]
    busiest = list(ts.aggregate(per_hour))
    rec.plan("busiest hours of the week", explain_aggregate(ts, per_hour))
    rec.log(f"  {len(orders)} orders stored; busiest UTC hours: " +
            ", ".join(f"{b['_id']:02d}:00 ({b['orders']} orders, INR {b['revenue']:,})" for b in busiest))
    rec.data["analytics"] = {"orders": len(orders), "busiest_hours": busiest}
    return baskets


def also_ordered(db, rec: Recorder, baskets: list[list[str]]) -> None:
    rec.section("'Also ordered' recommendations -> Graph")
    edges = fresh(db, "food_dish_edges")
    weight: dict[tuple[str, str], int] = {}
    for basket in baskets:
        for a, b in itertools.permutations(sorted(set(basket)), 2):
            weight[(a, b)] = weight.get((a, b), 0) + 1
    edges.insert_many([{"src": a, "dst": b, "w": w} for (a, b), w in weight.items() if w >= 25])
    edges.create_index("src")
    direct = list(edges.find({"src": "veg biryani"}).sort("w", -1).limit(3))
    two_hop = [{"$documents": [{"dish": "gulab jamun"}]},
               {"$graphLookup": {"from": "food_dish_edges", "startWith": "$dish", "connectFromField": "dst",
                                 "connectToField": "src", "as": "reach", "maxDepth": 1, "depthField": "hop",
                                 "restrictSearchWithMatch": {"w": {"$gte": 25}}}}]
    reach = list(db.aggregate(two_hop))[0]["reach"]
    rec.log(f"  {edges.estimated_document_count()} weighted co-order edges (>= 25 shared orders)")
    rec.log("  ordered with veg biryani: " + ", ".join(f"{e['dst']} ({e['w']})" for e in direct))
    rec.log(f"  gulab jamun reaches {len({r['dst'] for r in reach})} dishes within 2 hops (cross-sell candidates)")
    rec.data["graph"] = {"edges": edges.estimated_document_count(), "with_veg_biryani": [(e["dst"], e["w"]) for e in direct],
                         "gulab_jamun_2hop": len({r["dst"] for r in reach})}


def dish_search(db, rec: Recorder) -> None:
    rec.section("Dish search -> Search engine (relevance ranking)")
    coll = fresh(db, "food_search")
    rests = db["food_restaurants"]
    docs = [{"restaurant": r["_id"], "dish": item["dish"], "section": s["name"],
             "description": f"{item['dish']} from {r['name']}, {r['cuisine']} kitchen"}
            for r in rests.find() for s in r["menu"] for item in s["items"]]
    coll.insert_many(docs)
    coll.create_index([("dish", TEXT), ("description", TEXT)], weights={"dish": 5, "description": 1})
    query = {"$text": {"$search": "paneer masala"}}
    top = list(coll.aggregate([{"$match": query}, {"$group": {"_id": "$dish", "score": {"$max": {"$meta": "textScore"}}}},
                               {"$sort": {"score": -1}}, {"$limit": 4}, {"$project": {"dish": "$_id", "score": 1}}]))
    plan = rec.plan("full-text 'paneer masala' ranked by score", explain_find(coll, query))
    regex = rec.plan("same need with a plain regex (no ranking)", explain_find(coll, {"dish": {"$regex": "paneer|masala"}}))
    rec.log("  top matches: " + ", ".join(f"{t['dish']} ({t['score']:.2f})" for t in top))
    rec.data["search"] = {"documents": len(docs), "text_plan": plan, "regex_plan": regex,
                          "top": [(t["dish"], round(t["score"], 3)) for t in top]}


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_1_2_try_it_yourself", "Activity 1.2 (Try it yourself) - Food-delivery platform", client)
    rng = random.Random(SEED)
    menus(db, rec, rng)
    order_state(db, rec, rng)
    delivery_zones(db, rec, rng)
    baskets = analytics(db, rec, rng)
    also_ordered(db, rec, baskets)
    dish_search(db, rec)
    d = rec.data
    mapping = [
        ("Restaurant menus", "Document", f"whole page in 1 doc ({d['menus']['by_id']['plan']})"),
        ("Live order state", "Key-Value + TTL (Redis)", f"read {d['order_state']['poll_ms']} ms / write "
         f"{d['order_state']['update_ms']} ms by key"),
        ("Delivery zones", "Geospatial (document DB with geo index)", f"point-in-polygon -> {d['zones']['zone']}"),
        ("Order analytics", "Time-series / Wide-Column at scale", f"{d['analytics']['orders']} orders grouped by hour"),
        ("Also-ordered", "Graph (Neo4j)", f"{d['graph']['edges']} weighted edges, 2-hop reach "
         f"{d['graph']['gulab_jamun_2hop']}"),
        ("Dish search", "Search engine (Elasticsearch / Atlas Search)", "relevance score needed; a regex scanned "
         f"{d['search']['regex_plan']['docsExamined']} docs with no ranking"),
    ]
    rec.section("Mapping")
    for domain, family, why in mapping:
        rec.log(f"  {domain:<18} {family:<46} {why}")
    d["mapping"] = [{"domain": a, "family": b, "evidence": c} for a, b, c in mapping]
    rec.save()


if __name__ == "__main__":
    main()
```

### `build_report.py`

```python
"""Build REPORT.md for Lab 1 from the saved results/ files and the source code in this folder.

Author: Jovit Chawla (24BTRCL089). Every number in the generated report is read from results/*.json, which the
activity scripts write after running against MongoDB; this script never invents a value. Re-run it after the
activities (run_all.ps1 does both).
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ACTIVITIES = [
    ("activity_1_1", "activity_1_1_classify_products.py", "Activity 1.1 - Classify Database Products"),
    ("activity_1_1_try_it_yourself", "activity_1_1_try_it_yourself.py", "Activity 1.1 (Try it yourself) - ScyllaDB, InfluxDB, ArangoDB"),
    ("activity_1_2", "activity_1_2_map_application.py", "Activity 1.2 - Map a Real-World Application"),
    ("activity_1_2_try_it_yourself", "activity_1_2_try_it_yourself.py", "Activity 1.2 (Try it yourself) - Food-Delivery Platform"),
]


def load(name: str) -> dict:
    path = RESULTS / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path.name} is missing - run the activity scripts first (run_all.ps1).")
    return json.loads(path.read_text(encoding="utf-8"))


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(out)


def plan_row(label: str, p: dict) -> list:
    return [label, f"`{p['plan']}`", p["nReturned"], p["keysExamined"], p["docsExamined"], p["executionTimeMillis"]]


PLAN_HEADERS = ["Query", "Winning plan", "Returned", "Keys examined", "Docs examined", "Server ms"]


def results_1_1(m: dict) -> str:
    rows = [plan_row("S3: GET by key", m["s3"]["get_by_key"]), plan_row("S3: LIST by prefix", m["s3"]["prefix_list"]),
            plan_row("Memcached: cache GET", m["memcached"]["get_plan"]),
            plan_row("Memcached: recompute", m["memcached"]["recompute_plan"]),
            plan_row("Couchbase: doc by key", m["couchbase"]["by_key"]),
            plan_row("Couchbase: nested array query", m["couchbase"]["nested_query"]),
            plan_row("HBase: row-key range", m["hbase"]["rowkey_range"]),
            plan_row("HBase: non-key column filter", m["hbase"]["non_key_filter"])]
    n = m["neptune"]
    extra = (f"\n\n- S3 content search had to fetch **{m['s3']['content_search']['objects_fetched']}** objects to the client "
             f"({m['s3']['content_search']['ms']} ms) to find {m['s3']['content_search']['hits']} match.\n"
             f"- Memcached-style cache: miss (aggregate + set) **{m['memcached']['miss_ms']} ms**, hit "
             f"**{m['memcached']['hit_ms_median']} ms** (median of 20).\n"
             f"- Neptune-style traversal: `$graphLookup` reached **{n['reached']}** people within 3 hops in one query "
             f"({n['graphlookup_ms']} ms median); the same walk from the application needed {n['app_round_trips']} round "
             f"trips ({n['app_bfs_ms']} ms median on localhost).\n\n")
    verdict = table(["Product", "NoSQL family", "Evidence (measured)"],
                    [[c["product"], c["family"], c["evidence"]] for c in m["classification"]])
    return table(PLAN_HEADERS, rows) + extra + verdict


def results_1_1_tiy(m: dict) -> str:
    s, i = m["scylla"], m["influx"]
    rows = [plan_row("ScyllaDB: one partition, latest 20", s["partition_query"]),
            plan_row("ScyllaDB: no partition key", s["non_partition_query"]),
            plan_row("ArangoDB: key-value get", m["arango"]["kv"]), plan_row("ArangoDB: document query", m["arango"]["document"])]
    g = m["arango"]["graph_plus_document"]
    extra = (f"\n\n- InfluxDB-style time-series: {i['points']:,} points took **{i['timeseries_storage_bytes'] // 1024} KiB** "
             f"in a time-series collection vs **{i['plain_storage_bytes'] // 1024} KiB** as plain documents; one day "
             f"downsampled to {i['hourly_rows']} hourly rows.\n"
             f"- ArangoDB-style combined query: a 2-hop traversal joined to profile documents reached {g['reached']} "
             f"people, {g['same_city']} in Pune ({g['median_ms']} ms median).\n\n")
    verdict = table(["Product", "Classification", "Evidence (measured)"],
                    [[v["product"], v["classification"], v["evidence"]] for v in m["verdict"]])
    return table(PLAN_HEADERS, rows) + extra + verdict


def results_1_2(m: dict) -> str:
    rows = [plan_row("Nearest drivers ($geoNear)", m["driver_positions"]["geo_plan"]),
            plan_row("Rider profile by id", m["profiles"]["rider_by_id"]),
            plan_row("Login by phone", m["profiles"]["by_phone"]),
            plan_row("Trip history, no index", m["trip_history"]["without_index"]),
            plan_row("Trip history, compound index", m["trip_history"]["with_index"]),
            plan_row("Posts in one hour partition", m["social"]["partition_read"]),
            plan_row("Posts by a user (not partition key)", m["social"]["non_partition_read"])]
    f, so = m["fraud_graph"], m["social"]
    extra = (f"\n\n- Driver pings: 4000 key upserts in **{m['driver_positions']['burst_write_ms']} ms**; nearest drivers: "
             + ", ".join(f"{d} ({metres} m)" for d, metres in m["driver_positions"]["nearest"]) + ".\n"
             f"- Fraud graph: traversal from R00089 found {f['ring_found']} (planted ring: {f['ring_planted']}) in "
             f"{f['ms']} ms; an ordinary account reached only {f['control_reach']} nodes.\n"
             f"- Counters: {so['increments']:,} `$inc` upserts in {so['increments_ms']} ms; one increment "
             f"{so['single_inc_ms']} ms median. Trending in the measured hour: " + ", ".join(f"{t.split(':')[0]} = {n}" for t, n in so["trending"]) + ".\n"
             "- Note: `explain` reports the `$geoNear` stage before `$limit`, so its *returned* count is the number of "
             "candidates the geo stage produced, not the 5 drivers the query returned.\n\n")
    mapping = table(["Data domain", "NoSQL family", "Evidence (measured)"],
                    [[r["domain"], r["family"], r["evidence"]] for r in m["mapping"]])
    return table(PLAN_HEADERS, rows) + extra + mapping


def results_1_2_tiy(m: dict) -> str:
    rows = [plan_row("Restaurant page by id", m["menus"]["by_id"]), plan_row("Dish under price (nested)", m["menus"]["nested_query"]),
            plan_row("Order status by id", m["order_state"]["get"]), plan_row("Point-in-zone", m["zones"]["plan"]),
            plan_row("Full-text search", m["search"]["text_plan"]), plan_row("Regex search", m["search"]["regex_plan"])]
    busiest = ", ".join(f"{b['_id']:02d}:00 UTC ({b['orders']} orders)" for b in m["analytics"]["busiest_hours"])
    extra = (f"\n\n- Order status: read {m['order_state']['poll_ms']} ms, write {m['order_state']['update_ms']} ms (median of 50).\n"
             f"- Address mapped to **{m['zones']['zone']}**; {m['zones']['restaurants_within_3km']} restaurants within 3 km.\n"
             f"- {m['analytics']['orders']:,} orders; busiest hours: {busiest}.\n"
             "- Ordered with veg biryani: " + ", ".join(f"{d} ({w})" for d, w in m["graph"]["with_veg_biryani"]) + ".\n"
             "- Search ranking for 'paneer masala': " + ", ".join(f"{d} ({s})" for d, s in m["search"]["top"]) + ".\n\n")
    mapping = table(["Data domain", "Family / technology", "Evidence (measured)"],
                    [[r["domain"], r["family"], r["evidence"]] for r in m["mapping"]])
    return table(PLAN_HEADERS, rows) + extra + mapping


RESULT_BUILDERS = {"activity_1_1": results_1_1, "activity_1_1_try_it_yourself": results_1_1_tiy,
                   "activity_1_2": results_1_2, "activity_1_2_try_it_yourself": results_1_2_tiy}

HEADER = """# Lab 1 - Introduction to NoSQL: Families, Products and Data-Domain Mapping

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 1 activities) |
| **Executed on** | {run_at} on `{machine}`, {os} |
| **Software** | MongoDB {mongo} ({topology}), Python {python}, pymongo {pymongo} |

> **Own work.** All code, datasets and measurements in this folder (`lab-1/jovit_24BTRCL089/`) are my own and were
> produced by running the scripts below on my machine. The files in the parent `lab-1/` folder are a reference
> submission by another student; they were read only to understand what the activities ask for, and nothing from
> them (code, data, results or credentials) is reused here.

## 1. Experiment title

Classifying NoSQL database products into families and mapping application data domains to the right NoSQL model,
with every claim demonstrated on a live MongoDB server.

## 2. Aim

To understand the four classic NoSQL families (key-value, document, wide-column, graph) and the newer specialised
and multi-model systems, by reproducing each family's characteristic access pattern in MongoDB and measuring it.

## 3. Objectives

1. Classify Amazon S3, Memcached, Couchbase, Apache HBase and Amazon Neptune into NoSQL families (Activity 1.1).
2. Decide whether ScyllaDB, InfluxDB and ArangoDB fit one family or span several (Activity 1.1, Try it yourself).
3. Map the data domains of a ride-sharing app and a social-media analytics platform to families (Activity 1.2).
4. Repeat the mapping for an application of my own choice - a food-delivery platform (Activity 1.2, Try it yourself).
5. Justify every answer with measured query plans (`explain("executionStats")`) and timings, not assertions.

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | {os} |
| Machine | `{machine}` (laptop) |
| Database server | MongoDB Community {mongo}, {topology}, `mongodb://localhost:27017` |
| Language / driver | Python {python}, pymongo {pymongo} (`pip install -r requirements.txt`) |
| Database used | `jovit_lab01` (only this lab's own collections are dropped and recreated) |
| Optional | MongoDB Compass to browse the collections |

## 5. Theory

**Why NoSQL.** Relational databases store data in fixed-schema tables and scale mainly vertically. NoSQL systems
relax one or more of these constraints - schema, joins, strong consistency - to gain horizontal scale, flexible data
shapes or very low latency for a specific access pattern. They are usually grouped into four families:

- **Key-value stores** (Redis, Memcached, DynamoDB, Amazon S3 as an object store) map an opaque key to a value.
  Reads and writes by key are O(1)-like and extremely fast, but the store cannot look *inside* the value, so any
  query on content requires reading every value. Memcached keeps values only in memory with an expiry (TTL), which
  makes it a cache rather than a system of record.
- **Document stores** (MongoDB, Couchbase) store self-describing JSON/BSON documents. Related data is embedded
  (arrays, sub-documents), so one read returns a whole entity; documents in one collection may have different
  fields; secondary and multikey indexes allow queries on nested fields.
- **Wide-column (column-family) stores** (HBase, Cassandra, ScyllaDB) organise data by a row/partition key kept in
  sorted order, with columns grouped into families that can be sparse. Range scans on the key are cheap; queries on
  non-key columns are not supported efficiently, so tables are designed around the query.
- **Graph databases** (Neo4j, Amazon Neptune) store nodes and edges and traverse relationships directly
  ("index-free adjacency"), which makes multi-hop questions (friends-of-friends, fraud rings, recommendations)
  natural to express.

Beyond the four: **time-series databases** (InfluxDB, TimescaleDB, MongoDB time-series collections) are optimised
for append-only timestamped measurements - columnar compression, time bucketing and downsampling. **Multi-model**
databases (ArangoDB, Cosmos DB) expose several models over one engine. **Search engines** (Elasticsearch, Atlas
Search) rank results by relevance, which none of the four families do.

**How this lab demonstrates the families with one database.** MongoDB can imitate each access pattern: `_id`
lookups behave like a key-value store, nested documents and multikey indexes like a document store, a sorted
string row key with sparse sub-documents like a wide-column table, an edge collection with `$graphLookup` like a
graph, and time-series collections like a TSDB. Comparing the query plans - `EXPRESS_IXSCAN` (direct key lookup),
`IXSCAN` (index range), `COLLSCAN` (full scan) - and the keys/documents examined shows *which access pattern each
data shape makes cheap*, and that is exactly what decides which family a product belongs to.

## 6. Procedure

1. Start the MongoDB service (`Get-Service MongoDB` shows *Running*) or set `MONGO_URI` in a git-ignored `.env`
   created from `.env.example`.
2. Install the driver: `python -m pip install -r requirements.txt`.
3. Run each activity from this folder (or all at once with `run_all.ps1`):
   `python activity_1_1_classify_products.py`, `python activity_1_1_try_it_yourself.py`,
   `python activity_1_2_map_application.py`, `python activity_1_2_try_it_yourself.py`.
4. Each script generates its synthetic dataset (fixed random seed), creates the indexes, runs the queries, calls
   `explain` with `executionStats`, prints the measurements and saves them to `results/<activity>.json` and
   `results/<activity>.output.txt`.
5. Run `python build_report.py` to regenerate this report from the saved results.
6. Optionally open the `jovit_lab01` database in MongoDB Compass to inspect the collections.
"""

FOOTER = """
## 9. Results

{results}

## 10. Conclusion

{conclusion}
"""


def graph_sentence(n: dict) -> str:
    server, app = n["graphlookup_ms"], n["app_bfs_ms"]
    if server < app * 0.8:
        comparison = f"faster than the hop-by-hop application loop ({server} ms vs {app} ms)"
    elif server > app * 1.25:
        comparison = (f"slower than the hop-by-hop application loop on this small local graph ({server} ms vs {app} "
                      "ms), so the advantage measured here is one declarative query instead of several round trips")
    else:
        comparison = f"about as fast as the hop-by-hop application loop on this small local graph ({server} ms vs {app} ms)"
    return (f"- **Graph (Neptune):** a 3-hop relationship question was one server-side traversal reaching {n['reached']} "
            f"people, {comparison}; the application loop needed {n['app_round_trips']} round trips, a cost that grows "
            "with graph depth and network latency.")


def conclusion(data: dict[str, dict]) -> str:
    a = data["activity_1_1"]["measurements"]
    b = data["activity_1_1_try_it_yourself"]["measurements"]
    c = data["activity_1_2"]["measurements"]
    e = data["activity_1_2_try_it_yourself"]["measurements"]
    return "\n".join([
        f"- **Key-value (S3, Memcached):** a key lookup examined {a['s3']['get_by_key']['docsExamined']} document, while "
        f"searching object contents meant fetching all {a['s3']['content_search']['objects_fetched']} objects. A cached "
        f"result was served in {a['memcached']['hit_ms_median']} ms instead of {a['memcached']['miss_ms']} ms.",
        f"- **Document (Couchbase):** {a['couchbase']['distinct_shapes']} different document shapes lived in one "
        "collection and nested-array queries were answered from a multikey index.",
        f"- **Wide-column (HBase, ScyllaDB):** key-range reads examined only the rows returned "
        f"({a['hbase']['rowkey_range']['keysExamined']} keys for {a['hbase']['rowkey_range']['nReturned']} rows), while "
        f"filters on non-key columns scanned the whole table ({a['hbase']['non_key_filter']['docsExamined']} rows) - "
        "so ScyllaDB fits the single wide-column family.",
        graph_sentence(a["neptune"]),
        f"- **Beyond the four:** a time-series layout stored the same metrics in "
        f"{b['influx']['timeseries_storage_bytes'] // 1024} KiB instead of {b['influx']['plain_storage_bytes'] // 1024} "
        "KiB (InfluxDB is a time-series system); ArangoDB-style multi-model queries combined graph and document access "
        "in one query; relevance-ranked dish search needed a text index, i.e. a search engine.",
        f"- **Application mapping:** for ride-sharing, live positions suit a key-value/geo cache, profiles a document "
        f"store, trip history a document store (wide-column at very large scale - the compound index cut "
        f"{c['trip_history']['without_index']['docsExamined']} scanned documents to "
        f"{c['trip_history']['with_index']['docsExamined']}), and fraud detection a graph (the planted ring was found "
        f"exactly: {c['fraud_graph']['ring_found'] == c['fraud_graph']['ring_planted']}). The food-delivery platform "
        f"needed six technologies for six domains ({', '.join(r['family'].split(' (')[0] for r in e['mapping'])}), "
        "which shows why real systems are usually polyglot.",
    ])


def main() -> None:
    data = {name: load(name) for name, _, _ in ACTIVITIES}
    env = data["activity_1_1"]["environment"]
    parts = [HEADER.format(run_at=env["run_at_utc"], machine=env["machine"], os=env["os"], mongo=env["mongodb_server"],
                           topology=env["topology"], python=env["python"], pymongo=env["pymongo"])]
    parts.append("\n## 7. Source code\n\nThe complete source of every file used in this lab.\n")
    for file in ["common.py"] + [script for _, script, _ in ACTIVITIES] + ["build_report.py"]:
        parts.append(f"\n### `{file}`\n\n```python\n{(HERE / file).read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 8. Execution output\n\nConsole output captured from the actual runs (`results/*.output.txt`).\n")
    for name, _, title in ACTIVITIES:
        output = (RESULTS / f"{name}.output.txt").read_text(encoding="utf-8").rstrip()
        parts.append(f"\n### {title}\n\n```text\n{output}\n```\n")
    results = "\n\n".join(f"### {title}\n\n{RESULT_BUILDERS[name](data[name]['measurements'])}"
                          for name, _, title in ACTIVITIES)
    parts.append(FOOTER.format(results=results, conclusion=conclusion(data)))
    (HERE / "REPORT.md").write_text("".join(parts), encoding="utf-8")
    print(f"Wrote REPORT.md ({sum(len(p) for p in parts):,} characters)")


if __name__ == "__main__":
    main()
```

## 8. Execution output

Console output captured from the actual runs (`results/*.output.txt`).

### Activity 1.1 - Classify Database Products

```text
Activity 1.1 - Classify Database Products
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:15:15+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0

== Amazon S3 -> key-value object store ==
  stored 3000 objects in 3 buckets (6038 KiB of blobs)
  GET object by full key: plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  LIST keys with prefix 'assignments/2026/': plan=PROJECTION_COVERED <- IXSCAN pipeline=- returned=600 keysExamined=601 docsExamined=0 serverMs=13
  search INSIDE blob bodies for a byte marker: fetched 3000 objects client-side, found 1, 25.07 ms (no server-side index can help)

== Memcached -> in-memory key-value cache (cache-aside with TTL) ==
  TTL index: {expires_at: 1} expireAfterSeconds=0 (mongod deletes expired keys)
  cache GET by key: plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  recompute aggregation over grades: plan=GROUP <- COLLSCAN pipeline=['$cursor', '$sort'] returned=40 keysExamined=0 docsExamined=30000 serverMs=41
  cache miss (aggregate 30000 grades + SET): 29.246 ms, first call hit=False
  cache hit (GET by key, median of 20): 0.662 ms -> 44.2x faster

== Couchbase -> document store (nested JSON, flexible schema) ==
  2500 course documents with 4 different top-level shapes in ONE collection
  fetch whole course (nested arrays included) by key: plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  query INSIDE nested array: Wed classes in LAB-3: plan=FETCH <- IXSCAN pipeline=- returned=200 keysExamined=200 docsExamined=200 serverMs=3
  query an optional field: lab courses with a GPU: plan=COLLSCAN pipeline=- returned=407 keysExamined=0 docsExamined=2500 serverMs=3
  atomic in-place update of a nested array ($push): modified=1

== Apache HBase -> wide-column store (sorted row key, sparse column families) ==
  6720 rows; families present: env=6720, power=2688, alarm=144
  row-key RANGE scan: building 3 on 2026-03-04: plan=FETCH <- IXSCAN pipeline=- returned=96 keysExamined=96 docsExamined=96 serverMs=0
  rows come back in row-key order, first row key: bldg-03#2026-03-04T00:00
  filter on a NON-key column (env.temp_c > 33): plan=COLLSCAN pipeline=- returned=432 keysExamined=0 docsExamined=6720 serverMs=4

== Amazon Neptune -> graph database (multi-hop relationship traversal) ==
  1597 'knows' edges between 400 people
  $graphLookup from P007, 3 hops in ONE server-side query: 73 people reached (new per hop {0: 4, 1: 13, 2: 56}), median 3.303 ms
  same traversal done hop-by-hop by the application: 73 people, 3 round trips, median 5.979 ms

== Classification (each backed by the measurements above) ==
  Amazon S3       Key-Value (object store)       evidence: key GET examined 1 doc; body search had to fetch all 3000 objects
  Memcached       Key-Value (in-memory cache)    evidence: hit 0.662 ms vs recompute 29.246 ms; TTL expiry
  Couchbase       Document                       evidence: 4 document shapes in one collection; nested query used FETCH <- IXSCAN
  Apache HBase    Wide-Column (column-family)    evidence: row-key range examined 96 keys for 96 rows; non-key filter examined 6720 docs
  Amazon Neptune  Graph                          evidence: 73 people within 3 hops in 1 query vs 3 app round trips
```

### Activity 1.1 (Try it yourself) - ScyllaDB, InfluxDB, ArangoDB

```text
Activity 1.1 (Try it yourself) - ScyllaDB, InfluxDB, ArangoDB
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:15:18+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0

== ScyllaDB -> wide-column: PRIMARY KEY ((channel_id), sent_at DESC) ==
  latest 20 messages of one partition (ch-042): plan=LIMIT <- FETCH <- IXSCAN pipeline=- returned=20 keysExamined=20 docsExamined=20 serverMs=1
  same question without the partition key (by author): plan=SORT <- COLLSCAN pipeline=- returned=19 keysExamined=0 docsExamined=60000 serverMs=85
  partition query needed no SORT stage (clustering order served by the index): True

== InfluxDB -> time-series: measurement, tags, fields, timestamp ==
  100800 points from 20 hosts | on-disk storage: time-series 404 KiB vs plain collection 3244 KiB (8.0x smaller); points packed into 140 buckets
  1-hour downsample of web-07 for one day: plan=GROUP <- UNPACK_TS_BUCKET <- CLUSTERED_IXSCAN pipeline=['$cursor', '$sort'] returned=24 keysExamined=0 docsExamined=20 serverMs=4
  downsampled 720 raw points into 24 hourly rows; first: 2026-04-01 00:00 mean=53.87 max=94.35
  5-point moving average (window function), last value: 79.33

== ArangoDB -> multi-model: key-value + document + graph in one engine ==
  KEY-VALUE: get u0089 by key: plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  DOCUMENT: users in Pune who know mongodb: plan=FETCH <- IXSCAN pipeline=- returned=115 keysExamined=306 docsExamined=306 serverMs=3
  GRAPH + DOCUMENT in ONE query: u0089 reaches 12 people within 2 hops, 3 of them live in Pune (median 3.598 ms)

== Verdict ==
  ScyllaDB  Single family: Wide-Column                              partition query examined 20 keys, no SORT; without the key: 60000 docs scanned
  InfluxDB  Time-series (specialised; not one of the classic four)  time-series storage 404 KiB vs 3244 KiB plain; native downsampling
  ArangoDB  Multi-model: Key-Value + Document + Graph               the same data answered a key lookup, a nested-document query and a traversal joined to documents
```

### Activity 1.2 - Map a Real-World Application

```text
Activity 1.2 - Map a Real-World Application (ride-sharing + social analytics)
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:15:26+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0

== 1. Live driver positions -> Key-Value / geospatial cache ==
  4000 position upserts by key in one bulk write: 1050.6 ms (3,807 writes/s)
  5 nearest available drivers within 3 km: plan=FETCH <- GEO_NEAR_2DSPHERE <- FETCH <- IXSCAN pipeline=['$geoNearCursor', '$limit'] returned=32 keysExamined=176 docsExamined=153 serverMs=3
  nearest: D0296 113 m, D0142 351 m, D0388 373 m, D0189 536 m, D0447 966 m

== 2. Rider and driver profiles -> Document ==
  whole rider profile by id (places + payment in one doc): plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  whole driver profile by id (vehicle + documents): plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  login lookup by phone (unique secondary index): plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=4

== 3. Trip history -> Document (time-ordered per rider; wide-column at very large scale) ==
  rider's last-30-day trips, NO index: plan=SORT <- COLLSCAN pipeline=- returned=4 keysExamined=0 docsExamined=25000 serverMs=25
  same query with {rider:1, requested_at:-1}: plan=FETCH <- IXSCAN pipeline=- returned=4 keysExamined=4 docsExamined=4 serverMs=3

== 4. Fraud relationships -> Graph ==
  traversal from R00089 through shared devices/cards: connected accounts ['R00089', 'R00412', 'R01777', 'R02250', 'R02901'] (34 edges walked, 8.42 ms)
  control: an ordinary account (R00007) connects only to ['R00007', 'card-00007', 'device-00007']

== Part B - social-media analytics ==
  raw layer: all posts in partition 2026-05-10T04: plan=FETCH <- IXSCAN pipeline=- returned=524 keysExamined=524 docsExamined=524 serverMs=4
  raw layer: posts by user890 (not the partition key): plan=COLLSCAN pipeline=- returned=3 keysExamined=0 docsExamined=12000 serverMs=12
  real-time layer: 24000 counter increments ($inc upsert by key) in 2321.0 ms; single INCR median 0.586 ms
  trending in 2026-05-10T04: #ipl=149, #monsoon=144, #pune=139

== Mapping (each row backed by the measurements above) ==
  Live driver positions      Key-Value + geo (Redis GEO)                          1050.62 ms for 4000 key writes; geo query examined 153 docs
  Rider/driver profiles      Document (MongoDB)                                   profile by id: EXPRESS_IXSCAN, 1 doc
  Trip history               Document; Wide-Column at very large scale (Cassandra) 25000 docs scanned without index -> 4 with the compound index
  Fraud relationships        Graph (Neo4j / Neptune)                              ring of 5 found in one traversal; planted ring matched: True
  Social raw post stream     Wide-Column (Cassandra)                              hour partition read examined 524 docs vs 12000
  Social real-time counters  Key-Value (Redis INCR)                               single increment median 0.586 ms
```

### Activity 1.2 (Try it yourself) - Food-Delivery Platform

```text
Activity 1.2 (Try it yourself) - Food-delivery platform
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:15:32+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0

== Restaurant menus -> Document ==
  open a restaurant page (menu with sections, variants) by id: plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  restaurants serving 'dal baati churma' under 200 INR: plan=FETCH <- IXSCAN pipeline=- returned=59 keysExamined=155 docsExamined=155 serverMs=16

== Live order state -> Key-Value with TTL ==
  customer app polls ORD000089 status: plan=EXPRESS_IXSCAN pipeline=- returned=1 keysExamined=1 docsExamined=1 serverMs=0
  status read median 1.159 ms, status write median 1.145 ms; entries expire 2 h after creation (TTL)

== Delivery zones -> Geospatial ==
  which delivery zone contains this address?: plan=FETCH <- IXSCAN pipeline=- returned=1 keysExamined=5 docsExamined=1 serverMs=0
  address falls in ZONE-6 (fee 50 INR)
  restaurants within 3 km of the address: 55

== Order analytics -> Time-series ==
  busiest hours of the week: plan=GROUP <- UNPACK_TS_BUCKET <- COLLSCAN pipeline=['$cursor', '$sort'] returned=24 keysExamined=0 docsExamined=2030 serverMs=30
  15790 orders stored; busiest UTC hours: 20:00 (1321 orders, INR 1,026,052), 19:00 (1270 orders, INR 984,887), 13:00 (1259 orders, INR 963,178)

== 'Also ordered' recommendations -> Graph ==
  276 weighted co-order edges (>= 25 shared orders)
  ordered with veg biryani: chole bhature (615), paneer butter masala (563), dal makhani (555)
  gulab jamun reaches 18 dishes within 2 hops (cross-sell candidates)

== Dish search -> Search engine (relevance ranking) ==
  full-text 'paneer masala' ranked by score: plan=TEXT_MATCH <- FETCH <- OR <- IXSCAN pipeline=- returned=622 keysExamined=838 docsExamined=622 serverMs=3
  same need with a plain regex (no ranking): plan=COLLSCAN pipeline=- returned=622 keysExamined=0 docsExamined=3600 serverMs=4
  top matches: paneer butter masala (7.81), kadhai paneer (4.33), masala dosa (4.33)

== Mapping ==
  Restaurant menus   Document                                       whole page in 1 doc (EXPRESS_IXSCAN)
  Live order state   Key-Value + TTL (Redis)                        read 1.159 ms / write 1.145 ms by key
  Delivery zones     Geospatial (document DB with geo index)        point-in-polygon -> ZONE-6
  Order analytics    Time-series / Wide-Column at scale             15790 orders grouped by hour
  Also-ordered       Graph (Neo4j)                                  276 weighted edges, 2-hop reach 18
  Dish search        Search engine (Elasticsearch / Atlas Search)   relevance score needed; a regex scanned 3600 docs with no ranking
```

## 9. Results

### Activity 1.1 - Classify Database Products

| Query | Winning plan | Returned | Keys examined | Docs examined | Server ms |
|---|---|---|---|---|---|
| S3: GET by key | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 0 |
| S3: LIST by prefix | `PROJECTION_COVERED <- IXSCAN` | 600 | 601 | 0 | 13 |
| Memcached: cache GET | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 0 |
| Memcached: recompute | `GROUP <- COLLSCAN` | 40 | 0 | 30000 | 41 |
| Couchbase: doc by key | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 0 |
| Couchbase: nested array query | `FETCH <- IXSCAN` | 200 | 200 | 200 | 3 |
| HBase: row-key range | `FETCH <- IXSCAN` | 96 | 96 | 96 | 0 |
| HBase: non-key column filter | `COLLSCAN` | 432 | 0 | 6720 | 4 |

- S3 content search had to fetch **3000** objects to the client (25.07 ms) to find 1 match.
- Memcached-style cache: miss (aggregate + set) **29.246 ms**, hit **0.662 ms** (median of 20).
- Neptune-style traversal: `$graphLookup` reached **73** people within 3 hops in one query (3.303 ms median); the same walk from the application needed 3 round trips (5.979 ms median on localhost).

| Product | NoSQL family | Evidence (measured) |
|---|---|---|
| Amazon S3 | Key-Value (object store) | key GET examined 1 doc; body search had to fetch all 3000 objects |
| Memcached | Key-Value (in-memory cache) | hit 0.662 ms vs recompute 29.246 ms; TTL expiry |
| Couchbase | Document | 4 document shapes in one collection; nested query used FETCH <- IXSCAN |
| Apache HBase | Wide-Column (column-family) | row-key range examined 96 keys for 96 rows; non-key filter examined 6720 docs |
| Amazon Neptune | Graph | 73 people within 3 hops in 1 query vs 3 app round trips |

### Activity 1.1 (Try it yourself) - ScyllaDB, InfluxDB, ArangoDB

| Query | Winning plan | Returned | Keys examined | Docs examined | Server ms |
|---|---|---|---|---|---|
| ScyllaDB: one partition, latest 20 | `LIMIT <- FETCH <- IXSCAN` | 20 | 20 | 20 | 1 |
| ScyllaDB: no partition key | `SORT <- COLLSCAN` | 19 | 0 | 60000 | 85 |
| ArangoDB: key-value get | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 0 |
| ArangoDB: document query | `FETCH <- IXSCAN` | 115 | 306 | 306 | 3 |

- InfluxDB-style time-series: 100,800 points took **404 KiB** in a time-series collection vs **3244 KiB** as plain documents; one day downsampled to 24 hourly rows.
- ArangoDB-style combined query: a 2-hop traversal joined to profile documents reached 12 people, 3 in Pune (3.598 ms median).

| Product | Classification | Evidence (measured) |
|---|---|---|
| ScyllaDB | Single family: Wide-Column | partition query examined 20 keys, no SORT; without the key: 60000 docs scanned |
| InfluxDB | Time-series (specialised; not one of the classic four) | time-series storage 404 KiB vs 3244 KiB plain; native downsampling |
| ArangoDB | Multi-model: Key-Value + Document + Graph | the same data answered a key lookup, a nested-document query and a traversal joined to documents |

### Activity 1.2 - Map a Real-World Application

| Query | Winning plan | Returned | Keys examined | Docs examined | Server ms |
|---|---|---|---|---|---|
| Nearest drivers ($geoNear) | `FETCH <- GEO_NEAR_2DSPHERE <- FETCH <- IXSCAN` | 32 | 176 | 153 | 3 |
| Rider profile by id | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 0 |
| Login by phone | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 4 |
| Trip history, no index | `SORT <- COLLSCAN` | 4 | 0 | 25000 | 25 |
| Trip history, compound index | `FETCH <- IXSCAN` | 4 | 4 | 4 | 3 |
| Posts in one hour partition | `FETCH <- IXSCAN` | 524 | 524 | 524 | 4 |
| Posts by a user (not partition key) | `COLLSCAN` | 3 | 0 | 12000 | 12 |

- Driver pings: 4000 key upserts in **1050.62 ms**; nearest drivers: D0296 (113 m), D0142 (351 m), D0388 (373 m), D0189 (536 m), D0447 (966 m).
- Fraud graph: traversal from R00089 found ['R00089', 'R00412', 'R01777', 'R02250', 'R02901'] (planted ring: ['R00089', 'R00412', 'R01777', 'R02250', 'R02901']) in 8.42 ms; an ordinary account reached only 4 nodes.
- Counters: 24,000 `$inc` upserts in 2321.05 ms; one increment 0.586 ms median. Trending in the measured hour: #ipl = 149, #monsoon = 144, #pune = 139.
- Note: `explain` reports the `$geoNear` stage before `$limit`, so its *returned* count is the number of candidates the geo stage produced, not the 5 drivers the query returned.

| Data domain | NoSQL family | Evidence (measured) |
|---|---|---|
| Live driver positions | Key-Value + geo (Redis GEO) | 1050.62 ms for 4000 key writes; geo query examined 153 docs |
| Rider/driver profiles | Document (MongoDB) | profile by id: EXPRESS_IXSCAN, 1 doc |
| Trip history | Document; Wide-Column at very large scale (Cassandra) | 25000 docs scanned without index -> 4 with the compound index |
| Fraud relationships | Graph (Neo4j / Neptune) | ring of 5 found in one traversal; planted ring matched: True |
| Social raw post stream | Wide-Column (Cassandra) | hour partition read examined 524 docs vs 12000 |
| Social real-time counters | Key-Value (Redis INCR) | single increment median 0.586 ms |

### Activity 1.2 (Try it yourself) - Food-Delivery Platform

| Query | Winning plan | Returned | Keys examined | Docs examined | Server ms |
|---|---|---|---|---|---|
| Restaurant page by id | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 0 |
| Dish under price (nested) | `FETCH <- IXSCAN` | 59 | 155 | 155 | 16 |
| Order status by id | `EXPRESS_IXSCAN` | 1 | 1 | 1 | 0 |
| Point-in-zone | `FETCH <- IXSCAN` | 1 | 5 | 1 | 0 |
| Full-text search | `TEXT_MATCH <- FETCH <- OR <- IXSCAN` | 622 | 838 | 622 | 3 |
| Regex search | `COLLSCAN` | 622 | 0 | 3600 | 4 |

- Order status: read 1.159 ms, write 1.145 ms (median of 50).
- Address mapped to **ZONE-6**; 55 restaurants within 3 km.
- 15,790 orders; busiest hours: 20:00 UTC (1321 orders), 19:00 UTC (1270 orders), 13:00 UTC (1259 orders).
- Ordered with veg biryani: chole bhature (615), paneer butter masala (563), dal makhani (555).
- Search ranking for 'paneer masala': paneer butter masala (7.81), kadhai paneer (4.333), masala dosa (4.333).

| Data domain | Family / technology | Evidence (measured) |
|---|---|---|
| Restaurant menus | Document | whole page in 1 doc (EXPRESS_IXSCAN) |
| Live order state | Key-Value + TTL (Redis) | read 1.159 ms / write 1.145 ms by key |
| Delivery zones | Geospatial (document DB with geo index) | point-in-polygon -> ZONE-6 |
| Order analytics | Time-series / Wide-Column at scale | 15790 orders grouped by hour |
| Also-ordered | Graph (Neo4j) | 276 weighted edges, 2-hop reach 18 |
| Dish search | Search engine (Elasticsearch / Atlas Search) | relevance score needed; a regex scanned 3600 docs with no ranking |

## 10. Conclusion

- **Key-value (S3, Memcached):** a key lookup examined 1 document, while searching object contents meant fetching all 3000 objects. A cached result was served in 0.662 ms instead of 29.246 ms.
- **Document (Couchbase):** 4 different document shapes lived in one collection and nested-array queries were answered from a multikey index.
- **Wide-column (HBase, ScyllaDB):** key-range reads examined only the rows returned (96 keys for 96 rows), while filters on non-key columns scanned the whole table (6720 rows) - so ScyllaDB fits the single wide-column family.
- **Graph (Neptune):** a 3-hop relationship question was one server-side traversal reaching 73 people, faster than the hop-by-hop application loop (3.303 ms vs 5.979 ms); the application loop needed 3 round trips, a cost that grows with graph depth and network latency.
- **Beyond the four:** a time-series layout stored the same metrics in 404 KiB instead of 3244 KiB (InfluxDB is a time-series system); ArangoDB-style multi-model queries combined graph and document access in one query; relevance-ranked dish search needed a text index, i.e. a search engine.
- **Application mapping:** for ride-sharing, live positions suit a key-value/geo cache, profiles a document store, trip history a document store (wide-column at very large scale - the compound index cut 25000 scanned documents to 4), and fraud detection a graph (the planted ring was found exactly: True). The food-delivery platform needed six technologies for six domains (Document, Key-Value + TTL, Geospatial, Time-series / Wide-Column at scale, Graph, Search engine), which shows why real systems are usually polyglot.
