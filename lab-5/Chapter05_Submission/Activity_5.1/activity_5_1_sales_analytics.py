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
COLL_NAME = "orders"
# mongoexport-style dump of the seeded collection, written beside this script.
OUT_JSON = Path(__file__).resolve().parent / "activity_5_1_orders.json"

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


def export_collection(col, path: Path) -> None:
    """Dump a collection to JSON. json_util keeps BSON types (dates, ObjectIds)
    in extended-JSON form, so the file round-trips through mongoimport."""
    docs = list(col.find({}).sort("order_id", 1))
    path.write_text(json.dumps(docs, indent=2, default=json_util.default))
    print(f"[OK] Wrote {len(docs)} documents -> {path.name}")


def banner(text: str, char: str = "=", width: int = 72) -> None:
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)
    print()


def order(order_id, customer, product, quantity, unit_price, order_date, status, region):
    """Build one order document. order_date is stored as a real BSON date."""
    return {
        "order_id": order_id,
        "customer": customer,
        "product": product,
        "quantity": quantity,
        "unit_price": unit_price,
        "order_date": datetime.strptime(order_date, "%Y-%m-%d"),
        "status": status,
        "region": region,
    }


# 20 orders: 6 customers, 8 products, Jan-Apr 2024, three statuses.
ORDERS = [
    order("ORD-101", "Arjun", "Laptop",          1, 1200, "2024-01-05", "completed", "North"),
    order("ORD-102", "Sneha", "Monitor",         2,  320, "2024-01-11", "completed", "South"),
    order("ORD-103", "Kavya", "Keyboard",        3,   85, "2024-01-17", "completed", "East"),
    order("ORD-104", "Rahul", "Laptop",          2, 1200, "2024-01-23", "completed", "West"),
    order("ORD-105", "Divya", "Mouse",           4,   45, "2024-01-29", "cancelled", "North"),
    order("ORD-106", "Arjun", "Headphones",      2,  150, "2024-02-03", "completed", "North"),
    order("ORD-107", "Imran", "Docking Station", 1,  210, "2024-02-08", "pending",   "South"),
    order("ORD-108", "Sneha", "Laptop",          1, 1200, "2024-02-12", "completed", "South"),
    order("ORD-109", "Kavya", "Webcam",          2,   95, "2024-02-16", "completed", "East"),
    order("ORD-110", "Rahul", "Office Chair",    3,  260, "2024-02-21", "completed", "West"),
    order("ORD-111", "Divya", "Monitor",         1,  320, "2024-02-27", "completed", "North"),
    order("ORD-112", "Arjun", "Docking Station", 2,  210, "2024-03-04", "completed", "North"),
    order("ORD-113", "Imran", "Laptop",          1, 1200, "2024-03-09", "cancelled", "South"),
    order("ORD-114", "Sneha", "Keyboard",        5,   85, "2024-03-14", "completed", "South"),
    order("ORD-115", "Rahul", "Headphones",      4,  150, "2024-03-19", "completed", "West"),
    order("ORD-116", "Kavya", "Mouse",           6,   45, "2024-03-25", "pending",   "East"),
    order("ORD-117", "Arjun", "Office Chair",    1,  260, "2024-04-02", "completed", "North"),
    order("ORD-118", "Divya", "Laptop",          1, 1200, "2024-04-07", "completed", "North"),
    order("ORD-119", "Rahul", "Monitor",         3,  320, "2024-04-13", "completed", "West"),
    order("ORD-120", "Imran", "Headphones",      2,  150, "2024-04-18", "completed", "South"),
]

# Revenue for one order. Reused by several pipelines, so it lives in one place.
LINE_TOTAL = {"$multiply": ["$quantity", "$unit_price"]}


def main():
    banner("Lab 5.1: Sales Analytics Pipeline")
    col = reset_collection(DB_NAME, COLL_NAME)
    col.insert_many(ORDERS)
    print(f"[OK] Inserted {len(ORDERS)} orders into {DB_NAME}.{COLL_NAME}.\n")

    # --- PIPELINE 1: Monthly Revenue ---
    print("=== Monthly Revenue (completed orders only) ===")
    pipeline_monthly = [
        {"$match": {"status": "completed"}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$order_date"}},
            "total_revenue": {"$sum": LINE_TOTAL},
            "order_count": {"$sum": 1},
            "units_sold": {"$sum": "$quantity"},
            "avg_order_value": {"$avg": LINE_TOTAL},
            "customers": {"$addToSet": "$customer"},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "month": "$_id",
            "total_revenue": 1,
            "order_count": 1,
            "units_sold": 1,
            "avg_order_value": {"$round": ["$avg_order_value", 2]},
            "unique_customers": {"$size": "$customers"},
        }},
        {"$unset": "_id"},
    ]
    table = Table(title="Monthly Revenue Report")
    table.add_column("Month", style="cyan")
    table.add_column("Revenue", style="green", justify="right")
    table.add_column("Orders", justify="right")
    table.add_column("Units", justify="right")
    table.add_column("Avg Value", justify="right")
    table.add_column("Customers", justify="right")
    for row in col.aggregate(pipeline_monthly):
        table.add_row(
            row["month"], f"${row['total_revenue']:,.0f}",
            str(row["order_count"]), str(row["units_sold"]),
            f"${row['avg_order_value']:,.2f}",
            str(row["unique_customers"]),
        )
    console.print(table)

    # --- PIPELINE 2: Top 3 Customers (with the products they bought) ---
    print("\n=== Top 3 Customers by Total Spending ===")
    pipeline_top = [
        {"$match": {"status": "completed"}},
        {"$group": {
            "_id": "$customer",
            "total_spent": {"$sum": LINE_TOTAL},
            "orders": {"$sum": 1},
            "items_bought": {"$sum": "$quantity"},
            "products": {"$addToSet": "$product"},
        }},
        {"$sort": {"total_spent": -1}},
        {"$limit": 3},
    ]
    for row in col.aggregate(pipeline_top):
        print(f"  {row['_id']:<12} ${row['total_spent']:>8,.0f} across {row['orders']} orders ({row['items_bought']} items)")
        print(f"  {'':<12} products: {', '.join(sorted(row['products']))}")

    # --- PIPELINE 3: Average Order Value by Status ---
    print("\n=== Avg Order Value by Status ===")
    pipeline_status = [
        {"$group": {
            "_id": "$status",
            "avg_value": {"$avg": LINE_TOTAL},
            "total_value": {"$sum": LINE_TOTAL},
            "count": {"$sum": 1},
        }},
        {"$sort": {"avg_value": -1}},
    ]
    for row in col.aggregate(pipeline_status):
        print(f"  {row['_id']:<12} avg=${row['avg_value']:>8,.2f} ({row['count']} orders, total=${row['total_value']:,.0f})")

    # --- PIPELINE 4: $bucket categorization ---
    print("\n=== Order Value Buckets ===")
    pipeline_bucket = [
        {"$set": {"order_total": LINE_TOTAL}},
        {"$bucket": {
            "groupBy": "$order_total",
            "boundaries": [0, 250, 500, 1000, 2500],
            "default": "Very High",
            "output": {"count": {"$sum": 1}, "total": {"$sum": "$order_total"}},
        }},
    ]
    labels = ["$0 - $250", "$250 - $500", "$500 - $1K", "$1K - $2.5K", "Very High"]
    for i, row in enumerate(col.aggregate(pipeline_bucket)):
        label = labels[i] if i < len(labels) else str(row["_id"])
        print(f"  {label:<15} {row['count']} orders, total=${row['total']:,.0f}")

    # --- MongoDB JSON for the submission ---------------------------------
    print()
    export_collection(col, OUT_JSON)

    banner("Lab 5.1 Complete")
    close_client()


if __name__ == "__main__":
    main()
