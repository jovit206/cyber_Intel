"""Lab 5.2 Try It Yourself - $lookup with a pipeline sub-expression

The plain join form:

    {"$lookup": {"from": ..., "localField": ..., "foreignField": ..., "as": ...}}

can only say "match these two keys". It drags back EVERY matching foreign
document, and any further filtering has to happen afterwards, in a later stage,
on data that has already crossed the join.

The 3.6+ form replaces localField/foreignField with `let` + `pipeline`:

    {"$lookup": {
        "from":     <foreign collection>,
        "let":      {<var>: <expression on the LOCAL document>},
        "pipeline": [ ...stages that run on the FOREIGN collection... ],
        "as":       <output array>
    }}

`let` copies local fields into variables the sub-pipeline reads as $$var, and
the sub-pipeline is an ordinary aggregation over the foreign collection. So the
$match, the $sort, the $limit and the $project all run on the foreign side --
the joined documents are filtered and trimmed BEFORE they are returned.

This script reads the two collections seeded by Activity 5.2 (read-only) and
demonstrates:
    1. a sub-pipeline that filters the foreign side on status, date and quantity
    2. the same question answered the old way, to show what the join drags back
    3. $sort + $limit inside the sub-pipeline -- a top-N-per-product join
"""

import json
import os
import sys
from datetime import datetime
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

DB_NAME = "Chapter05"
PRODUCTS_COLL = "products"
ORDERS_COLL = "product_orders"
OUT_JSON = Path(__file__).resolve().parent / "activity_5_2_try_it_yourself_filtered_joins.json"

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


# The filter applied on the FOREIGN side. Only orders that satisfy all three
# conditions are allowed across the join.
QUARTER_START = datetime(2024, 1, 1)
QUARTER_END = datetime(2024, 4, 1)   # exclusive -- Q1 2024
MIN_QUANTITY = 2


def main():
    banner("Lab 5.2 Try It Yourself: $lookup with a pipeline sub-expression")

    db = get_client()[DB_NAME]
    products_col = db[PRODUCTS_COLL]
    orders_col = db[ORDERS_COLL]

    n_products = products_col.count_documents({})
    n_orders = orders_col.count_documents({})
    if n_products == 0 or n_orders == 0:
        sys.exit(
            f"[FAIL] {DB_NAME}.{PRODUCTS_COLL} / {DB_NAME}.{ORDERS_COLL} is empty. Seed them first:\n"
            f"       python ../Activity_5.2/activity_5_2_lookup_joins.py"
        )
    print(f"[OK] Reading {n_products} products and {n_orders} orders from {DB_NAME} (read-only).")
    print(f"[OK] Foreign-side filter: status='completed', "
          f"order_date in [{QUARTER_START:%Y-%m-%d}, {QUARTER_END:%Y-%m-%d}), "
          f"quantity >= {MIN_QUANTITY}.\n")

    # --- 1. FILTER THE FOREIGN SIDE INSIDE THE SUB-PIPELINE --------------
    # $expr is what joins the two sides: it is the only way to compare a field
    # of the foreign document ("$product_id") against a `let` variable
    # ("$$pid"). The other three clauses are plain query operators, so they can
    # use an index on the foreign collection.
    print("=== 1. Sub-pipeline join: only qualifying orders come back ===")
    pipeline_filtered = [
        {"$lookup": {
            "from": ORDERS_COLL,
            "let": {"pid": "$product_id", "price": "$base_price"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$product_id", "$$pid"]},   # the join condition
                    "status": "completed",                         # foreign-side filters
                    "order_date": {"$gte": QUARTER_START, "$lt": QUARTER_END},
                    "quantity": {"$gte": MIN_QUANTITY},
                }},
                # Trimming here means the narrow shape is what crosses the
                # join -- and $$price, a LOCAL field, is usable right here.
                {"$project": {
                    "_id": 0,
                    "order_id": 1,
                    "customer": 1,
                    "quantity": 1,
                    "order_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$order_date"}},
                    "line_total": {"$multiply": ["$quantity", "$$price"]},
                }},
                {"$sort": {"order_date": 1}},
            ],
            "as": "qualifying_orders",
        }},
        # Products with nothing qualifying come back with an empty array; drop them.
        {"$match": {"qualifying_orders": {"$ne": []}}},
        {"$project": {
            "_id": 0,
            "product_id": 1,
            "name": 1,
            "category": 1,
            "base_price": 1,
            "qualifying_orders": 1,
            "matched": {"$size": "$qualifying_orders"},
            "revenue": {"$sum": "$qualifying_orders.line_total"},
        }},
        {"$sort": {"revenue": -1}},
    ]
    filtered = list(products_col.aggregate(pipeline_filtered))

    table = Table(title="Q1 2024 completed orders of 2+ units, joined per product")
    table.add_column("ID", style="cyan")
    table.add_column("Product", style="magenta")
    table.add_column("Matched", justify="right")
    table.add_column("Orders joined")
    table.add_column("Revenue", style="green", justify="right")
    for row in filtered:
        detail = ", ".join(
            f"{o['order_id']} ({o['customer']} x{o['quantity']})" for o in row["qualifying_orders"]
        )
        table.add_row(row["product_id"], row["name"], str(row["matched"]),
                      detail, f"${row['revenue']:,}")
    console.print(table)
    joined_docs = sum(r["matched"] for r in filtered)
    print(f"  {len(filtered)} products kept, {joined_docs} order documents pulled across the join.")

    # --- 2. THE SAME QUESTION WITHOUT A SUB-PIPELINE ---------------------
    # localField/foreignField cannot express any of the three filters, so the
    # join returns every order for every product and the filtering has to be
    # redone afterwards with $filter. Same answer, more data moved.
    print("\n=== 2. Same answer the old way (localField/foreignField + $filter) ===")
    pipeline_naive = [
        {"$lookup": {
            "from": ORDERS_COLL,
            "localField": "product_id",
            "foreignField": "product_id",
            "as": "all_orders",           # everything, unfiltered
        }},
        {"$set": {
            "qualifying_orders": {
                "$filter": {
                    "input": "$all_orders",
                    "as": "o",
                    "cond": {"$and": [
                        {"$eq": ["$$o.status", "completed"]},
                        {"$gte": ["$$o.order_date", QUARTER_START]},
                        {"$lt": ["$$o.order_date", QUARTER_END]},
                        {"$gte": ["$$o.quantity", MIN_QUANTITY]},
                    ]},
                }
            }
        }},
        {"$project": {
            "_id": 0,
            "product_id": 1,
            "name": 1,
            "pulled": {"$size": "$all_orders"},         # what the join really returned
            "kept": {"$size": "$qualifying_orders"},    # what survived the later filter
        }},
        {"$sort": {"pulled": -1}},
    ]
    naive = list(products_col.aggregate(pipeline_naive))

    table = Table(title="Documents pulled across the join vs. actually needed")
    table.add_column("ID", style="cyan")
    table.add_column("Product", style="magenta")
    table.add_column("Pulled by join", justify="right")
    table.add_column("Kept after $filter", justify="right")
    table.add_column("Wasted", style="red", justify="right")
    for row in naive:
        table.add_row(row["product_id"], row["name"], str(row["pulled"]),
                      str(row["kept"]), str(row["pulled"] - row["kept"]))
    console.print(table)
    pulled_total = sum(r["pulled"] for r in naive)
    kept_total = sum(r["kept"] for r in naive)
    print(f"  Plain join moved {pulled_total} order documents to keep {kept_total}.")
    print(f"  Sub-pipeline join moved {joined_docs} -- the filter ran on the foreign side, "
          f"before the documents were returned.")
    assert kept_total == joined_docs, "both forms must produce the same answer"
    print("  [OK] Both forms agree on the result; only the volume differs.")

    # --- 3. $sort + $limit INSIDE THE SUB-PIPELINE -----------------------
    # A top-N-per-parent join. There is no way to express this at all with
    # localField/foreignField -- $limit has to run on the foreign side.
    print("\n=== 3. Top 2 largest completed orders per product (sub-pipeline $sort + $limit) ===")
    pipeline_topn = [
        {"$lookup": {
            "from": ORDERS_COLL,
            "let": {"pid": "$product_id", "price": "$base_price"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$product_id", "$$pid"]}, "status": "completed"}},
                {"$sort": {"quantity": -1}},
                {"$limit": 2},
                {"$project": {
                    "_id": 0, "order_id": 1, "customer": 1, "quantity": 1,
                    "line_total": {"$multiply": ["$quantity", "$$price"]},
                }},
            ],
            "as": "top_orders",
        }},
        {"$match": {"top_orders": {"$ne": []}}},
        {"$project": {"_id": 0, "product_id": 1, "name": 1, "top_orders": 1}},
        {"$sort": {"product_id": 1}},
    ]
    top_n = list(products_col.aggregate(pipeline_topn))
    for row in top_n:
        print(f"\n  {row['product_id']}  {row['name']}")
        for o in row["top_orders"]:
            print(f"    {o['order_id']}  {o['customer']:<8} qty {o['quantity']:>2}"
                  f"  = ${o['line_total']:>6,}")

    # --- MongoDB JSON for the submission ---------------------------------
    payload = {
        "database": DB_NAME,
        "collections": {"products": PRODUCTS_COLL, "orders": ORDERS_COLL},
        "foreign_side_filter": {
            "status": "completed",
            "order_date": {"$gte": QUARTER_START, "$lt": QUARTER_END},
            "quantity": {"$gte": MIN_QUANTITY},
        },
        "pipeline_1_subpipeline_join": {"pipeline": pipeline_filtered, "results": filtered},
        "pipeline_2_plain_join_comparison": {
            "pipeline": pipeline_naive,
            "results": naive,
            "documents_pulled": pulled_total,
            "documents_kept": kept_total,
            "documents_pulled_by_subpipeline": joined_docs,
        },
        "pipeline_3_top_n_per_product": {"pipeline": pipeline_topn, "results": top_n},
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=json_util.default))
    print(f"\n[OK] Wrote results JSON -> {OUT_JSON.name}")

    banner("Lab 5.2 Try It Yourself Complete")
    close_client()


if __name__ == "__main__":
    main()
