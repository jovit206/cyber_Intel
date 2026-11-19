import json
import os
import random
import sys
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
COLL_NAME = "products"
OUT_JSON = Path(__file__).resolve().parent / "activity_6_1_try_it_yourself_index_stats.json"

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


# The indexes Activity 6.1 designed, and which queries each one serves.
SERVES = {
    "idx_category_price_rating": "Q1 (prefix), Q5 (ESR)",
    "idx_price": "Q2",
    "idx_tags": "Q3 (multikey)",
    "idx_name_text": "Q4",
    "_id_": "_id lookups",
}
# Why an index earns its rank, printed when it comes out as the most used.
NOTES = {
    "idx_category_price_rating": ["Q1 and Q5 share it through the {category, price} prefix, so one well-ordered",
                                  "compound index carries the two most frequent category queries."],
}
SEARCH_TERMS = ["headphones", "novel", "jacket", "blender", "puzzle", "perfume", "coffee", "backpack"]


# A storefront-shaped mix: browsing a category is far more common than a
# filtered "top rated" view. Parameters are randomised per run so the workload
# isn't one query repeated. Each builder returns (filter, sort, limit).
def browse_category(categories, tags):
    return {"category": random.choice(categories)}, [("price", 1)], 20


def price_band(categories, tags):
    low = random.randint(5, 950)
    return {"price": {"$gte": low, "$lte": low + 50}}, None, 0


def by_tag(categories, tags):
    return {"tags": random.choice(tags)}, None, 0


def search_name(categories, tags):
    return {"$text": {"$search": random.choice(SEARCH_TERMS)}}, None, 0


def top_rated(categories, tags):
    return {"category": random.choice(categories), "rating": {"$gte": 4.5}}, [("price", 1)], 0


WORKLOAD = [
    ("Q1", "Browse a category, cheapest 20", 40, browse_category),
    ("Q2", "$50 price band", 25, price_band),
    ("Q3", "Products with a tag", 20, by_tag),
    ("Q4", "Text search on name", 10, search_name),
    ("Q5", "Category rated >= 4.5, by price", 5, top_rated),
]


def index_stats(col):
    """Current $indexStats output, keyed by index name."""
    return {s["name"]: s for s in col.aggregate([{"$indexStats": {}}])}


def main():
    banner("Lab 6.1 Try It Yourself: Index usage with $indexStats")

    col = get_client()[DB_NAME][COLL_NAME]
    total_docs = col.estimated_document_count()
    missing = set(SERVES) - {idx["name"] for idx in col.list_indexes()}
    if total_docs == 0 or missing:
        sys.exit(
            f"[FAIL] {DB_NAME}.{COLL_NAME} is not seeded and indexed. Run Activity 6.1 first:\n"
            f"       python ../Activity_6.1/activity_6_1_index_design.py"
        )
    print(f"[OK] Querying {total_docs:,} products in {DB_NAME}.{COLL_NAME} (read-only).\n")

    categories = col.distinct("category")
    tags = col.distinct("tags")

    # $indexStats counters are cumulative since mongod started (or the index
    # was built), and explain() in Activity 6.1 already bumped them. Measure
    # this workload as the delta between two snapshots, not the raw totals.
    baseline = {name: s["accesses"]["ops"] for name, s in index_stats(col).items()}

    random.seed(7)
    print("=== Running the workload ===")
    for qid, label, runs, build in WORKLOAD:
        for _ in range(runs):
            flt, sort, limit = build(categories, tags)
            list(col.find(flt, sort=sort, limit=limit))  # iterate, or the query never executes
        print(f"  {qid} {label:34} x{runs}")
    print(f"[OK] Executed {sum(runs for _, _, runs, _ in WORKLOAD)} queries.\n")

    stats = index_stats(col)
    usage = sorted(
        ({"name": name,
          "key": dict(s["key"]),
          "ops": s["accesses"]["ops"] - baseline.get(name, 0),
          "ops_total": s["accesses"]["ops"],
          "since": s["accesses"]["since"]} for name, s in stats.items()),
        key=lambda row: row["ops"], reverse=True,
    )
    workload_ops = sum(row["ops"] for row in usage) or 1

    table = Table(title="$indexStats: Index Usage During the Workload")
    table.add_column("Rank", justify="right")
    table.add_column("Index", style="cyan")
    table.add_column("Serves")
    table.add_column("Ops", style="green", justify="right")
    table.add_column("Share", justify="right")
    table.add_column("Ops since start", justify="right")
    table.add_column("Tracking since (UTC)")
    for rank, row in enumerate(usage, 1):
        table.add_row(
            str(rank), row["name"], SERVES.get(row["name"], "-"),
            str(row["ops"]), f"{row['ops'] / workload_ops:.1%}",
            str(row["ops_total"]), row["since"].strftime("%Y-%m-%d %H:%M:%S"),
        )
    console.print(table)

    top = usage[0]
    print(f"\n  Most used: {top['name']} ({top['ops']} ops, serves {SERVES.get(top['name'], '-')}).")
    for line in NOTES.get(top["name"], []):
        print(f"  {line}")
    unused = [row["name"] for row in usage if row["ops"] == 0 and row["name"] != "_id_"]
    print(f"  Unused secondary indexes: {', '.join(unused) or 'none'} -- any listed here are drop candidates.")
    id_ops = next(row["ops"] for row in usage if row["name"] == "_id_")
    print(f"  _id_ shows {id_ops} ops; it is mandatory and cannot be dropped, whatever its usage.")
    print("  Note: $indexStats counters reset when mongod restarts or an index is rebuilt.")

    # --- MongoDB JSON for the submission ---------------------------------
    payload = {
        "database": DB_NAME,
        "collection": COLL_NAME,
        "workload": [{"query": qid, "description": label, "runs": runs} for qid, label, runs, _ in WORKLOAD],
        "pipeline": [{"$indexStats": {}}],
        "usage_ranked": usage,
        "most_used_index": top["name"],
        "unused_secondary_indexes": unused,
        "raw_index_stats": list(stats.values()),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=json_util.default))
    print(f"\n[OK] Wrote results JSON -> {OUT_JSON.name}")

    banner("Lab 6.1 Try It Yourself Complete")
    close_client()


if __name__ == "__main__":
    main()
