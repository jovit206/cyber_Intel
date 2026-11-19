"""
Activity 2.2 - Bookstore CRUD Operations
Chapter 02 : Cloud Computing Primer and Environment Setup

Create / Read / Update / Delete against a `bookstore.books` collection using
PyMongo. Each book document embeds its publisher as a nested object and carries
a tags array - the embedding pattern previewed early from Chapter 3.

Exports the final collection to activity_2_2_books.json.

Run:  python activity_2_2_bookstore_crud.py
"""

import json
import os
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

BOOKS = [
    {"title": "The Pragmatic Programmer", "author": "David Thomas & Andrew Hunt",
     "year": 2019, "genre": "Software Engineering", "price": 49.99, "isbn": "978-0135957059",
     "tags": ["programming", "career", "best-practices"],
     "publisher": {"name": "Addison-Wesley", "location": "Boston"}},
    {"title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann",
     "year": 2017, "genre": "Databases", "price": 45.99, "isbn": "978-1449373320",
     "tags": ["databases", "distributed-systems", "architecture"],
     "publisher": {"name": "O'Reilly Media", "location": "Sebastopol"}},
    {"title": "Deep Learning", "author": "Ian Goodfellow, Yoshua Bengio, Aaron Courville",
     "year": 2016, "genre": "Machine Learning", "price": 72.00, "isbn": "978-0262035613",
     "tags": ["AI", "neural-networks", "theory"],
     "publisher": {"name": "MIT Press", "location": "Cambridge"}},
    {"title": "Clean Code", "author": "Robert C. Martin",
     "year": 2008, "genre": "Software Engineering", "price": 39.99, "isbn": "978-0132350884",
     "tags": ["programming", "clean-code", "refactoring"],
     "publisher": {"name": "Prentice Hall", "location": "Upper Saddle River"}},
    {"title": "Hands-On Machine Learning", "author": "Aurelien Geron",
     "year": 2022, "genre": "Machine Learning", "price": 59.99, "isbn": "978-1098125974",
     "tags": ["AI", "scikit-learn", "tensorflow", "practical"],
     "publisher": {"name": "O'Reilly Media", "location": "Sebastopol"}},
]


def banner(text):
    print("\n" + "=" * 72)
    print(f" {text}")
    print("=" * 72)


def export(col, filename):
    docs = list(col.find({}))
    for d in docs:
        d["_id"] = str(d["_id"])
    out = HERE / filename
    out.write_text(json.dumps(docs, indent=2, default=str))
    print(f"\n[OK] Exported {len(docs)} documents -> {out.name}")


def main():
    banner("Activity 2.2 : Bookstore CRUD Operations")

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        print(f"Connected to MongoDB {client.server_info()['version']}")
    except PyMongoError as exc:
        print(f"[ERROR] Cannot reach MongoDB: {exc}")
        print("  docker run -d -p 27017:27017 --name mongodb mongo:7")
        return

    col = client["bookstore"]["books"]
    col.drop()                                    # start from a clean slate

    # ------------------------------------------------------------- CREATE ---
    print("\n=== CREATE: Inserting 5 books ===")
    result = col.insert_many(BOOKS)
    print(f"[OK] Inserted {len(result.inserted_ids)} books")
    for _id in result.inserted_ids:
        print(f"  _id: {_id}")

    # --------------------------------------------------------------- READ ---
    print("\n=== READ: Books published after 2020  {'year': {'$gt': 2020}} ===")
    for b in col.find({"year": {"$gt": 2020}}, {"title": 1, "year": 1, "price": 1, "_id": 0}):
        print(f"  {b['title']:<45} ({b['year']})  ${b['price']:.2f}")

    print("\n=== READ: Machine Learning books  {'genre': 'Machine Learning'} ===")
    for b in col.find({"genre": "Machine Learning"}, {"title": 1, "author": 1, "_id": 0}):
        print(f"  {b['title']:<45} by {b['author']}")

    print("\n=== READ: Books priced $40-$60  {'price': {'$gte': 40, '$lte': 60}} ===")
    for b in col.find({"price": {"$gte": 40, "$lte": 60}}, {"title": 1, "price": 1, "_id": 0}):
        print(f"  {b['title']:<45} ${b['price']:.2f}")

    print("\n=== READ: Books tagged 'AI'  {'tags': 'AI'} ===")
    for b in col.find({"tags": "AI"}, {"title": 1, "tags": 1, "_id": 0}):
        print(f"  {b['title']:<45} tags: {b['tags']}")

    print("\n=== READ: Books from O'Reilly  {'publisher.name': \"O'Reilly Media\"} ===")
    for b in col.find({"publisher.name": "O'Reilly Media"},
                      {"title": 1, "publisher": 1, "_id": 0}):
        print(f"  {b['title']:<45} {b['publisher']['name']}, {b['publisher']['location']}")

    # ------------------------------------------------------------- UPDATE ---
    print("\n=== UPDATE: 10% price increase on all books  {'$mul': {'price': 1.10}} ===")
    before = {b["title"]: b["price"] for b in col.find({}, {"title": 1, "price": 1, "_id": 0})}
    upd = col.update_many({}, {"$mul": {"price": 1.10}})
    print(f"[OK] Matched {upd.matched_count}, modified {upd.modified_count} books")
    after = {b["title"]: b["price"] for b in col.find({}, {"title": 1, "price": 1, "_id": 0})}
    for title, old in before.items():
        print(f"  {title:<45} ${old:>6.2f} -> ${after[title]:>6.2f}")

    # ------------------------------------------------------------- DELETE ---
    print("\n=== DELETE: Removing 'Clean Code' ===")
    dele = col.delete_one({"title": "Clean Code"})
    print(f"[OK] Deleted {dele.deleted_count} document(s)")
    print(f"  Remaining books: {col.count_documents({})}")

    # --------------------------------------------------------------- FINAL ---
    print("\n=== Final Collection State (bookstore.books) ===")
    for b in col.find({}, {"_id": 0}):
        print(f"  {b['title']:<45} ({b['year']})  ${b['price']:.2f}  [{b['genre']}]")

    mirror_to_atlas(list(col.find({})), "bookstore", "books")
    export(col, "activity_2_2_books.json")
    client.close()
    banner("Activity 2.2 Complete")


if __name__ == "__main__":
    main()
