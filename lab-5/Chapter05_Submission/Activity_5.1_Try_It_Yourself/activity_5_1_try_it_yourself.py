import json
import os
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

DB_NAME = "Chapter05"
COLL_NAME = "orders"
OUT_JSON = Path(__file__).resolve().parent / "activity_5_1_try_it_yourself_buckets.json"

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


# Order value, computed rather than stored. $bucket's groupBy accepts an
# expression, but the boundaries comparison is cheaper -- and the value is
# reusable by later stages -- if we materialise it once with $set first.
LINE_TOTAL = {"$multiply": ["$quantity", "$unit_price"]}

# boundaries must be sorted ascending, and each bucket is
# [lower, upper) -- lower bound inclusive, upper bound exclusive.
#   [0, 50)    -> small
#   [50, 200)  -> medium
#   >= 200     -> falls out of the last boundary, caught by `default`
BOUNDARIES = [0, 50, 200]
LABELS = {0: "small  (under $50)", 50: "medium ($50 - $200)", "large": "large  (over $200)"}


def main():
    banner("Lab 5.1 Try It Yourself: $bucket order size categories")

    col = get_client()[DB_NAME][COLL_NAME]
    total_docs = col.count_documents({})
    if total_docs == 0:
        sys.exit(
            f"[FAIL] {DB_NAME}.{COLL_NAME} is empty. Seed it first:\n"
            f"       python ../Activity_5.1/activity_5_1_sales_analytics.py"
        )
    print(f"[OK] Reading {total_docs} orders from {DB_NAME}.{COLL_NAME} (read-only).\n")

    pipeline = [
        # 1. Materialise the value we want to bucket on.
        {"$set": {"order_total": LINE_TOTAL}},
        # 2. Drop each order into a bucket by that value.
        {"$bucket": {
            "groupBy": "$order_total",
            "boundaries": BOUNDARIES,
            "default": "large",          # anything >= the last boundary lands here
            "output": {                  # accumulators, exactly like $group
                "count": {"$sum": 1},
                "revenue": {"$sum": "$order_total"},
                "avg_order_value": {"$avg": "$order_total"},
                "units": {"$sum": "$quantity"},
                "orders": {"$push": {
                    "order_id": "$order_id",
                    "customer": "$customer",
                    "product": "$product",
                    "qty": "$quantity",
                    "total": "$order_total",
                }},
            },
        }},
    ]

    results = list(col.aggregate(pipeline))

    table = Table(title="Orders by Size Category")
    table.add_column("Category", style="cyan")
    table.add_column("Orders", justify="right")
    table.add_column("Units", justify="right")
    table.add_column("Revenue", style="green", justify="right")
    table.add_column("Avg Value", justify="right")
    table.add_column("Share", justify="right")
    for row in results:
        table.add_row(
            LABELS.get(row["_id"], str(row["_id"])),
            str(row["count"]), str(row["units"]),
            f"${row['revenue']:,.0f}",
            f"${row['avg_order_value']:,.2f}",
            f"{row['count'] / total_docs:.0%}",
        )
    console.print(table)

    bucketed = sum(row["count"] for row in results)
    print(f"\n[OK] {bucketed} of {total_docs} orders bucketed "
          f"({total_docs - bucketed} dropped -- a document is dropped only if its "
          f"groupBy value is below the first boundary and no default is set).")

    # Which orders landed where -- proof the boundaries behave as documented.
    print("\n=== Orders in each bucket ===")
    for row in results:
        print(f"\n  {LABELS.get(row['_id'], row['_id'])}")
        for o in sorted(row["orders"], key=lambda d: d["total"]):
            print(f"    {o['order_id']}  {o['customer']:<8} {o['product']:<16}"
                  f" {o['qty']} x = ${o['total']:>6,.0f}")

    # --- MongoDB JSON for the submission ---------------------------------
    # json_util keeps BSON types in extended-JSON form, so the file round-trips.
    payload = {
        "database": DB_NAME,
        "collection": COLL_NAME,
        "orders_in_collection": total_docs,
        "boundaries": BOUNDARIES,
        "default_bucket": "large",
        "pipeline": pipeline,
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=json_util.default))
    print(f"\n[OK] Wrote results JSON -> {OUT_JSON.name}")

    banner("Lab 5.1 Try It Yourself Complete")
    close_client()


if __name__ == "__main__":
    main()
