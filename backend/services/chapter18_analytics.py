from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.database import Database

from backend.services.chapter18_connections import Chapter18Connections
from backend.services.chapter18_storage import FULL_THREATS


def analytics_snapshot(
    application_database: Database,
    connections: Chapter18Connections,
) -> dict[str, Any]:
    collection = connections.mongo_database(application_database)[FULL_THREATS]
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=30)
    provenance = list(collection.aggregate([
        {"$group": {"_id": "$provenance", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    sources = list(collection.aggregate([
        {"$match": {"provenance": "imported"}},
        {"$group": {
            "_id": "$from_source",
            "count": {"$sum": 1},
            "latest": {"$max": "$timestamp"},
        }},
        {"$sort": {"count": -1, "_id": 1}},
        {"$limit": 20},
    ]))
    threat_types = list(collection.aggregate([
        {"$match": {"provenance": "imported"}},
        {"$group": {"_id": "$threat_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
        {"$limit": 20},
    ]))
    daily = list(collection.aggregate([
        {"$match": {"provenance": "imported", "timestamp": {"$gte": window_start}}},
        {"$group": {
            "_id": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": "$timestamp",
                    "timezone": "UTC",
                }
            },
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]))
    return {
        "generated_at": now.isoformat(),
        "provenance": [
            {"kind": row["_id"], "count": row["count"]}
            for row in provenance
        ],
        "sources": [
            {
                "source": row["_id"],
                "count": row["count"],
                "latest": row["latest"].replace(tzinfo=timezone.utc).isoformat()
                if row.get("latest") and row["latest"].tzinfo is None
                else row["latest"].isoformat() if row.get("latest") else None,
            }
            for row in sources
        ],
        "threat_types": [
            {"threat_type": row["_id"], "count": row["count"]}
            for row in threat_types
        ],
        "daily": [{"date": row["_id"], "count": row["count"]} for row in daily],
        "window_days": 30,
        "dataset_note": "Counts reflect stored records; imported data is user-provided and not independently verified.",
    }


def geographic_snapshot(
    application_database: Database,
    connections: Chapter18Connections,
) -> dict[str, Any]:
    collection = connections.mongo_database(application_database)[FULL_THREATS]
    countries = list(collection.aggregate([
        {
            "$match": {
                "provenance": "imported",
                "country_code": {"$type": "string"},
            }
        },
        {"$group": {"_id": "$country_code", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
        {"$limit": 100},
    ]))
    points = list(collection.aggregate([
        {
            "$match": {
                "provenance": "imported",
                "latitude": {"$type": "number"},
                "longitude": {"$type": "number"},
            }
        },
        {"$group": {
            "_id": {"latitude": "$latitude", "longitude": "$longitude"},
            "count": {"$sum": 1},
            "country_code": {"$first": "$country_code"},
        }},
        {"$sort": {"count": -1}},
        {"$limit": 500},
    ]))
    geolocated_count = collection.count_documents({
        "provenance": "imported",
        "latitude": {"$type": "number"},
        "longitude": {"$type": "number"},
    })
    return {
        "countries": [
            {"country_code": row["_id"], "count": row["count"]}
            for row in countries
        ],
        "points": [
            {
                "latitude": row["_id"]["latitude"],
                "longitude": row["_id"]["longitude"],
                "country_code": row.get("country_code"),
                "count": row["count"],
            }
            for row in points
        ],
        "geolocated_record_count": geolocated_count,
        "location_groups_returned": len(points),
        "truncated": len(points) == 500,
        "note": (
            "Only source-provided coordinates and country codes are shown. "
            "No location is inferred from an indicator or target."
        ),
    }
