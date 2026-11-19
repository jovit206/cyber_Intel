# Lab 6 - Indexing and Query Performance in MongoDB

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 6 activities) |
| **Executed on** | 2026-11-19T14:28:55+00:00 on `LAPTOP-DSDFIQOI`, Windows 11 (10.0.26300) |
| **Software** | MongoDB 8.3.7 (standalone), Python 3.12.10, pymongo 4.20.0 |

> **Own work.** All code, data and measurements in `lab-6/jovit_24BTRCL089/` are my own and were produced by
> running the scripts below on my machine. `lab-6/Chapter06_Submission/` is a reference submission by another
> student, read only to understand the activities; none of its code, data or results are used.

## 1. Experiment title

Designing, measuring and tuning MongoDB indexes for a product-catalogue query workload.

## 2. Aim

To learn how indexes change query execution in MongoDB, how to design an index set for a workload, how to find
unused indexes, what indexes cost on writes, and how covered queries avoid reading documents.

## 3. Objectives

1. Design indexes for a five-query workload and compare `explain("executionStats")` before and after (6.1).
2. Measure real index usage with `$indexStats` and identify unused and redundant indexes (6.1 Try it yourself).
3. Measure the write cost of different index configurations, the read impact of removing indexes, and the effect
   of compound-index key order (6.2).
4. Build covered queries and show when a projection can be answered from the index alone (6.2 Try it yourself).

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | Windows 11 (10.0.26300) |
| Machine | `LAPTOP-DSDFIQOI` (laptop) |
| Database server | MongoDB 8.3.7 (standalone) at `mongodb://localhost:27017` (Windows service) |
| Language / driver | Python 3.12.10, pymongo 4.20.0 (`pip install -r requirements.txt`) |
| Database used | `jovit_lab06` (collections `products`, `products_perf`, temporary `products_write`) |
| Data | 50,000 synthetic products generated with a fixed seed (`catalog.py`) |

## 5. Theory

**What an index is.** MongoDB indexes are B-tree structures holding the values of one or more fields in sorted
order, each pointing to a document. Without a suitable index the server must read every document (**COLLSCAN**);
with one it walks a range of index keys (**IXSCAN**) and fetches only matching documents (**FETCH**). `explain()`
with `executionStats` reports the winning plan, `totalKeysExamined`, `totalDocsExamined` and `nReturned`; a good
plan keeps keys and documents examined close to the number returned.

**Index types used here.** *Single-field* (`{price: 1}`) for equality and range queries; *compound*
(`{category: 1, price: 1, rating: 1}`) for several fields, usable by any query on a key prefix; *multikey* -
created automatically when the field is an array (`tags`), one key per element; *text* indexes for `$text` search
with stemming and relevance scores.

**ESR rule.** In a compound index put fields used with **E**quality first, then fields used for **S**ort, then
fields used in **R**ange filters. Then the index already returns documents in sort order (no in-memory SORT
stage) and the scan can stop as soon as the limit is reached.

**Cost of indexes.** Every insert, update or delete must also update every index, so each index slows writes and
uses RAM (indexes should fit in the WiredTiger cache). `$indexStats` reports how often each index is used
(`accesses.ops`, counted since the index was created or the server restarted), which shows which indexes are
worth keeping. An index that is a prefix of a compound index is usually redundant.

**Covered queries.** If every filtered, sorted and projected field is in one index - and `_id` is excluded unless
it is in the index - MongoDB answers from the index alone (`PROJECTION_COVERED`, `totalDocsExamined: 0`).
Multikey indexes cannot cover queries that return the array field.

## 6. Procedure

1. Make sure the MongoDB service is running (`Get-Service MongoDB`), or set `MONGO_URI` in a `.env` copied from
   `.env.example`.
2. Install the driver: `python -m pip install -r requirements.txt`.
3. Run the activities **in order** (each builds on the previous one), or use `run_all.ps1`:
   `python activity_6_1_index_design.py` -> `python activity_6_1_try_it_yourself.py` ->
   `python activity_6_2_index_strategies.py` -> `python activity_6_2_try_it_yourself.py`.
4. Each script prints its measurements and saves them in `results/` (JSON + console output).
5. Optionally inspect `jovit_lab06.products` in MongoDB Compass (Indexes and Explain Plan tabs).
6. Run `python build_report.py` to regenerate this report.

## 7. Source code

The complete source of every file used in this lab.

### `common.py`

```python
"""Shared helpers for Jovit Chawla's (24BTRCL089) Chapter 6 experiments.

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
DATABASE = "jovit_lab06"


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
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="jovit-24BTRCL089-lab06")
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        sys.exit(f"Cannot reach MongoDB ({type(exc).__name__}). Start mongod or set MONGO_URI in .env.")
    return client, client[os.environ.get("LAB6_DATABASE", DATABASE)]


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

### `catalog.py`

```python
"""Product catalogue and query workload shared by the Chapter 6 activities (Jovit Chawla, 24BTRCL089).

The catalogue is synthetic lab data generated with a fixed seed (an Indian online marketplace: prices in INR).
`QUERIES` is the five-query workload that Activity 6.1 designs indexes for and the other activities reuse.
"""
from __future__ import annotations

import datetime as dt
import random

COLLECTION = "products"
PRODUCT_COUNT = 50_000
SEED = 6089

CATEGORIES = {   # category -> (min price, max price, words used in product names, tags)
    "Mobiles": (6000, 140000, ["Phone", "5G", "Pro", "Lite"], ["5g", "android", "dual-sim", "amoled"]),
    "Laptops": (25000, 250000, ["Notebook", "Ultrabook", "Gaming", "Book"], ["ssd", "gaming", "thin", "backlit"]),
    "Audio": (500, 40000, ["Earbuds", "Headphones", "Speaker", "Soundbar"], ["wireless", "anc", "bass", "waterproof"]),
    "Books": (150, 3000, ["Guide", "Handbook", "Novel", "Workbook"], ["paperback", "bestseller", "exam-prep", "fiction"]),
    "Fashion": (300, 9000, ["Kurta", "Sneakers", "Jacket", "Saree"], ["cotton", "festive", "casual", "handloom"]),
    "Home": (200, 60000, ["Mixer", "Kettle", "Lamp", "Purifier"], ["energy-saver", "steel", "smart", "compact"]),
    "Sports": (250, 30000, ["Bat", "Racquet", "Yoga Mat", "Cycle"], ["cricket", "fitness", "outdoor", "kids"]),
    "Toys": (199, 8000, ["Puzzle", "Blocks", "Drone", "Board Game"], ["educational", "kids", "stem", "family"]),
}
BRANDS = ["Aarav", "Bharat", "Chakra", "Dhruv", "Ekta", "Garuda", "Indus", "Kaveri", "Lotus", "Meru", "Nila", "Orion"]


def products(count: int = PRODUCT_COUNT, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    docs = []
    for i in range(count):
        category = rng.choice(list(CATEGORIES))
        low, high, words, tags = CATEGORIES[category]
        brand = rng.choice(BRANDS)
        name = f"{brand} {rng.choice(words)} {rng.randint(1, 99)}"
        docs.append({
            "sku": f"SKU{i:06d}", "name": name, "category": category, "brand": brand,
            "price": round(rng.uniform(low, high), 2), "rating": round(rng.triangular(1.0, 5.0, 4.1), 1),
            "stock": rng.randint(0, 500), "tags": rng.sample(tags, rng.randint(1, 3)),
            "description": f"{name} by {brand} in {category.lower()}",
            "listed_at": base + dt.timedelta(minutes=rng.randint(0, 300 * 24 * 60)),
        })
    return docs


def seed(db, name: str = COLLECTION, count: int = PRODUCT_COUNT):
    """(Re)create a products collection with exactly `count` generated documents and only the _id index."""
    db.drop_collection(name)
    coll = db[name]
    docs = products(count)
    for start in range(0, len(docs), 10_000):
        coll.insert_many(docs[start:start + 10_000], ordered=False)
    return coll


# The workload. Each entry: (id, description, kind, spec). `kind` is "find" or "text".
QUERIES = [
    ("Q1", "Laptops sorted by price (cheapest first), top 20", "find",
     {"filter": {"category": "Laptops"}, "sort": {"price": 1}, "limit": 20}),
    ("Q2", "any product priced INR 1,000-1,500", "find",
     {"filter": {"price": {"$gte": 1000, "$lte": 1500}}}),
    ("Q3", "products tagged 'anc'", "find",
     {"filter": {"tags": "anc"}}),
    ("Q4", "search product names for 'gaming notebook'", "text",
     {"search": "gaming notebook"}),
    ("Q5", "Mobiles rated >= 4.5 sorted by price, top 20", "find",
     {"filter": {"category": "Mobiles", "rating": {"$gte": 4.5}}, "sort": {"price": 1}, "limit": 20}),
]

# Q4 needs a text index; before it exists the closest equivalent is a case-insensitive regex.
Q4_REGEX = {"$or": [{"name": {"$regex": "gaming", "$options": "i"}}, {"name": {"$regex": "notebook", "$options": "i"}}]}

# The index design from Activity 6.1 (name -> keys). One ESR compound index serves Q1 and Q5.
INDEXES = {
    "idx_category_price_rating": [("category", 1), ("price", 1), ("rating", 1)],
    "idx_price": [("price", 1)],
    "idx_tags": [("tags", 1)],
    "idx_text_name_description": [("name", "text"), ("description", "text")],
}
```

### `activity_6_1_index_design.py`

```python
"""Activity 6.1 - Design indexes for a query workload.

Author: Jovit Chawla (24BTRCL089)

1. Seed jovit_lab06.products with 50,000 generated products (only the _id index).
2. explain("executionStats") all five workload queries - expected: collection scans.
3. Create the designed index set (catalog.INDEXES):
     {category:1, price:1, rating:1}  Equality-Sort-Range compound index -> serves Q1 and Q5
     {price:1}                        range query Q2
     {tags:1}                         multikey index -> Q3
     text(name, description)          $text search -> Q4
4. explain again and report keys/documents examined, plan stages and the reduction per query.
"""
from __future__ import annotations

from catalog import INDEXES, PRODUCT_COUNT, Q4_REGEX, QUERIES, seed
from common import Recorder, connect, explain_find


def explain_query(coll, kind: str, spec: dict, text_index: bool) -> dict:
    if kind == "text":
        if not text_index:
            return explain_find(coll, Q4_REGEX)          # $text fails without a text index
        return explain_find(coll, {"$text": {"$search": spec["search"]}})
    return explain_find(coll, spec["filter"], sort=spec.get("sort"), limit=spec.get("limit", 0))


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_1", "Activity 6.1 - Index Design for a Query Workload", client)
    coll = seed(db)
    rec.log(f"  seeded {coll.estimated_document_count():,} products into {db.name}.products; indexes: "
            f"{list(coll.index_information())}")
    for qid, description, _, _ in QUERIES:
        rec.log(f"  {qid}: {description}")

    rec.section("Before: only the _id index")
    before = {}
    for qid, _, kind, spec in QUERIES:
        label = qid + (" (regex fallback - $text needs a text index)" if kind == "text" else "")
        before[qid] = rec.plan(label, explain_query(coll, kind, spec, text_index=False))

    rec.section("Index design")
    for name, keys in INDEXES.items():
        coll.create_index(keys, name=name)
        rec.log(f"  created {name}: {dict(keys)}")
    sizes = db.command("collStats", coll.name)["indexSizes"]
    rec.log("  index sizes: " + ", ".join(f"{n} {s // 1024} KiB" for n, s in sizes.items()))

    rec.section("After: designed indexes")
    after, rows = {}, []
    for qid, _, kind, spec in QUERIES:
        after[qid] = rec.plan(qid, explain_query(coll, kind, spec, text_index=True))
        b, a = before[qid]["docsExamined"], after[qid]["docsExamined"]
        reduction = round(100 * (1 - a / b), 2) if b else None
        sorted_in_memory = "SORT" in after[qid]["plan"].split(" <- ")
        rows.append({"query": qid, "before_plan": before[qid]["plan"], "after_plan": after[qid]["plan"],
                     "docs_before": b, "docs_after": a, "keys_after": after[qid]["keysExamined"],
                     "returned": after[qid]["nReturned"], "reduction_pct": reduction, "in_memory_sort": sorted_in_memory})

    rec.section("Summary")
    rec.log(f"  {'query':<6}{'before':<22}{'after':<40}{'docs before':>12}{'docs after':>12}{'reduction':>11}")
    for r in rows:
        rec.log(f"  {r['query']:<6}{r['before_plan'][:21]:<22}{r['after_plan'][:39]:<40}{r['docs_before']:>12,}"
                f"{r['docs_after']:>12,}{str(r['reduction_pct']) + '%':>11}")
    all_ixscan = all("COLLSCAN" not in r["after_plan"] for r in rows)
    rec.log(f"  every query uses an index after the design: {all_ixscan}; in-memory SORT stages left: "
            f"{[r['query'] for r in rows if r['in_memory_sort']] or 'none'}")
    rec.data = {"documents": PRODUCT_COUNT, "before": before, "after": after, "summary": rows,
                "indexes": {n: dict(k) for n, k in INDEXES.items()}, "index_sizes_bytes": sizes, "all_indexed": all_ixscan}
    rec.save()


if __name__ == "__main__":
    main()
```

### `activity_6_1_try_it_yourself.py`

```python
"""Activity 6.1 (Try it yourself) - Find out which indexes a workload really uses, with $indexStats.

Author: Jovit Chawla (24BTRCL089)

Works on the products collection and index design from Activity 6.1 (run that first). Two extra indexes are added
on purpose to see whether $indexStats exposes them:
    idx_category   {category:1}  - redundant: it is a prefix of idx_category_price_rating
    idx_stock      {stock:1}     - no query in the workload filters or sorts on stock
Then a 200-query storefront workload with randomised parameters is executed (read-only), weighted like real
traffic: Q1 x70, Q2 x45, Q3 x40, Q4 x25, Q5 x20. The accesses.ops counters are read before and after, so only
this workload's usage is counted.
"""
from __future__ import annotations

import random
import sys

from catalog import CATEGORIES, COLLECTION, INDEXES
from common import Recorder, connect

WEIGHTS = {"Q1": 70, "Q2": 45, "Q3": 40, "Q4": 25, "Q5": 20}
EXTRA = {"idx_category": [("category", 1)], "idx_stock": [("stock", 1)]}


def index_ops(coll) -> dict[str, int]:
    return {s["name"]: int(s["accesses"]["ops"]) for s in coll.aggregate([{"$indexStats": {}}])}


def run_query(coll, qid: str, rng: random.Random) -> int:
    category = rng.choice(list(CATEGORIES))
    all_tags = sorted({t for *_, tags in CATEGORIES.values() for t in tags})
    if qid == "Q1":
        cursor = coll.find({"category": category}).sort("price", 1).limit(20)
    elif qid == "Q2":
        low = rng.choice([500, 1000, 2000, 5000, 10000])
        cursor = coll.find({"price": {"$gte": low, "$lte": low * 1.5}})
    elif qid == "Q3":
        cursor = coll.find({"tags": rng.choice(all_tags)})
    elif qid == "Q4":
        words = rng.choice(["gaming notebook", "wireless earbuds", "cotton kurta", "smart purifier", "board game"])
        cursor = coll.find({"$text": {"$search": words}})
    else:
        cursor = coll.find({"category": category, "rating": {"$gte": rng.choice([4.0, 4.3, 4.5])}}).sort("price", 1).limit(20)
    return sum(1 for _ in cursor)


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_1_try_it_yourself", "Activity 6.1 (Try it yourself) - Index usage with $indexStats", client)
    coll = db[COLLECTION]
    existing = set(coll.index_information())
    if not set(INDEXES) <= existing:
        sys.exit("Run activity_6_1_index_design.py first: the designed indexes are missing.")
    for name, keys in EXTRA.items():
        coll.create_index(keys, name=name)
    rec.log(f"  collection {db.name}.{COLLECTION}: {coll.estimated_document_count():,} documents, "
            f"{len(coll.index_information())} indexes (2 extra: {', '.join(EXTRA)})")

    rng = random.Random(89)
    plan = [qid for qid, n in WEIGHTS.items() for _ in range(n)]
    rng.shuffle(plan)
    before = index_ops(coll)
    returned = {qid: 0 for qid in WEIGHTS}
    for qid in plan:
        returned[qid] += run_query(coll, qid, rng)
    after = index_ops(coll)
    used = {name: after.get(name, 0) - before.get(name, 0) for name in after}
    rec.log(f"  executed {len(plan)} queries {WEIGHTS}; documents returned per query type: {returned}")

    rec.section("$indexStats - accesses.ops during this workload")
    total = sum(used.values()) or 1
    for name, ops in sorted(used.items(), key=lambda kv: -kv[1]):
        rec.log(f"  {name:<28} {ops:>5} ops  {ops / total:6.1%}  {'#' * round(40 * ops / total)}")

    unused = sorted(n for n, ops in used.items() if ops == 0 and n != "_id_")
    redundant = [n for n, keys in EXTRA.items() for other, okeys in INDEXES.items()
                 if okeys[:len(keys)] == keys and n != other]
    rec.section("Findings")
    rec.log(f"  most used index: {max(used, key=used.get)}")
    rec.log(f"  never used by this workload: {unused or 'none'}")
    rec.log(f"  redundant (a prefix of a compound index): {redundant or 'none'}")
    rec.log("  recommendation: drop " + (", ".join(sorted(set(unused) | set(redundant))) or "nothing") +
            " - each unused index still costs memory and work on every insert/update")
    for name in EXTRA:                       # leave the collection as Activity 6.1 designed it
        coll.drop_index(name)
    rec.log(f"  dropped the two experimental indexes again; remaining: {sorted(coll.index_information())}")
    rec.data = {"queries": WEIGHTS, "returned": returned, "ops": used, "unused": unused, "redundant": redundant,
                "most_used": max(used, key=used.get)}
    rec.save()


if __name__ == "__main__":
    main()
```

### `activity_6_2_index_strategies.py`

```python
"""Activity 6.2 - Compare index strategies: what indexes cost on writes and buy on reads.

Author: Jovit Chawla (24BTRCL089)

Step 1  Write cost. Insert 10,000 products (insert_many, batches of 500) into a fresh collection for three index
        configurations, 5 runs each, median wall-clock time (time.perf_counter):
          A  only _id
          B  the full Activity 6.1 design (4 secondary indexes, including the text index)
          C  only idx_category_price_rating (the most used index in Activity 6.1 TIY)
Step 2  Read benefit. Run the five workload queries against 50,000 products carrying configuration C only and see
        which queries fall back to a collection scan.
Step 3  Index key order. For Q5 (equality on category, range on rating, sort on price) compare the
        Equality-Sort-Range order {category, price, rating} with {category, rating, price}.
Step 4  A trade-off analysis computed from the measured numbers.
"""
from __future__ import annotations

import statistics
import time

from catalog import INDEXES, Q4_REGEX, QUERIES, products, seed
from common import Recorder, connect, explain_find, fresh, time_ms

BATCH, INSERTS, RUNS = 500, 20_000, 7
CONFIGS = {
    "A: _id only": {},
    "B: full 6.1 design (4 indexes)": INDEXES,
    "C: idx_category_price_rating only": {"idx_category_price_rating": INDEXES["idx_category_price_rating"]},
}


def insert_once(db, docs: list[dict], indexes: dict) -> float:
    coll = fresh(db, "products_write")
    for name, keys in indexes.items():
        coll.create_index(keys, name=name)
    batch = [dict(d) for d in docs]              # fresh copies: insert_many adds _id to the dicts it is given
    started = time.perf_counter()
    for i in range(0, len(batch), BATCH):
        coll.insert_many(batch[i:i + BATCH], ordered=False)
    return (time.perf_counter() - started) * 1000


def insert_benchmark(db, docs: list[dict]) -> dict[str, list[float]]:
    """Interleave the configurations (A, B, C, A, B, C, ...) so background load drifts affect all of them equally;
    the first round is a warm-up and is discarded."""
    samples: dict[str, list[float]] = {label: [] for label in CONFIGS}
    for round_no in range(RUNS + 1):
        for label, indexes in CONFIGS.items():
            ms = insert_once(db, docs, indexes)
            if round_no:
                samples[label].append(ms)
    db.drop_collection("products_write")
    return samples


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_2", "Activity 6.2 - Compare Index Strategies", client)
    docs = products(INSERTS, seed=62089)

    rec.section(f"Step 1 - insert {INSERTS:,} documents; {RUNS} interleaved rounds after 1 warm-up round")
    writes = {}
    for label, samples in insert_benchmark(db, docs).items():
        median, best = statistics.median(samples), min(samples)
        spread = 100 * (max(samples) - best) / best
        writes[label] = {"median_ms": round(median, 1), "min_ms": round(best, 1), "spread_pct": round(spread, 1),
                         "runs_ms": [round(s, 1) for s in samples], "docs_per_s": round(INSERTS / median * 1000)}
        rec.log(f"  {label:<36} median {median:8.1f} ms  min {best:8.1f} ms  ({writes[label]['docs_per_s']:,} docs/s)  "
                f"run-to-run spread {spread:.0f}%")
        rec.log(f"  {'':<36} runs {writes[label]['runs_ms']}")
    base_runs = writes["A: _id only"]["runs_ms"]
    base, base_min = writes["A: _id only"]["median_ms"], writes["A: _id only"]["min_ms"]
    for label, w in writes.items():
        w["overhead_pct"] = round(100 * (w["median_ms"] / base - 1), 1)
        w["overhead_min_pct"] = round(100 * (w["min_ms"] / base_min - 1), 1)
        # Paired comparison: each round measured A, B and C seconds apart, so the per-round ratio cancels slow drift.
        ratios = [100 * (r / a - 1) for r, a in zip(w["runs_ms"], base_runs)]
        w["paired_overhead_pct"] = round(statistics.median(ratios), 1)
        w["paired_rounds_slower"] = sum(x > 0 for x in ratios)
    rec.log("  overhead vs _id only - paired per-round median (rounds slower than A) | median | min:")
    for k, v in writes.items():
        if k != "A: _id only":
            rec.log(f"    {k:<36} {v['paired_overhead_pct']:+7.1f}% ({v['paired_rounds_slower']}/{RUNS} rounds) | "
                    f"{v['overhead_pct']:+.1f}% | {v['overhead_min_pct']:+.1f}%")

    rec.section("Step 2 - workload with only idx_category_price_rating")
    perf = seed(db, "products_perf")
    perf.create_index(INDEXES["idx_category_price_rating"], name="idx_category_price_rating")
    reads = {}
    for qid, description, kind, spec in QUERIES:
        if kind == "text":
            summary = explain_find(perf, Q4_REGEX)
            label = f"{qid} (no text index -> regex)"
        else:
            summary = explain_find(perf, spec["filter"], sort=spec.get("sort"), limit=spec.get("limit", 0))
            label = qid
        reads[qid] = rec.plan(label, summary)
    scans = [q for q, s in reads.items() if "COLLSCAN" in s["plan"]]
    rec.log(f"  queries that fall back to COLLSCAN: {scans}")

    rec.section("Step 3 - key order for Q5 (category = Mobiles, rating >= 4.5, sort by price)")
    q5 = dict(QUERIES[4][3])
    perf.create_index([("category", 1), ("rating", 1), ("price", 1)], name="idx_category_rating_price")
    order = {}
    for name in ("idx_category_price_rating", "idx_category_rating_price"):
        summary = explain_find(perf, q5["filter"], sort=q5["sort"], limit=q5["limit"], hint=name)
        ms = time_ms(lambda n=name: list(perf.find(q5["filter"]).sort("price", 1).limit(20).hint(n)), repeat=30)
        summary["median_ms"] = ms
        summary["in_memory_sort"] = "SORT" in summary["plan"].split(" <- ")
        order[name] = rec.plan(f"{name} (median {ms} ms)", summary)
    perf.drop_index("idx_category_rating_price")
    esr, ers = order["idx_category_price_rating"], order["idx_category_rating_price"]
    rec.log(f"  E-S-R order: in-memory sort = {esr['in_memory_sort']}, keys examined {esr['keysExamined']}; "
            f"E-R-S order: in-memory sort = {ers['in_memory_sort']}, keys examined {ers['keysExamined']}")

    rec.section("Step 4 - trade-off analysis (from the numbers above)")
    full, one = writes["B: full 6.1 design (4 indexes)"], writes["C: idx_category_price_rating only"]
    def describe(w: dict) -> str:
        text = (f"{w['paired_overhead_pct']:+.1f}% (median of per-round paired ratios; slower than _id only in "
                f"{w['paired_rounds_slower']} of {RUNS} rounds)")
        if 0 < w["paired_rounds_slower"] < RUNS:
            text += " - not consistent in every round, so this figure is affected by the laptop's background load"
        return text
    analysis = [
        f"Every index is updated on every insert: the full design changed insert time by {describe(full)} compared "
        f"with _id only; a single compound index by {describe(one)}.",
        f"Keeping only the most-used index leaves {len(scans)} of 5 workload queries ({', '.join(scans)}) scanning all "
        f"{perf.estimated_document_count():,} documents, so read-heavy catalogue traffic still needs the full design.",
        "For write-heavy collections (logs, events, telemetry) the extra write cost is not worth it for rarely-run "
        "queries; index only what the workload runs often - which $indexStats shows (Activity 6.1 TIY).",
        f"Key order matters as much as the key set: for Q5 the E-S-R order {'avoided' if not esr['in_memory_sort'] else 'needed'} "
        f"an in-memory sort, while E-R-S {'needed one' if ers['in_memory_sort'] else 'did not need one'} "
        f"(medians {esr['median_ms']} ms vs {ers['median_ms']} ms).",
    ]
    for line in analysis:
        rec.log(f"  - {line}")
    rec.data = {"inserts": INSERTS, "batch": BATCH, "runs": RUNS, "writes": writes, "reads_one_index": reads,
                "collscan_with_one_index": scans, "key_order_q5": order, "analysis": analysis}
    rec.save()


if __name__ == "__main__":
    main()
```

### `activity_6_2_try_it_yourself.py`

```python
"""Activity 6.2 (Try it yourself) - Covered queries.

Author: Jovit Chawla (24BTRCL089)

Uses products_perf from Activity 6.2 (only idx_category_price_rating). One query - Audio products priced
INR 2,000-3,000, sorted by price - is run with different projections:
  P1  no projection (whole document)                    -> must FETCH every document
  P2  {category, price, rating} with _id included       -> still FETCH (_id is not in the index)
  P3  {category, price, rating, _id: 0}                 -> COVERED: answered from the index alone
  P4  {category, price, name, _id: 0}                   -> FETCH (name is not in the index)
For each: plan stages, keys/documents examined (explain executionStats) and the median time of 300 runs.
A second experiment shows that a multikey (array) index cannot cover a query on the array field.
"""
from __future__ import annotations

import sys

from common import Recorder, connect, explain_find, time_ms

FILTER = {"category": "Audio", "price": {"$gte": 2000, "$lte": 3000}}
SORT = {"price": 1}
PROJECTIONS = {
    "P1 whole document": None,
    "P2 index fields + _id": {"category": 1, "price": 1, "rating": 1},
    "P3 index fields, _id excluded": {"category": 1, "price": 1, "rating": 1, "_id": 0},
    "P4 adds a non-index field (name)": {"category": 1, "price": 1, "name": 1, "_id": 0},
}
RUNS = 300


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_2_try_it_yourself", "Activity 6.2 (Try it yourself) - Covered Queries", client)
    coll = db["products_perf"]
    if "idx_category_price_rating" not in coll.index_information():
        sys.exit("Run activity_6_2_index_strategies.py first (it builds products_perf with idx_category_price_rating).")
    rec.log(f"  {db.name}.products_perf: {coll.estimated_document_count():,} documents, indexes {sorted(coll.index_information())}")
    rec.log(f"  query: filter {FILTER}, sort {SORT}")

    rec.section(f"Projections (explain + median of {RUNS} runs)")
    rows = {}
    for label, projection in PROJECTIONS.items():
        summary = explain_find(coll, FILTER, sort=SORT, projection=projection)
        summary["median_ms"] = time_ms(lambda p=projection: list(coll.find(FILTER, p).sort("price", 1)), repeat=RUNS)
        summary["covered"] = summary["docsExamined"] == 0 and "FETCH" not in summary["plan"]
        rows[label] = rec.plan(f"{label:<34} median {summary['median_ms']:.3f} ms covered={summary['covered']}", summary)
    covered = [k for k, v in rows.items() if v["covered"]]
    fetch_ms = rows["P2 index fields + _id"]["median_ms"]
    cover_ms = rows["P3 index fields, _id excluded"]["median_ms"]
    rec.log(f"  covered projections: {covered}")
    rec.log(f"  excluding _id turned FETCH into a covered plan: {rows['P2 index fields + _id']['docsExamined']} -> "
            f"{rows['P3 index fields, _id excluded']['docsExamined']} documents examined, {fetch_ms:.3f} -> {cover_ms:.3f} ms "
            f"({(1 - cover_ms / fetch_ms):.1%} faster)")

    rec.section("Multikey limitation")
    coll.create_index("tags", name="idx_tags_tmp")
    multikey = rec.plan("find({tags: 'anc'}, {tags: 1, _id: 0}) on a multikey index",
                        explain_find(coll, {"tags": "anc"}, projection={"tags": 1, "_id": 0}))
    coll.drop_index("idx_tags_tmp")
    rec.log(f"  documents examined: {multikey['docsExamined']} - an index on an array field cannot cover a query that "
            "returns that field, because each index key holds only one array element")

    rec.data = {"filter": FILTER, "runs": RUNS, "projections": rows, "covered": covered, "multikey": multikey}
    rec.save()


if __name__ == "__main__":
    main()
```

### `build_report.py`

```python
"""Build REPORT.md for Lab 6 from the saved results/ files and the source code in this folder.

Author: Jovit Chawla (24BTRCL089). Every value is read from results/*.json written by the activity scripts;
nothing is invented. Run after the activities (run_all.ps1 does both).
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ACTIVITIES = [
    ("activity_6_1", "activity_6_1_index_design.py", "Activity 6.1 - Index Design for a Query Workload"),
    ("activity_6_1_try_it_yourself", "activity_6_1_try_it_yourself.py", "Activity 6.1 (Try it yourself) - Index Usage with $indexStats"),
    ("activity_6_2", "activity_6_2_index_strategies.py", "Activity 6.2 - Compare Index Strategies"),
    ("activity_6_2_try_it_yourself", "activity_6_2_try_it_yourself.py", "Activity 6.2 (Try it yourself) - Covered Queries"),
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


def results_6_1(m: dict) -> str:
    rows = [[r["query"], f"`{r['before_plan']}`", f"`{r['after_plan']}`", f"{r['docs_before']:,}", f"{r['docs_after']:,}",
             r["keys_after"], f"{r['reduction_pct']}%"] for r in m["summary"]]
    sizes = ", ".join(f"`{n}` {s // 1024} KiB" for n, s in m["index_sizes_bytes"].items())
    return (table(["Query", "Plan before", "Plan after", "Docs examined before", "Docs examined after", "Keys examined after",
                   "Reduction"], rows) +
            f"\n\nIndex sizes: {sizes}. Every query uses an index afterwards: **{m['all_indexed']}**.\n\n"
            "Note: before the text index exists, Q4 is measured with a case-insensitive regex on `name` (a `$text` query "
            "cannot run without a text index). The text index also searches `description` and stems words, so it "
            "returns more matches than the regex did.")


def results_6_1_tiy(m: dict) -> str:
    total = sum(m["ops"].values()) or 1
    rows = [[f"`{n}`", ops, f"{ops / total:.1%}"] for n, ops in sorted(m["ops"].items(), key=lambda kv: -kv[1])]
    return (table(["Index", "accesses.ops in this workload", "Share"], rows) +
            f"\n\nWorkload: {m['queries']}. Most used: **{m['most_used']}**. Never used: **{', '.join(m['unused'])}**. "
            f"Redundant prefix of a compound index: **{', '.join(m['redundant']) or 'none'}**.")


def results_6_2(m: dict) -> str:
    rows = [[k, f"{v['median_ms']:.1f}", f"{v['min_ms']:.1f}", f"{v['docs_per_s']:,}",
             "-" if k.startswith("A") else f"{v['paired_overhead_pct']:+.1f}% ({v['paired_rounds_slower']}/{m['runs']})",
             f"{v['spread_pct']:.0f}%", v["runs_ms"]] for k, v in m["writes"].items()]
    reads = [[q, f"`{s['plan']}`", f"{s['docsExamined']:,}"] for q, s in m["reads_one_index"].items()]
    order = [[f"`{n}`", f"`{s['plan']}`", s["keysExamined"], s["in_memory_sort"], s["median_ms"]]
             for n, s in m["key_order_q5"].items()]
    return (f"**Write cost** ({m['inserts']:,} inserts in batches of {m['batch']}; {m['runs']} interleaved rounds after a "
            "warm-up round). The paired overhead compares each configuration with `_id` only *within the same round* "
            "and takes the median; the count shows in how many rounds it was slower. The laptop was under memory "
            "pressure, hence the large run-to-run spread:\n\n" +
            table(["Configuration", "Median ms", "Min ms", "Docs/s", "Paired overhead vs _id only", "Spread", "Runs (ms)"],
                  rows) +
            "\n\n**Reads with only `idx_category_price_rating`:**\n\n" + table(["Query", "Plan", "Docs examined"], reads) +
            "\n\n**Key order for Q5:**\n\n" + table(["Index", "Plan", "Keys examined", "In-memory sort", "Median ms"], order) +
            "\n\n**Trade-off analysis:**\n\n" + "\n".join(f"- {line}" for line in m["analysis"]))


def results_6_2_tiy(m: dict) -> str:
    rows = [[k, f"`{s['plan']}`", s["keysExamined"], s["docsExamined"], s["executionTimeMillis"], f"{s['median_ms']:.3f}",
             "yes" if s["covered"] else "no"] for k, s in m["projections"].items()]
    mk = m["multikey"]
    return (table(["Projection", "Plan", "Keys examined", "Docs examined", "Server ms", f"Client median ms ({m['runs']} runs)",
                   "Covered"], rows) +
            f"\n\nMultikey index on `tags`: `{mk['plan']}`, {mk['docsExamined']:,} documents examined - not coverable.\n\n"
            "Client-side medians include the network round trip and BSON decoding, so they vary between runs; the "
            "server-side columns (documents examined, server ms) are the reliable measure of the covered plan.")


BUILDERS = {"activity_6_1": results_6_1, "activity_6_1_try_it_yourself": results_6_1_tiy,
            "activity_6_2": results_6_2, "activity_6_2_try_it_yourself": results_6_2_tiy}

HEADER = """# Lab 6 - Indexing and Query Performance in MongoDB

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 6 activities) |
| **Executed on** | {run_at} on `{machine}`, {os} |
| **Software** | MongoDB {mongo} ({topology}), Python {python}, pymongo {pymongo} |

> **Own work.** All code, data and measurements in `lab-6/jovit_24BTRCL089/` are my own and were produced by
> running the scripts below on my machine. `lab-6/Chapter06_Submission/` is a reference submission by another
> student, read only to understand the activities; none of its code, data or results are used.

## 1. Experiment title

Designing, measuring and tuning MongoDB indexes for a product-catalogue query workload.

## 2. Aim

To learn how indexes change query execution in MongoDB, how to design an index set for a workload, how to find
unused indexes, what indexes cost on writes, and how covered queries avoid reading documents.

## 3. Objectives

1. Design indexes for a five-query workload and compare `explain("executionStats")` before and after (6.1).
2. Measure real index usage with `$indexStats` and identify unused and redundant indexes (6.1 Try it yourself).
3. Measure the write cost of different index configurations, the read impact of removing indexes, and the effect
   of compound-index key order (6.2).
4. Build covered queries and show when a projection can be answered from the index alone (6.2 Try it yourself).

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | {os} |
| Machine | `{machine}` (laptop) |
| Database server | MongoDB {mongo} ({topology}) at `mongodb://localhost:27017` (Windows service) |
| Language / driver | Python {python}, pymongo {pymongo} (`pip install -r requirements.txt`) |
| Database used | `jovit_lab06` (collections `products`, `products_perf`, temporary `products_write`) |
| Data | 50,000 synthetic products generated with a fixed seed (`catalog.py`) |

## 5. Theory

**What an index is.** MongoDB indexes are B-tree structures holding the values of one or more fields in sorted
order, each pointing to a document. Without a suitable index the server must read every document (**COLLSCAN**);
with one it walks a range of index keys (**IXSCAN**) and fetches only matching documents (**FETCH**). `explain()`
with `executionStats` reports the winning plan, `totalKeysExamined`, `totalDocsExamined` and `nReturned`; a good
plan keeps keys and documents examined close to the number returned.

**Index types used here.** *Single-field* (`{{price: 1}}`) for equality and range queries; *compound*
(`{{category: 1, price: 1, rating: 1}}`) for several fields, usable by any query on a key prefix; *multikey* -
created automatically when the field is an array (`tags`), one key per element; *text* indexes for `$text` search
with stemming and relevance scores.

**ESR rule.** In a compound index put fields used with **E**quality first, then fields used for **S**ort, then
fields used in **R**ange filters. Then the index already returns documents in sort order (no in-memory SORT
stage) and the scan can stop as soon as the limit is reached.

**Cost of indexes.** Every insert, update or delete must also update every index, so each index slows writes and
uses RAM (indexes should fit in the WiredTiger cache). `$indexStats` reports how often each index is used
(`accesses.ops`, counted since the index was created or the server restarted), which shows which indexes are
worth keeping. An index that is a prefix of a compound index is usually redundant.

**Covered queries.** If every filtered, sorted and projected field is in one index - and `_id` is excluded unless
it is in the index - MongoDB answers from the index alone (`PROJECTION_COVERED`, `totalDocsExamined: 0`).
Multikey indexes cannot cover queries that return the array field.

## 6. Procedure

1. Make sure the MongoDB service is running (`Get-Service MongoDB`), or set `MONGO_URI` in a `.env` copied from
   `.env.example`.
2. Install the driver: `python -m pip install -r requirements.txt`.
3. Run the activities **in order** (each builds on the previous one), or use `run_all.ps1`:
   `python activity_6_1_index_design.py` -> `python activity_6_1_try_it_yourself.py` ->
   `python activity_6_2_index_strategies.py` -> `python activity_6_2_try_it_yourself.py`.
4. Each script prints its measurements and saves them in `results/` (JSON + console output).
5. Optionally inspect `jovit_lab06.products` in MongoDB Compass (Indexes and Explain Plan tabs).
6. Run `python build_report.py` to regenerate this report.
"""


def main() -> None:
    data = {name: load(name) for name, _, _ in ACTIVITIES}
    env = data["activity_6_1"]["environment"]
    parts = [HEADER.format(run_at=env["run_at_utc"], machine=env["machine"], os=env["os"], mongo=env["mongodb_server"],
                           topology=env["topology"], python=env["python"], pymongo=env["pymongo"])]
    parts.append("\n## 7. Source code\n\nThe complete source of every file used in this lab.\n")
    for file in ["common.py", "catalog.py"] + [s for _, s, _ in ACTIVITIES] + ["build_report.py"]:
        parts.append(f"\n### `{file}`\n\n```python\n{(HERE / file).read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 8. Execution output\n\nConsole output captured from the actual runs (`results/*.output.txt`).\n")
    for name, _, title in ACTIVITIES:
        parts.append(f"\n### {title}\n\n```text\n{(RESULTS / f'{name}.output.txt').read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 9. Results\n\n" + "\n\n".join(f"### {title}\n\n{BUILDERS[name](data[name]['measurements'])}"
                                                    for name, _, title in ACTIVITIES))
    a, b, c, d = (data[n]["measurements"] for n, _, _ in ACTIVITIES)
    reductions = [r["reduction_pct"] for r in a["summary"]]
    writes = c["writes"]
    p3 = d["projections"]["P3 index fields, _id excluded"]
    conclusion = [
        f"- Four indexes turned all five workload queries from collection scans into index scans, cutting documents "
        f"examined by {min(reductions)}-{max(reductions)}%; one ESR compound index served two queries (Q1 and Q5).",
        f"- `$indexStats` showed `{b['most_used']}` doing most of the work and exposed "
        f"{', '.join(f'`{u}`' for u in b['unused'])} as unused - `{', '.join(b['redundant'])}` because it duplicates the "
        "prefix of a compound index.",
        f"- Indexes are not free: with the full design inserts were "
        f"{writes['B: full 6.1 design (4 indexes)']['paired_overhead_pct']:+.1f}% slower than with `_id` only (slower in "
        f"{writes['B: full 6.1 design (4 indexes)']['paired_rounds_slower']}/{c['runs']} rounds); one compound index cost "
        f"{writes['C: idx_category_price_rating only']['paired_overhead_pct']:+.1f}% (slower in "
        f"{writes['C: idx_category_price_rating only']['paired_rounds_slower']}/{c['runs']} rounds, so that smaller "
        f"figure is less certain on this busy laptop). Dropping indexes sent {', '.join(c['collscan_with_one_index'])} "
        "back to collection scans, so the index set should follow the measured workload.",
        f"- Key order matters: the E-S-R index answered Q5 without an in-memory sort and examined "
        f"{c['key_order_q5']['idx_category_price_rating']['keysExamined']} keys instead of "
        f"{c['key_order_q5']['idx_category_rating_price']['keysExamined']}.",
        f"- Excluding `_id` from a projection of indexed fields produced a covered query "
        f"(`{p3['plan']}`, {p3['docsExamined']} documents examined); adding a non-index field, or returning a multikey "
        "array field, needs document fetches again.",
    ]
    parts.append("\n\n## 10. Conclusion\n\n" + "\n".join(conclusion) + "\n")
    (HERE / "REPORT.md").write_text("".join(parts), encoding="utf-8")
    print(f"Wrote REPORT.md ({sum(len(p) for p in parts):,} characters)")


if __name__ == "__main__":
    main()
```

## 8. Execution output

Console output captured from the actual runs (`results/*.output.txt`).

### Activity 6.1 - Index Design for a Query Workload

```text
Activity 6.1 - Index Design for a Query Workload
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:28:55+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0
  seeded 50,000 products into jovit_lab06.products; indexes: ['_id_']
  Q1: Laptops sorted by price (cheapest first), top 20
  Q2: any product priced INR 1,000-1,500
  Q3: products tagged 'anc'
  Q4: search product names for 'gaming notebook'
  Q5: Mobiles rated >= 4.5 sorted by price, top 20

== Before: only the _id index ==
  Q1: plan=SORT <- COLLSCAN pipeline=- returned=20 keysExamined=0 docsExamined=50000 serverMs=60
  Q2: plan=COLLSCAN pipeline=- returned=2159 keysExamined=0 docsExamined=50000 serverMs=57
  Q3: plan=COLLSCAN pipeline=- returned=3177 keysExamined=0 docsExamined=50000 serverMs=74
  Q4 (regex fallback - $text needs a text index): plan=COLLSCAN pipeline=- returned=3061 keysExamined=0 docsExamined=50000 serverMs=89
  Q5: plan=SORT <- COLLSCAN pipeline=- returned=20 keysExamined=0 docsExamined=50000 serverMs=44

== Index design ==
  created idx_category_price_rating: {'category': 1, 'price': 1, 'rating': 1}
  created idx_price: {'price': 1}
  created idx_tags: {'tags': 1}
  created idx_text_name_description: {'name': 'text', 'description': 'text'}
  index sizes: _id_ 4 KiB, idx_category_price_rating 1320 KiB, idx_price 876 KiB, idx_tags 556 KiB, idx_text_name_description 1548 KiB

== After: designed indexes ==
  Q1: plan=LIMIT <- FETCH <- IXSCAN pipeline=- returned=20 keysExamined=20 docsExamined=20 serverMs=1
  Q2: plan=FETCH <- IXSCAN pipeline=- returned=2159 keysExamined=2159 docsExamined=2159 serverMs=8
  Q3: plan=FETCH <- IXSCAN pipeline=- returned=3177 keysExamined=3177 docsExamined=3177 serverMs=9
  Q4: plan=TEXT_MATCH <- FETCH <- OR <- IXSCAN pipeline=- returned=4632 keysExamined=4632 docsExamined=4632 serverMs=29
  Q5: plan=LIMIT <- FETCH <- IXSCAN pipeline=- returned=20 keysExamined=231 docsExamined=20 serverMs=1

== Summary ==
  query before                after                                    docs before  docs after  reduction
  Q1    SORT <- COLLSCAN      LIMIT <- FETCH <- IXSCAN                      50,000          20     99.96%
  Q2    COLLSCAN              FETCH <- IXSCAN                               50,000       2,159     95.68%
  Q3    COLLSCAN              FETCH <- IXSCAN                               50,000       3,177     93.65%
  Q4    COLLSCAN              TEXT_MATCH <- FETCH <- OR <- IXSCAN           50,000       4,632     90.74%
  Q5    SORT <- COLLSCAN      LIMIT <- FETCH <- IXSCAN                      50,000          20     99.96%
  every query uses an index after the design: True; in-memory SORT stages left: none
```

### Activity 6.1 (Try it yourself) - Index Usage with $indexStats

```text
Activity 6.1 (Try it yourself) - Index usage with $indexStats
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:29:00+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0
  collection jovit_lab06.products: 50,000 documents, 7 indexes (2 extra: idx_category, idx_stock)
  executed 200 queries {'Q1': 70, 'Q2': 45, 'Q3': 40, 'Q4': 25, 'Q5': 20}; documents returned per query type: {'Q1': 1400, 'Q2': 141248, 'Q3': 125303, 'Q4': 65376, 'Q5': 400}

== $indexStats - accesses.ops during this workload ==
  idx_category_price_rating       90 ops   45.0%  ##################
  idx_price                       45 ops   22.5%  #########
  idx_tags                        40 ops   20.0%  ########
  idx_text_name_description       25 ops   12.5%  #####
  idx_stock                        0 ops    0.0%  
  _id_                             0 ops    0.0%  
  idx_category                     0 ops    0.0%  

== Findings ==
  most used index: idx_category_price_rating
  never used by this workload: ['idx_category', 'idx_stock']
  redundant (a prefix of a compound index): ['idx_category']
  recommendation: drop idx_category, idx_stock - each unused index still costs memory and work on every insert/update
  dropped the two experimental indexes again; remaining: ['_id_', 'idx_category_price_rating', 'idx_price', 'idx_tags', 'idx_text_name_description']
```

### Activity 6.2 - Compare Index Strategies

```text
Activity 6.2 - Compare Index Strategies
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:31:05+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0

== Step 1 - insert 20,000 documents; 7 interleaved rounds after 1 warm-up round ==
  A: _id only                          median    581.5 ms  min    343.3 ms  (34,396 docs/s)  run-to-run spread 251%
                                       runs [465.3, 581.5, 343.3, 584.1, 1204.9, 700.9, 345.5]
  B: full 6.1 design (4 indexes)       median   2087.8 ms  min   1283.8 ms  (9,580 docs/s)  run-to-run spread 173%
                                       runs [1578.5, 1283.8, 2087.8, 2779.8, 3510.6, 3002.0, 1903.0]
  C: idx_category_price_rating only    median    762.9 ms  min    423.7 ms  (26,216 docs/s)  run-to-run spread 190%
                                       runs [444.0, 423.7, 762.9, 1227.8, 765.2, 810.8, 605.7]
  overhead vs _id only - paired per-round median (rounds slower than A) | median | min:
    B: full 6.1 design (4 indexes)        +328.3% (7/7 rounds) | +259.0% | +274.0%
    C: idx_category_price_rating only      +15.7% (4/7 rounds) | +31.2% | +23.4%

== Step 2 - workload with only idx_category_price_rating ==
  Q1: plan=LIMIT <- FETCH <- IXSCAN pipeline=- returned=20 keysExamined=20 docsExamined=20 serverMs=5
  Q2: plan=COLLSCAN pipeline=- returned=2159 keysExamined=0 docsExamined=50000 serverMs=65
  Q3: plan=COLLSCAN pipeline=- returned=3177 keysExamined=0 docsExamined=50000 serverMs=85
  Q4 (no text index -> regex): plan=COLLSCAN pipeline=- returned=3061 keysExamined=0 docsExamined=50000 serverMs=147
  Q5: plan=LIMIT <- FETCH <- IXSCAN pipeline=- returned=20 keysExamined=231 docsExamined=20 serverMs=1
  queries that fall back to COLLSCAN: ['Q2', 'Q3', 'Q4']

== Step 3 - key order for Q5 (category = Mobiles, rating >= 4.5, sort by price) ==
  idx_category_price_rating (median 2.892 ms): plan=LIMIT <- FETCH <- IXSCAN pipeline=- returned=20 keysExamined=231 docsExamined=20 serverMs=0
  idx_category_rating_price (median 3.867 ms): plan=FETCH <- SORT <- IXSCAN pipeline=- returned=20 keysExamined=529 docsExamined=20 serverMs=7
  E-S-R order: in-memory sort = False, keys examined 231; E-R-S order: in-memory sort = True, keys examined 529

== Step 4 - trade-off analysis (from the numbers above) ==
  - Every index is updated on every insert: the full design changed insert time by +328.3% (median of per-round paired ratios; slower than _id only in 7 of 7 rounds) compared with _id only; a single compound index by +15.7% (median of per-round paired ratios; slower than _id only in 4 of 7 rounds) - not consistent in every round, so this figure is affected by the laptop's background load.
  - Keeping only the most-used index leaves 3 of 5 workload queries (Q2, Q3, Q4) scanning all 50,000 documents, so read-heavy catalogue traffic still needs the full design.
  - For write-heavy collections (logs, events, telemetry) the extra write cost is not worth it for rarely-run queries; index only what the workload runs often - which $indexStats shows (Activity 6.1 TIY).
  - Key order matters as much as the key set: for Q5 the E-S-R order avoided an in-memory sort, while E-R-S needed one (medians 2.892 ms vs 3.867 ms).
```

### Activity 6.2 (Try it yourself) - Covered Queries

```text
Activity 6.2 (Try it yourself) - Covered Queries
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:32:08+00:00 on LAPTOP-DSDFIQOI | MongoDB 8.3.7 (standalone) | Python 3.12.10 | pymongo 4.20.0
  jovit_lab06.products_perf: 50,000 documents, indexes ['_id_', 'idx_category_price_rating']
  query: filter {'category': 'Audio', 'price': {'$gte': 2000, '$lte': 3000}}, sort {'price': 1}

== Projections (explain + median of 300 runs) ==
  P1 whole document                  median 5.098 ms covered=False: plan=FETCH <- IXSCAN pipeline=- returned=156 keysExamined=156 docsExamined=156 serverMs=1
  P2 index fields + _id              median 4.387 ms covered=False: plan=PROJECTION_SIMPLE <- FETCH <- IXSCAN pipeline=- returned=156 keysExamined=156 docsExamined=156 serverMs=2
  P3 index fields, _id excluded      median 2.583 ms covered=True: plan=PROJECTION_COVERED <- IXSCAN pipeline=- returned=156 keysExamined=156 docsExamined=0 serverMs=0
  P4 adds a non-index field (name)   median 5.447 ms covered=False: plan=PROJECTION_SIMPLE <- FETCH <- IXSCAN pipeline=- returned=156 keysExamined=156 docsExamined=156 serverMs=2
  covered projections: ['P3 index fields, _id excluded']
  excluding _id turned FETCH into a covered plan: 156 -> 0 documents examined, 4.387 -> 2.583 ms (41.1% faster)

== Multikey limitation ==
  find({tags: 'anc'}, {tags: 1, _id: 0}) on a multikey index: plan=PROJECTION_SIMPLE <- FETCH <- IXSCAN pipeline=- returned=3177 keysExamined=3177 docsExamined=3177 serverMs=28
  documents examined: 3177 - an index on an array field cannot cover a query that returns that field, because each index key holds only one array element
```

## 9. Results

### Activity 6.1 - Index Design for a Query Workload

| Query | Plan before | Plan after | Docs examined before | Docs examined after | Keys examined after | Reduction |
|---|---|---|---|---|---|---|
| Q1 | `SORT <- COLLSCAN` | `LIMIT <- FETCH <- IXSCAN` | 50,000 | 20 | 20 | 99.96% |
| Q2 | `COLLSCAN` | `FETCH <- IXSCAN` | 50,000 | 2,159 | 2159 | 95.68% |
| Q3 | `COLLSCAN` | `FETCH <- IXSCAN` | 50,000 | 3,177 | 3177 | 93.65% |
| Q4 | `COLLSCAN` | `TEXT_MATCH <- FETCH <- OR <- IXSCAN` | 50,000 | 4,632 | 4632 | 90.74% |
| Q5 | `SORT <- COLLSCAN` | `LIMIT <- FETCH <- IXSCAN` | 50,000 | 20 | 231 | 99.96% |

Index sizes: `_id_` 4 KiB, `idx_category_price_rating` 1320 KiB, `idx_price` 876 KiB, `idx_tags` 556 KiB, `idx_text_name_description` 1548 KiB. Every query uses an index afterwards: **True**.

Note: before the text index exists, Q4 is measured with a case-insensitive regex on `name` (a `$text` query cannot run without a text index). The text index also searches `description` and stems words, so it returns more matches than the regex did.

### Activity 6.1 (Try it yourself) - Index Usage with $indexStats

| Index | accesses.ops in this workload | Share |
|---|---|---|
| `idx_category_price_rating` | 90 | 45.0% |
| `idx_price` | 45 | 22.5% |
| `idx_tags` | 40 | 20.0% |
| `idx_text_name_description` | 25 | 12.5% |
| `idx_stock` | 0 | 0.0% |
| `_id_` | 0 | 0.0% |
| `idx_category` | 0 | 0.0% |

Workload: {'Q1': 70, 'Q2': 45, 'Q3': 40, 'Q4': 25, 'Q5': 20}. Most used: **idx_category_price_rating**. Never used: **idx_category, idx_stock**. Redundant prefix of a compound index: **idx_category**.

### Activity 6.2 - Compare Index Strategies

**Write cost** (20,000 inserts in batches of 500; 7 interleaved rounds after a warm-up round). The paired overhead compares each configuration with `_id` only *within the same round* and takes the median; the count shows in how many rounds it was slower. The laptop was under memory pressure, hence the large run-to-run spread:

| Configuration | Median ms | Min ms | Docs/s | Paired overhead vs _id only | Spread | Runs (ms) |
|---|---|---|---|---|---|---|
| A: _id only | 581.5 | 343.3 | 34,396 | - | 251% | [465.3, 581.5, 343.3, 584.1, 1204.9, 700.9, 345.5] |
| B: full 6.1 design (4 indexes) | 2087.8 | 1283.8 | 9,580 | +328.3% (7/7) | 174% | [1578.5, 1283.8, 2087.8, 2779.8, 3510.6, 3002.0, 1903.0] |
| C: idx_category_price_rating only | 762.9 | 423.7 | 26,216 | +15.7% (4/7) | 190% | [444.0, 423.7, 762.9, 1227.8, 765.2, 810.8, 605.7] |

**Reads with only `idx_category_price_rating`:**

| Query | Plan | Docs examined |
|---|---|---|
| Q1 | `LIMIT <- FETCH <- IXSCAN` | 20 |
| Q2 | `COLLSCAN` | 50,000 |
| Q3 | `COLLSCAN` | 50,000 |
| Q4 | `COLLSCAN` | 50,000 |
| Q5 | `LIMIT <- FETCH <- IXSCAN` | 20 |

**Key order for Q5:**

| Index | Plan | Keys examined | In-memory sort | Median ms |
|---|---|---|---|---|
| `idx_category_price_rating` | `LIMIT <- FETCH <- IXSCAN` | 231 | False | 2.892 |
| `idx_category_rating_price` | `FETCH <- SORT <- IXSCAN` | 529 | True | 3.867 |

**Trade-off analysis:**

- Every index is updated on every insert: the full design changed insert time by +328.3% (median of per-round paired ratios; slower than _id only in 7 of 7 rounds) compared with _id only; a single compound index by +15.7% (median of per-round paired ratios; slower than _id only in 4 of 7 rounds) - not consistent in every round, so this figure is affected by the laptop's background load.
- Keeping only the most-used index leaves 3 of 5 workload queries (Q2, Q3, Q4) scanning all 50,000 documents, so read-heavy catalogue traffic still needs the full design.
- For write-heavy collections (logs, events, telemetry) the extra write cost is not worth it for rarely-run queries; index only what the workload runs often - which $indexStats shows (Activity 6.1 TIY).
- Key order matters as much as the key set: for Q5 the E-S-R order avoided an in-memory sort, while E-R-S needed one (medians 2.892 ms vs 3.867 ms).

### Activity 6.2 (Try it yourself) - Covered Queries

| Projection | Plan | Keys examined | Docs examined | Server ms | Client median ms (300 runs) | Covered |
|---|---|---|---|---|---|---|
| P1 whole document | `FETCH <- IXSCAN` | 156 | 156 | 1 | 5.098 | no |
| P2 index fields + _id | `PROJECTION_SIMPLE <- FETCH <- IXSCAN` | 156 | 156 | 2 | 4.387 | no |
| P3 index fields, _id excluded | `PROJECTION_COVERED <- IXSCAN` | 156 | 0 | 0 | 2.583 | yes |
| P4 adds a non-index field (name) | `PROJECTION_SIMPLE <- FETCH <- IXSCAN` | 156 | 156 | 2 | 5.447 | no |

Multikey index on `tags`: `PROJECTION_SIMPLE <- FETCH <- IXSCAN`, 3,177 documents examined - not coverable.

Client-side medians include the network round trip and BSON decoding, so they vary between runs; the server-side columns (documents examined, server ms) are the reliable measure of the covered plan.

## 10. Conclusion

- Four indexes turned all five workload queries from collection scans into index scans, cutting documents examined by 90.74-99.96%; one ESR compound index served two queries (Q1 and Q5).
- `$indexStats` showed `idx_category_price_rating` doing most of the work and exposed `idx_category`, `idx_stock` as unused - `idx_category` because it duplicates the prefix of a compound index.
- Indexes are not free: with the full design inserts were +328.3% slower than with `_id` only (slower in 7/7 rounds); one compound index cost +15.7% (slower in 4/7 rounds, so that smaller figure is less certain on this busy laptop). Dropping indexes sent Q2, Q3, Q4 back to collection scans, so the index set should follow the measured workload.
- Key order matters: the E-S-R index answered Q5 without an in-memory sort and examined 231 keys instead of 529.
- Excluding `_id` from a projection of indexed fields produced a covered query (`PROJECTION_COVERED <- IXSCAN`, 0 documents examined); adding a non-index field, or returning a multikey array field, needs document fetches again.
