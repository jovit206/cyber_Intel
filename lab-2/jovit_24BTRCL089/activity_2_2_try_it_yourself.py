"""Activity 2.2 (Try it yourself) - Raise the price of every book published before 2010 by 10 percent.

Author: Jovit Chawla (24BTRCL089)

Uses its own collection (jovit_lab02.books_price_update) so it never disturbs Activity 2.2's data. Steps:
  1. seed the books and snapshot every price
  2. one update_many with an aggregation-pipeline update, so the new price is rounded to 2 decimals on the
     server and the change is stamped:  price = round(price * 1.10, 2)  for  {year: {$lt: 2010}}
  3. guard against applying the increase twice ({price_revised_2010: {$ne: true}} in the filter)
  4. verify book by book: every pre-2010 price equals round(old * 1.10, 2), every later book is unchanged
  5. print the Compass filter to inspect the result in the GUI
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json

from books import BOOKS
from common import RESULTS_DIR, Recorder, connect

FILTER = {"year": {"$lt": 2010}, "price_revised_2010": {"$ne": True}}
PIPELINE = [{"$set": {"old_price_inr": "$price_inr",
                      "price_inr": {"$round": [{"$multiply": ["$price_inr", 1.10]}, 2]},
                      "price_revised_2010": True, "price_revised_at": "$$NOW"}}]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", default="docker", choices=["docker", "native", "atlas"])
    args = parser.parse_args()
    client, db = connect(args.deployment)
    rec = Recorder("activity_2_2_try_it_yourself", "Activity 2.2 (Try it yourself) - 10% price rise for pre-2010 books")
    rec.log(f"Deployment: {args.deployment} (MongoDB {client.server_info()['version']}), collection "
            f"{db.name}.books_price_update")
    db.drop_collection("books_price_update")
    coll = db["books_price_update"]
    coll.create_index("isbn", unique=True)
    coll.insert_many(copy.deepcopy(BOOKS))
    before = {b["isbn"]: (b["title"], b["year"], b["price_inr"]) for b in coll.find({}, {"_id": 0})}
    eligible = sum(1 for _, year, _ in before.values() if year < 2010)
    rec.log(f"  seeded {len(before)} books; {eligible} were published before 2010")

    rec.section("Update")
    rec.log(f"  filter:   {json.dumps(FILTER)}")
    rec.log("  update:   [{$set: {old_price_inr: '$price_inr', price_inr: {$round: [{$multiply: ['$price_inr', 1.10]}, 2]}, "
            "price_revised_2010: true, price_revised_at: '$$NOW'}}]")
    result = coll.update_many(FILTER, PIPELINE)
    rec.log(f"  matched {result.matched_count}, modified {result.modified_count}")
    again = coll.update_many(FILTER, PIPELINE)
    rec.log(f"  running the same update again: matched {again.matched_count}, modified {again.modified_count} "
            "(the guard stops a second 10% increase)")

    rec.section("Verification")
    rows, problems = [], []
    for doc in coll.find({}, {"_id": 0}).sort([("year", 1), ("title", 1)]):
        title, year, old = before[doc["isbn"]]
        expected = round(old * 1.10, 2) if year < 2010 else old
        ok = abs(doc["price_inr"] - expected) < 1e-9
        rows.append({"title": title, "year": year, "before": old, "after": doc["price_inr"], "expected": expected,
                     "changed": year < 2010, "ok": ok})
        if not ok:
            problems.append(title)
        rec.log(f"  {'OK  ' if ok else 'FAIL'} {year}  {title:<40} {old:>8.2f} -> {doc['price_inr']:>8.2f}"
                f"{'  (+10%)' if year < 2010 else '  (unchanged)'}")
    rec.log(f"  {len(rows) - len(problems)}/{len(rows)} prices correct; modified count matches eligible books: "
            f"{result.modified_count == eligible}")
    total_before = round(sum(p for _, _, p in before.values()), 2)
    total_after = round(sum(r["after"] for r in rows), 2)
    rec.log(f"  catalogue value: INR {total_before:,.2f} -> INR {total_after:,.2f}")

    rec.section("Check in MongoDB Compass")
    rec.log(f"  connect to {('mongodb://localhost:27028' if args.deployment == 'docker' else 'your deployment')}, open "
            f"{db.name} > books_price_update and use:")
    rec.log('    Filter:  { "year": { "$lt": 2010 } }      -> every result has price_revised_2010: true and old_price_inr')
    rec.log('    Filter:  { "year": { "$gte": 2010 } }     -> no result has price_revised_2010')

    final = list(coll.find({}, {"_id": 0}).sort("isbn", 1))
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "activity_2_2_try_it_yourself_books.json").write_text(
        json.dumps(final, indent=2, ensure_ascii=False, default=lambda v: v.isoformat() if isinstance(v, dt.datetime) else str(v)),
        encoding="utf-8")
    rec.log(f"\n  exported {len(final)} books to results/activity_2_2_try_it_yourself_books.json")
    rec.data = {"deployment": args.deployment, "eligible": eligible, "matched": result.matched_count,
                "modified": result.modified_count, "second_run_modified": again.modified_count, "rows": rows,
                "problems": problems, "catalogue_before": total_before, "catalogue_after": total_after}
    rec.save()
    if problems or result.modified_count != eligible or again.modified_count != 0:
        raise SystemExit("verification failed")


if __name__ == "__main__":
    main()
