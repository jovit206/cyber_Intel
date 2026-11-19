import gc
import json
import os
import random
import statistics
import textwrap
import time
import timeit
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from rich.console import Console
from rich.table import Table


try:
    from dotenv import load_dotenv

    for _env in (Path(__file__).resolve().parent / ".env",
                 Path(__file__).resolve().parent.parent / ".env",
                 Path(__file__).resolve().parent.parent.parent / ".env"):
        if _env.exists():
            load_dotenv(_env)
            break
except ImportError:
    pass

DB_NAME = "chapter_06"
# A separate collection from Activity 6.1's `products`, so dropping indexes
# here never breaks the 6.1 scripts.
COLL_NAME = "products_perf"
BATCH = 10_000
TRIALS = 5  # timed repetitions per insert configuration; the median is reported
QUERY_RUNS = 20  # timed repetitions per query; the average is reported
OUT_JSON = Path(__file__).resolve().parent / "activity_6_2_index_strategies.json"
# Activity 6.1's $indexStats result names the most-used index.
USAGE_JSON = (Path(__file__).resolve().parent.parent
              / "Activity_6.1_Try_It_Yourself" / "activity_6_1_try_it_yourself_index_stats.json")
DEFAULT_CRITICAL = "idx_category_price_rating"

console = Console()
_client = None


def get_client():
    """Return a singleton MongoClient."""
    global _client
    if _client is None:
        uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        kwargs = {"serverSelectionTimeoutMS": 5000}
        if uri.startswith("mongodb+srv://"):
            import certifi  # macOS Python often lacks system root CAs

            kwargs["tlsCAFile"] = certifi.where()
            kwargs["serverSelectionTimeoutMS"] = 10000
        _client = MongoClient(uri, **kwargs)
        _client.admin.command("ping")  # fail fast on a bad URI
        print(f"[OK] Connected to MongoDB at {uri.split('@')[-1]}")
    return _client


def reset_collection(db_name: str, collection_name: str):
    """Drop and recreate a collection, so re-running the lab is idempotent."""
    db = get_client()[db_name]
    if collection_name in db.list_collection_names():
        db.drop_collection(collection_name)
        print(f"[OK] Dropped collection: {db_name}.{collection_name}")
    return db[collection_name]


def close_client():
    global _client
    if _client is not None:
        _client.close()
        _client = None
        print("[OK] MongoDB connection closed.")


def banner(text: str, char: str = "=", width: int = 72) -> None:
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)
    print()


# --- Same product data and queries as Activity 6.1 ---------------------------
CATEGORIES = {
    "Electronics": ["Headphones", "Speaker", "Charger", "Keyboard", "Monitor"],
    "Books": ["Novel", "Cookbook", "Guide", "Biography", "Atlas"],
    "Clothing": ["Jacket", "Sneakers", "Hoodie", "Jeans", "Scarf"],
    "Home & Kitchen": ["Blender", "Kettle", "Lamp", "Cookware Set", "Mug"],
    "Sports": ["Yoga Mat", "Dumbbell", "Tennis Racket", "Water Bottle", "Backpack"],
    "Toys": ["Puzzle", "Robot Kit", "Board Game", "Plush Bear", "Building Blocks"],
    "Beauty": ["Moisturizer", "Perfume", "Hair Dryer", "Lipstick", "Sunscreen"],
    "Grocery": ["Green Tea", "Coffee Beans", "Olive Oil", "Honey", "Granola"],
}
ADJECTIVES = ["Premium", "Classic", "Compact", "Deluxe", "Essential", "Pro", "Ultra", "Smart", "Eco", "Vintage"]
BRANDS = ["Acme", "Zenith", "Nova", "Orbit", "Summit", "Apex", "Luma", "Terra"]
TAGS = ["bestseller", "new-arrival", "sale", "eco-friendly", "wireless", "handmade",
        "limited-edition", "gift-idea", "premium", "budget", "imported", "clearance"]


def generate_products(count):
    """Build `count` random products: name, category, price, rating, stock, tags."""
    random.seed(42)
    category_names = list(CATEGORIES)
    products = []
    for i in range(count):
        category = random.choice(category_names)
        noun = random.choice(CATEGORIES[category])
        products.append({
            "sku": f"SKU-{i:05d}",
            "name": f"{random.choice(ADJECTIVES)} {random.choice(BRANDS)} {noun}",
            "category": category,
            "price": round(random.uniform(5, 1000), 2),
            "rating": round(random.uniform(1, 5), 1),
            "stock": random.randint(0, 500),
            "tags": random.sample(TAGS, k=random.randint(1, 4)),
        })
    return products


QUERIES = [
    {"id": "Q1", "label": "Electronics, cheapest 20 first",
     "filter": {"category": "Electronics"}, "sort": [("price", 1)], "limit": 20},
    {"id": "Q2", "label": "Price between $100 and $150",
     "filter": {"price": {"$gte": 100, "$lte": 150}}},
    {"id": "Q3", "label": "Tagged 'wireless'",
     "filter": {"tags": "wireless"}},
    {"id": "Q4", "label": "Text search name: 'headphones'",
     "filter": {"$text": {"$search": "headphones"}},
     "fallback": {"name": {"$regex": "headphones", "$options": "i"}}},
    {"id": "Q5", "label": "Books rated >= 4.5, by price",
     "filter": {"category": "Books", "rating": {"$gte": 4.5}}, "sort": [("price", 1)]},
]

# Step 2: five indexes, each leading with a different field. The first four
# are Activity 6.1's design; idx_stock (a low-stock report) serves none of the
# five queries -- it is here to show that an index costs every write whether
# or not anything reads it.
INDEXES = [
    ([("category", 1), ("price", 1), ("rating", 1)], "idx_category_price_rating", "Q1 (prefix), Q5 (ESR)"),
    ([("price", 1)], "idx_price", "Q2"),
    ([("tags", 1)], "idx_tags", "Q3 (multikey)"),
    ([("name", "text")], "idx_name_text", "Q4"),
    ([("stock", 1)], "idx_stock", "none of Q1-Q5"),
]


def time_insert(col, docs, label):
    """Steps 1-2: time insert_many(docs) with time.perf_counter().

    One ~0.1s measurement swings by 50% from run to run, so the insert is timed
    TRIALS times and the median is reported. Between trials the batch is deleted
    (untimed), so every trial starts from the same collection state and the last
    trial's documents stay in the collection."""
    times = []
    for trial in range(TRIALS):
        batch = [dict(d) for d in docs]  # fresh copies: insert_many adds _id in place
        gc.collect()
        gc.disable()  # keep a garbage-collection pause out of the timed region, as timeit does
        start = time.perf_counter()
        result = col.insert_many(batch)
        times.append(time.perf_counter() - start)
        gc.enable()
        if trial < TRIALS - 1:
            col.delete_many({"_id": {"$in": result.inserted_ids}})
    elapsed = statistics.median(times)
    print(f"  {label}: {len(docs):,} inserts, median of {TRIALS} runs = {elapsed:.3f}s"
          f" ({len(docs) / elapsed:,.0f} docs/sec; range {min(times):.3f}-{max(times):.3f}s)")
    return elapsed, times


def slowdown(seconds, baseline):
    """Percentage slowdown of `seconds` relative to `baseline`."""
    return (seconds - baseline) / baseline * 100


def explain(col, flt, sort=None, limit=None):
    """Run the explain command with executionStats verbosity for one find()."""
    cmd = {"find": col.name, "filter": flt}
    if sort:
        cmd["sort"] = dict(sort)
    if limit:
        cmd["limit"] = limit
    return col.database.command("explain", cmd, verbosity="executionStats")


def plan_summary(plan):
    """Flatten the winning plan into 'SORT > COLLSCAN' plus the indexes it used."""
    node = plan["queryPlanner"]["winningPlan"]
    node = node.get("queryPlan", node)  # slot-based engine nests the plan one level down
    stages, indexes = [], []

    def walk(n):
        stages.append(n["stage"])
        if "indexName" in n:
            indexes.append(n["indexName"])
        children = list(n.get("inputStages", []))
        if "inputStage" in n:
            children.append(n["inputStage"])
        for child in children:
            walk(child)

    walk(node)
    stats = plan["executionStats"]
    return {
        "stage": " > ".join(stages),
        "index": ", ".join(dict.fromkeys(indexes)) or "-",
        "totalDocsExamined": stats["totalDocsExamined"],
        "totalKeysExamined": stats["totalKeysExamined"],
        "nReturned": stats["nReturned"],
    }


def time_query(col, flt, sort, limit):
    """Average wall-clock milliseconds over QUERY_RUNS executions."""
    # list() iterates the cursor; without it the query never executes.
    total = timeit.timeit(lambda: list(col.find(flt, sort=sort, limit=limit or 0)), number=QUERY_RUNS)
    return total / QUERY_RUNS * 1000


def measure_queries(col):
    """Explain and time all five queries against the collection's current indexes."""
    has_text = any(idx["key"].get("_fts") == "text" for idx in col.list_indexes())
    results = {}
    for q in QUERIES:
        # $text cannot run without a text index; fall back to the $regex equivalent.
        use_fallback = "fallback" in q and not has_text
        flt = q["fallback"] if use_fallback else q["filter"]
        s = plan_summary(explain(col, flt, q.get("sort"), q.get("limit")))
        s["avg_ms"] = time_query(col, flt, q.get("sort"), q.get("limit"))
        s["note"] = "regex fallback" if use_fallback else ""
        results[q["id"]] = s
        suffix = "  [regex fallback]" if use_fallback else ""
        print(f"  {q['id']} {q['label']:32} {s['stage']:28} docs={s['totalDocsExamined']:>6,}"
              f"  avg={s['avg_ms']:6.2f} ms{suffix}")
    return results


def pick_critical_index():
    """The index Activity 6.1's $indexStats measured as most used."""
    serves = {name: queries for _, name, queries in INDEXES}
    if USAGE_JSON.exists():
        name = json.loads(USAGE_JSON.read_text()).get("most_used_index")
        if name in serves:
            return name, f"most used in Activity 6.1's $indexStats ({USAGE_JSON.name})"
    return DEFAULT_CRITICAL, f"serves {serves[DEFAULT_CRITICAL]}"


def print_write_table(rows):
    """rows: (label, seconds) pairs; the first row is the no-index baseline."""
    baseline = rows[0][1]
    table = Table(title=f"Write Throughput: {BATCH:,} inserts, median of {TRIALS} runs")
    table.add_column("Configuration", style="cyan")
    table.add_column("Seconds", justify="right")
    table.add_column("Docs/sec", justify="right")
    table.add_column("Slowdown vs no indexes", style="red", justify="right")
    for i, (label, seconds) in enumerate(rows):
        change = "-" if i == 0 else f"{slowdown(seconds, baseline):+.1f}%"
        table.add_row(label, f"{seconds:.3f}", f"{BATCH / seconds:,.0f}", change)
    console.print(table)


def print_query_table(full, lean, critical, doc_count):
    table = Table(title=f"Query Performance: all {len(INDEXES)} indexes vs only {critical} ({doc_count:,} docs)")
    table.add_column("Query", style="cyan")
    table.add_column(f"{len(INDEXES)} indexes: stage")
    table.add_column("Docs", justify="right")
    table.add_column("Avg ms", justify="right")
    table.add_column("1 index: stage")
    table.add_column("Docs", justify="right")
    table.add_column("Avg ms", justify="right")
    table.add_column("Change", style="red", justify="right")
    for q in QUERIES:
        full_q, lean_q = full[q["id"]], lean[q["id"]]
        table.add_row(
            q["id"] + ("*" if lean_q["note"] else ""),
            full_q["stage"], f"{full_q['totalDocsExamined']:,}", f"{full_q['avg_ms']:.2f}",
            lean_q["stage"], f"{lean_q['totalDocsExamined']:,}", f"{lean_q['avg_ms']:.2f}",
            f"{lean_q['avg_ms'] / full_q['avg_ms']:.1f}x",
        )
    console.print(table)
    for q in QUERIES:
        if lean[q["id"]]["note"]:
            print(f"  * {q['id']} with 1 index is the $regex fallback -- $text cannot run without a text index.")


def build_analysis(t0, t5, t1, full, lean, critical, doc_count):
    """Step 4: the trade-off, written from this run's measured numbers."""
    kept = [q["id"] for q in QUERIES if lean[q["id"]]["index"] != "-"]
    lost = [q["id"] for q in QUERIES if lean[q["id"]]["index"] == "-"]
    lost_detail = "; ".join(
        f"{qid} {full[qid]['totalDocsExamined']:,} -> {lean[qid]['totalDocsExamined']:,} docs examined, "
        f"{full[qid]['avg_ms']:.1f} -> {lean[qid]['avg_ms']:.1f} ms" for qid in lost
    )
    # Indexes no query's winning plan used while all of them existed.
    used = {name for r in full.values() for name in r["index"].split(", ")}
    unused = " and ".join(name for _, name, _ in INDEXES if name not in used)
    unused_note = f"{unused} served none of the {len(QUERIES)} queries, yet every write paid for it. " if unused else ""
    like_unused = f", like {unused} here" if unused else ""
    return [
        f"Write cost. Inserting {BATCH:,} products took {t0:.3f}s (median of {TRIALS} runs) with only "
        f"the _id index and {t5:.3f}s with {len(INDEXES)} secondary indexes: a {slowdown(t5, t0):+.1f}% "
        f"slowdown ({BATCH / t0:,.0f} -> {BATCH / t5:,.0f} docs/sec). Every insert has to add an entry "
        f"to every index, and the multikey tags index and the text index on name add several entries "
        f"per document. {unused_note}With only {critical} kept, the same insert took {t1:.3f}s "
        f"({slowdown(t1, t0):+.1f}% vs no indexes).",

        f"Read cost. After dropping to {critical}, {', '.join(kept) or 'no query'} still used an "
        f"index, while {', '.join(lost) or 'no query'} fell back to a COLLSCAN that examines all "
        f"{doc_count:,} documents ({lost_detail or 'no change'}). Wall-clock time moves less than "
        f"documents examined because, at {doc_count:,} documents, much of each query's time is "
        f"sending its matches back to the client. Documents examined is the gap that grows with the "
        f"collection: a COLLSCAN reads every document, an index scan reads only the matching keys.",

        f"Trade-off. Indexes move cost from reads to writes. A read-heavy product catalogue should "
        f"keep the indexes its frequent queries actually use -- $indexStats shows which -- and drop "
        f"the ones nothing reads{like_unused}. A write-heavy workload such as logs or sensor data "
        f"should keep the fewest indexes that still meet its read latency targets. One index that "
        f"serves several queries, as {critical} does for {' and '.join(kept) or 'none'}, gives the "
        f"most read benefit per unit of write cost.",
    ]


def main():
    banner("Lab 6.2: Compare Index Strategies")
    col = reset_collection(DB_NAME, COLL_NAME)
    # Create the collection up front so the first timed insert doesn't also
    # pay for creating it.
    col.database.create_collection(COLL_NAME)

    # Generate every document before any timer starts: only the insert is measured.
    docs = generate_products(3 * BATCH)
    batch_1, batch_2, batch_3 = docs[:BATCH], docs[BATCH:2 * BATCH], docs[2 * BATCH:]

    # Step 1: insert with no indexes except _id
    print(f"=== Step 1: Insert {BATCH:,} products, no indexes except _id ===")
    t0, t0_runs = time_insert(col, batch_1, "0 secondary indexes")

    # Step 2: five indexes, then another batch of inserts
    print(f"\n=== Step 2: Create {len(INDEXES)} indexes, insert another {BATCH:,} ===")
    for keys, name, serves in INDEXES:
        col.create_index(keys, name=name)
        print(f"  {name:28} keys={dict(keys)}  -> {serves}")
    t5, t5_runs = time_insert(col, batch_2, f"{len(INDEXES)} secondary indexes")
    print(f"\n  Percentage slowdown with {len(INDEXES)} indexes: {slowdown(t5, t0):+.1f}%")

    # Step 3: the five queries with every index, then with only the critical one
    doc_count = col.estimated_document_count()
    print(f"\n=== Step 3: Five queries with all {len(INDEXES)} indexes ({doc_count:,} docs, avg of {QUERY_RUNS} runs) ===")
    full = measure_queries(col)

    critical, reason = pick_critical_index()
    print("\n=== Step 3: Drop all but the most critical index ===")
    print(f"  Keeping {critical}: {reason}.")
    for _, name, _ in INDEXES:
        if name != critical:
            col.drop_index(name)
            print(f"  Dropped {name}")
    print(f"  Remaining: {[idx['name'] for idx in col.list_indexes()]}")

    print(f"\n=== Step 3: Five queries with only {critical} ===")
    lean = measure_queries(col)
    print()
    print_query_table(full, lean, critical, doc_count)

    # The middle option for the analysis: what does the one kept index cost on writes?
    print(f"\n=== Write cost with only {critical} ===")
    t1, t1_runs = time_insert(col, batch_3, "1 secondary index")
    print()
    print_write_table(
        [("No indexes (only _id)", t0), (f"{len(INDEXES)} secondary indexes", t5), (f"1 index ({critical})", t1)]
    )

    # Step 4: analysis
    print("\n=== Step 4: Analysis -- query performance vs write throughput ===")
    analysis = build_analysis(t0, t5, t1, full, lean, critical, doc_count)
    for paragraph in analysis:
        print(textwrap.fill(paragraph, width=90, initial_indent="  ", subsequent_indent="  "))
        print()
    print("  Timings vary from run to run; re-run to check the numbers are stable.")

    # --- MongoDB JSON for the submission ---------------------------------
    payload = {
        "database": DB_NAME,
        "collection": COLL_NAME,
        "batch_size": BATCH,
        "indexes_created": [{"name": name, "key": dict(keys), "serves": serves} for keys, name, serves in INDEXES],
        "insert_trials": TRIALS,
        "insert_seconds_median": {"no_indexes": t0, "five_indexes": t5, "critical_index_only": t1},
        "insert_seconds_all_runs": {"no_indexes": t0_runs, "five_indexes": t5_runs, "critical_index_only": t1_runs},
        "slowdown_percent": {"five_indexes": slowdown(t5, t0), "critical_index_only": slowdown(t1, t0)},
        "critical_index": critical,
        "queries": QUERIES,
        "query_runs": QUERY_RUNS,
        "queries_all_indexes": full,
        "queries_critical_index_only": lean,
        "analysis": analysis,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=json_util.default))
    print(f"\n[OK] Wrote results JSON -> {OUT_JSON.name}")

    banner("Lab 6.2 Complete")
    close_client()


if __name__ == "__main__":
    main()
