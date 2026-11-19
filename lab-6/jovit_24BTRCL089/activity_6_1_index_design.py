"""Activity 6.1 - Design indexes for a query workload.

Author: Jovit Chawla (24BTRCL089)

1. Seed jovit_lab06.products with 50,000 generated products (only the _id index).
2. explain("executionStats") all five workload queries - expected: collection scans.
3. Create the designed index set (catalog.INDEXES):
     {category:1, price:1, rating:1}  Equality-Sort-Range compound index -> serves Q1 and Q5
     {price:1}                        range query Q2
     {tags:1}                         multikey index -> Q3
     text(name, description)          $text search -> Q4
4. explain again and report keys/documents examined, plan stages and the reduction per query.
"""
from __future__ import annotations

from catalog import INDEXES, PRODUCT_COUNT, Q4_REGEX, QUERIES, seed
from common import Recorder, connect, explain_find


def explain_query(coll, kind: str, spec: dict, text_index: bool) -> dict:
    if kind == "text":
        if not text_index:
            return explain_find(coll, Q4_REGEX)          # $text fails without a text index
        return explain_find(coll, {"$text": {"$search": spec["search"]}})
    return explain_find(coll, spec["filter"], sort=spec.get("sort"), limit=spec.get("limit", 0))


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_1", "Activity 6.1 - Index Design for a Query Workload", client)
    coll = seed(db)
    rec.log(f"  seeded {coll.estimated_document_count():,} products into {db.name}.products; indexes: "
            f"{list(coll.index_information())}")
    for qid, description, _, _ in QUERIES:
        rec.log(f"  {qid}: {description}")

    rec.section("Before: only the _id index")
    before = {}
    for qid, _, kind, spec in QUERIES:
        label = qid + (" (regex fallback - $text needs a text index)" if kind == "text" else "")
        before[qid] = rec.plan(label, explain_query(coll, kind, spec, text_index=False))

    rec.section("Index design")
    for name, keys in INDEXES.items():
        coll.create_index(keys, name=name)
        rec.log(f"  created {name}: {dict(keys)}")
    sizes = db.command("collStats", coll.name)["indexSizes"]
    rec.log("  index sizes: " + ", ".join(f"{n} {s // 1024} KiB" for n, s in sizes.items()))

    rec.section("After: designed indexes")
    after, rows = {}, []
    for qid, _, kind, spec in QUERIES:
        after[qid] = rec.plan(qid, explain_query(coll, kind, spec, text_index=True))
        b, a = before[qid]["docsExamined"], after[qid]["docsExamined"]
        reduction = round(100 * (1 - a / b), 2) if b else None
        sorted_in_memory = "SORT" in after[qid]["plan"].split(" <- ")
        rows.append({"query": qid, "before_plan": before[qid]["plan"], "after_plan": after[qid]["plan"],
                     "docs_before": b, "docs_after": a, "keys_after": after[qid]["keysExamined"],
                     "returned": after[qid]["nReturned"], "reduction_pct": reduction, "in_memory_sort": sorted_in_memory})

    rec.section("Summary")
    rec.log(f"  {'query':<6}{'before':<22}{'after':<40}{'docs before':>12}{'docs after':>12}{'reduction':>11}")
    for r in rows:
        rec.log(f"  {r['query']:<6}{r['before_plan'][:21]:<22}{r['after_plan'][:39]:<40}{r['docs_before']:>12,}"
                f"{r['docs_after']:>12,}{str(r['reduction_pct']) + '%':>11}")
    all_ixscan = all("COLLSCAN" not in r["after_plan"] for r in rows)
    rec.log(f"  every query uses an index after the design: {all_ixscan}; in-memory SORT stages left: "
            f"{[r['query'] for r in rows if r['in_memory_sort']] or 'none'}")
    rec.data = {"documents": PRODUCT_COUNT, "before": before, "after": after, "summary": rows,
                "indexes": {n: dict(k) for n, k in INDEXES.items()}, "index_sizes_bytes": sizes, "all_indexed": all_ixscan}
    rec.save()


if __name__ == "__main__":
    main()
