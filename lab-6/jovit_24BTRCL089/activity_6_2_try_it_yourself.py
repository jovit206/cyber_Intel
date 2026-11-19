"""Activity 6.2 (Try it yourself) - Covered queries.

Author: Jovit Chawla (24BTRCL089)

Uses products_perf from Activity 6.2 (only idx_category_price_rating). One query - Audio products priced
INR 2,000-3,000, sorted by price - is run with different projections:
  P1  no projection (whole document)                    -> must FETCH every document
  P2  {category, price, rating} with _id included       -> still FETCH (_id is not in the index)
  P3  {category, price, rating, _id: 0}                 -> COVERED: answered from the index alone
  P4  {category, price, name, _id: 0}                   -> FETCH (name is not in the index)
For each: plan stages, keys/documents examined (explain executionStats) and the median time of 300 runs.
A second experiment shows that a multikey (array) index cannot cover a query on the array field.
"""
from __future__ import annotations

import sys

from common import Recorder, connect, explain_find, time_ms

FILTER = {"category": "Audio", "price": {"$gte": 2000, "$lte": 3000}}
SORT = {"price": 1}
PROJECTIONS = {
    "P1 whole document": None,
    "P2 index fields + _id": {"category": 1, "price": 1, "rating": 1},
    "P3 index fields, _id excluded": {"category": 1, "price": 1, "rating": 1, "_id": 0},
    "P4 adds a non-index field (name)": {"category": 1, "price": 1, "name": 1, "_id": 0},
}
RUNS = 300


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_2_try_it_yourself", "Activity 6.2 (Try it yourself) - Covered Queries", client)
    coll = db["products_perf"]
    if "idx_category_price_rating" not in coll.index_information():
        sys.exit("Run activity_6_2_index_strategies.py first (it builds products_perf with idx_category_price_rating).")
    rec.log(f"  {db.name}.products_perf: {coll.estimated_document_count():,} documents, indexes {sorted(coll.index_information())}")
    rec.log(f"  query: filter {FILTER}, sort {SORT}")

    rec.section(f"Projections (explain + median of {RUNS} runs)")
    rows = {}
    for label, projection in PROJECTIONS.items():
        summary = explain_find(coll, FILTER, sort=SORT, projection=projection)
        summary["median_ms"] = time_ms(lambda p=projection: list(coll.find(FILTER, p).sort("price", 1)), repeat=RUNS)
        summary["covered"] = summary["docsExamined"] == 0 and "FETCH" not in summary["plan"]
        rows[label] = rec.plan(f"{label:<34} median {summary['median_ms']:.3f} ms covered={summary['covered']}", summary)
    covered = [k for k, v in rows.items() if v["covered"]]
    fetch_ms = rows["P2 index fields + _id"]["median_ms"]
    cover_ms = rows["P3 index fields, _id excluded"]["median_ms"]
    rec.log(f"  covered projections: {covered}")
    rec.log(f"  excluding _id turned FETCH into a covered plan: {rows['P2 index fields + _id']['docsExamined']} -> "
            f"{rows['P3 index fields, _id excluded']['docsExamined']} documents examined, {fetch_ms:.3f} -> {cover_ms:.3f} ms "
            f"({(1 - cover_ms / fetch_ms):.1%} faster)")

    rec.section("Multikey limitation")
    coll.create_index("tags", name="idx_tags_tmp")
    multikey = rec.plan("find({tags: 'anc'}, {tags: 1, _id: 0}) on a multikey index",
                        explain_find(coll, {"tags": "anc"}, projection={"tags": 1, "_id": 0}))
    coll.drop_index("idx_tags_tmp")
    rec.log(f"  documents examined: {multikey['docsExamined']} - an index on an array field cannot cover a query that "
            "returns that field, because each index key holds only one array element")

    rec.data = {"filter": FILTER, "runs": RUNS, "projections": rows, "covered": covered, "multikey": multikey}
    rec.save()


if __name__ == "__main__":
    main()
