"""Stable synthetic financial activity, isolated from operational wallet ledgers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pymongo import ReplaceOne

COLLECTION = "demo_financial_activity"
SOURCE = "synthetic-demo-v1"
RECORD_COUNT = 1400
BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


def build_records() -> list[dict]:
    rows = []
    for n in range(1, RECORD_COUNT + 1):
        customer_no = (n * 37) % 100 + 1
        created = BASE_TIME + timedelta(minutes=n * 19)
        amount = (n * 137) % 24900 + 100
        rows.append({
            "_id": f"demo-tx-{n:04d}", "transaction_id": f"DEMO-TX-{n:04d}",
            "order_id": f"DEMO-ORDER-{n:04d}", "customer_id": f"DEMO-CUSTOMER-{customer_no:03d}",
            "user_id": f"demo-user-{customer_no:03d}", "order_date": created,
            "created_at": created, "type": ("wallet_top_up", "subscription_purchase", "wallet_transfer")[n % 3],
            "direction": ("credit", "debit", "none")[n % 3], "amount": amount,
            "currency": "INR", "status": "committed", "description": "Synthetic demonstration transaction",
            "counterparty_id": f"DEMO-CUSTOMER-{(customer_no % 100) + 1:03d}",
            "source": SOURCE, "is_synthetic": True,
        })
    return rows


def seed(db) -> dict:
    collection = db[COLLECTION]
    collection.create_index("transaction_id", unique=True, name="demo_transaction_id_unique")
    rows = build_records()
    collection.bulk_write([ReplaceOne({"_id": row["_id"]}, row, upsert=True) for row in rows], ordered=False)
    count = collection.count_documents({"source": SOURCE, "is_synthetic": True})
    if count != RECORD_COUNT:
        raise RuntimeError(f"Expected {RECORD_COUNT} isolated demo rows; found {count}.")
    return {"collection": COLLECTION, "inserted_or_updated": len(rows), "total_demo_records": count,
            "source": SOURCE, "real_wallet_collections_touched": False}
