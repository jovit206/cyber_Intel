"""Activity 6.2 - Compare index strategies: what indexes cost on writes and buy on reads.

Author: Jovit Chawla (24BTRCL089)

Step 1  Write cost. Insert 10,000 products (insert_many, batches of 500) into a fresh collection for three index
        configurations, 5 runs each, median wall-clock time (time.perf_counter):
          A  only _id
          B  the full Activity 6.1 design (4 secondary indexes, including the text index)
          C  only idx_category_price_rating (the most used index in Activity 6.1 TIY)
Step 2  Read benefit. Run the five workload queries against 50,000 products carrying configuration C only and see
        which queries fall back to a collection scan.
Step 3  Index key order. For Q5 (equality on category, range on rating, sort on price) compare the
        Equality-Sort-Range order {category, price, rating} with {category, rating, price}.
Step 4  A trade-off analysis computed from the measured numbers.
"""
from __future__ import annotations

import statistics
import time

from catalog import INDEXES, Q4_REGEX, QUERIES, products, seed
from common import Recorder, connect, explain_find, fresh, time_ms

BATCH, INSERTS, RUNS = 500, 20_000, 7
CONFIGS = {
    "A: _id only": {},
    "B: full 6.1 design (4 indexes)": INDEXES,
    "C: idx_category_price_rating only": {"idx_category_price_rating": INDEXES["idx_category_price_rating"]},
}


def insert_once(db, docs: list[dict], indexes: dict) -> float:
    coll = fresh(db, "products_write")
    for name, keys in indexes.items():
        coll.create_index(keys, name=name)
    batch = [dict(d) for d in docs]              # fresh copies: insert_many adds _id to the dicts it is given
    started = time.perf_counter()
    for i in range(0, len(batch), BATCH):
        coll.insert_many(batch[i:i + BATCH], ordered=False)
    return (time.perf_counter() - started) * 1000


def insert_benchmark(db, docs: list[dict]) -> dict[str, list[float]]:
    """Interleave the configurations (A, B, C, A, B, C, ...) so background load drifts affect all of them equally;
    the first round is a warm-up and is discarded."""
    samples: dict[str, list[float]] = {label: [] for label in CONFIGS}
    for round_no in range(RUNS + 1):
        for label, indexes in CONFIGS.items():
            ms = insert_once(db, docs, indexes)
            if round_no:
                samples[label].append(ms)
    db.drop_collection("products_write")
    return samples


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_6_2", "Activity 6.2 - Compare Index Strategies", client)
    docs = products(INSERTS, seed=62089)

    rec.section(f"Step 1 - insert {INSERTS:,} documents; {RUNS} interleaved rounds after 1 warm-up round")
    writes = {}
    for label, samples in insert_benchmark(db, docs).items():
        median, best = statistics.median(samples), min(samples)
        spread = 100 * (max(samples) - best) / best
        writes[label] = {"median_ms": round(median, 1), "min_ms": round(best, 1), "spread_pct": round(spread, 1),
                         "runs_ms": [round(s, 1) for s in samples], "docs_per_s": round(INSERTS / median * 1000)}
        rec.log(f"  {label:<36} median {median:8.1f} ms  min {best:8.1f} ms  ({writes[label]['docs_per_s']:,} docs/s)  "
                f"run-to-run spread {spread:.0f}%")
        rec.log(f"  {'':<36} runs {writes[label]['runs_ms']}")
    base_runs = writes["A: _id only"]["runs_ms"]
    base, base_min = writes["A: _id only"]["median_ms"], writes["A: _id only"]["min_ms"]
    for label, w in writes.items():
        w["overhead_pct"] = round(100 * (w["median_ms"] / base - 1), 1)
        w["overhead_min_pct"] = round(100 * (w["min_ms"] / base_min - 1), 1)
        # Paired comparison: each round measured A, B and C seconds apart, so the per-round ratio cancels slow drift.
        ratios = [100 * (r / a - 1) for r, a in zip(w["runs_ms"], base_runs)]
        w["paired_overhead_pct"] = round(statistics.median(ratios), 1)
        w["paired_rounds_slower"] = sum(x > 0 for x in ratios)
    rec.log("  overhead vs _id only - paired per-round median (rounds slower than A) | median | min:")
    for k, v in writes.items():
        if k != "A: _id only":
            rec.log(f"    {k:<36} {v['paired_overhead_pct']:+7.1f}% ({v['paired_rounds_slower']}/{RUNS} rounds) | "
                    f"{v['overhead_pct']:+.1f}% | {v['overhead_min_pct']:+.1f}%")

    rec.section("Step 2 - workload with only idx_category_price_rating")
    perf = seed(db, "products_perf")
    perf.create_index(INDEXES["idx_category_price_rating"], name="idx_category_price_rating")
    reads = {}
    for qid, description, kind, spec in QUERIES:
        if kind == "text":
            summary = explain_find(perf, Q4_REGEX)
            label = f"{qid} (no text index -> regex)"
        else:
            summary = explain_find(perf, spec["filter"], sort=spec.get("sort"), limit=spec.get("limit", 0))
            label = qid
        reads[qid] = rec.plan(label, summary)
    scans = [q for q, s in reads.items() if "COLLSCAN" in s["plan"]]
    rec.log(f"  queries that fall back to COLLSCAN: {scans}")

    rec.section("Step 3 - key order for Q5 (category = Mobiles, rating >= 4.5, sort by price)")
    q5 = dict(QUERIES[4][3])
    perf.create_index([("category", 1), ("rating", 1), ("price", 1)], name="idx_category_rating_price")
    order = {}
    for name in ("idx_category_price_rating", "idx_category_rating_price"):
        summary = explain_find(perf, q5["filter"], sort=q5["sort"], limit=q5["limit"], hint=name)
        ms = time_ms(lambda n=name: list(perf.find(q5["filter"]).sort("price", 1).limit(20).hint(n)), repeat=30)
        summary["median_ms"] = ms
        summary["in_memory_sort"] = "SORT" in summary["plan"].split(" <- ")
        order[name] = rec.plan(f"{name} (median {ms} ms)", summary)
    perf.drop_index("idx_category_rating_price")
    esr, ers = order["idx_category_price_rating"], order["idx_category_rating_price"]
    rec.log(f"  E-S-R order: in-memory sort = {esr['in_memory_sort']}, keys examined {esr['keysExamined']}; "
            f"E-R-S order: in-memory sort = {ers['in_memory_sort']}, keys examined {ers['keysExamined']}")

    rec.section("Step 4 - trade-off analysis (from the numbers above)")
    full, one = writes["B: full 6.1 design (4 indexes)"], writes["C: idx_category_price_rating only"]
    def describe(w: dict) -> str:
        text = (f"{w['paired_overhead_pct']:+.1f}% (median of per-round paired ratios; slower than _id only in "
                f"{w['paired_rounds_slower']} of {RUNS} rounds)")
        if 0 < w["paired_rounds_slower"] < RUNS:
            text += " - not consistent in every round, so this figure is affected by the laptop's background load"
        return text
    analysis = [
        f"Every index is updated on every insert: the full design changed insert time by {describe(full)} compared "
        f"with _id only; a single compound index by {describe(one)}.",
        f"Keeping only the most-used index leaves {len(scans)} of 5 workload queries ({', '.join(scans)}) scanning all "
        f"{perf.estimated_document_count():,} documents, so read-heavy catalogue traffic still needs the full design.",
        "For write-heavy collections (logs, events, telemetry) the extra write cost is not worth it for rarely-run "
        "queries; index only what the workload runs often - which $indexStats shows (Activity 6.1 TIY).",
        f"Key order matters as much as the key set: for Q5 the E-S-R order {'avoided' if not esr['in_memory_sort'] else 'needed'} "
        f"an in-memory sort, while E-R-S {'needed one' if ers['in_memory_sort'] else 'did not need one'} "
        f"(medians {esr['median_ms']} ms vs {ers['median_ms']} ms).",
    ]
    for line in analysis:
        rec.log(f"  - {line}")
    rec.data = {"inserts": INSERTS, "batch": BATCH, "runs": RUNS, "writes": writes, "reads_one_index": reads,
                "collscan_with_one_index": scans, "key_order_q5": order, "analysis": analysis}
    rec.save()


if __name__ == "__main__":
    main()
