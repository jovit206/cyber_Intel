#!/usr/bin/env python3
"""
Activity 1.2 - Try it yourself
23CSE528 - NoSQL Databases using MongoDB - Chapter 01
Student: Kamlesh Choudhary

Repeats the domain-mapping exercise for an e-commerce marketplace, with five
domains implemented against a live MongoDB server:
  product_catalogue     - category-specific nested attributes
  cart_and_session      - TTL index, key-only vs. content access
  order_payment_history - compound index range query
  recommendation_graph  - $graphLookup 2-hop "bought together"
  product_search        - text index with $meta: "textScore" ranking
This file produces the EVIDENCE; the verdict is in the report.
"""

import os
import random
import sys
import datetime as dt

from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT
from pymongo.errors import PyMongoError
from bson import ObjectId, json_util

RANDOM_SEED = 42
BASE_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
PRODUCT_COUNT, CART_COUNT, ORDER_COUNT, CUSTOMER_COUNT = 300, 1000, 5000, 800


# --------------------------------------------------------------------------
# Helpers (duplicated per activity so each folder is self-contained)
# --------------------------------------------------------------------------

def connect():
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=4000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        sys.exit(f"FATAL: no MongoDB server answered at {uri}: {exc}")
    db = client["nosql_labs"]
    host, port = client.address
    print(f"MongoDB version : {db.command('buildInfo')['version']}")
    print(f"Server address  : {host}:{port}")
    print(f"Database        : {db.name}")
    print("-" * 72)
    return client, db


def _walk(node, acc):
    if isinstance(node, dict):
        for key in ("totalDocsExamined", "totalKeysExamined", "nReturned", "executionTimeMillis"):
            if isinstance(node.get(key), (int, float)):
                acc.setdefault(key, []).append(node[key])
        if isinstance(node.get("stage"), str):
            acc.setdefault("stages", []).append(node["stage"])
        for value in node.values():
            _walk(value, acc)
    elif isinstance(node, list):
        for item in node:
            _walk(item, acc)


def stats_from(explain_doc):
    acc = {}
    _walk(explain_doc, acc)
    stages = list(dict.fromkeys(acc.get("stages", [])))
    return {
        "stage": " -> ".join(stages) if stages else "N/A",
        "totalDocsExamined": sum(acc.get("totalDocsExamined", [])),
        "totalKeysExamined": sum(acc.get("totalKeysExamined", [])),
        "nReturned": max(acc.get("nReturned", [0]), default=0),
        "executionTimeMillis": max(acc.get("executionTimeMillis", [0]), default=0),
    }


def explain_find(db, coll, filter_, sort=None, projection=None):
    inner = {"find": coll, "filter": filter_}
    if sort:
        inner["sort"] = dict(sort) if isinstance(sort, list) else sort
    if projection:
        inner["projection"] = projection
    return stats_from(db.command({"explain": inner, "verbosity": "executionStats"}))


def explain_agg(db, coll, pipeline):
    return stats_from(db.command({"explain": {"aggregate": coll, "pipeline": pipeline, "cursor": {}},
                                  "verbosity": "executionStats"}))


def print_table(headers, rows):
    cols = [[str(h)] + [str(r[i]) for r in rows] for i, h in enumerate(headers)]
    widths = [min(max(len(c) for c in col) + 2, 40) for col in cols]

    def cell(value, width):
        text = str(value)
        return (text[:width - 4] + "..." if len(text) > width - 1 else text).ljust(width)

    print("".join(cell(h, w) for h, w in zip(headers, widths))[:112])
    print("-" * min(sum(widths), 112))
    for row in rows:
        print("".join(cell(c, w) for c, w in zip(row, widths))[:112])
    print()


def export(db, coll, out_path):
    docs = list(db[coll].find())
    with open(out_path, "w") as fh:
        fh.write(json_util.dumps(docs, indent=2) + "\n")
    print(f"Exported {len(docs)} document(s) from '{coll}' -> {os.path.basename(out_path)}")


# --------------------------------------------------------------------------
# Domain 1: product_catalogue - different attributes per category
# --------------------------------------------------------------------------

def build_catalogue(db):
    db.products.drop()
    random.seed(RANDOM_SEED)
    categories = ["electronics", "apparel", "books"]
    docs = []
    for i in range(PRODUCT_COUNT):
        category = categories[i % len(categories)]
        doc = {"_id": f"prod-{i:04d}", "name": f"{category.title()} Item {i}",
               "category": category, "price": round(random.uniform(5, 999), 2)}
        if category == "electronics":
            doc["attributes"] = {"screen_size_in": round(random.uniform(4, 17), 1),
                                 "ram_gb": random.choice([4, 8, 16, 32])}
            doc["description"] = f"{doc['name']} - a device for gaming and work."
        elif category == "apparel":
            doc["attributes"] = {"size": random.choice(["S", "M", "L", "XL"]),
                                 "material": random.choice(["cotton", "polyester", "wool"])}
            doc["description"] = f"{doc['name']} - a {doc['attributes']['material']} garment."
        else:
            doc["attributes"] = {"author": f"Author {i % 40}", "pages": random.randint(120, 800)}
            doc["description"] = f"{doc['name']} - a gaming and technology reference book."
        docs.append(doc)
    db.products.insert_many(docs)
    # screen_size_in exists only on electronics documents.
    db.products.create_index("attributes.screen_size_in")
    return explain_find(db, "products",
                        {"category": "electronics", "attributes.screen_size_in": {"$gte": 13.0}})


# --------------------------------------------------------------------------
# Domain 2: cart_and_session - TTL index, key-only access
# --------------------------------------------------------------------------

def build_carts(db):
    db.carts.drop()
    random.seed(RANDOM_SEED)
    now = dt.datetime.now(dt.timezone.utc)
    db.carts.insert_many([{
        "_id": f"cart-{c:04d}",
        "items": [{"sku": f"prod-{random.randint(0, PRODUCT_COUNT - 1):04d}",
                   "qty": random.randint(1, 3)} for _ in range(random.randint(1, 4))],
        "updated_at": now,
    } for c in range(CART_COUNT)])
    db.carts.create_index("updated_at", expireAfterSeconds=1800)

    ttl_index = db.carts.index_information()["updated_at_1"]
    print(f"Cart TTL index: {ttl_index}\n")
    key_only = explain_find(db, "carts", {"_id": "cart-0042"})
    content = explain_find(db, "carts", {"items.sku": "prod-0007"})
    return key_only, content, ttl_index


# --------------------------------------------------------------------------
# Domain 3: order_payment_history - compound index range query
# --------------------------------------------------------------------------

def build_orders(db):
    db.orders.drop()
    random.seed(RANDOM_SEED)
    db.orders.insert_many([{
        "_id": ObjectId(),
        "customer_id": f"cust-{i % CUSTOMER_COUNT:04d}",
        "order_date": BASE_TIME + dt.timedelta(hours=i * 2),
        "total": round(random.uniform(15, 1200), 2),
        "status": random.choices(["paid", "refunded", "pending"], weights=[85, 8, 7])[0],
    } for i in range(ORDER_COUNT)])
    db.orders.create_index([("customer_id", ASCENDING), ("order_date", DESCENDING)])

    return explain_find(db, "orders",
                        {"customer_id": "cust-0003",
                         "order_date": {"$gte": BASE_TIME,
                                        "$lte": BASE_TIME + dt.timedelta(days=60)}},
                        sort=[("order_date", -1)])


# --------------------------------------------------------------------------
# Domain 4: recommendation_graph - 2-hop "bought together"
# --------------------------------------------------------------------------

def build_recommendations(db):
    db.co_purchases.drop()
    ids = [f"prod-{i:04d}" for i in range(PRODUCT_COUNT)]
    # Deterministic stand-in for a mined co-purchase graph: each product is
    # bought with the next two in sequence.
    db.co_purchases.insert_many([
        {"_id": pid, "bought_with": [ids[(i + 1) % PRODUCT_COUNT], ids[(i + 2) % PRODUCT_COUNT]]}
        for i, pid in enumerate(ids)])
    db.co_purchases.create_index("bought_with")

    seed = "prod-0010"
    pipeline = [
        {"$match": {"_id": seed}},
        {"$graphLookup": {"from": "co_purchases", "startWith": "$bought_with",
                          "connectFromField": "bought_with", "connectToField": "_id",
                          "as": "recommendations", "maxDepth": 1, "depthField": "depth"}},
    ]
    result = db.co_purchases.aggregate(pipeline).next()
    recs = sorted({r["_id"] for r in result["recommendations"]})
    print(f"2-hop 'customers who bought {seed} also bought': {', '.join(recs)}\n")
    return explain_agg(db, "co_purchases", pipeline), recs, seed


# --------------------------------------------------------------------------
# Domain 5: product_search - text index with relevance ranking
# --------------------------------------------------------------------------

def build_search(db):
    db.products.create_index([("name", TEXT), ("description", TEXT)])
    query = "gaming device"
    filter_ = {"$text": {"$search": query}}
    projection = {"score": {"$meta": "textScore"}}
    # Tie-break on _id so the ranked top-5 is reproducible across runs.
    results = list(db.products.find(filter_, projection)
                   .sort([("score", {"$meta": "textScore"}), ("_id", ASCENDING)]).limit(5))
    stats = explain_find(db, "products", filter_, projection=projection)

    print(f'$text search for "{query}", ranked by $meta: "textScore":')
    print_table(["Product", "Category", "textScore"],
                [[r["_id"], r["category"], round(r["score"], 3)] for r in results])
    return stats, results, query


def persist(db, catalogue, cart, order_stats, graph, search):
    key_only, content, ttl_index = cart
    graph_stats, recs, seed = graph
    search_stats, search_results, search_query = search

    db.ecommerce_domain_mapping.drop()
    db.ecommerce_domain_mapping.insert_many([
        {"_id": ObjectId(), "app": "ecommerce-marketplace", "domain": "product_catalogue",
         "family": "Document (MongoDB)", "evidence": {"nested_field_query": catalogue}},
        {"_id": ObjectId(), "app": "ecommerce-marketplace", "domain": "cart_and_session",
         "family": "Key-Value (production: Redis)",
         "evidence": {"key_lookup": key_only, "content_query": content, "ttl_index": ttl_index}},
        {"_id": ObjectId(), "app": "ecommerce-marketplace", "domain": "order_payment_history",
         "family": "Document (MongoDB)", "evidence": {"range_query": order_stats}},
        {"_id": ObjectId(), "app": "ecommerce-marketplace", "domain": "recommendation_graph",
         "family": "Graph (production: Neo4j at depth/scale)",
         "evidence": {"graphlookup": graph_stats, "seed": seed, "recommendations": recs}},
        {"_id": ObjectId(), "app": "ecommerce-marketplace", "domain": "product_search",
         "family": "Relevance search - outside the four classical families",
         "evidence": {"text_search": search_stats, "query": search_query,
                      "top_results": [{"_id": r["_id"], "score": r["score"]}
                                      for r in search_results]}},
    ])
    print("Persisted 5 domain-mapping document(s) to 'ecommerce_domain_mapping'.\n")


def main():
    _, db = connect()
    print(f"Generating {PRODUCT_COUNT} products, {CART_COUNT} carts, {ORDER_COUNT} orders "
          f"(seed={RANDOM_SEED})...\n")

    catalogue = build_catalogue(db)
    cart = build_carts(db)
    order_stats = build_orders(db)
    graph = build_recommendations(db)
    search = build_search(db)

    key_only, content, _ = cart
    graph_stats, _, _ = graph
    search_stats, _, _ = search

    print("MEASURED EXPLAIN STATS (executionStats)")
    print_table(
        ["Domain / Query", "Stage", "DocsExamined", "Returned", "TimeMs"],
        [[label, s["stage"], s["totalDocsExamined"], s["nReturned"], s["executionTimeMillis"]]
         for label, s in [
             ("catalogue: electronics screen_size>=13", catalogue),
             ("cart: key-only _id lookup", key_only),
             ("cart: content query on items.sku", content),
             ("orders: compound index range", order_stats),
             ("recommendations: $graphLookup 2-hop", graph_stats),
             ("search: $text + textScore", search_stats),
         ]],
    )

    persist(db, catalogue, cart, order_stats, graph, search)
    export(db, "ecommerce_domain_mapping",
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecommerce_domain_mapping.json"))


if __name__ == "__main__":
    main()
