"""Activity 1.2 (Try it yourself) - Map a second application: a food-delivery platform.

Author: Jovit Chawla (24BTRCL089)

Domains and the family each one maps to:
  restaurant menus         -> Document          nested sections / variants / add-ons, read in one go
  live order state         -> Key-Value + TTL   order id -> current status, expires after delivery
  delivery zones           -> Geospatial        is this address inside a restaurant's delivery polygon?
  order analytics          -> Time-series       orders per hour, revenue per kitchen (column-oriented at scale)
  "also ordered" graph     -> Graph             dishes connected through shared orders
  dish search              -> Search engine     relevance-ranked full text (outside the four classic families:
                                                Elasticsearch / Atlas Search)

All data is synthetic, generated with a fixed seed.
"""
from __future__ import annotations

import datetime as dt
import itertools
import random

from pymongo import ASCENDING, GEOSPHERE, TEXT

from common import Recorder, connect, explain_aggregate, explain_find, fresh, time_ms

SEED = 8924
NOW = dt.datetime(2026, 6, 6, 20, 0, tzinfo=dt.timezone.utc)
JAIPUR = (75.7873, 26.9124)
DISHES = ["paneer butter masala", "dal makhani", "veg biryani", "chicken biryani", "masala dosa", "idli sambar",
          "chole bhature", "pav bhaji", "margherita pizza", "veg burger", "hakka noodles", "gulab jamun",
          "rasmalai", "dal baati churma", "kadhai paneer", "butter naan", "jeera rice", "mango lassi"]


def menus(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Restaurant menus -> Document")
    coll = fresh(db, "food_restaurants")
    docs = []
    for i in range(300):
        sections = []
        for name in rng.sample(["Starters", "Mains", "Breads", "Desserts", "Drinks"], 3):
            items = [{"dish": d, "price": rng.randint(80, 450), "veg": "chicken" not in d,
                      "variants": [{"size": s, "extra": e} for s, e in (("half", 0), ("full", rng.randint(40, 120)))]}
                     for d in rng.sample(DISHES, 4)]
            sections.append({"name": name, "items": items})
        docs.append({"_id": f"REST{i:03d}", "name": f"Kitchen {i}", "cuisine": rng.choice(["North Indian", "South Indian",
                     "Rajasthani", "Chinese", "Fast food"]), "menu": sections,
                     "loc": {"type": "Point", "coordinates": [JAIPUR[0] + rng.uniform(-0.06, 0.06),
                                                              JAIPUR[1] + rng.uniform(-0.06, 0.06)]}})
    coll.insert_many(docs)
    coll.create_index("menu.items.dish")
    whole = rec.plan("open a restaurant page (menu with sections, variants) by id", explain_find(coll, {"_id": "REST089"}))
    nested = rec.plan("restaurants serving 'dal baati churma' under 200 INR",
                      explain_find(coll, {"menu.items": {"$elemMatch": {"dish": "dal baati churma", "price": {"$lt": 200}}}}))
    rec.data["menus"] = {"restaurants": len(docs), "by_id": whole, "nested_query": nested}


def order_state(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Live order state -> Key-Value with TTL")
    coll = fresh(db, "food_order_state")
    coll.create_index("expire_at", expireAfterSeconds=0)
    statuses = ["placed", "accepted", "cooking", "picked_up", "delivered"]
    coll.insert_many([{"_id": f"ORD{i:06d}", "status": rng.choice(statuses), "courier": f"C{rng.randint(1, 400):03d}",
                       "expire_at": NOW + dt.timedelta(hours=2)} for i in range(20000)])
    get = rec.plan("customer app polls ORD000089 status", explain_find(coll, {"_id": "ORD000089"}))
    poll_ms = time_ms(lambda: coll.find_one({"_id": "ORD000089"}, {"status": 1}), repeat=50)
    update_ms = time_ms(lambda: coll.update_one({"_id": "ORD000089"}, {"$set": {"status": "picked_up"}}), repeat=50)
    rec.log(f"  status read median {poll_ms} ms, status write median {update_ms} ms; entries expire 2 h after creation (TTL)")
    rec.data["order_state"] = {"orders": 20000, "get": get, "poll_ms": poll_ms, "update_ms": update_ms}


def delivery_zones(db, rec: Recorder, rng: random.Random) -> None:
    rec.section("Delivery zones -> Geospatial")
    zones = fresh(db, "food_zones")
    zones.create_index([("area", GEOSPHERE)])
    lon, lat = JAIPUR
    for i, (dx, dy) in enumerate(itertools.product((-0.04, 0.0, 0.04), repeat=2)):
        cx, cy, r = lon + dx, lat + dy, 0.02
        ring = [[cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r], [cx - r, cy - r]]
        zones.insert_one({"_id": f"ZONE-{i}", "area": {"type": "Polygon", "coordinates": [ring]}, "fee": 20 + 5 * i})
    address = {"type": "Point", "coordinates": [lon + 0.041, lat - 0.035]}
    query = {"area": {"$geoIntersects": {"$geometry": address}}}
    zone = zones.find_one(query)
    plan = rec.plan("which delivery zone contains this address?", explain_find(zones, query))
    rec.log(f"  address falls in {zone['_id'] if zone else 'no zone'} (fee {zone['fee'] if zone else '-'} INR)")
    rests = db["food_restaurants"]
    rests.create_index([("loc", GEOSPHERE)])
    near = rests.count_documents({"loc": {"$geoWithin": {"$centerSphere": [address["coordinates"], 3 / 6378.1]}}})
    rec.log(f"  restaurants within 3 km of the address: {near}")
    rec.data["zones"] = {"zone": zone["_id"] if zone else None, "plan": plan, "restaurants_within_3km": near}


def analytics(db, rec: Recorder, rng: random.Random) -> list[list[str]]:
    rec.section("Order analytics -> Time-series")
    db.drop_collection("food_orders_ts")
    ts = db.create_collection("food_orders_ts", timeseries={"timeField": "at", "metaField": "kitchen", "granularity": "minutes"})
    baskets, orders = [], []
    for _ in range(30000):
        at = NOW - dt.timedelta(minutes=rng.randint(0, 7 * 24 * 60))
        hour = at.hour
        weight = 3 if 12 <= hour <= 14 or 19 <= hour <= 22 else 1          # lunch and dinner peaks
        if rng.random() > weight / 3:
            continue
        basket = rng.sample(DISHES[:8], rng.randint(1, 3)) if rng.random() < 0.7 else rng.sample(DISHES, 2)
        baskets.append(basket)
        orders.append({"at": at, "kitchen": f"REST{rng.randrange(300):03d}", "amount": rng.randint(150, 1400),
                       "items": len(basket)})
    ts.insert_many(orders)
    per_hour = [{"$group": {"_id": {"$hour": "$at"}, "orders": {"$sum": 1}, "revenue": {"$sum": "$amount"}}},
                {"$sort": {"orders": -1}}, {"$limit": 3}]
    busiest = list(ts.aggregate(per_hour))
    rec.plan("busiest hours of the week", explain_aggregate(ts, per_hour))
    rec.log(f"  {len(orders)} orders stored; busiest UTC hours: " +
            ", ".join(f"{b['_id']:02d}:00 ({b['orders']} orders, INR {b['revenue']:,})" for b in busiest))
    rec.data["analytics"] = {"orders": len(orders), "busiest_hours": busiest}
    return baskets


def also_ordered(db, rec: Recorder, baskets: list[list[str]]) -> None:
    rec.section("'Also ordered' recommendations -> Graph")
    edges = fresh(db, "food_dish_edges")
    weight: dict[tuple[str, str], int] = {}
    for basket in baskets:
        for a, b in itertools.permutations(sorted(set(basket)), 2):
            weight[(a, b)] = weight.get((a, b), 0) + 1
    edges.insert_many([{"src": a, "dst": b, "w": w} for (a, b), w in weight.items() if w >= 25])
    edges.create_index("src")
    direct = list(edges.find({"src": "veg biryani"}).sort("w", -1).limit(3))
    two_hop = [{"$documents": [{"dish": "gulab jamun"}]},
               {"$graphLookup": {"from": "food_dish_edges", "startWith": "$dish", "connectFromField": "dst",
                                 "connectToField": "src", "as": "reach", "maxDepth": 1, "depthField": "hop",
                                 "restrictSearchWithMatch": {"w": {"$gte": 25}}}}]
    reach = list(db.aggregate(two_hop))[0]["reach"]
    rec.log(f"  {edges.estimated_document_count()} weighted co-order edges (>= 25 shared orders)")
    rec.log("  ordered with veg biryani: " + ", ".join(f"{e['dst']} ({e['w']})" for e in direct))
    rec.log(f"  gulab jamun reaches {len({r['dst'] for r in reach})} dishes within 2 hops (cross-sell candidates)")
    rec.data["graph"] = {"edges": edges.estimated_document_count(), "with_veg_biryani": [(e["dst"], e["w"]) for e in direct],
                         "gulab_jamun_2hop": len({r["dst"] for r in reach})}


def dish_search(db, rec: Recorder) -> None:
    rec.section("Dish search -> Search engine (relevance ranking)")
    coll = fresh(db, "food_search")
    rests = db["food_restaurants"]
    docs = [{"restaurant": r["_id"], "dish": item["dish"], "section": s["name"],
             "description": f"{item['dish']} from {r['name']}, {r['cuisine']} kitchen"}
            for r in rests.find() for s in r["menu"] for item in s["items"]]
    coll.insert_many(docs)
    coll.create_index([("dish", TEXT), ("description", TEXT)], weights={"dish": 5, "description": 1})
    query = {"$text": {"$search": "paneer masala"}}
    top = list(coll.aggregate([{"$match": query}, {"$group": {"_id": "$dish", "score": {"$max": {"$meta": "textScore"}}}},
                               {"$sort": {"score": -1}}, {"$limit": 4}, {"$project": {"dish": "$_id", "score": 1}}]))
    plan = rec.plan("full-text 'paneer masala' ranked by score", explain_find(coll, query))
    regex = rec.plan("same need with a plain regex (no ranking)", explain_find(coll, {"dish": {"$regex": "paneer|masala"}}))
    rec.log("  top matches: " + ", ".join(f"{t['dish']} ({t['score']:.2f})" for t in top))
    rec.data["search"] = {"documents": len(docs), "text_plan": plan, "regex_plan": regex,
                          "top": [(t["dish"], round(t["score"], 3)) for t in top]}


def main() -> None:
    client, db = connect()
    rec = Recorder("activity_1_2_try_it_yourself", "Activity 1.2 (Try it yourself) - Food-delivery platform", client)
    rng = random.Random(SEED)
    menus(db, rec, rng)
    order_state(db, rec, rng)
    delivery_zones(db, rec, rng)
    baskets = analytics(db, rec, rng)
    also_ordered(db, rec, baskets)
    dish_search(db, rec)
    d = rec.data
    mapping = [
        ("Restaurant menus", "Document", f"whole page in 1 doc ({d['menus']['by_id']['plan']})"),
        ("Live order state", "Key-Value + TTL (Redis)", f"read {d['order_state']['poll_ms']} ms / write "
         f"{d['order_state']['update_ms']} ms by key"),
        ("Delivery zones", "Geospatial (document DB with geo index)", f"point-in-polygon -> {d['zones']['zone']}"),
        ("Order analytics", "Time-series / Wide-Column at scale", f"{d['analytics']['orders']} orders grouped by hour"),
        ("Also-ordered", "Graph (Neo4j)", f"{d['graph']['edges']} weighted edges, 2-hop reach "
         f"{d['graph']['gulab_jamun_2hop']}"),
        ("Dish search", "Search engine (Elasticsearch / Atlas Search)", "relevance score needed; a regex scanned "
         f"{d['search']['regex_plan']['docsExamined']} docs with no ranking"),
    ]
    rec.section("Mapping")
    for domain, family, why in mapping:
        rec.log(f"  {domain:<18} {family:<46} {why}")
    d["mapping"] = [{"domain": a, "family": b, "evidence": c} for a, b, c in mapping]
    rec.save()


if __name__ == "__main__":
    main()
