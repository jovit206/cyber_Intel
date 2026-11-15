"""Idempotently seed a separate MongoDB database with clearly marked demo inventory.

Uses the application's existing MONGO_URI for its configured replica set, but writes
only to LAB10_DEMO_DATABASE (default: lab10_demo), never chapter07, bank, or shop.
Existing products and reservation records are preserved.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from pymongo import MongoClient


PRODUCTS = [
    ("LAPTOP-15", "Laptop 15-inch", 4, 84999.00, 3),
    ("MOUSE-WL-01", "Wireless Mouse", 18, 1499.00, 5),
    ("KEYBOARD-MECH-01", "Mechanical Keyboard", 2, 6299.00, 5),
    ("USB-C-HUB-07", "USB-C Hub", 0, 3299.00, 3),
    ("MONITOR-27-01", "Monitor 27-inch", 6, 21999.00, 2),
    ("SSD-EXT-1TB", "External SSD 1 TB", 3, 8999.00, 4),
    ("HEADPHONES-BT-01", "Bluetooth Headphones", 10, 4999.00, 3),
    ("WEBCAM-HD-01", "HD Webcam", 1, 2799.00, 3),
    ("STAND-ALU-01", "Aluminum Laptop Stand", 12, 1899.00, 3),
    ("CABLE-USBC-2M", "USB-C Cable 2 m", 0, 699.00, 5),
]


def seed_database(db) -> tuple[int, int]:
    """Insert missing demonstration items while preserving all existing stock and orders."""
    inventory = db.inventory
    inventory.create_index("product_id", unique=True)
    db.orders.create_index("order_id", unique=True)
    now = datetime.now(timezone.utc)
    inserted = 0
    for product_id, name, stock, price, threshold in PRODUCTS:
        result = inventory.update_one(
            {"product_id": product_id},
            {"$setOnInsert": {
                "product_id": product_id,
                "name": name,
                "stock": stock,
                "version": 0,
                "price": price,
                "low_stock_threshold": threshold,
                "updated_at": now,
                "is_demo": True,
            }},
            upsert=True,
        )
        inserted += int(result.upserted_id is not None)
    return inserted, inventory.count_documents({})


def seed() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        raise RuntimeError("MONGO_URI is required to seed the isolated demo inventory database.")
    database_name = os.getenv("LAB10_DEMO_DATABASE", "lab10_demo").strip()
    if not database_name or database_name in {"chapter07", "bank", "shop"}:
        raise RuntimeError("LAB10_DEMO_DATABASE must be separate from chapter07, bank, and shop.")

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        if not client.admin.command("hello").get("setName"):
            raise RuntimeError("The configured MongoDB server is not a replica set.")
        inserted, count = seed_database(client[database_name])
        print(f"Demo inventory database: {database_name}")
        print(f"Products inserted: {inserted}; products present: {count}")
        print("Existing products and orders were preserved; demonstration orders were not fabricated.")
        return inserted
    finally:
        client.close()


if __name__ == "__main__":
    seed()
