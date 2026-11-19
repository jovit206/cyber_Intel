"""Activity 2.2 - Bookstore CRUD with PyMongo.

Author: Jovit Chawla (24BTRCL089)

Runs Create, Read, Update and Delete operations on jovit_lab02.books (Docker deployment by default; pass
--deployment native to use the Windows service). Book documents embed the publisher as a sub-document and keep
authors/tags as arrays. Every step checks its own result against the database, so the run fails if an
operation did not do what it claims. The final collection is exported to results/activity_2_2_books.json.
"""
from __future__ import annotations

import argparse
import copy
import json

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from books import BOOKS
from common import RESULTS_DIR, Recorder, connect

PROJECTION = {"_id": 0, "title": 1, "year": 1, "price_inr": 1}


def show(rec: Recorder, label: str, docs: list[dict]) -> None:
    rec.log(f"  {label} ({len(docs)}):")
    for doc in docs:
        rec.log(f"    - {json.dumps(doc, ensure_ascii=False, default=str)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", default="docker", choices=["docker", "native", "atlas"])
    args = parser.parse_args()
    client, db = connect(args.deployment)
    rec = Recorder("activity_2_2", "Activity 2.2 - Bookstore CRUD Operations")
    rec.log(f"Deployment: {args.deployment} (MongoDB {client.server_info()['version']}), collection {db.name}.books")
    db.drop_collection("books")
    books = db["books"]
    books.create_index("isbn", unique=True)
    books.create_index([("tags", ASCENDING)])
    steps: dict[str, dict] = {}

    rec.section("CREATE")
    first, rest = copy.deepcopy(BOOKS[0]), copy.deepcopy(BOOKS[1:])
    one = books.insert_one(first)
    many = books.insert_many(rest)
    rec.log(f"  insert_one -> _id {one.inserted_id}; insert_many -> {len(many.inserted_ids)} ids")
    try:
        books.insert_one({"isbn": BOOKS[0]["isbn"], "title": "duplicate"})
        duplicate = "accepted (unexpected)"
    except DuplicateKeyError:
        duplicate = "rejected with DuplicateKeyError (unique index on isbn)"
    rec.log(f"  inserting a second book with ISBN {BOOKS[0]['isbn']}: {duplicate}")
    total = books.count_documents({})
    assert total == len(BOOKS), total
    steps["create"] = {"inserted": total, "duplicate_isbn": duplicate}

    rec.section("READ")
    by_isbn = books.find_one({"isbn": "978-1449373320"}, {"_id": 0, "title": 1, "publisher.name": 1, "authors": 1})
    rec.log(f"  find_one by ISBN: {by_isbn}")
    db_books = list(books.find({"tags": "databases"}, PROJECTION).sort("year", ASCENDING))
    show(rec, "books tagged 'databases', oldest first", db_books)
    mit = list(books.find({"publisher.name": "MIT Press"}, PROJECTION))
    show(rec, "query on the embedded publisher: MIT Press", mit)
    range_ = list(books.find({"year": {"$gte": 1995, "$lte": 2010}, "price_inr": {"$lt": 800}}, PROJECTION)
                  .sort([("price_inr", DESCENDING)]))
    show(rec, "published 1995-2010 and under INR 800, most expensive first", range_)
    multi_author = books.count_documents({"authors.2": {"$exists": True}})
    rec.log(f"  books with three or more authors: {multi_author}")
    per_publisher = list(books.aggregate([{"$group": {"_id": "$publisher.name", "titles": {"$sum": 1},
                                                      "stock": {"$sum": "$stock"}}}, {"$sort": {"titles": -1, "_id": 1}}]))
    rec.log("  titles per publisher: " + ", ".join(f"{p['_id']}={p['titles']}" for p in per_publisher))
    assert len(db_books) == 2 and len(mit) == 2
    steps["read"] = {"by_isbn": by_isbn, "databases": db_books, "mit_press": mit, "range": range_,
                     "three_plus_authors": multi_author, "per_publisher": per_publisher}

    rec.section("UPDATE")
    sold = books.update_one({"isbn": "978-1593276034", "stock": {"$gte": 2}}, {"$inc": {"stock": -2}})
    tagged = books.update_one({"isbn": "978-1449373320"}, {"$addToSet": {"tags": "nosql"}})
    tagged_again = books.update_one({"isbn": "978-1449373320"}, {"$addToSet": {"tags": "nosql"}})
    moved = books.update_many({"publisher.name": "Addison-Wesley"}, {"$set": {"publisher.imprint": "Pearson"}})
    upsert = books.update_one({"isbn": "978-0596517748"},
                              {"$setOnInsert": {"title": "JavaScript: The Good Parts", "authors": ["Douglas Crockford"],
                                                "year": 2008, "publisher": {"name": "O'Reilly Media", "country": "USA"},
                                                "tags": ["javascript"], "price_inr": 480.0, "stock": 0}},
                              upsert=True)
    rec.log(f"  sell 2 copies of Python Crash Course ($inc): matched {sold.matched_count}, modified {sold.modified_count}")
    rec.log(f"  add tag 'nosql' ($addToSet): modified {tagged.modified_count}; repeating it modified "
            f"{tagged_again.modified_count} (no duplicate tag)")
    rec.log(f"  set publisher.imprint on all Addison-Wesley books (update_many): modified {moved.modified_count}")
    rec.log(f"  upsert a new title by ISBN: upserted_id {upsert.upserted_id}")
    after = books.find_one({"isbn": "978-1593276034"}, {"_id": 0, "stock": 1})
    tags = books.find_one({"isbn": "978-1449373320"}, {"_id": 0, "tags": 1})["tags"]
    assert after["stock"] == 13 and tags.count("nosql") == 1 and moved.modified_count == 3
    rec.log(f"  verified: stock now {after['stock']}, tags {tags}")
    steps["update"] = {"stock_after_sale": after["stock"], "tags": tags, "imprint_modified": moved.modified_count,
                       "upserted": upsert.upserted_id is not None}

    rec.section("DELETE")
    out_of_stock = books.delete_many({"stock": 0})
    one_gone = books.delete_one({"isbn": "978-0201835953"})
    remaining = books.count_documents({})
    rec.log(f"  delete_many out-of-stock books: deleted {out_of_stock.deleted_count}")
    rec.log(f"  delete_one The Mythical Man-Month: deleted {one_gone.deleted_count}")
    assert remaining == len(BOOKS) - 1 and books.count_documents({"isbn": "978-0201835953"}) == 0
    rec.log(f"  verified: {remaining} books remain, deleted ISBN no longer found")
    steps["delete"] = {"deleted_out_of_stock": out_of_stock.deleted_count, "deleted_one": one_gone.deleted_count,
                       "remaining": remaining}

    final = list(books.find({}, {"_id": 0}).sort("isbn", ASCENDING))
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "activity_2_2_books.json").write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    rec.log(f"\n  exported {len(final)} books to results/activity_2_2_books.json")
    rec.log("  all CRUD checks passed")
    rec.data = {"deployment": args.deployment, "steps": steps, "final_count": len(final)}
    rec.save()


if __name__ == "__main__":
    main()
