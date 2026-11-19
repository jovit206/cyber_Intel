"""Product catalogue and query workload shared by the Chapter 6 activities (Jovit Chawla, 24BTRCL089).

The catalogue is synthetic lab data generated with a fixed seed (an Indian online marketplace: prices in INR).
`QUERIES` is the five-query workload that Activity 6.1 designs indexes for and the other activities reuse.
"""
from __future__ import annotations

import datetime as dt
import random

COLLECTION = "products"
PRODUCT_COUNT = 50_000
SEED = 6089

CATEGORIES = {   # category -> (min price, max price, words used in product names, tags)
    "Mobiles": (6000, 140000, ["Phone", "5G", "Pro", "Lite"], ["5g", "android", "dual-sim", "amoled"]),
    "Laptops": (25000, 250000, ["Notebook", "Ultrabook", "Gaming", "Book"], ["ssd", "gaming", "thin", "backlit"]),
    "Audio": (500, 40000, ["Earbuds", "Headphones", "Speaker", "Soundbar"], ["wireless", "anc", "bass", "waterproof"]),
    "Books": (150, 3000, ["Guide", "Handbook", "Novel", "Workbook"], ["paperback", "bestseller", "exam-prep", "fiction"]),
    "Fashion": (300, 9000, ["Kurta", "Sneakers", "Jacket", "Saree"], ["cotton", "festive", "casual", "handloom"]),
    "Home": (200, 60000, ["Mixer", "Kettle", "Lamp", "Purifier"], ["energy-saver", "steel", "smart", "compact"]),
    "Sports": (250, 30000, ["Bat", "Racquet", "Yoga Mat", "Cycle"], ["cricket", "fitness", "outdoor", "kids"]),
    "Toys": (199, 8000, ["Puzzle", "Blocks", "Drone", "Board Game"], ["educational", "kids", "stem", "family"]),
}
BRANDS = ["Aarav", "Bharat", "Chakra", "Dhruv", "Ekta", "Garuda", "Indus", "Kaveri", "Lotus", "Meru", "Nila", "Orion"]


def products(count: int = PRODUCT_COUNT, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    docs = []
    for i in range(count):
        category = rng.choice(list(CATEGORIES))
        low, high, words, tags = CATEGORIES[category]
        brand = rng.choice(BRANDS)
        name = f"{brand} {rng.choice(words)} {rng.randint(1, 99)}"
        docs.append({
            "sku": f"SKU{i:06d}", "name": name, "category": category, "brand": brand,
            "price": round(rng.uniform(low, high), 2), "rating": round(rng.triangular(1.0, 5.0, 4.1), 1),
            "stock": rng.randint(0, 500), "tags": rng.sample(tags, rng.randint(1, 3)),
            "description": f"{name} by {brand} in {category.lower()}",
            "listed_at": base + dt.timedelta(minutes=rng.randint(0, 300 * 24 * 60)),
        })
    return docs


def seed(db, name: str = COLLECTION, count: int = PRODUCT_COUNT):
    """(Re)create a products collection with exactly `count` generated documents and only the _id index."""
    db.drop_collection(name)
    coll = db[name]
    docs = products(count)
    for start in range(0, len(docs), 10_000):
        coll.insert_many(docs[start:start + 10_000], ordered=False)
    return coll


# The workload. Each entry: (id, description, kind, spec). `kind` is "find" or "text".
QUERIES = [
    ("Q1", "Laptops sorted by price (cheapest first), top 20", "find",
     {"filter": {"category": "Laptops"}, "sort": {"price": 1}, "limit": 20}),
    ("Q2", "any product priced INR 1,000-1,500", "find",
     {"filter": {"price": {"$gte": 1000, "$lte": 1500}}}),
    ("Q3", "products tagged 'anc'", "find",
     {"filter": {"tags": "anc"}}),
    ("Q4", "search product names for 'gaming notebook'", "text",
     {"search": "gaming notebook"}),
    ("Q5", "Mobiles rated >= 4.5 sorted by price, top 20", "find",
     {"filter": {"category": "Mobiles", "rating": {"$gte": 4.5}}, "sort": {"price": 1}, "limit": 20}),
]

# Q4 needs a text index; before it exists the closest equivalent is a case-insensitive regex.
Q4_REGEX = {"$or": [{"name": {"$regex": "gaming", "$options": "i"}}, {"name": {"$regex": "notebook", "$options": "i"}}]}

# The index design from Activity 6.1 (name -> keys). One ESR compound index serves Q1 and Q5.
INDEXES = {
    "idx_category_price_rating": [("category", 1), ("price", 1), ("rating", 1)],
    "idx_price": [("price", 1)],
    "idx_tags": [("tags", 1)],
    "idx_text_name_description": [("name", "text"), ("description", "text")],
}
