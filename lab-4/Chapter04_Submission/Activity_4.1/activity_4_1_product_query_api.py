"""Lab 4.1 - Build a Product Query API

Full CRUD with comparison, logical, array operators, projections, pagination --
run against the real Amazon product export (1.43M documents) rather than a
seven-item hand-written list.

Prerequisite:
    python load_amazon_products.py

The READ section queries the loaded dataset read-only. The CREATE / UPDATE /
DELETE sections operate only on documents this lab creates itself (ASINs
prefixed "LAB-"), so re-running the script never mutates the imported data.
"""

import argparse
import json
import sys
from pathlib import Path

from bson import json_util

from pymongo import DESCENDING, ReturnDocument
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
COLL_NAME = "products"

# Every document this lab writes carries this ASIN prefix, so cleanup is exact.
LAB_PREFIX = "LAB-"

# Demo parameters are chosen from the data at runtime rather than hardcoded, so
# the lab returns non-empty results whether you loaded 5,000 rows or all 1.43M.
PARAMS: dict = {}

# Every result the lab produces is collected here for the --json export.
CAPTURED: dict = {"create": {}, "read": {}, "update": {}, "delete": {}}
_SECTION = "read"


def pick_demo_params(col):
    """Choose two categories and a price cut-off that actually match data."""
    top = list(col.aggregate([
        {"$match": {"isBestSeller": True, "price": {"$gt": 0}}},
        {"$sortByCount": "$category"},
        {"$limit": 2},
    ], allowDiskUse=True))
    if len(top) < 2:  # tiny subset with few bestsellers - fall back to any category
        top = list(col.aggregate([{"$sortByCount": "$category"}, {"$limit": 2}],
                                 allowDiskUse=True))
    cats = [t["_id"] for t in top]
    cat_a, cat_b = cats[0], (cats[1] if len(cats) > 1 else cats[0])

    # 75th-percentile price within those categories, so "expensive" means
    # something at any dataset size.
    pct = list(col.aggregate([
        {"$match": {"category": {"$in": [cat_a, cat_b]}, "price": {"$gt": 0}}},
        {"$group": {"_id": None,
                    "p75": {"$percentile": {"input": "$price", "p": [0.75],
                                            "method": "approximate"}}}},
    ], allowDiskUse=True))
    price_hi = round(pct[0]["p75"][0], 2) if pct else 100.0

    # Highest list price present, so the $elemMatch demo has a real cut-off.
    top_list = list(col.find({"offers.kind": "list"}, {"listPrice": 1, "_id": 0})
                       .sort("listPrice", DESCENDING).limit(1))
    list_hi = round((top_list[0]["listPrice"] if top_list else 500) * 0.25, 2)

    cat_a_id = (col.find_one({"category": cat_a}, {"category_id": 1, "_id": 0})
                or {}).get("category_id", 0)
    return {"CAT_A": cat_a, "CAT_B": cat_b, "CAT_A_ID": cat_a_id,
            "PRICE_HI": price_hi, "LIST_HI": list_hi}


def show(rows, columns, title, limit=8, query=None, total=None):
    """Render query results as a table and capture them for the JSON export.

    `columns` is [(header, extractor), ...]. `rows` may be a cursor; it is
    materialized up to `limit` so the same documents are both printed and
    recorded.
    """
    materialized = []
    for row in rows:
        if len(materialized) >= limit:
            break
        materialized.append(row)

    table = Table(title=title, title_style="bold cyan", header_style="bold")
    for header, _ in columns:
        table.add_column(header, overflow="fold")
    for row in materialized:
        table.add_row(*[str(extract(row)) for _, extract in columns])
    console.print(table)
    if not materialized:
        console.print("  [dim](no matches)[/dim]")

    entry = {"shown": len(materialized), "documents": materialized}
    if query is not None:
        entry["filter"] = query
    if total is not None:
        entry["total_matching"] = total
    CAPTURED[_SECTION][title] = entry
    return materialized


def short(text, width=52):
    text = str(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def search_products(col, query_filter, projection=None, sort_key=None, sort_dir=1,
                    page=1, page_size=5):
    """Generic paginated search. Returns (results, total_matching)."""
    skip = (page - 1) * page_size
    cursor = col.find(query_filter, projection)
    if sort_key:
        cursor = cursor.sort(sort_key, sort_dir)
    total = col.count_documents(query_filter)
    results = list(cursor.skip(skip).limit(page_size))
    return results, total


# --------------------------------------------------------------------------
# CREATE
# --------------------------------------------------------------------------
def demo_create(col):
    global _SECTION
    _SECTION = "create"
    banner("CREATE - insert_one / insert_many", char="-")

    one = {
        "_id": f"{LAB_PREFIX}0001",
        "asin": f"{LAB_PREFIX}0001",
        "title": "Lab Sample - Packable Rain Jacket",
        "price": 129.99,
        "listPrice": 179.99,
        "category_id": PARAMS["CAT_A_ID"],
        "category": PARAMS["CAT_A"],
        "isBestSeller": True,
        "active": True,
        "metrics": {"stars": 4.6, "reviews": 812, "boughtInLastMonth": 1500},
        "tags": ["bestseller", "discounted", "top-rated", "popular", "premium"],
        "offers": [{"kind": "current", "amount": 129.99}, {"kind": "list", "amount": 179.99}],
    }
    result = col.insert_one(one)
    print(f"  insert_one    -> _id={result.inserted_id}")

    many = [
        {
            "_id": f"{LAB_PREFIX}000{n}",
            "asin": f"{LAB_PREFIX}000{n}",
            "title": f"Lab Sample - Merino Base Layer {n}",
            "price": price,
            "listPrice": 0.0,
            "category_id": PARAMS["CAT_A_ID"],
            "category": PARAMS["CAT_A"],
            "isBestSeller": False,
            "active": True,
            "metrics": {"stars": stars, "reviews": 40 * n, "boughtInLastMonth": 100 * n},
            "tags": ["mid-range" if price <= 100 else "premium"],
            "offers": [{"kind": "current", "amount": price}],
        }
        for n, price, stars in ((2, 34.99, 4.1), (3, 74.50, 4.4), (4, 118.00, 4.8))
    ]
    result = col.insert_many(many)
    print(f"  insert_many   -> {len(result.inserted_ids)} ids: {result.inserted_ids}")
    lab_count = col.count_documents({"_id": {"$regex": f"^{LAB_PREFIX}"}})
    print(f"  count_documents({{_id: /^{LAB_PREFIX}/}}) = {lab_count}")
    CAPTURED["create"] = {
        "insert_one": {"inserted_id": one["_id"], "document": one},
        "insert_many": {"inserted_ids": result.inserted_ids, "documents": many},
        "lab_documents_after_create": lab_count,
    }


# --------------------------------------------------------------------------
# READ - the query-language tour, over the real 1.43M-document dataset
# --------------------------------------------------------------------------
def demo_read(col):
    global _SECTION
    _SECTION = "read"
    banner("READ - query operators over the imported dataset", char="-")

    cat_a, cat_b = PARAMS["CAT_A"], PARAMS["CAT_B"]
    price_hi, list_hi = PARAMS["PRICE_HI"], PARAMS["LIST_HI"]
    title_col = ("Title", lambda r: short(r["title"]))

    # 1. Comparison operators
    show(
        col.find({"price": {"$gte": 500, "$lte": 800}},
                 {"title": 1, "price": 1, "_id": 0}),
        [title_col, ("Price", lambda r: f"${r['price']:,.2f}")],
        "1. Comparison - price $gte 500, $lte 800",
    )

    # 2. Logical operators: (category A OR category B) AND price > 150
    show(
        col.find({"$or": [{"category": cat_a}, {"category": cat_b}],
                  "price": {"$gt": price_hi}},
                 {"title": 1, "category": 1, "price": 1, "_id": 0}),
        [title_col,
         ("Category", lambda r: r["category"]),
         ("Price", lambda r: f"${r['price']:,.2f}")],
        f"2. Logical - ({cat_a} OR {cat_b}) AND price > {price_hi}",
    )

    # 3. Array-field matching: no operator needed to match one element
    show(
        col.find({"tags": "bestseller"}, {"title": 1, "tags": 1, "_id": 0}),
        [title_col, ("Tags", lambda r: ", ".join(r["tags"]))],
        "3. Array - documents tagged 'bestseller'",
    )

    # 4. $elemMatch: ONE offers element must satisfy BOTH conditions together.
    #    Compare with {"offers.kind": "list", "offers.amount": {"$gt": 500}},
    #    which different elements could satisfy separately.
    show(
        col.find({"offers": {"$elemMatch": {"kind": "list", "amount": {"$gt": list_hi}}}},
                 {"title": 1, "price": 1, "offers": 1, "_id": 0}),
        [title_col,
         ("Now", lambda r: f"${r['price']:,.2f}"),
         ("List", lambda r: f"${next(o['amount'] for o in r['offers'] if o['kind'] == 'list'):,.2f}")],
        f"4. $elemMatch - one offer that is BOTH kind=list AND amount > {list_hi}",
    )

    # 5. Projection with a dotted path into the nested metrics sub-document
    show(
        col.find({"category": cat_a, "isBestSeller": True},
                 {"title": 1, "metrics.stars": 1, "_id": 0}),
        [title_col, ("Stars", lambda r: r["metrics"]["stars"])],
        f"5. Projection - title + metrics.stars only ({cat_a} bestsellers)",
    )

    # 6. Pagination via skip/limit
    filt = {"tags": "top-rated", "price": {"$gt": price_hi}}
    for page in (1, 2):
        rows, total = search_products(col, filt,
                                      {"title": 1, "price": 1, "metrics.stars": 1, "_id": 0},
                                      sort_key="price", sort_dir=DESCENDING,
                                      page=page, page_size=5)
        show(rows,
             [title_col,
              ("Price", lambda r: f"${r['price']:,.2f}"),
              ("Stars", lambda r: r["metrics"]["stars"])],
             f"6. Pagination - page {page} of {total:,} top-rated products over ${price_hi}",
             total=total)

    # 7. $all - every listed tag must be present
    show(
        col.find({"tags": {"$all": ["bestseller", "discounted"]}},
                 {"title": 1, "price": 1, "tags": 1, "_id": 0}),
        [title_col,
         ("Price", lambda r: f"${r['price']:,.2f}"),
         ("Tags", lambda r: ", ".join(r["tags"]))],
        "7. $all - tagged BOTH 'bestseller' AND 'discounted'",
    )

    # 8. $ne / $in / $nin
    show(
        col.find({"category": {"$in": [cat_a, cat_b]},
                  "tags": {"$ne": "unrated"},
                  "metrics.stars": {"$gte": 4.7}},
                 {"title": 1, "category": 1, "metrics.stars": 1, "_id": 0}),
        [title_col,
         ("Category", lambda r: r["category"]),
         ("Stars", lambda r: r["metrics"]["stars"])],
        "8. $in + $ne - rated products in either category, 4.7 stars or better",
    )

    # 9. Sort on a nested field
    show(
        col.find({"tags": "popular"},
                 {"title": 1, "metrics.boughtInLastMonth": 1, "_id": 0})
           .sort("metrics.boughtInLastMonth", DESCENDING),
        [title_col,
         ("Bought/month", lambda r: f"{r['metrics']['boughtInLastMonth']:,}")],
        "9. Sort - most bought in the last month",
    )


# --------------------------------------------------------------------------
# UPDATE
# --------------------------------------------------------------------------
def demo_update(col):
    global _SECTION
    _SECTION = "update"
    banner("UPDATE - update_one / update_many / find_one_and_update", char="-")
    lab_filter = {"_id": {"$regex": f"^{LAB_PREFIX}"}}

    before = col.find_one({"_id": f"{LAB_PREFIX}0001"}, {"price": 1, "_id": 0})
    result = col.update_one({"_id": f"{LAB_PREFIX}0001"},
                            {"$set": {"price": 109.99},
                             "$currentDate": {"updatedAt": True}})
    after = col.find_one({"_id": f"{LAB_PREFIX}0001"}, {"price": 1, "updatedAt": 1, "_id": 0})
    print(f"  update_one    -> matched={result.matched_count} modified={result.modified_count}")
    print(f"                   price {before['price']} -> {after['price']}, updatedAt={after['updatedAt']}")
    CAPTURED["update"]["update_one"] = {
        "filter": {"_id": f"{LAB_PREFIX}0001"},
        "update": {"$set": {"price": 109.99}, "$currentDate": {"updatedAt": True}},
        "matched": result.matched_count, "modified": result.modified_count,
        "before": before, "after": after,
    }

    result = col.update_many(lab_filter, {"$inc": {"metrics.boughtInLastMonth": 25}})
    print(f"  update_many   -> matched={result.matched_count} modified={result.modified_count} "
          f"($inc on a nested field)")
    CAPTURED["update"]["update_many"] = {
        "filter": lab_filter, "update": {"$inc": {"metrics.boughtInLastMonth": 25}},
        "matched": result.matched_count, "modified": result.modified_count,
    }

    doc = col.find_one_and_update(
        {"_id": f"{LAB_PREFIX}0004"},
        {"$addToSet": {"tags": "clearance"},
         "$set": {"active": False}},
        projection={"title": 1, "tags": 1, "active": 1, "_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    print(f"  find_one_and_update -> {doc}")
    CAPTURED["update"]["find_one_and_update"] = {
        "filter": {"_id": f"{LAB_PREFIX}0004"},
        "update": {"$addToSet": {"tags": "clearance"}, "$set": {"active": False}},
        "returned_document_after": doc,
    }

    show(col.find(lab_filter, {"title": 1, "price": 1, "metrics.boughtInLastMonth": 1, "_id": 0}),
         [("Title", lambda r: short(r["title"])),
          ("Price", lambda r: f"${r['price']:,.2f}"),
          ("Bought/month", lambda r: r["metrics"]["boughtInLastMonth"])],
         "Lab documents after the updates")


# --------------------------------------------------------------------------
# DELETE
# --------------------------------------------------------------------------
def demo_delete(col):
    global _SECTION
    _SECTION = "delete"
    banner("DELETE - delete_one / delete_many", char="-")
    lab_filter = {"_id": {"$regex": f"^{LAB_PREFIX}"}}

    result = col.delete_one({"_id": f"{LAB_PREFIX}0002"})
    print(f"  delete_one    -> deleted={result.deleted_count}")

    remaining = col.count_documents(lab_filter)
    result = col.delete_many(lab_filter)
    print(f"  delete_many   -> deleted={result.deleted_count} (of {remaining} remaining)")
    final = col.count_documents(lab_filter)
    print(f"  count_documents(lab docs) = {final}  <- back to zero")
    CAPTURED["delete"] = {
        "delete_one": {"filter": {"_id": f"{LAB_PREFIX}0002"}, "deleted": 1},
        "delete_many": {"filter": lab_filter, "deleted": result.deleted_count},
        "lab_documents_remaining": final,
    }


def main():
    parser = argparse.ArgumentParser(description="Lab 4.1 - CRUD + query operators")
    parser.add_argument("--json", metavar="PATH", nargs="?",
                        const="lab_01_results.json", default=None,
                        help="write every CRUD/query result to a JSON file "
                             "(default: exports/lab_01_results.json)")
    args = parser.parse_args()

    banner("Lab 4.1: Product Query API (Amazon product export)")

    col = get_db(DB_NAME)[COLL_NAME]
    total = col.estimated_document_count()
    if total == 0:
        sys.exit(f"[FAIL] {DB_NAME}.{COLL_NAME} is empty.\n"
                 f"       Run:  python load_amazon_products.py")
    print(f"[OK] {DB_NAME}.{COLL_NAME} holds {total:,} documents.")

    # Clear any lab documents left behind by an interrupted previous run.
    col.delete_many({"_id": {"$regex": f"^{LAB_PREFIX}"}})

    PARAMS.update(pick_demo_params(col))
    print(f"[OK] Demo parameters chosen from the loaded data: "
          f"categories={PARAMS['CAT_A']!r} / {PARAMS['CAT_B']!r}, "
          f"price cut-off ${PARAMS['PRICE_HI']}, list-price cut-off ${PARAMS['LIST_HI']}")

    demo_create(col)
    demo_read(col)
    demo_update(col)
    demo_delete(col)

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = Path(__file__).resolve().parent / out
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "database": DB_NAME,
            "collection": COLL_NAME,
            "documents_in_collection": total,
            "demo_parameters": PARAMS,
            "results": CAPTURED,
        }
        out.write_text(json.dumps(payload, indent=2, default=json_util.default,
                                  ensure_ascii=False))
        print(f"\n[OK] Wrote results JSON -> {out}  ({out.stat().st_size / 1024:,.1f} KB)")

    banner("Lab 4.1 Complete")
    close_client()


if __name__ == "__main__":
    main()
