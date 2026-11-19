import json
import os
import random
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import OperationFailure
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
DOC_COUNT = 50_000
# explain() before/after results for the submission, written beside this script.
OUT_JSON = Path(__file__).resolve().parent / "activity_6_1_explain_results.json"

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


# --- Step 1: product data ---------------------------------------------------
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


def generate_products(count=DOC_COUNT):
    """Build `count` random products: name, category, price, rating, stock, tags."""
    random.seed(42)  # same dataset every run, so before/after numbers are comparable
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


# --- Step 2: the query workload ---------------------------------------------
# Q4 needs a text index to run at all, so before any index exists it is
# measured with the only thing that works without one: a case-insensitive
# $regex on name ("fallback").
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

# --- Step 4: the index design -------------------------------------------------
# Four indexes cover all five queries:
#   * {category, price, rating} follows ESR (Equality, Sort, Range) for Q5, and
#     its {category, price} prefix also serves Q1 -- one index, two queries,
#     and neither needs an in-memory SORT stage.
#   * {price} serves the Q2 range. The compound index can't: price is not its
#     leftmost field.
#   * {tags} is a multikey index -- one index entry per array element -- so Q3
#     jumps straight to the 'wireless' entries.
#   * A text index on name is required before $text (Q4) can run at all.
INDEXES = [
    ([("category", 1), ("price", 1), ("rating", 1)], "idx_category_price_rating", "Q1 (prefix), Q5 (ESR)"),
    ([("price", 1)], "idx_price", "Q2"),
    ([("tags", 1)], "idx_tags", "Q3 (multikey)"),
    ([("name", "text")], "idx_name_text", "Q4"),
]


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
        "executionTimeMillis": stats["executionTimeMillis"],
    }


def record(col, q, use_fallback=False):
    """Explain one query, print its metrics, and return the summary."""
    flt = q["fallback"] if use_fallback else q["filter"]
    s = plan_summary(explain(col, flt, q.get("sort"), q.get("limit")))
    s["note"] = "regex fallback" if use_fallback else ""
    suffix = "  [regex fallback]" if use_fallback else ""
    print(f"  {q['id']} {q['label']}{suffix}")
    print(f"    Stage: {s['stage']}")
    print(f"    Index: {s['index']}")
    print(f"    totalDocsExamined: {s['totalDocsExamined']:,}  totalKeysExamined: {s['totalKeysExamined']:,}"
          f"  nReturned: {s['nReturned']:,}  time: {s['executionTimeMillis']} ms")
    return s


def explain_all(col):
    """Steps 3-4: explain every query against the collection's current indexes."""
    has_text = any(idx["key"].get("_fts") == "text" for idx in col.list_indexes())
    results = {}
    for q in QUERIES:
        use_fallback = "fallback" in q and not has_text
        if use_fallback:
            # Show why the fallback is needed: $text is rejected outright.
            try:
                explain(col, q["filter"])
            except OperationFailure as exc:
                print(f"  {q['id']} $text without an index fails: {exc.details.get('errmsg', exc)}")
        results[q["id"]] = record(col, q, use_fallback)
    return results


def print_comparison(before, after):
    table = Table(title="explain(): Before vs After Indexing")
    table.add_column("Query", style="cyan")
    table.add_column("Before stage")
    table.add_column("Docs", justify="right")
    table.add_column("After stage")
    table.add_column("Index used", style="magenta")
    table.add_column("Docs", justify="right")
    table.add_column("Keys", justify="right")
    table.add_column("Docs cut", style="green", justify="right")
    for q in QUERIES:
        b, a = before[q["id"]], after[q["id"]]
        cut = 1 - a["totalDocsExamined"] / b["totalDocsExamined"] if b["totalDocsExamined"] else 0
        table.add_row(
            q["id"] + ("*" if b["note"] else ""),
            b["stage"], f"{b['totalDocsExamined']:,}",
            a["stage"], a["index"],
            f"{a['totalDocsExamined']:,}", f"{a['totalKeysExamined']:,}",
            f"{cut:.2%}",
        )
    console.print(table)
    for q in QUERIES:
        if before[q["id"]]["note"]:
            print(f"  * {q['id']} 'before' is the $regex fallback -- $text cannot run without a text index.")

    # An index changes how fast a query runs, never what it returns.
    for q in QUERIES:
        b, a = before[q["id"]], after[q["id"]]
        status = "same" if a["nReturned"] == b["nReturned"] else f"MISMATCH (before={b['nReturned']:,})"
        print(f"  {q['id']}: nReturned={a['nReturned']:,} ({status})")


def main():
    banner("Lab 6.1: Index Design for a Query Workload")
    col = reset_collection(DB_NAME, COLL_NAME)

    # Step 1: generate and load the data
    print(f"Generating {DOC_COUNT:,} products... (may take a few seconds)")
    doc_count = len(col.insert_many(generate_products()).inserted_ids)
    samples = list(col.find({}, {"_id": 0}).limit(3))
    print(f"[OK] Inserted {doc_count:,} products into {DB_NAME}.{COLL_NAME}.")
    print(f"     Sample: {samples[0]}\n")

    # Steps 2-3: the five queries, explained before any index exists
    print("=== Step 3: EXPLAIN without indexes (only _id_) ===")
    before = explain_all(col)

    # Step 4: create the designed indexes, then explain again
    print("\n=== Step 4: Creating Indexes ===")
    for keys, name, serves in INDEXES:
        col.create_index(keys, name=name)
        print(f"  {name:28} keys={dict(keys)}  -> {serves}")
    print(f"[OK] Created {len(INDEXES)} indexes.\n")

    print("=== Step 4: EXPLAIN with indexes ===")
    after = explain_all(col)

    print()
    print_comparison(before, after)

    print("\n=== Index Summary ===")
    for idx in col.list_indexes():
        print(f"  {idx['name']:30} keys={dict(idx['key'])}")

    # --- MongoDB JSON for the submission ---------------------------------
    payload = {
        "database": DB_NAME,
        "collection": COLL_NAME,
        "documents": doc_count,
        "sample_documents": samples,
        "queries": QUERIES,
        "indexes": [{"name": name, "key": dict(keys), "serves": serves} for keys, name, serves in INDEXES],
        "explain_before": before,
        "explain_after": after,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=json_util.default))
    print(f"\n[OK] Wrote explain results JSON -> {OUT_JSON.name}")

    banner("Lab 6.1 Complete")
    close_client()


if __name__ == "__main__":
    main()
