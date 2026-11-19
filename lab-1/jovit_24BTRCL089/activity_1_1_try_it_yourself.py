"""Activity 1.1 (Try it yourself) - Do ScyllaDB, InfluxDB and ArangoDB fit one family or several?

Author: Jovit Chawla (24BTRCL089)

  ScyllaDB  - partition key + clustering order (Cassandra data model)  -> ONE family: wide-column
  InfluxDB  - measurement + tags + timestamped fields, downsampling     -> time-series (outside the classic four)
  ArangoDB  - one engine answering key-value, document AND graph queries -> multi-model (spans several)

Each claim is demonstrated on a live MongoDB server. All data is synthetic, generated with a fixed seed.
"""
from __future__ import annotations

import datetime as dt
import random

from pymongo import ASCENDING, DESCENDING

from common import Recorder, connect, explain_aggregate, explain_find, fresh, time_ms

SEED = 2489
START = dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc)


def scylla(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("ScyllaDB -> wide-column: PRIMARY KEY ((channel_id), sent_at DESC)")
    coll = fresh(db, "scylla_messages")
    coll.create_index([("channel_id", ASCENDING), ("sent_at", DESCENDING)])   # partition key, clustering column
    docs = [{"channel_id": f"ch-{rng.randint(1, 200):03d}", "sent_at": START + dt.timedelta(seconds=rng.randint(0, 30 * 86400)),
             "author": f"user{rng.randint(1, 3000)}", "text": "msg"} for _ in range(60000)]
    coll.insert_many(docs)
    partition = rec.plan("latest 20 messages of one partition (ch-042)",
                         explain_find(coll, {"channel_id": "ch-042"}, sort={"sent_at": -1}, limit=20))
    no_key = rec.plan("same question without the partition key (by author)",
                      explain_find(coll, {"author": "user42"}, sort={"sent_at": -1}, limit=20))
    sorted_by_index = "SORT" not in partition["plan"]
    rec.log(f"  partition query needed no SORT stage (clustering order served by the index): {sorted_by_index}")
    rec.data["scylla"] = {"rows": len(docs), "partition_query": partition, "non_partition_query": no_key,
                          "clustering_order_from_index": sorted_by_index}


def influx(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("InfluxDB -> time-series: measurement, tags, fields, timestamp")
    db.drop_collection("influx_cpu_ts")
    ts = db.create_collection("influx_cpu_ts", timeseries={"timeField": "time", "metaField": "tags", "granularity": "minutes"})
    plain = fresh(db, "influx_cpu_plain")
    points = []
    for host in range(20):
        for minute in range(0, 7 * 24 * 60, 2):           # one week, a point every 2 minutes, per host
            points.append({"time": START + dt.timedelta(minutes=minute),
                           "tags": {"host": f"web-{host:02d}", "region": "ap-south-1" if host < 10 else "eu-west-1"},
                           "usage_user": round(rng.uniform(2, 95), 2), "usage_system": round(rng.uniform(1, 30), 2)})
    ts.insert_many([dict(p) for p in points])
    plain.insert_many([dict(p) for p in points])
    db.client.admin.command("fsync")      # checkpoint so storageSize reflects the data just written
    ts_stats = db.command("collStats", "influx_cpu_ts")
    plain_stats = db.command("collStats", "influx_cpu_plain")
    ts_bytes = ts_stats.get("storageSize", 0)
    plain_bytes = plain_stats.get("storageSize", 0)
    buckets = db["system.buckets.influx_cpu_ts"].estimated_document_count() if "system.buckets.influx_cpu_ts" in \
        db.list_collection_names() else ts_stats.get("timeseries", {}).get("bucketCount")
    rec.log(f"  {len(points)} points from 20 hosts | on-disk storage: time-series {ts_bytes // 1024} KiB vs "
            f"plain collection {plain_bytes // 1024} KiB ({round(plain_bytes / max(ts_bytes, 1), 1)}x smaller); "
            f"points packed into {buckets} buckets")
    downsample = [{"$match": {"tags.host": "web-07", "time": {"$gte": START, "$lt": START + dt.timedelta(days=1)}}},
                  {"$group": {"_id": {"$dateTrunc": {"date": "$time", "unit": "hour"}},
                              "mean_user": {"$avg": "$usage_user"}, "max_user": {"$max": "$usage_user"}}},
                  {"$sort": {"_id": 1}}]
    hourly = list(ts.aggregate(downsample))
    rec.plan("1-hour downsample of web-07 for one day", explain_aggregate(ts, downsample))
    rec.log(f"  downsampled 720 raw points into {len(hourly)} hourly rows; first: "
            f"{hourly[0]['_id']:%Y-%m-%d %H:%M} mean={hourly[0]['mean_user']:.2f} max={hourly[0]['max_user']}")
    window = [{"$match": {"tags.host": "web-07"}}, {"$sort": {"time": 1}}, {"$limit": 30},
              {"$setWindowFields": {"sortBy": {"time": 1}, "output": {
                  "moving_avg": {"$avg": "$usage_user", "window": {"documents": [-4, 0]}}}}}]
    moving = list(ts.aggregate(window))
    rec.log(f"  5-point moving average (window function), last value: {moving[-1]['moving_avg']:.2f}")
    rec.data["influx"] = {"points": len(points), "timeseries_storage_bytes": ts_bytes, "plain_storage_bytes": plain_bytes,
                          "hourly_rows": len(hourly)}


def arango(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("ArangoDB -> multi-model: key-value + document + graph in one engine")
    users, follows = fresh(db, "arango_users"), fresh(db, "arango_follows")
    cities = ["Jaipur", "Pune", "Delhi", "Bengaluru", "Kolkata"]
    users.insert_many([{"_id": f"u{i:04d}", "name": f"User {i}", "profile": {"city": rng.choice(cities),
                        "skills": rng.sample(["python", "mongodb", "ml", "cloud", "react"], 2)}} for i in range(1500)])
    users.create_index("profile.city")
    follows.insert_many([{"_from": f"u{i:04d}", "_to": f"u{rng.randrange(1500):04d}"} for i in range(1500) for _ in range(3)])
    follows.create_index("_from")
    kv = rec.plan("KEY-VALUE: get u0089 by key", explain_find(users, {"_id": "u0089"}))
    doc = rec.plan("DOCUMENT: users in Pune who know mongodb",
                   explain_find(users, {"profile.city": "Pune", "profile.skills": "mongodb"}))
    combined = [{"$match": {"_id": "u0089"}},
                {"$graphLookup": {"from": "arango_follows", "startWith": "$_id", "connectFromField": "_to",
                                  "connectToField": "_from", "as": "reach", "maxDepth": 1}},
                {"$lookup": {"from": "arango_users", "localField": "reach._to", "foreignField": "_id", "as": "people"}},
                {"$project": {"same_city": {"$size": {"$filter": {"input": "$people", "cond": {
                    "$eq": ["$$this.profile.city", "Pune"]}}}}, "reached": {"$size": "$people"}}}]
    out = list(users.aggregate(combined))[0]
    ms = time_ms(lambda: list(users.aggregate(combined)))
    rec.log(f"  GRAPH + DOCUMENT in ONE query: u0089 reaches {out['reached']} people within 2 hops, "
            f"{out['same_city']} of them live in Pune (median {ms} ms)")
    rec.data["arango"] = {"kv": kv, "document": doc, "graph_plus_document": {**out, "median_ms": ms}}


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_1_1_try_it_yourself", "Activity 1.1 (Try it yourself) - ScyllaDB, InfluxDB, ArangoDB", client)
    rng = random.Random(SEED)
    scylla(db, rec, rng)
    influx(db, rec, rng)
    arango(db, rec, rng)
    d = rec.data
    verdict = [
        ("ScyllaDB", "Single family: Wide-Column", "partition query examined "
         f"{d['scylla']['partition_query']['keysExamined']} keys, no SORT; without the key: "
         f"{d['scylla']['non_partition_query']['docsExamined']} docs scanned"),
        ("InfluxDB", "Time-series (specialised; not one of the classic four)",
         f"time-series storage {d['influx']['timeseries_storage_bytes'] // 1024} KiB vs "
         f"{d['influx']['plain_storage_bytes'] // 1024} KiB plain; native downsampling"),
        ("ArangoDB", "Multi-model: Key-Value + Document + Graph",
         "the same data answered a key lookup, a nested-document query and a traversal joined to documents"),
    ]
    rec.section("Verdict")
    for product, family, why in verdict:
        rec.log(f"  {product:<9} {family:<55} {why}")
    d["verdict"] = [{"product": p, "classification": f, "evidence": w} for p, f, w in verdict]
    rec.save()


if __name__ == "__main__":
    main()
