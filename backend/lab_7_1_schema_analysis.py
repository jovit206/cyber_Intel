"""Lab 7.1: schema analysis and embedding-versus-referencing benchmark."""

from __future__ import annotations

import time
from typing import Any

from backend.config import settings
from backend.database import close_database, connect_database

LIMIT_16MB = 16 * 1024 * 1024
COLLECTIONS = ("events", "attributes", "threats", "reports", "passages", "threats_embedded")
RELATIONSHIPS = [
    {
        "relationship": "events -> attributes (IOCs)",
        "pattern": "REFERENCING",
        "rationale": "Unbounded 1:N growth makes embedded event documents grow without bound.",
        "embeddedAlternative": "Embed attributes: [{type, value, timestamp, ...}] in event documents.",
        "tradeoffs": "Embedding saves a query but increases document size and update cost.",
    },
    {
        "relationship": "threats -> attributes (IOCs)",
        "pattern": "REFERENCING (via threat_ids)",
        "rationale": "M:N IOC relationships avoid duplicating shared indicators.",
        "embeddedAlternative": "Embed iocs: [...] directly inside each threat document.",
        "tradeoffs": "Embedding can duplicate indicators and create inconsistent copies.",
    },
    {
        "relationship": "reports -> passages (Chunks)",
        "pattern": "REFERENCING",
        "rationale": "Report summaries can be queried without loading every passage.",
        "embeddedAlternative": "Embed passages: [{page, chunk, text}, ...] in reports.",
        "tradeoffs": "Referencing keeps report metadata documents small.",
    },
    {
        "relationship": "events -> threat summaries",
        "pattern": "EMBEDDING (Hybrid)",
        "rationale": "The bounded threat summary is stored with its event for feed reads.",
        "embeddedAlternative": "Already represented as embedded event summaries.",
        "tradeoffs": "Small bounded duplication reduces read-time joins.",
    },
    {
        "relationship": "threats -> recent report citations",
        "pattern": "EMBEDDING (Subset Pattern)",
        "rationale": "A bounded report citation subset can be displayed with threat metadata.",
        "embeddedAlternative": "Query passages and reports for each threat view.",
        "tradeoffs": "A small metadata subset avoids repeated lookups.",
    },
]


def analyze_schema(database=None) -> dict[str, Any]:
    owns_database = database is None
    db = database if database is not None else connect_database()
    try:
        size_stats = []
        for name in COLLECTIONS:
            rows = list(db[name].aggregate([
                {"$project": {"size": {"$bsonSize": "$$ROOT"}}},
                {"$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "avgSize": {"$avg": "$size"},
                    "minSize": {"$min": "$size"},
                    "maxSize": {"$max": "$size"},
                    "totalBytes": {"$sum": "$size"},
                }},
            ]))
            if not rows:
                continue
            row = rows[0]
            size_stats.append({
                "collection": name,
                "count": row["count"],
                "avgSize": round(row["avgSize"]),
                "minSize": row["minSize"],
                "maxSize": row["maxSize"],
                "totalBytes": row["totalBytes"],
                "pctOf16MB": f"{row['maxSize'] / LIMIT_16MB * 100:.4f}%",
            })
        return {"sizeStats": size_stats, "relationships": RELATIONSHIPS}
    finally:
        if owns_database:
            close_database()


def benchmark_referencing_vs_embedded(database=None, iterations: int = 500) -> dict[str, float]:
    owns_database = database is None
    db = database if database is not None else connect_database()
    run_id = str(time.perf_counter_ns())
    threat = db["threats"].find_one({"source": "misp-galaxy"})
    if not threat:
        if owns_database:
            close_database()
        raise RuntimeError("Sync MISP Galaxy clusters before running the Lab 7.1 benchmark.")
    threat_id = threat["_id"]
    real_attributes = list(db["attributes"].find({
        "threat_ids": threat_id,
        "is_current": {"$ne": False},
    }).limit(100))
    if not real_attributes:
        if owns_database:
            close_database()
        raise RuntimeError("No real attributes reference a Galaxy cluster; sync MISP feeds before benchmarking.")
    benchmark_attribute_ids = [f"lab71:{run_id}:attr:{index}" for index in range(100)]
    benchmark_embedded_id = f"lab71:{run_id}:embedded"
    try:
        start = time.perf_counter()
        for _ in range(iterations):
            db["threats"].find_one({"_id": threat_id})
            list(db["attributes"].find({
                "threat_ids": threat_id,
                "is_current": {"$ne": False},
            }))
        referencing_read_ms = (time.perf_counter() - start) * 1000

        embedded_iocs = []
        for index in range(100):
            source = real_attributes[index % len(real_attributes)]
            embedded_iocs.append({
                "type": source.get("type"),
                "value": source.get("value"),
                "category": source.get("category"),
                "to_ids": source.get("to_ids"),
                "timestamp": source.get("timestamp"),
            })
        db["threats_embedded"].insert_one({
            "_id": benchmark_embedded_id,
            "source": "lab-benchmark",
            "derived_from": threat_id,
            "name": threat.get("name"),
            "embedded_iocs": embedded_iocs,
        })

        start = time.perf_counter()
        for _ in range(iterations):
            db["threats_embedded"].find_one({"_id": benchmark_embedded_id})
        embedded_read_ms = (time.perf_counter() - start) * 1000

        embedded = db["threats_embedded"]
        try:
            insert_docs = []
            for index in range(100):
                source = real_attributes[index % len(real_attributes)]
                insert_docs.append({
                    **{
                        key: value for key, value in source.items()
                        if key not in {"_id", "attribute_uuid", "vt_lookup_checked_at"}
                    },
                    "_id": benchmark_attribute_ids[index],
                    "benchmark": True,
                })
            start = time.perf_counter()
            db["attributes"].insert_many(insert_docs, ordered=True)
            referencing_write_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            for index in range(100):
                source = real_attributes[index % len(real_attributes)]
                embedded.update_one(
                    {"_id": benchmark_embedded_id},
                    {"$push": {"embedded_iocs": {
                        "type": source.get("type"),
                        "value": source.get("value"),
                        "category": source.get("category"),
                        "to_ids": source.get("to_ids"),
                        "timestamp": source.get("timestamp"),
                    }}},
                )
            embedded_write_ms = (time.perf_counter() - start) * 1000
        finally:
            db["attributes"].delete_many({"_id": {"$in": benchmark_attribute_ids}})
        return {
            "referencingReadMs": referencing_read_ms,
            "embeddedReadMs": embedded_read_ms,
            "referencingWriteMs": referencing_write_ms,
            "embeddedWriteMs": embedded_write_ms,
        }
    finally:
        db["threats_embedded"].delete_one({"_id": benchmark_embedded_id})
        if owns_database:
            close_database()


def run() -> dict[str, Any]:
    database = connect_database()
    try:
        analysis = analyze_schema(database)
        benchmark = benchmark_referencing_vs_embedded(database)
        print(f"Connected to MongoDB database: {settings.mongo_database}")
        for item in analysis["sizeStats"]:
            print(
                f"{item['collection']}: {item['count']} documents, "
                f"average {item['avgSize']} bytes, max {item['maxSize']} bytes"
            )
        for relationship in analysis["relationships"]:
            print(
                f"{relationship['relationship']}: {relationship['pattern']} — "
                f"{relationship['rationale']}"
            )
        print(f"Referencing read benchmark: {benchmark['referencingReadMs']:.2f} ms")
        print(f"Embedded read benchmark: {benchmark['embeddedReadMs']:.2f} ms")
        print(f"Referencing write benchmark: {benchmark['referencingWriteMs']:.2f} ms")
        print(f"Embedded write benchmark: {benchmark['embeddedWriteMs']:.2f} ms")
        return {**analysis, "benchmark": benchmark}
    finally:
        close_database()


if __name__ == "__main__":
    run()
