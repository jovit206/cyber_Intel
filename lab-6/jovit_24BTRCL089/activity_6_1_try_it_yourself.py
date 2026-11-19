"""Activity 6.1 (Try it yourself) - Find out which indexes a workload really uses, with $indexStats.

Author: Jovit Chawla (24BTRCL089)

Works on the products collection and index design from Activity 6.1 (run that first). Two extra indexes are added
on purpose to see whether $indexStats exposes them:
    idx_category   {category:1}  - redundant: it is a prefix of idx_category_price_rating
    idx_stock      {stock:1}     - no query in the workload filters or sorts on stock
Then a 200-query storefront workload with randomised parameters is executed (read-only), weighted like real
traffic: Q1 x70, Q2 x45, Q3 x40, Q4 x25, Q5 x20. The accesses.ops counters are read before and after, so only
this workload's usage is counted.
"""
from __future__ import annotations

import random
import sys

from catalog import CATEGORIES, COLLECTION, INDEXES
from common import Recorder, connect

WEIGHTS = {"Q1": 70, "Q2": 45, "Q3": 40, "Q4": 25, "Q5": 20}
EXTRA = {"idx_category": [("category", 1)], "idx_stock": [("stock", 1)]}


def index_ops(coll) -> dict[str, int]:
    return {s["name"]: int(s["accesses"]["ops"]) for s in coll.aggregate([{"$indexStats": {}}])}


def run_query(coll, qid: str, rng: random.Random) -> int:
    category = rng.choice(list(CATEGORIES))
    all_tags = sorted({t for *_, tags in CATEGORIES.values() for t in tags})
    if qid == "Q1":
        cursor = coll.find({"category": category}).sort("price", 1).limit(20)
    elif qid == "Q2":
        low = rng.choice([500, 1000, 2000, 5000, 10000])
        cursor = coll.find({"price": {"$gte": low, "$lte": low * 1.5}})
    elif qid == "Q3":
        cursor = coll.find({"tags": rng.choice(all_tags)})
    elif qid == "Q4":
        words = rng.choice(["gaming notebook", "wireless earbuds", "cotton kurta", "smart purifier", "board game"])
        cursor = coll.find({"$text": {"$search": words}})
    else:
        cursor = coll.find({"category": category, "rating": {"$gte": rng.choice([4.0, 4.3, 4.5])}}).sort("price", 1).limit(20)
    return sum(1 for _ in cursor)


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_1_try_it_yourself", "Activity 6.1 (Try it yourself) - Index usage with $indexStats", client)
    coll = db[COLLECTION]
    existing = set(coll.index_information())
    if not set(INDEXES) <= existing:
        sys.exit("Run activity_6_1_index_design.py first: the designed indexes are missing.")
    for name, keys in EXTRA.items():
        coll.create_index(keys, name=name)
    rec.log(f"  collection {db.name}.{COLLECTION}: {coll.estimated_document_count():,} documents, "
            f"{len(coll.index_information())} indexes (2 extra: {', '.join(EXTRA)})")

    rng = random.Random(89)
    plan = [qid for qid, n in WEIGHTS.items() for _ in range(n)]
    rng.shuffle(plan)
    before = index_ops(coll)
    returned = {qid: 0 for qid in WEIGHTS}
    for qid in plan:
        returned[qid] += run_query(coll, qid, rng)
    after = index_ops(coll)
    used = {name: after.get(name, 0) - before.get(name, 0) for name in after}
    rec.log(f"  executed {len(plan)} queries {WEIGHTS}; documents returned per query type: {returned}")

    rec.section("$indexStats - accesses.ops during this workload")
    total = sum(used.values()) or 1
    for name, ops in sorted(used.items(), key=lambda kv: -kv[1]):
        rec.log(f"  {name:<28} {ops:>5} ops  {ops / total:6.1%}  {'#' * round(40 * ops / total)}")

    unused = sorted(n for n, ops in used.items() if ops == 0 and n != "_id_")
    redundant = [n for n, keys in EXTRA.items() for other, okeys in INDEXES.items()
                 if okeys[:len(keys)] == keys and n != other]
    rec.section("Findings")
    rec.log(f"  most used index: {max(used, key=used.get)}")
    rec.log(f"  never used by this workload: {unused or 'none'}")
    rec.log(f"  redundant (a prefix of a compound index): {redundant or 'none'}")
    rec.log("  recommendation: drop " + (", ".join(sorted(set(unused) | set(redundant))) or "nothing") +
            " - each unused index still costs memory and work on every insert/update")
    for name in EXTRA:                       # leave the collection as Activity 6.1 designed it
        coll.drop_index(name)
    rec.log(f"  dropped the two experimental indexes again; remaining: {sorted(coll.index_information())}")
    rec.data = {"queries": WEIGHTS, "returned": returned, "ops": used, "unused": unused, "redundant": redundant,
                "most_used": max(used, key=used.get)}
    rec.save()


if __name__ == "__main__":
    main()
