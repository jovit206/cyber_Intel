"""Activity 1.2 - Map a real-world application's data domains to NoSQL families.

Author: Jovit Chawla (24BTRCL089)

Part A - ride-sharing app (city: Pune), four data domains:
  1. live driver positions   -> Key-Value / geo cache (Redis GEO)      2dsphere + $geoNear, TTL on stale pings
  2. rider & driver profiles -> Document (MongoDB)                       heterogeneous docs, one read, no joins
  3. trip history            -> Document at app scale / Wide-Column at   compound index vs. collection scan
                                 very large scale (Cassandra)
  4. fraud relationships     -> Graph (Neo4j / Neptune)                  $graphLookup over shared devices/cards

Part B - social-media analytics platform, two layers:
  raw post stream            -> Wide-Column (Cassandra, time-bucketed partitions)
  real-time counters         -> Key-Value (Redis INCR)

All data is synthetic and generated with a fixed seed; the fraud ring is planted on purpose so the
traversal has something real to find.
"""
from __future__ import annotations

import datetime as dt
import random
import time

from pymongo import ASCENDING, DESCENDING, GEOSPHERE, UpdateOne

from common import Recorder, connect, explain_aggregate, explain_find, fresh, time_ms

SEED = 1289
NOW = dt.datetime(2026, 5, 10, 18, 0, tzinfo=dt.timezone.utc)
PUNE = (73.8567, 18.5204)            # lon, lat
DRIVERS, RIDERS, TRIPS = 800, 3000, 25000


def point(rng: random.Random, spread: float = 0.08) -> dict:
    return {"type": "Point", "coordinates": [round(PUNE[0] + rng.uniform(-spread, spread), 6),
                                             round(PUNE[1] + rng.uniform(-spread, spread), 6)]}


def driver_positions(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("1. Live driver positions -> Key-Value / geospatial cache")
    coll = fresh(db, "ride_driver_positions")
    coll.create_index([("loc", GEOSPHERE)])
    coll.create_index("pinged_at", expireAfterSeconds=120)       # a driver who stops pinging disappears
    coll.insert_many([{"_id": f"D{i:04d}", "loc": point(rng), "available": rng.random() < 0.6, "pinged_at": NOW}
                      for i in range(DRIVERS)])
    ops = [UpdateOne({"_id": f"D{rng.randrange(DRIVERS):04d}"}, {"$set": {"loc": point(rng), "pinged_at": NOW}})
           for _ in range(4000)]
    started = time.perf_counter()
    coll.bulk_write(ops, ordered=False)
    burst_ms = (time.perf_counter() - started) * 1000
    rec.log(f"  4000 position upserts by key in one bulk write: {burst_ms:.1f} ms ({4000 / burst_ms * 1000:,.0f} writes/s)")
    pickup = {"type": "Point", "coordinates": [73.8478, 18.5314]}   # Shivajinagar
    near = [{"$geoNear": {"near": pickup, "distanceField": "metres", "maxDistance": 3000, "query": {"available": True},
                          "spherical": True}}, {"$limit": 5}]
    found = list(coll.aggregate(near))
    summary = rec.plan("5 nearest available drivers within 3 km", explain_aggregate(coll, near))
    rec.log("  nearest: " + ", ".join(f"{d['_id']} {d['metres']:.0f} m" for d in found))
    rec.data["driver_positions"] = {"burst_write_ms": round(burst_ms, 2), "nearest": [(d["_id"], round(d["metres"]))
                                    for d in found], "geo_plan": summary, "ttl_seconds": 120}


def profiles(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("2. Rider and driver profiles -> Document")
    coll = fresh(db, "ride_profiles")
    docs = [{"_id": f"R{i:05d}", "kind": "rider", "name": f"Rider {i}", "phone": f"+91-90{i:08d}",
             "saved_places": [{"label": "home", "loc": point(rng)}, {"label": "work", "loc": point(rng)}],
             "payment": {"type": rng.choice(["UPI", "card", "cash"])}} for i in range(RIDERS)]
    docs += [{"_id": f"D{i:04d}", "kind": "driver", "name": f"Driver {i}", "phone": f"+91-80{i:08d}",
              "vehicle": {"model": rng.choice(["WagonR", "Dzire", "Nexon EV"]), "plate": f"MH12-{i:04d}"},
              "documents": [{"type": t, "verified": rng.random() < 0.95} for t in ("licence", "RC", "insurance")],
              "rating": round(rng.uniform(3.8, 5.0), 2)} for i in range(DRIVERS)]
    coll.insert_many(docs)
    coll.create_index("phone", unique=True)
    rider = rec.plan("whole rider profile by id (places + payment in one doc)", explain_find(coll, {"_id": "R00089"}))
    driver = rec.plan("whole driver profile by id (vehicle + documents)", explain_find(coll, {"_id": "D0089"}))
    phone = rec.plan("login lookup by phone (unique secondary index)", explain_find(coll, {"phone": "+91-9000000089"}))
    rec.data["profiles"] = {"documents": len(docs), "rider_by_id": rider, "driver_by_id": driver, "by_phone": phone}


def trip_history(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("3. Trip history -> Document (time-ordered per rider; wide-column at very large scale)")
    coll = fresh(db, "ride_trips")
    coll.insert_many([{"rider": f"R{rng.randrange(RIDERS):05d}", "driver": f"D{rng.randrange(DRIVERS):04d}",
                       "requested_at": NOW - dt.timedelta(minutes=rng.randint(0, 90 * 24 * 60)),
                       "fare_inr": rng.randint(60, 900), "status": rng.choice(["completed"] * 9 + ["cancelled"])}
                      for _ in range(TRIPS)])
    query = {"rider": "R00089", "requested_at": {"$gte": NOW - dt.timedelta(days=30)}}
    before = rec.plan("rider's last-30-day trips, NO index", explain_find(coll, query, sort={"requested_at": -1}))
    coll.create_index([("rider", ASCENDING), ("requested_at", DESCENDING)])
    after = rec.plan("same query with {rider:1, requested_at:-1}", explain_find(coll, query, sort={"requested_at": -1}))
    rec.data["trip_history"] = {"trips": TRIPS, "without_index": before, "with_index": after}


def fraud_graph(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("4. Fraud relationships -> Graph")
    links = fresh(db, "ride_links")
    edges = []
    for i in range(RIDERS):                               # ordinary accounts: their own device and card
        edges.append({"from": f"R{i:05d}", "to": f"device-{i:05d}"})
        edges.append({"from": f"R{i:05d}", "to": f"card-{i:05d}"})
    ring = ["R00089", "R00412", "R01777", "R02250", "R02901"]  # planted ring sharing devices and cards in a chain
    shared = ["device-RING-A", "card-RING-B", "device-RING-C", "card-RING-D"]
    for k, hub in enumerate(shared):
        edges.append({"from": ring[k], "to": hub})
        edges.append({"from": ring[k + 1], "to": hub})
    # store both directions so a traversal can go account -> identifier -> account
    links.insert_many(edges + [{"from": e["to"], "to": e["from"]} for e in edges])
    links.create_index("from")
    pipeline = [{"$documents": [{"start": "R00089"}]},
                {"$graphLookup": {"from": "ride_links", "startWith": "$start", "connectFromField": "to",
                                  "connectToField": "from", "as": "hits", "maxDepth": 8, "depthField": "depth"}}]
    started = time.perf_counter()
    hits = list(db.aggregate(pipeline))[0]["hits"]
    ms = round((time.perf_counter() - started) * 1000, 2)
    accounts = sorted({h["to"] for h in hits if h["to"].startswith("R")} | {"R00089"})
    rec.log(f"  traversal from R00089 through shared devices/cards: connected accounts {accounts} "
            f"({len(hits)} edges walked, {ms} ms)")
    normal = list(db.aggregate([{"$documents": [{"start": "R00007"}]}, pipeline[1]]))[0]["hits"]
    rec.log(f"  control: an ordinary account (R00007) connects only to {sorted({h['to'] for h in normal})}")
    rec.data["fraud_graph"] = {"edges": len(edges) * 2, "ring_found": accounts, "ring_planted": sorted(ring),
                               "ms": ms, "control_reach": len(normal)}


def social_analytics(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Part B - social-media analytics")
    posts = fresh(db, "social_posts")
    tags = ["#ipl", "#monsoon", "#exam", "#ai", "#pune", "#cricket", "#music", "#startup"]
    start = NOW - dt.timedelta(hours=24)
    docs = []
    for i in range(12000):
        at = start + dt.timedelta(seconds=rng.randint(0, 24 * 3600 - 1))
        docs.append({"bucket": f"{at:%Y-%m-%dT%H}", "posted_at": at, "user": f"user{rng.randint(1, 5000)}",
                     "tags": rng.sample(tags, 2), "text": "post"})
    posts.insert_many(docs)
    posts.create_index([("bucket", ASCENDING), ("posted_at", ASCENDING)])   # partition = hour, clustering = time
    hour = f"{start + dt.timedelta(hours=10):%Y-%m-%dT%H}"
    part = rec.plan(f"raw layer: all posts in partition {hour}", explain_find(posts, {"bucket": hour}, sort={"posted_at": 1}))
    someone = docs[89]["user"]
    scan = rec.plan(f"raw layer: posts by {someone} (not the partition key)", explain_find(posts, {"user": someone}))

    counters = fresh(db, "social_counters")
    ops = [UpdateOne({"_id": f"{tag}:{d['posted_at']:%Y-%m-%dT%H}"}, {"$inc": {"n": 1}}, upsert=True)
           for d in docs for tag in d["tags"]]
    started = time.perf_counter()
    counters.bulk_write(ops, ordered=False)
    inc_ms = (time.perf_counter() - started) * 1000
    one_inc = time_ms(lambda: counters.update_one({"_id": "#ai:live"}, {"$inc": {"n": 1}}, upsert=True), repeat=50)
    counters.create_index([("n", DESCENDING)])
    top = list(counters.find({"_id": {"$regex": f":{hour}$"}}).sort("n", -1).limit(3))
    rec.log(f"  real-time layer: {len(ops)} counter increments ($inc upsert by key) in {inc_ms:.1f} ms; "
            f"single INCR median {one_inc} ms")
    rec.log(f"  trending in {hour}: " + ", ".join(f"{t['_id'].split(':')[0]}={t['n']}" for t in top))
    rec.data["social"] = {"posts": len(docs), "partition_read": part, "non_partition_read": scan,
                          "increments": len(ops), "increments_ms": round(inc_ms, 2), "single_inc_ms": one_inc,
                          "trending": [(t["_id"], t["n"]) for t in top]}


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_1_2", "Activity 1.2 - Map a Real-World Application (ride-sharing + social analytics)", client)
    rng = random.Random(SEED)
    for step in (driver_positions, profiles, trip_history, fraud_graph, social_analytics):
        step(db, rec, rng)
    d = rec.data
    mapping = [
        ("Live driver positions", "Key-Value + geo (Redis GEO)", f"{d['driver_positions']['burst_write_ms']} ms for 4000 key "
         f"writes; geo query examined {d['driver_positions']['geo_plan']['docsExamined']} docs"),
        ("Rider/driver profiles", "Document (MongoDB)", f"profile by id: {d['profiles']['rider_by_id']['plan']}, "
         f"{d['profiles']['rider_by_id']['docsExamined']} doc"),
        ("Trip history", "Document; Wide-Column at very large scale (Cassandra)",
         f"{d['trip_history']['without_index']['docsExamined']} docs scanned without index -> "
         f"{d['trip_history']['with_index']['docsExamined']} with the compound index"),
        ("Fraud relationships", "Graph (Neo4j / Neptune)", f"ring of {len(d['fraud_graph']['ring_found'])} found "
         f"in one traversal; planted ring matched: {d['fraud_graph']['ring_found'] == d['fraud_graph']['ring_planted']}"),
        ("Social raw post stream", "Wide-Column (Cassandra)", f"hour partition read examined "
         f"{d['social']['partition_read']['docsExamined']} docs vs {d['social']['non_partition_read']['docsExamined']}"),
        ("Social real-time counters", "Key-Value (Redis INCR)", f"single increment median {d['social']['single_inc_ms']} ms"),
    ]
    rec.section("Mapping (each row backed by the measurements above)")
    for domain, family, why in mapping:
        rec.log(f"  {domain:<26} {family:<52} {why}")
    d["mapping"] = [{"domain": a, "family": b, "evidence": c} for a, b, c in mapping]
    rec.save()


if __name__ == "__main__":
    main()
