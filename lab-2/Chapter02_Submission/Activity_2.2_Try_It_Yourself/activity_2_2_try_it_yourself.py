"""
Activity 2.2 - Try It Yourself
"Add an update operation to your script that increases the price of all books
 published before 2010 by 10 percent. Verify the update in Compass."

Seeds bookstore.books_price_update with 8 titles (4 published before 2010, 4 from 2010
onwards), snapshots every price, then runs the targeted bulk update:

    col.update_many({"year": {"$lt": 2010}}, {"$mul": {"price": 1.10}})

and proves that ONLY the pre-2010 books changed. Prints the exact filter to
paste into MongoDB Compass so the update can be verified in the GUI.

Exports the updated collection to activity_2_2_try_it_yourself_books.json.

Run:  python activity_2_2_try_it_yourself.py
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
MONGO_URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017")
ATLAS_URI = os.getenv("MONGO_URI_ATLAS")


def mirror_to_atlas(docs, db_name, col_name):
    """Copy the finished collection up to Atlas so the cloud cluster holds the
    same data as the Docker deployment. Purely additive - the activity's own
    work still happens against MONGO_URI_LOCAL first."""
    if not ATLAS_URI:
        print(f"[SKIP] Atlas mirror: MONGO_URI_ATLAS is not set in .env")
        return
    try:
        client = MongoClient(ATLAS_URI, serverSelectionTimeoutMS=10000,
                             tlsCAFile=certifi.where())
        col = client[db_name][col_name]
        col.delete_many({})
        if docs:
            col.insert_many(docs)
        print(f"[OK] Mirrored {len(docs)} document(s) to Atlas -> {db_name}.{col_name}")
        client.close()
    except PyMongoError as exc:
        print(f"[WARN] Atlas mirror failed: {type(exc).__name__} - {exc}")

CUTOFF_YEAR = 2010
INCREASE = 1.10                       # +10%

BOOKS = [
    # ---- published BEFORE 2010 : these four must be updated ----------------
    {"title": "Clean Code", "author": "Robert C. Martin",
     "year": 2008, "genre": "Software Engineering", "price": 39.99, "isbn": "978-0132350884",
     "tags": ["programming", "clean-code", "refactoring"],
     "publisher": {"name": "Prentice Hall", "location": "Upper Saddle River"}},
    {"title": "Design Patterns", "author": "Gamma, Helm, Johnson & Vlissides",
     "year": 1994, "genre": "Software Engineering", "price": 54.99, "isbn": "978-0201633610",
     "tags": ["programming", "patterns", "oop"],
     "publisher": {"name": "Addison-Wesley", "location": "Boston"}},
    {"title": "Refactoring", "author": "Martin Fowler",
     "year": 1999, "genre": "Software Engineering", "price": 47.50, "isbn": "978-0201485677",
     "tags": ["programming", "refactoring", "best-practices"],
     "publisher": {"name": "Addison-Wesley", "location": "Boston"}},
    {"title": "Introduction to Algorithms", "author": "Cormen, Leiserson, Rivest & Stein",
     "year": 2009, "genre": "Computer Science", "price": 89.00, "isbn": "978-0262033848",
     "tags": ["algorithms", "theory", "textbook"],
     "publisher": {"name": "MIT Press", "location": "Cambridge"}},

    # ---- published 2010 or later : these four must NOT change --------------
    {"title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann",
     "year": 2017, "genre": "Databases", "price": 45.99, "isbn": "978-1449373320",
     "tags": ["databases", "distributed-systems", "architecture"],
     "publisher": {"name": "O'Reilly Media", "location": "Sebastopol"}},
    {"title": "Deep Learning", "author": "Ian Goodfellow, Yoshua Bengio, Aaron Courville",
     "year": 2016, "genre": "Machine Learning", "price": 72.00, "isbn": "978-0262035613",
     "tags": ["AI", "neural-networks", "theory"],
     "publisher": {"name": "MIT Press", "location": "Cambridge"}},
    {"title": "The Pragmatic Programmer", "author": "David Thomas & Andrew Hunt",
     "year": 2019, "genre": "Software Engineering", "price": 49.99, "isbn": "978-0135957059",
     "tags": ["programming", "career", "best-practices"],
     "publisher": {"name": "Addison-Wesley", "location": "Boston"}},
    {"title": "Hands-On Machine Learning", "author": "Aurelien Geron",
     "year": 2022, "genre": "Machine Learning", "price": 59.99, "isbn": "978-1098125974",
     "tags": ["AI", "scikit-learn", "tensorflow", "practical"],
     "publisher": {"name": "O'Reilly Media", "location": "Sebastopol"}},
]


def banner(text):
    print("\n" + "=" * 78)
    print(f" {text}")
    print("=" * 78)


def price_table(col, heading):
    print(f"\n{heading}")
    print(f"  {'Title':<42} {'Year':>6} {'Price':>10}")
    print("  " + "-" * 60)
    snapshot = {}
    for b in col.find({}, {"title": 1, "year": 1, "price": 1, "_id": 0}).sort("year", 1):
        marker = "*" if b["year"] < CUTOFF_YEAR else " "
        print(f" {marker}{b['title']:<42} {b['year']:>6} {b['price']:>9.2f}")
        snapshot[b["title"]] = b["price"]
    print("  (* = published before 2010)")
    return snapshot


def main():
    banner("Activity 2.2 Try It Yourself : +10% price for books published before 2010")

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        print(f"Connected to MongoDB {client.server_info()['version']}  ({MONGO_URI})")
    except PyMongoError as exc:
        print(f"[ERROR] Cannot reach MongoDB: {exc}")
        return

    col = client["bookstore"]["books_price_update"]
    col.drop()

    # ------------------------------------------------------------- SEED -----
    print("\n=== SETUP: Seeding the bookstore ===")
    col.insert_many(BOOKS)
    total = col.count_documents({})
    old_count = col.count_documents({"year": {"$lt": CUTOFF_YEAR}})
    print(f"[OK] Inserted {total} books "
          f"({old_count} published before {CUTOFF_YEAR}, {total - old_count} from {CUTOFF_YEAR} onwards)")

    before = price_table(col, "=== BEFORE the update ===")

    # ----------------------------------------------------------- UPDATE -----
    banner("UPDATE : increase price of all books published before 2010 by 10%")
    print('  Filter : {"year": {"$lt": 2010}}')
    print('  Update : {"$mul": {"price": 1.10}}')

    result = col.update_many({"year": {"$lt": CUTOFF_YEAR}},
                             {"$mul": {"price": INCREASE}})
    print(f"\n[OK] matched_count  = {result.matched_count}")
    print(f"[OK] modified_count = {result.modified_count}")

    after = price_table(col, "=== AFTER the update ===")

    # ----------------------------------------------------------- VERIFY -----
    banner("VERIFICATION")
    print(f"  {'Title':<42} {'Year':>6} {'Old':>9} {'New':>9} {'Change':>9}")
    print("  " + "-" * 79)
    changed, unchanged, errors = 0, 0, []
    for b in col.find({}, {"title": 1, "year": 1, "_id": 0}).sort("year", 1):
        title, year = b["title"], b["year"]
        old, new = before[title], after[title]
        delta = (new - old) / old * 100 if old else 0
        print(f"  {title:<42} {year:>6} {old:>9.2f} {new:>9.2f} {delta:>8.2f}%")

        if year < CUTOFF_YEAR:
            changed += 1
            if abs(new - round(old * INCREASE, 10)) > 0.01:
                errors.append(f"{title}: expected {old * INCREASE:.2f}, got {new:.2f}")
        else:
            unchanged += 1
            if abs(new - old) > 1e-9:
                errors.append(f"{title}: should NOT have changed ({old:.2f} -> {new:.2f})")

    print(f"\n  Books updated (+10%)     : {changed}")
    print(f"  Books left untouched     : {unchanged}")
    if errors:
        print("  [FAIL] Verification errors:")
        for e in errors:
            print(f"    - {e}")
    else:
        print("  [OK] Every pre-2010 book rose by exactly 10%; "
              "every 2010+ book is unchanged.")

    # ------------------------------------------------- COMPASS INSTRUCTIONS --
    banner("VERIFY IN MONGODB COMPASS")
    print(f"  1. Connect Compass to : {MONGO_URI}")
    print("  2. Open database      : bookstore    ->  collection: books_price_update")
    print("  3. Paste this into the FILTER bar to see the updated books:")
    print('        {"year": {"$lt": 2010}}')
    print("  4. Confirm the four prices now read:")
    for b in col.find({"year": {"$lt": CUTOFF_YEAR}},
                      {"title": 1, "price": 1, "_id": 0}).sort("year", 1):
        print(f"        {b['title']:<42} ${b['price']:.2f}")
    print("  5. Then switch the filter to see the untouched books:")
    print('        {"year": {"$gte": 2010}}')

    mirror_to_atlas(list(col.find({})), "bookstore", "books_price_update")

    # ----------------------------------------------------------- EXPORT -----
    docs = list(col.find({}))
    for d in docs:
        d["_id"] = str(d["_id"])
    out = HERE / "activity_2_2_try_it_yourself_books.json"
    out.write_text(json.dumps({
        "activity": "2.2 Try It Yourself - 10% price increase for books published before 2010",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "filter": {"year": {"$lt": CUTOFF_YEAR}},
        "update": {"$mul": {"price": INCREASE}},
        "matched_count": result.matched_count,
        "modified_count": result.modified_count,
        "prices_before": before,
        "prices_after": after,
        "documents": docs,
    }, indent=2, default=str))
    print(f"\n[OK] Exported {len(docs)} documents -> {out.name}")

    client.close()
    banner("Activity 2.2 Try It Yourself Complete")


if __name__ == "__main__":
    main()
