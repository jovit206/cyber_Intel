"""Build REPORT.md for Lab 6 from the saved results/ files and the source code in this folder.

Author: Jovit Chawla (24BTRCL089). Every value is read from results/*.json written by the activity scripts;
nothing is invented. Run after the activities (run_all.ps1 does both).
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ACTIVITIES = [
    ("activity_6_1", "activity_6_1_index_design.py", "Activity 6.1 - Index Design for a Query Workload"),
    ("activity_6_1_try_it_yourself", "activity_6_1_try_it_yourself.py", "Activity 6.1 (Try it yourself) - Index Usage with $indexStats"),
    ("activity_6_2", "activity_6_2_index_strategies.py", "Activity 6.2 - Compare Index Strategies"),
    ("activity_6_2_try_it_yourself", "activity_6_2_try_it_yourself.py", "Activity 6.2 (Try it yourself) - Covered Queries"),
]


def load(name: str) -> dict:
    path = RESULTS / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path.name} is missing - run the activity scripts first (run_all.ps1).")
    return json.loads(path.read_text(encoding="utf-8"))


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(out)


def results_6_1(m: dict) -> str:
    rows = [[r["query"], f"`{r['before_plan']}`", f"`{r['after_plan']}`", f"{r['docs_before']:,}", f"{r['docs_after']:,}",
             r["keys_after"], f"{r['reduction_pct']}%"] for r in m["summary"]]
    sizes = ", ".join(f"`{n}` {s // 1024} KiB" for n, s in m["index_sizes_bytes"].items())
    return (table(["Query", "Plan before", "Plan after", "Docs examined before", "Docs examined after", "Keys examined after",
                   "Reduction"], rows) +
            f"\n\nIndex sizes: {sizes}. Every query uses an index afterwards: **{m['all_indexed']}**.\n\n"
            "Note: before the text index exists, Q4 is measured with a case-insensitive regex on `name` (a `$text` query "
            "cannot run without a text index). The text index also searches `description` and stems words, so it "
            "returns more matches than the regex did.")


def results_6_1_tiy(m: dict) -> str:
    total = sum(m["ops"].values()) or 1
    rows = [[f"`{n}`", ops, f"{ops / total:.1%}"] for n, ops in sorted(m["ops"].items(), key=lambda kv: -kv[1])]
    return (table(["Index", "accesses.ops in this workload", "Share"], rows) +
            f"\n\nWorkload: {m['queries']}. Most used: **{m['most_used']}**. Never used: **{', '.join(m['unused'])}**. "
            f"Redundant prefix of a compound index: **{', '.join(m['redundant']) or 'none'}**.")


def results_6_2(m: dict) -> str:
    rows = [[k, f"{v['median_ms']:.1f}", f"{v['min_ms']:.1f}", f"{v['docs_per_s']:,}",
             "-" if k.startswith("A") else f"{v['paired_overhead_pct']:+.1f}% ({v['paired_rounds_slower']}/{m['runs']})",
             f"{v['spread_pct']:.0f}%", v["runs_ms"]] for k, v in m["writes"].items()]
    reads = [[q, f"`{s['plan']}`", f"{s['docsExamined']:,}"] for q, s in m["reads_one_index"].items()]
    order = [[f"`{n}`", f"`{s['plan']}`", s["keysExamined"], s["in_memory_sort"], s["median_ms"]]
             for n, s in m["key_order_q5"].items()]
    return (f"**Write cost** ({m['inserts']:,} inserts in batches of {m['batch']}; {m['runs']} interleaved rounds after a "
            "warm-up round). The paired overhead compares each configuration with `_id` only *within the same round* "
            "and takes the median; the count shows in how many rounds it was slower. The laptop was under memory "
            "pressure, hence the large run-to-run spread:\n\n" +
            table(["Configuration", "Median ms", "Min ms", "Docs/s", "Paired overhead vs _id only", "Spread", "Runs (ms)"],
                  rows) +
            "\n\n**Reads with only `idx_category_price_rating`:**\n\n" + table(["Query", "Plan", "Docs examined"], reads) +
            "\n\n**Key order for Q5:**\n\n" + table(["Index", "Plan", "Keys examined", "In-memory sort", "Median ms"], order) +
            "\n\n**Trade-off analysis:**\n\n" + "\n".join(f"- {line}" for line in m["analysis"]))


def results_6_2_tiy(m: dict) -> str:
    rows = [[k, f"`{s['plan']}`", s["keysExamined"], s["docsExamined"], s["executionTimeMillis"], f"{s['median_ms']:.3f}",
             "yes" if s["covered"] else "no"] for k, s in m["projections"].items()]
    mk = m["multikey"]
    return (table(["Projection", "Plan", "Keys examined", "Docs examined", "Server ms", f"Client median ms ({m['runs']} runs)",
                   "Covered"], rows) +
            f"\n\nMultikey index on `tags`: `{mk['plan']}`, {mk['docsExamined']:,} documents examined - not coverable.\n\n"
            "Client-side medians include the network round trip and BSON decoding, so they vary between runs; the "
            "server-side columns (documents examined, server ms) are the reliable measure of the covered plan.")


BUILDERS = {"activity_6_1": results_6_1, "activity_6_1_try_it_yourself": results_6_1_tiy,
            "activity_6_2": results_6_2, "activity_6_2_try_it_yourself": results_6_2_tiy}

HEADER = """# Lab 6 - Indexing and Query Performance in MongoDB

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 6 activities) |
| **Executed on** | {run_at} on `{machine}`, {os} |
| **Software** | MongoDB {mongo} ({topology}), Python {python}, pymongo {pymongo} |

> **Own work.** All code, data and measurements in `lab-6/jovit_24BTRCL089/` are my own and were produced by
> running the scripts below on my machine. `lab-6/Chapter06_Submission/` is a reference submission by another
> student, read only to understand the activities; none of its code, data or results are used.

## 1. Experiment title

Designing, measuring and tuning MongoDB indexes for a product-catalogue query workload.

## 2. Aim

To learn how indexes change query execution in MongoDB, how to design an index set for a workload, how to find
unused indexes, what indexes cost on writes, and how covered queries avoid reading documents.

## 3. Objectives

1. Design indexes for a five-query workload and compare `explain("executionStats")` before and after (6.1).
2. Measure real index usage with `$indexStats` and identify unused and redundant indexes (6.1 Try it yourself).
3. Measure the write cost of different index configurations, the read impact of removing indexes, and the effect
   of compound-index key order (6.2).
4. Build covered queries and show when a projection can be answered from the index alone (6.2 Try it yourself).

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | {os} |
| Machine | `{machine}` (laptop) |
| Database server | MongoDB {mongo} ({topology}) at `mongodb://localhost:27017` (Windows service) |
| Language / driver | Python {python}, pymongo {pymongo} (`pip install -r requirements.txt`) |
| Database used | `jovit_lab06` (collections `products`, `products_perf`, temporary `products_write`) |
| Data | 50,000 synthetic products generated with a fixed seed (`catalog.py`) |

## 5. Theory

**What an index is.** MongoDB indexes are B-tree structures holding the values of one or more fields in sorted
order, each pointing to a document. Without a suitable index the server must read every document (**COLLSCAN**);
with one it walks a range of index keys (**IXSCAN**) and fetches only matching documents (**FETCH**). `explain()`
with `executionStats` reports the winning plan, `totalKeysExamined`, `totalDocsExamined` and `nReturned`; a good
plan keeps keys and documents examined close to the number returned.

**Index types used here.** *Single-field* (`{{price: 1}}`) for equality and range queries; *compound*
(`{{category: 1, price: 1, rating: 1}}`) for several fields, usable by any query on a key prefix; *multikey* -
created automatically when the field is an array (`tags`), one key per element; *text* indexes for `$text` search
with stemming and relevance scores.

**ESR rule.** In a compound index put fields used with **E**quality first, then fields used for **S**ort, then
fields used in **R**ange filters. Then the index already returns documents in sort order (no in-memory SORT
stage) and the scan can stop as soon as the limit is reached.

**Cost of indexes.** Every insert, update or delete must also update every index, so each index slows writes and
uses RAM (indexes should fit in the WiredTiger cache). `$indexStats` reports how often each index is used
(`accesses.ops`, counted since the index was created or the server restarted), which shows which indexes are
worth keeping. An index that is a prefix of a compound index is usually redundant.

**Covered queries.** If every filtered, sorted and projected field is in one index - and `_id` is excluded unless
it is in the index - MongoDB answers from the index alone (`PROJECTION_COVERED`, `totalDocsExamined: 0`).
Multikey indexes cannot cover queries that return the array field.

## 6. Procedure

1. Make sure the MongoDB service is running (`Get-Service MongoDB`), or set `MONGO_URI` in a `.env` copied from
   `.env.example`.
2. Install the driver: `python -m pip install -r requirements.txt`.
3. Run the activities **in order** (each builds on the previous one), or use `run_all.ps1`:
   `python activity_6_1_index_design.py` -> `python activity_6_1_try_it_yourself.py` ->
   `python activity_6_2_index_strategies.py` -> `python activity_6_2_try_it_yourself.py`.
4. Each script prints its measurements and saves them in `results/` (JSON + console output).
5. Optionally inspect `jovit_lab06.products` in MongoDB Compass (Indexes and Explain Plan tabs).
6. Run `python build_report.py` to regenerate this report.
"""


def main() -> None:
    data = {name: load(name) for name, _, _ in ACTIVITIES}
    env = data["activity_6_1"]["environment"]
    parts = [HEADER.format(run_at=env["run_at_utc"], machine=env["machine"], os=env["os"], mongo=env["mongodb_server"],
                           topology=env["topology"], python=env["python"], pymongo=env["pymongo"])]
    parts.append("\n## 7. Source code\n\nThe complete source of every file used in this lab.\n")
    for file in ["common.py", "catalog.py"] + [s for _, s, _ in ACTIVITIES] + ["build_report.py"]:
        parts.append(f"\n### `{file}`\n\n```python\n{(HERE / file).read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 8. Execution output\n\nConsole output captured from the actual runs (`results/*.output.txt`).\n")
    for name, _, title in ACTIVITIES:
        parts.append(f"\n### {title}\n\n```text\n{(RESULTS / f'{name}.output.txt').read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 9. Results\n\n" + "\n\n".join(f"### {title}\n\n{BUILDERS[name](data[name]['measurements'])}"
                                                    for name, _, title in ACTIVITIES))
    a, b, c, d = (data[n]["measurements"] for n, _, _ in ACTIVITIES)
    reductions = [r["reduction_pct"] for r in a["summary"]]
    writes = c["writes"]
    p3 = d["projections"]["P3 index fields, _id excluded"]
    conclusion = [
        f"- Four indexes turned all five workload queries from collection scans into index scans, cutting documents "
        f"examined by {min(reductions)}-{max(reductions)}%; one ESR compound index served two queries (Q1 and Q5).",
        f"- `$indexStats` showed `{b['most_used']}` doing most of the work and exposed "
        f"{', '.join(f'`{u}`' for u in b['unused'])} as unused - `{', '.join(b['redundant'])}` because it duplicates the "
        "prefix of a compound index.",
        f"- Indexes are not free: with the full design inserts were "
        f"{writes['B: full 6.1 design (4 indexes)']['paired_overhead_pct']:+.1f}% slower than with `_id` only (slower in "
        f"{writes['B: full 6.1 design (4 indexes)']['paired_rounds_slower']}/{c['runs']} rounds); one compound index cost "
        f"{writes['C: idx_category_price_rating only']['paired_overhead_pct']:+.1f}% (slower in "
        f"{writes['C: idx_category_price_rating only']['paired_rounds_slower']}/{c['runs']} rounds, so that smaller "
        f"figure is less certain on this busy laptop). Dropping indexes sent {', '.join(c['collscan_with_one_index'])} "
        "back to collection scans, so the index set should follow the measured workload.",
        f"- Key order matters: the E-S-R index answered Q5 without an in-memory sort and examined "
        f"{c['key_order_q5']['idx_category_price_rating']['keysExamined']} keys instead of "
        f"{c['key_order_q5']['idx_category_rating_price']['keysExamined']}.",
        f"- Excluding `_id` from a projection of indexed fields produced a covered query "
        f"(`{p3['plan']}`, {p3['docsExamined']} documents examined); adding a non-index field, or returning a multikey "
        "array field, needs document fetches again.",
    ]
    parts.append("\n\n## 10. Conclusion\n\n" + "\n".join(conclusion) + "\n")
    (HERE / "REPORT.md").write_text("".join(parts), encoding="utf-8")
    print(f"Wrote REPORT.md ({sum(len(p) for p in parts):,} characters)")


if __name__ == "__main__":
    main()
