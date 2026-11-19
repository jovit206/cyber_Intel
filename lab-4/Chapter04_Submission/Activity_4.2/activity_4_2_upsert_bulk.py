"""Lab 4.2 - Implement Upsert and Bulk Operations

Conditional inserts + batch modifications, driven by real rows from the Amazon
product export instead of a hand-written seed list.

Prerequisite:
    python load_amazon_products.py

The inventory collection is seeded at runtime from real bestsellers in
nosql_labs.products, keyed by ASIN. Nothing is hardcoded, so this still works
if you reloaded the CSV with --limit.

A note on the numeric field: the source CSV has no stock column, so this lab
uses `unitsSoldLastMonth` (the real boughtInLastMonth column) as the $inc
target. "Sold 10 more units" is the same non-idempotency lesson as "stock -10",
without inventing data that isn't in the export.
"""

import argparse
import json
import sys
from pathlib import Path

from bson import json_util
from pymongo import UpdateOne, DESCENDING
from rich.console import Console
from rich.table import Table

# --- self-contained MongoDB connection --------------------------------------
# This activity folder runs on its own -- there is no shared package to install.
# Settings come from MONGO_URI / MONGO_DB in the environment or a .env beside
# this script, falling back to a local mongod on the default port.
import os
from pathlib import Path

from pymongo import MongoClient

try:
    from dotenv import load_dotenv
    for _env in (Path(__file__).resolve().parent / ".env",
                 Path(__file__).resolve().parent.parent / ".env"):
        if _env.exists():
            load_dotenv(_env)
            break
except ImportError:
    pass

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
        _client = MongoClient(uri, **kwargs)
        _client.admin.command("ping")
        print(f"[OK] Connected to MongoDB at {uri.split('@')[-1]}")
    return _client


def get_db(db_name=None):
    """Return a database handle."""
    return get_client()[db_name or os.getenv("MONGO_DB", "nosql_labs")]


def reset_collection(db_name, collection_name):
    """Drop and recreate a collection."""
    db = get_db(db_name)
    if collection_name in db.list_collection_names():
        db.drop_collection(collection_name)
        print(f"[OK] Dropped collection: {db_name}.{collection_name}")
    return db[collection_name]


def close_client():
    """Close the connection."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        print("[OK] MongoDB connection closed.")


def banner(text, char="=", width=72):
    """Print a section banner."""
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)
    print()
# ----------------------------------------------------------------------------

console = Console()

DB_NAME = "nosql_labs"
SOURCE_COLL = "products"
INVENTORY_COLL = "inventory"
SEED_SIZE = 4

# Every result the lab produces is collected here for the --json export.
CAPTURED: dict = {"seed": {}, "upsert": {}, "bulk_run_1": {}, "bulk_run_2": {},
                  "idempotency": {}}


def build_ops(specs):
    """Turn [(note, filter, update, upsert), ...] into UpdateOne operations.

    The specs are kept alongside the operations so the JSON export can describe
    exactly what was sent without reaching into pymongo's private attributes.
    """
    return [UpdateOne(f, u, upsert=up) for _, f, u, up in specs]


def describe_ops(specs):
    """Render the bulk operation list as plain data for the JSON export."""
    return [{"note": note, "filter": f, "update": u, "upsert": up}
            for note, f, u, up in specs]


def bulk_result_dict(result):
    return {
        "matched": result.matched_count,
        "modified": result.modified_count,
        "upserted": result.upserted_count,
        "upserted_ids": result.upserted_ids,
    }


def fetch_seed_products(db, n=SEED_SIZE):
    """Pull real products from the imported dataset to stock the inventory."""
    cursor = (db[SOURCE_COLL]
              .find({"isBestSeller": True, "price": {"$gt": 0}},
                    {"asin": 1, "title": 1, "price": 1, "listPrice": 1,
                     "metrics.boughtInLastMonth": 1, "_id": 0})
              .sort("metrics.boughtInLastMonth", DESCENDING)
              .limit(n + 1))
    rows = list(cursor)
    if len(rows) < n + 1:
        sys.exit(f"[FAIL] Only {len(rows)} source products found in "
                 f"{DB_NAME}.{SOURCE_COLL}.\n       Run:  python load_amazon_products.py")

    docs = [{
        "asin": r["asin"],
        "title": r["title"][:60],
        "price": r["price"],
        "listPrice": r["listPrice"],
        "unitsSoldLastMonth": r["metrics"]["boughtInLastMonth"],
    } for r in rows]
    # The last one is held back so the "upsert a new key" demo has a real ASIN
    # that is genuinely absent from the inventory collection.
    return docs[:n], docs[n]


def upsert_product(col, asin, update_fields):
    """Upsert a product by ASIN. Creates if not exists, updates if it does."""
    result = col.update_one(
        {"asin": asin},
        {"$set": update_fields, "$setOnInsert": {"asin": asin}},
        upsert=True,
    )
    action = "updated" if result.matched_count else "inserted"
    print(f"  [{action.upper():8}] ASIN={asin} "
          f"(matched={result.matched_count}, upserted_id={'YES' if result.upserted_id else 'NO'})")
    return result


def print_inventory(col, heading, capture_key=None):
    """Render the whole inventory as a table, and record it for --json."""
    docs = list(col.find({}, {"_id": 0}).sort("asin"))

    table = Table(title=heading, title_style="bold cyan", header_style="bold")
    table.add_column("ASIN", overflow="fold")
    table.add_column("Title", overflow="fold")
    table.add_column("Price", justify="right")
    table.add_column("Sold/mo", justify="right")
    table.add_column("Active", justify="center")
    for doc in docs:
        active = doc.get("active")
        table.add_row(
            str(doc.get("asin", "N/A")),
            str(doc.get("title", "N/A"))[:44],
            f"${doc.get('price', 0):,.2f}",
            f"{doc.get('unitsSoldLastMonth', 0):,}",
            "-" if active is None else str(active),
        )
    console.print(table)

    if capture_key:
        CAPTURED[capture_key]["inventory"] = docs
    return docs


def main():
    parser = argparse.ArgumentParser(
        description="Lab 4.2 - upsert, bulk writes and idempotency")
    parser.add_argument("--json", metavar="PATH", nargs="?",
                        const="lab_02_results.json", default=None,
                        help="write every upsert/bulk result to a JSON file "
                             "(default: exports/lab_02_results.json)")
    parser.add_argument("--seed-size", type=int, default=SEED_SIZE,
                        help=f"products to seed the inventory with "
                             f"(default {SEED_SIZE}, minimum {SEED_SIZE})")
    args = parser.parse_args()

    # The bulk-write demo targets four distinct products (one per operator), so
    # a smaller inventory would make two operations collide on one document and
    # muddy the idempotency comparison.
    if args.seed_size < SEED_SIZE:
        parser.error(f"--seed-size must be at least {SEED_SIZE}: the bulk write "
                     f"demonstrates $inc, $mul, $set and an upsert on four "
                     f"separate products.")

    banner("Lab 4.2: Upsert and Bulk Operations (Amazon product export)")

    db = get_db(DB_NAME)
    source_count = db[SOURCE_COLL].estimated_document_count()
    if source_count == 0:
        sys.exit(f"[FAIL] {DB_NAME}.{SOURCE_COLL} is empty.\n"
                 f"       Run:  python load_amazon_products.py")

    seed, held_back = fetch_seed_products(db, args.seed_size)
    col = reset_collection(DB_NAME, INVENTORY_COLL)
    col.insert_many(seed)
    col.create_index("asin", unique=True)
    print(f"[OK] Seeded {len(seed)} real products into {DB_NAME}.{INVENTORY_COLL}.")
    CAPTURED["seed"] = {
        "source_collection": SOURCE_COLL,
        "source_documents": source_count,
        "seeded": seed,
        "held_back_for_upsert_demo": held_back,
    }
    print_inventory(col, "Starting inventory", capture_key="seed")

    existing = seed[0]["asin"]

    # --- UPSERT DEMO ---
    print("\n=== Upsert: existing ASIN (should update) ===")
    new_price = round(seed[0]["price"] * 0.85, 2)
    result = upsert_product(col, existing, {"price": new_price})
    after_price = col.find_one({"asin": existing}, {"price": 1, "_id": 0})["price"]
    print(f"  Result: price {seed[0]['price']} -> {after_price}")
    CAPTURED["upsert"]["existing_key"] = {
        "asin": existing,
        "update": {"price": new_price},
        "matched": result.matched_count,
        "modified": result.modified_count,
        "upserted_id": result.upserted_id,
        "price_before": seed[0]["price"],
        "price_after": after_price,
        "outcome": "updated in place - no new document",
    }

    print("\n=== Upsert: ASIN not yet in inventory (should insert) ===")
    fields = {"title": held_back["title"], "price": held_back["price"],
              "unitsSoldLastMonth": held_back["unitsSoldLastMonth"]}
    result = upsert_product(col, held_back["asin"], fields)
    inserted_doc = col.find_one({"asin": held_back["asin"]}, {"_id": 0})
    print(f"  Result: {inserted_doc}")
    CAPTURED["upsert"]["absent_key"] = {
        "asin": held_back["asin"],
        "update": fields,
        "matched": result.matched_count,
        "modified": result.modified_count,
        "upserted_id": result.upserted_id,
        "document": inserted_doc,
        "outcome": "inserted - filter + update became a new document",
    }

    # --- BULK WRITE DEMO ---
    # (note, filter, update, upsert) so the JSON can describe what was sent.
    specs = [
        ("$inc - sold 10 more units (relative, NOT idempotent)",
         {"asin": seed[0]["asin"]}, {"$inc": {"unitsSoldLastMonth": 10}}, False),
        ("$mul - apply a 10% discount (relative, NOT idempotent)",
         {"asin": seed[1]["asin"]}, {"$mul": {"price": 0.9}}, False),
        ("$set - mark active (literal, idempotent)",
         {"asin": seed[2]["asin"]}, {"$set": {"active": True}}, False),
        ("$set + upsert - key does not exist yet, so this inserts",
         {"asin": f"LAB-{seed[3]['asin']}"},
         {"$set": {"title": "Lab Bundle - " + seed[3]["title"][:40],
                   "price": 79.99, "unitsSoldLastMonth": 0}}, True),
    ]
    operations = build_ops(specs)

    print(f"\n=== Bulk Write: {len(operations)} operations in one round trip ===")
    for note, *_ in specs:
        print(f"    - {note}")
    bulk_result = col.bulk_write(operations)
    print(f"  Matched:      {bulk_result.matched_count}")
    print(f"  Modified:     {bulk_result.modified_count}")
    print(f"  Upserted:     {bulk_result.upserted_count}")
    print(f"  Upserted IDs: {bulk_result.upserted_ids}")
    CAPTURED["bulk_run_1"] = {"operations": describe_ops(specs),
                              "result": bulk_result_dict(bulk_result)}
    print_inventory(col, "Inventory after run 1", capture_key="bulk_run_1")

    # Capture the fields we are about to re-touch, to compare after run 2.
    watched = {"$inc": seed[0]["asin"], "$mul": seed[1]["asin"], "$set": seed[2]["asin"]}
    before = {op: col.find_one({"asin": asin}, {"_id": 0}) for op, asin in watched.items()}

    # --- IDEMPOTENCY DEMO: the exact same bulk write, a second time ---
    print("\n=== Re-running the SAME bulk write (not all operators are idempotent) ===")
    bulk_result2 = col.bulk_write(operations)
    print(f"  Matched:      {bulk_result2.matched_count}")
    print(f"  Modified:     {bulk_result2.modified_count}")
    print(f"  Upserted:     {bulk_result2.upserted_count}  <- 0: the key now exists")
    CAPTURED["bulk_run_2"] = {"operations": describe_ops(specs),
                              "result": bulk_result_dict(bulk_result2)}
    print_inventory(col, "Inventory after run 2", capture_key="bulk_run_2")

    after = {op: col.find_one({"asin": asin}, {"_id": 0}) for op, asin in watched.items()}

    inc_b, inc_a = before["$inc"]["unitsSoldLastMonth"], after["$inc"]["unitsSoldLastMonth"]
    mul_b, mul_a = before["$mul"]["price"], after["$mul"]["price"]
    set_b, set_a = before["$set"]["active"], after["$set"]["active"]

    diff = Table(title="What moved on the second run", title_style="bold cyan",
                 header_style="bold")
    # Short headers so the row content never has to wrap or truncate.
    for header in ("Op", "Field", "Run 1", "Run 2", "Verdict"):
        diff.add_column(header, no_wrap=True)
    diff.add_row("$inc", "unitsSoldLastMonth", f"{inc_b:,}", f"{inc_a:,}",
                 "CHANGED - not idempotent" if inc_a != inc_b else "unchanged")
    diff.add_row("$mul", "price", f"${mul_b:,.2f}", f"${mul_a:,.2f}",
                 "CHANGED - not idempotent" if mul_a != mul_b else "unchanged")
    diff.add_row("$set", "active", str(set_b), str(set_a),
                 "CHANGED" if set_a != set_b else "unchanged - $set IS idempotent")
    console.print(diff)

    CAPTURED["idempotency"] = {
        "$inc": {"field": "unitsSoldLastMonth", "asin": watched["$inc"],
                 "after_run_1": inc_b, "after_run_2": inc_a,
                 "idempotent": inc_a == inc_b},
        "$mul": {"field": "price", "asin": watched["$mul"],
                 "after_run_1": mul_b, "after_run_2": mul_a,
                 "idempotent": mul_a == mul_b},
        "$set": {"field": "active", "asin": watched["$set"],
                 "after_run_1": set_b, "after_run_2": set_a,
                 "idempotent": set_a == set_b},
    }

    print("\n  $set writes a literal value, so applying it twice lands on the same")
    print("  state. $inc and $mul are relative to the current value, so each run")
    print("  moves the field again. That distinction is the whole reason retry")
    print("  logic is dangerous around non-idempotent updates: a network timeout")
    print("  on a write that actually succeeded server-side, followed by a client")
    print("  retry, silently double-applies the $inc.")

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = Path(__file__).resolve().parent / out
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "database": DB_NAME,
            "inventory_collection": INVENTORY_COLL,
            "seed_size": len(seed),
            "results": CAPTURED,
        }
        out.write_text(json.dumps(payload, indent=2, default=json_util.default,
                                  ensure_ascii=False))
        print(f"\n[OK] Wrote results JSON -> {out}  ({out.stat().st_size / 1024:,.1f} KB)")

    banner("Lab 4.2 Complete")
    close_client()


if __name__ == "__main__":
    main()
