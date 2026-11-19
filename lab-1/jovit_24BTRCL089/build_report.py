"""Build REPORT.md for Lab 1 from the saved results/ files and the source code in this folder.

Author: Jovit Chawla (24BTRCL089). Every number in the generated report is read from results/*.json, which the
activity scripts write after running against MongoDB; this script never invents a value. Re-run it after the
activities (run_all.ps1 does both).
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ACTIVITIES = [
    ("activity_1_1", "activity_1_1_classify_products.py", "Activity 1.1 - Classify Database Products"),
    ("activity_1_1_try_it_yourself", "activity_1_1_try_it_yourself.py", "Activity 1.1 (Try it yourself) - ScyllaDB, InfluxDB, ArangoDB"),
    ("activity_1_2", "activity_1_2_map_application.py", "Activity 1.2 - Map a Real-World Application"),
    ("activity_1_2_try_it_yourself", "activity_1_2_try_it_yourself.py", "Activity 1.2 (Try it yourself) - Food-Delivery Platform"),
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


def plan_row(label: str, p: dict) -> list:
    return [label, f"`{p['plan']}`", p["nReturned"], p["keysExamined"], p["docsExamined"], p["executionTimeMillis"]]


PLAN_HEADERS = ["Query", "Winning plan", "Returned", "Keys examined", "Docs examined", "Server ms"]


def results_1_1(m: dict) -> str:
    rows = [plan_row("S3: GET by key", m["s3"]["get_by_key"]), plan_row("S3: LIST by prefix", m["s3"]["prefix_list"]),
            plan_row("Memcached: cache GET", m["memcached"]["get_plan"]),
            plan_row("Memcached: recompute", m["memcached"]["recompute_plan"]),
            plan_row("Couchbase: doc by key", m["couchbase"]["by_key"]),
            plan_row("Couchbase: nested array query", m["couchbase"]["nested_query"]),
            plan_row("HBase: row-key range", m["hbase"]["rowkey_range"]),
            plan_row("HBase: non-key column filter", m["hbase"]["non_key_filter"])]
    n = m["neptune"]
    extra = (f"\n\n- S3 content search had to fetch **{m['s3']['content_search']['objects_fetched']}** objects to the client "
             f"({m['s3']['content_search']['ms']} ms) to find {m['s3']['content_search']['hits']} match.\n"
             f"- Memcached-style cache: miss (aggregate + set) **{m['memcached']['miss_ms']} ms**, hit "
             f"**{m['memcached']['hit_ms_median']} ms** (median of 20).\n"
             f"- Neptune-style traversal: `$graphLookup` reached **{n['reached']}** people within 3 hops in one query "
             f"({n['graphlookup_ms']} ms median); the same walk from the application needed {n['app_round_trips']} round "
             f"trips ({n['app_bfs_ms']} ms median on localhost).\n\n")
    verdict = table(["Product", "NoSQL family", "Evidence (measured)"],
                    [[c["product"], c["family"], c["evidence"]] for c in m["classification"]])
    return table(PLAN_HEADERS, rows) + extra + verdict


def results_1_1_tiy(m: dict) -> str:
    s, i = m["scylla"], m["influx"]
    rows = [plan_row("ScyllaDB: one partition, latest 20", s["partition_query"]),
            plan_row("ScyllaDB: no partition key", s["non_partition_query"]),
            plan_row("ArangoDB: key-value get", m["arango"]["kv"]), plan_row("ArangoDB: document query", m["arango"]["document"])]
    g = m["arango"]["graph_plus_document"]
    extra = (f"\n\n- InfluxDB-style time-series: {i['points']:,} points took **{i['timeseries_storage_bytes'] // 1024} KiB** "
             f"in a time-series collection vs **{i['plain_storage_bytes'] // 1024} KiB** as plain documents; one day "
             f"downsampled to {i['hourly_rows']} hourly rows.\n"
             f"- ArangoDB-style combined query: a 2-hop traversal joined to profile documents reached {g['reached']} "
             f"people, {g['same_city']} in Pune ({g['median_ms']} ms median).\n\n")
    verdict = table(["Product", "Classification", "Evidence (measured)"],
                    [[v["product"], v["classification"], v["evidence"]] for v in m["verdict"]])
    return table(PLAN_HEADERS, rows) + extra + verdict


def results_1_2(m: dict) -> str:
    rows = [plan_row("Nearest drivers ($geoNear)", m["driver_positions"]["geo_plan"]),
            plan_row("Rider profile by id", m["profiles"]["rider_by_id"]),
            plan_row("Login by phone", m["profiles"]["by_phone"]),
            plan_row("Trip history, no index", m["trip_history"]["without_index"]),
            plan_row("Trip history, compound index", m["trip_history"]["with_index"]),
            plan_row("Posts in one hour partition", m["social"]["partition_read"]),
            plan_row("Posts by a user (not partition key)", m["social"]["non_partition_read"])]
    f, so = m["fraud_graph"], m["social"]
    extra = (f"\n\n- Driver pings: 4000 key upserts in **{m['driver_positions']['burst_write_ms']} ms**; nearest drivers: "
             + ", ".join(f"{d} ({metres} m)" for d, metres in m["driver_positions"]["nearest"]) + ".\n"
             f"- Fraud graph: traversal from R00089 found {f['ring_found']} (planted ring: {f['ring_planted']}) in "
             f"{f['ms']} ms; an ordinary account reached only {f['control_reach']} nodes.\n"
             f"- Counters: {so['increments']:,} `$inc` upserts in {so['increments_ms']} ms; one increment "
             f"{so['single_inc_ms']} ms median. Trending in the measured hour: " + ", ".join(f"{t.split(':')[0]} = {n}" for t, n in so["trending"]) + ".\n"
             "- Note: `explain` reports the `$geoNear` stage before `$limit`, so its *returned* count is the number of "
             "candidates the geo stage produced, not the 5 drivers the query returned.\n\n")
    mapping = table(["Data domain", "NoSQL family", "Evidence (measured)"],
                    [[r["domain"], r["family"], r["evidence"]] for r in m["mapping"]])
    return table(PLAN_HEADERS, rows) + extra + mapping


def results_1_2_tiy(m: dict) -> str:
    rows = [plan_row("Restaurant page by id", m["menus"]["by_id"]), plan_row("Dish under price (nested)", m["menus"]["nested_query"]),
            plan_row("Order status by id", m["order_state"]["get"]), plan_row("Point-in-zone", m["zones"]["plan"]),
            plan_row("Full-text search", m["search"]["text_plan"]), plan_row("Regex search", m["search"]["regex_plan"])]
    busiest = ", ".join(f"{b['_id']:02d}:00 UTC ({b['orders']} orders)" for b in m["analytics"]["busiest_hours"])
    extra = (f"\n\n- Order status: read {m['order_state']['poll_ms']} ms, write {m['order_state']['update_ms']} ms (median of 50).\n"
             f"- Address mapped to **{m['zones']['zone']}**; {m['zones']['restaurants_within_3km']} restaurants within 3 km.\n"
             f"- {m['analytics']['orders']:,} orders; busiest hours: {busiest}.\n"
             "- Ordered with veg biryani: " + ", ".join(f"{d} ({w})" for d, w in m["graph"]["with_veg_biryani"]) + ".\n"
             "- Search ranking for 'paneer masala': " + ", ".join(f"{d} ({s})" for d, s in m["search"]["top"]) + ".\n\n")
    mapping = table(["Data domain", "Family / technology", "Evidence (measured)"],
                    [[r["domain"], r["family"], r["evidence"]] for r in m["mapping"]])
    return table(PLAN_HEADERS, rows) + extra + mapping


RESULT_BUILDERS = {"activity_1_1": results_1_1, "activity_1_1_try_it_yourself": results_1_1_tiy,
                   "activity_1_2": results_1_2, "activity_1_2_try_it_yourself": results_1_2_tiy}

HEADER = """# Lab 1 - Introduction to NoSQL: Families, Products and Data-Domain Mapping

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 1 activities) |
| **Executed on** | {run_at} on `{machine}`, {os} |
| **Software** | MongoDB {mongo} ({topology}), Python {python}, pymongo {pymongo} |

> **Own work.** All code, datasets and measurements in this folder (`lab-1/jovit_24BTRCL089/`) are my own and were
> produced by running the scripts below on my machine. The files in the parent `lab-1/` folder are a reference
> submission by another student; they were read only to understand what the activities ask for, and nothing from
> them (code, data, results or credentials) is reused here.

## 1. Experiment title

Classifying NoSQL database products into families and mapping application data domains to the right NoSQL model,
with every claim demonstrated on a live MongoDB server.

## 2. Aim

To understand the four classic NoSQL families (key-value, document, wide-column, graph) and the newer specialised
and multi-model systems, by reproducing each family's characteristic access pattern in MongoDB and measuring it.

## 3. Objectives

1. Classify Amazon S3, Memcached, Couchbase, Apache HBase and Amazon Neptune into NoSQL families (Activity 1.1).
2. Decide whether ScyllaDB, InfluxDB and ArangoDB fit one family or span several (Activity 1.1, Try it yourself).
3. Map the data domains of a ride-sharing app and a social-media analytics platform to families (Activity 1.2).
4. Repeat the mapping for an application of my own choice - a food-delivery platform (Activity 1.2, Try it yourself).
5. Justify every answer with measured query plans (`explain("executionStats")`) and timings, not assertions.

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | {os} |
| Machine | `{machine}` (laptop) |
| Database server | MongoDB Community {mongo}, {topology}, `mongodb://localhost:27017` |
| Language / driver | Python {python}, pymongo {pymongo} (`pip install -r requirements.txt`) |
| Database used | `jovit_lab01` (only this lab's own collections are dropped and recreated) |
| Optional | MongoDB Compass to browse the collections |

## 5. Theory

**Why NoSQL.** Relational databases store data in fixed-schema tables and scale mainly vertically. NoSQL systems
relax one or more of these constraints - schema, joins, strong consistency - to gain horizontal scale, flexible data
shapes or very low latency for a specific access pattern. They are usually grouped into four families:

- **Key-value stores** (Redis, Memcached, DynamoDB, Amazon S3 as an object store) map an opaque key to a value.
  Reads and writes by key are O(1)-like and extremely fast, but the store cannot look *inside* the value, so any
  query on content requires reading every value. Memcached keeps values only in memory with an expiry (TTL), which
  makes it a cache rather than a system of record.
- **Document stores** (MongoDB, Couchbase) store self-describing JSON/BSON documents. Related data is embedded
  (arrays, sub-documents), so one read returns a whole entity; documents in one collection may have different
  fields; secondary and multikey indexes allow queries on nested fields.
- **Wide-column (column-family) stores** (HBase, Cassandra, ScyllaDB) organise data by a row/partition key kept in
  sorted order, with columns grouped into families that can be sparse. Range scans on the key are cheap; queries on
  non-key columns are not supported efficiently, so tables are designed around the query.
- **Graph databases** (Neo4j, Amazon Neptune) store nodes and edges and traverse relationships directly
  ("index-free adjacency"), which makes multi-hop questions (friends-of-friends, fraud rings, recommendations)
  natural to express.

Beyond the four: **time-series databases** (InfluxDB, TimescaleDB, MongoDB time-series collections) are optimised
for append-only timestamped measurements - columnar compression, time bucketing and downsampling. **Multi-model**
databases (ArangoDB, Cosmos DB) expose several models over one engine. **Search engines** (Elasticsearch, Atlas
Search) rank results by relevance, which none of the four families do.

**How this lab demonstrates the families with one database.** MongoDB can imitate each access pattern: `_id`
lookups behave like a key-value store, nested documents and multikey indexes like a document store, a sorted
string row key with sparse sub-documents like a wide-column table, an edge collection with `$graphLookup` like a
graph, and time-series collections like a TSDB. Comparing the query plans - `EXPRESS_IXSCAN` (direct key lookup),
`IXSCAN` (index range), `COLLSCAN` (full scan) - and the keys/documents examined shows *which access pattern each
data shape makes cheap*, and that is exactly what decides which family a product belongs to.

## 6. Procedure

1. Start the MongoDB service (`Get-Service MongoDB` shows *Running*) or set `MONGO_URI` in a git-ignored `.env`
   created from `.env.example`.
2. Install the driver: `python -m pip install -r requirements.txt`.
3. Run each activity from this folder (or all at once with `run_all.ps1`):
   `python activity_1_1_classify_products.py`, `python activity_1_1_try_it_yourself.py`,
   `python activity_1_2_map_application.py`, `python activity_1_2_try_it_yourself.py`.
4. Each script generates its synthetic dataset (fixed random seed), creates the indexes, runs the queries, calls
   `explain` with `executionStats`, prints the measurements and saves them to `results/<activity>.json` and
   `results/<activity>.output.txt`.
5. Run `python build_report.py` to regenerate this report from the saved results.
6. Optionally open the `jovit_lab01` database in MongoDB Compass to inspect the collections.
"""

FOOTER = """
## 9. Results

{results}

## 10. Conclusion

{conclusion}
"""


def graph_sentence(n: dict) -> str:
    server, app = n["graphlookup_ms"], n["app_bfs_ms"]
    if server < app * 0.8:
        comparison = f"faster than the hop-by-hop application loop ({server} ms vs {app} ms)"
    elif server > app * 1.25:
        comparison = (f"slower than the hop-by-hop application loop on this small local graph ({server} ms vs {app} "
                      "ms), so the advantage measured here is one declarative query instead of several round trips")
    else:
        comparison = f"about as fast as the hop-by-hop application loop on this small local graph ({server} ms vs {app} ms)"
    return (f"- **Graph (Neptune):** a 3-hop relationship question was one server-side traversal reaching {n['reached']} "
            f"people, {comparison}; the application loop needed {n['app_round_trips']} round trips, a cost that grows "
            "with graph depth and network latency.")


def conclusion(data: dict[str, dict]) -> str:
    a = data["activity_1_1"]["measurements"]
    b = data["activity_1_1_try_it_yourself"]["measurements"]
    c = data["activity_1_2"]["measurements"]
    e = data["activity_1_2_try_it_yourself"]["measurements"]
    return "\n".join([
        f"- **Key-value (S3, Memcached):** a key lookup examined {a['s3']['get_by_key']['docsExamined']} document, while "
        f"searching object contents meant fetching all {a['s3']['content_search']['objects_fetched']} objects. A cached "
        f"result was served in {a['memcached']['hit_ms_median']} ms instead of {a['memcached']['miss_ms']} ms.",
        f"- **Document (Couchbase):** {a['couchbase']['distinct_shapes']} different document shapes lived in one "
        "collection and nested-array queries were answered from a multikey index.",
        f"- **Wide-column (HBase, ScyllaDB):** key-range reads examined only the rows returned "
        f"({a['hbase']['rowkey_range']['keysExamined']} keys for {a['hbase']['rowkey_range']['nReturned']} rows), while "
        f"filters on non-key columns scanned the whole table ({a['hbase']['non_key_filter']['docsExamined']} rows) - "
        "so ScyllaDB fits the single wide-column family.",
        graph_sentence(a["neptune"]),
        f"- **Beyond the four:** a time-series layout stored the same metrics in "
        f"{b['influx']['timeseries_storage_bytes'] // 1024} KiB instead of {b['influx']['plain_storage_bytes'] // 1024} "
        "KiB (InfluxDB is a time-series system); ArangoDB-style multi-model queries combined graph and document access "
        "in one query; relevance-ranked dish search needed a text index, i.e. a search engine.",
        f"- **Application mapping:** for ride-sharing, live positions suit a key-value/geo cache, profiles a document "
        f"store, trip history a document store (wide-column at very large scale - the compound index cut "
        f"{c['trip_history']['without_index']['docsExamined']} scanned documents to "
        f"{c['trip_history']['with_index']['docsExamined']}), and fraud detection a graph (the planted ring was found "
        f"exactly: {c['fraud_graph']['ring_found'] == c['fraud_graph']['ring_planted']}). The food-delivery platform "
        f"needed six technologies for six domains ({', '.join(r['family'].split(' (')[0] for r in e['mapping'])}), "
        "which shows why real systems are usually polyglot.",
    ])


def main() -> None:
    data = {name: load(name) for name, _, _ in ACTIVITIES}
    env = data["activity_1_1"]["environment"]
    parts = [HEADER.format(run_at=env["run_at_utc"], machine=env["machine"], os=env["os"], mongo=env["mongodb_server"],
                           topology=env["topology"], python=env["python"], pymongo=env["pymongo"])]
    parts.append("\n## 7. Source code\n\nThe complete source of every file used in this lab.\n")
    for file in ["common.py"] + [script for _, script, _ in ACTIVITIES] + ["build_report.py"]:
        parts.append(f"\n### `{file}`\n\n```python\n{(HERE / file).read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 8. Execution output\n\nConsole output captured from the actual runs (`results/*.output.txt`).\n")
    for name, _, title in ACTIVITIES:
        output = (RESULTS / f"{name}.output.txt").read_text(encoding="utf-8").rstrip()
        parts.append(f"\n### {title}\n\n```text\n{output}\n```\n")
    results = "\n\n".join(f"### {title}\n\n{RESULT_BUILDERS[name](data[name]['measurements'])}"
                          for name, _, title in ACTIVITIES)
    parts.append(FOOTER.format(results=results, conclusion=conclusion(data)))
    (HERE / "REPORT.md").write_text("".join(parts), encoding="utf-8")
    print(f"Wrote REPORT.md ({sum(len(p) for p in parts):,} characters)")


if __name__ == "__main__":
    main()
