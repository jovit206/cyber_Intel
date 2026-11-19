"""Lab 5.2 - Join Data with $lookup

Two collections, deliberately NOT embedded:

    products          name, category, base_price   (the reference data)
    product_orders    product_id, quantity, ...    (points at products by id)

Because the price lives only in `products`, every order total has to be
computed at read time by joining the two collections -- which is exactly what
$lookup is for.

Pipelines in this script:
    1. orders JOIN products  -> order date, product name, quantity, line total
    2. products LEFT JOIN orders, keep the misses -> products never ordered
    3. products LEFT JOIN orders (let/pipeline form) -> revenue per product
"""

import json
import os
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
# Both collections in one file -- the join only makes sense with both halves.
OUT_JSON = Path(__file__).resolve().parent / "activity_5_2_products_and_orders.json"

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


def export_collections(db, path: Path) -> None:
    """Dump both collections to one JSON file. json_util keeps BSON types
    (dates, ObjectIds) in extended-JSON form, so the file round-trips."""
    payload = {
        "database": DB_NAME,
        "collections": {
            PRODUCTS_COLL: list(db[PRODUCTS_COLL].find({}).sort("product_id", 1)),
            ORDERS_COLL: list(db[ORDERS_COLL].find({}).sort("order_id", 1)),
        },
    }
    path.write_text(json.dumps(payload, indent=2, default=json_util.default))
    counts = {k: len(v) for k, v in payload["collections"].items()}
    print(f"[OK] Wrote {counts} -> {path.name}")


def banner(text: str, char: str = "=", width: int = 72) -> None:
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)
    print()


def product(product_id, name, category, base_price):
    """Build one product document. base_price lives HERE and nowhere else."""
    return {
        "product_id": product_id,
        "name": name,
        "category": category,
        "base_price": base_price,
    }


def order(order_id, customer, product_id, quantity, order_date, status, region):
    """Build one order document.

    Note what is missing: no product name, no price. The order references the
    product by id only -- the join is what puts the two halves back together.
    """
    return {
        "order_id": order_id,
        "customer": customer,
        "product_id": product_id,
        "quantity": quantity,
        "order_date": datetime.strptime(order_date, "%Y-%m-%d"),
        "status": status,
        "region": region,
    }


# 12 products. The last four are never referenced by any order -- they are what
# pipeline 2 is meant to find.
PRODUCTS = [
    product("P001", "Laptop Pro 14",         "Electronics", 1200),
    product("P002", "27-inch Monitor",       "Electronics",  320),
    product("P003", "Mechanical Keyboard",   "Accessories",   85),
    product("P004", "Wireless Mouse",        "Accessories",   45),
    product("P005", "ANC Headphones",        "Audio",        150),
    product("P006", "Docking Station",       "Accessories",  210),
    product("P007", "HD Webcam",             "Electronics",   95),
    product("P008", "Ergonomic Chair",       "Furniture",    260),
    product("P009", "Standing Desk",         "Furniture",    540),
    product("P010", "USB-C Hub",             "Accessories",   65),
    product("P011", "Studio Microphone",     "Audio",        180),
    product("P012", "Laptop Stand",          "Furniture",     70),
]

# 20 orders: 6 customers, Jan-Apr 2024, three statuses, 8 distinct products.
ORDERS = [
    order("ORD-201", "Arjun", "P001", 1, "2024-01-05", "completed", "North"),
    order("ORD-202", "Sneha", "P002", 2, "2024-01-11", "completed", "South"),
    order("ORD-203", "Kavya", "P003", 3, "2024-01-17", "completed", "East"),
    order("ORD-204", "Rahul", "P001", 2, "2024-01-23", "completed", "West"),
    order("ORD-205", "Divya", "P004", 4, "2024-01-29", "cancelled", "North"),
    order("ORD-206", "Arjun", "P005", 2, "2024-02-03", "completed", "North"),
    order("ORD-207", "Imran", "P006", 1, "2024-02-08", "pending",   "South"),
    order("ORD-208", "Sneha", "P001", 1, "2024-02-12", "completed", "South"),
    order("ORD-209", "Kavya", "P007", 2, "2024-02-16", "completed", "East"),
    order("ORD-210", "Rahul", "P008", 3, "2024-02-21", "completed", "West"),
    order("ORD-211", "Divya", "P002", 1, "2024-02-27", "completed", "North"),
    order("ORD-212", "Arjun", "P006", 2, "2024-03-04", "completed", "North"),
    order("ORD-213", "Imran", "P001", 1, "2024-03-09", "cancelled", "South"),
    order("ORD-214", "Sneha", "P003", 5, "2024-03-14", "completed", "South"),
    order("ORD-215", "Rahul", "P005", 4, "2024-03-19", "completed", "West"),
    order("ORD-216", "Kavya", "P004", 6, "2024-03-25", "pending",   "East"),
    order("ORD-217", "Arjun", "P008", 1, "2024-04-02", "completed", "North"),
    order("ORD-218", "Divya", "P001", 1, "2024-04-07", "completed", "North"),
    order("ORD-219", "Rahul", "P002", 3, "2024-04-13", "completed", "West"),
    order("ORD-220", "Imran", "P005", 2, "2024-04-18", "completed", "South"),
]

# quantity lives on the order, base_price on the product -- so this expression
# only makes sense AFTER the join has flattened the product into the document.
LINE_TOTAL = {"$multiply": ["$quantity", "$product.base_price"]}


def main():
    banner("Lab 5.2: Join Data with $lookup")

    products_col = reset_collection(DB_NAME, PRODUCTS_COLL)
    orders_col = reset_collection(DB_NAME, ORDERS_COLL)
    products_col.insert_many(PRODUCTS)
    orders_col.insert_many(ORDERS)
    print(f"[OK] Inserted {len(PRODUCTS)} products into {DB_NAME}.{PRODUCTS_COLL}.")
    print(f"[OK] Inserted {len(ORDERS)} orders into {DB_NAME}.{ORDERS_COLL}.\n")

    # --- PIPELINE 1: orders JOIN products -------------------------------
    # $lookup always produces an ARRAY (`product`), even for a one-to-one
    # match. $unwind collapses that single-element array into a subdocument so
    # $product.base_price can be addressed directly.
    print("=== 1. Orders Joined to Products (order date, product, qty, total) ===")
    pipeline_join = [
        {"$lookup": {
            "from": PRODUCTS_COLL,
            "localField": "product_id",
            "foreignField": "product_id",
            "as": "product",
        }},
        {"$unwind": "$product"},
        {"$project": {
            "_id": 0,
            "order_id": 1,
            "customer": 1,
            "order_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$order_date"}},
            "product_name": "$product.name",
            "category": "$product.category",
            "quantity": 1,
            "base_price": "$product.base_price",
            "order_total": LINE_TOTAL,
        }},
        {"$sort": {"order_date": 1}},
    ]
    joined = list(orders_col.aggregate(pipeline_join))

    table = Table(title="Order Lines (orders ⋈ products)")
    table.add_column("Order", style="cyan")
    table.add_column("Date")
    table.add_column("Customer")
    table.add_column("Product", style="magenta")
    table.add_column("Qty", justify="right")
    table.add_column("Unit", justify="right")
    table.add_column("Total", style="green", justify="right")
    for row in joined:
        table.add_row(
            row["order_id"], row["order_date"], row["customer"], row["product_name"],
            str(row["quantity"]), f"${row['base_price']:,}", f"${row['order_total']:,}",
        )
    console.print(table)
    print(f"  {len(joined)} of {len(ORDERS)} orders matched a product "
          f"(an $unwind with no preserveNullAndEmptyArrays drops the misses).")
    print(f"  Grand total: ${sum(r['order_total'] for r in joined):,}")

    # --- PIPELINE 2: products LEFT JOIN orders, keep the misses ----------
    # This is the left outer join. Every product survives the $lookup; the ones
    # nobody ordered come back with an EMPTY `orders` array, and $size: 0 is
    # what isolates them.
    print("\n=== 2. Products Never Ordered (left outer join + empty-array filter) ===")
    pipeline_never = [
        {"$lookup": {
            "from": ORDERS_COLL,
            "localField": "product_id",
            "foreignField": "product_id",
            "as": "orders",
        }},
        {"$match": {"orders": {"$size": 0}}},
        {"$project": {"_id": 0, "product_id": 1, "name": 1, "category": 1, "base_price": 1}},
        {"$sort": {"product_id": 1}},
    ]
    never_ordered = list(products_col.aggregate(pipeline_never))
    if never_ordered:
        table = Table(title="Products With Zero Orders")
        table.add_column("ID", style="cyan")
        table.add_column("Product", style="magenta")
        table.add_column("Category")
        table.add_column("Base Price", justify="right")
        for p in never_ordered:
            table.add_row(p["product_id"], p["name"], p["category"], f"${p['base_price']:,}")
        console.print(table)
        print(f"  {len(never_ordered)} of {len(PRODUCTS)} products have never been ordered.")
    else:
        print("  All products have been ordered at least once.")

    # --- PIPELINE 3: let/pipeline $lookup -> revenue per product ---------
    # The other $lookup form: `let` exposes fields of the products document to
    # a sub-pipeline, and $$pid / $$price are how the sub-pipeline reads them.
    # Filtering to completed orders inside the sub-pipeline is something
    # localField/foreignField simply cannot express.
    print("\n=== 3. Revenue per Product (let/pipeline $lookup, completed orders only) ===")
    pipeline_revenue = [
        {"$lookup": {
            "from": ORDERS_COLL,
            "let": {"pid": "$product_id", "price": "$base_price"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$product_id", "$$pid"]},
                    "status": "completed",
                }},
                {"$group": {
                    "_id": None,
                    "units": {"$sum": "$quantity"},
                    "orders": {"$sum": 1},
                    "revenue": {"$sum": {"$multiply": ["$quantity", "$$price"]}},
                }},
            ],
            "as": "stats",
        }},
        # preserveNullAndEmptyArrays keeps the zero-revenue products in the
        # result instead of silently dropping them.
        {"$unwind": {"path": "$stats", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "_id": 0,
            "product_id": 1,
            "name": 1,
            "category": 1,
            "orders": {"$ifNull": ["$stats.orders", 0]},
            "units": {"$ifNull": ["$stats.units", 0]},
            "revenue": {"$ifNull": ["$stats.revenue", 0]},
        }},
        {"$sort": {"revenue": -1, "product_id": 1}},
    ]
    table = Table(title="Product Revenue (completed orders)")
    table.add_column("ID", style="cyan")
    table.add_column("Product", style="magenta")
    table.add_column("Category")
    table.add_column("Orders", justify="right")
    table.add_column("Units", justify="right")
    table.add_column("Revenue", style="green", justify="right")
    for row in products_col.aggregate(pipeline_revenue):
        table.add_row(
            row["product_id"], row["name"], row["category"],
            str(row["orders"]), str(row["units"]), f"${row['revenue']:,}",
        )
    console.print(table)

    # --- MongoDB JSON for the submission ---------------------------------
    print()
    export_collections(products_col.database, OUT_JSON)

    banner("Lab 5.2 Complete")
    close_client()


if __name__ == "__main__":
    main()
