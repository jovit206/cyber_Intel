"""Lab 7.2: real-record-derived working set, BSON sizing and random reads."""

from __future__ import annotations

import time
from typing import Any

from backend.config import settings
from backend.database import close_database, connect_database

COLLECTION_NAME = "threat_reports_real"
TOTAL_DOCS = 100_000
BATCH_SIZE = 5_000
SOURCE_SAMPLE_LIMIT = 1_000
TARGET_PAYLOAD_BYTES = 2_048


def format_bytes(value: int | float | None) -> str:
    if not value:
        return "0 B"
    sizes = ("B", "KB", "MB", "GB", "TB")
    scaled = float(value)
    unit = 0
    while scaled >= 1024 and unit < len(sizes) - 1:
        scaled /= 1024
        unit += 1
    return f"{scaled:.2f} {sizes[unit]}"


def _real_source_rows(database) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_queries = [
        (
            "events",
            {"source": "misp", "misp_uuid": {"$exists": True}},
            {"_id": 1, "info": 1, "source_feed": 1, "timestamp": 1},
            ("info",),
        ),
        (
            "attributes",
            {"source_feed": {"$exists": True}, "is_current": {"$ne": False}},
            {"_id": 1, "value": 1, "comment": 1, "source_feed": 1, "timestamp": 1},
            ("value", "comment"),
        ),
        (
            "passages",
            {"source_pdf": {"$exists": True}, "is_current": {"$ne": False}},
            {"_id": 1, "text": 1, "source_pdf": 1, "timestamp": 1},
            ("text",),
        ),
        (
            "reports",
            {"source_repository": {"$exists": True}},
            {"_id": 1, "title": 1, "pdf_url": 1, "timestamp": 1},
            ("title",),
        ),
    ]
    for collection_name, filters, projection, text_fields in source_queries:
        cursor = database[collection_name].find(filters, projection).sort("_id", 1).limit(SOURCE_SAMPLE_LIMIT)
        for source in cursor:
            content = next(
                (source.get(field) for field in text_fields if isinstance(source.get(field), str) and source[field].strip()),
                None,
            )
            if not content:
                continue
            rows.append({
                "collection": collection_name,
                "record_id": str(source["_id"]),
                "source_feed": source.get("source_feed"),
                "timestamp": source.get("timestamp"),
                "content": content,
            })
    return rows


def _padded_source_content(content: str) -> str:
    encoded = content.encode("utf-8")
    if not encoded:
        return ""
    repeated = (encoded * ((TARGET_PAYLOAD_BYTES + len(encoded) - 1) // len(encoded)))[:TARGET_PAYLOAD_BYTES]
    return repeated.decode("utf-8", errors="ignore")


def populate_working_set(database, total_docs: int = TOTAL_DOCS) -> int:
    collection = database[COLLECTION_NAME]
    source_rows = _real_source_rows(database)
    if not source_rows:
        raise RuntimeError(
            "No real CTI events, attributes, or report passages are available; sync sources before building the Lab 7.2 workload."
        )
    existing_count = collection.count_documents({"derived_from": {"$exists": True}})
    if existing_count >= total_docs:
        print(f"Collection '{COLLECTION_NAME}' already has {existing_count:,} real-derived benchmark documents; reusing it.")
        return existing_count

    remaining = total_docs - existing_count
    print(
        f"Building {remaining:,} Lab 7.2 documents from {len(source_rows):,} real source records "
        f"in '{COLLECTION_NAME}' (existing documents will not be dropped)."
    )
    for offset in range(0, remaining, BATCH_SIZE):
        batch_size = min(BATCH_SIZE, remaining - offset)
        documents = []
        for index in range(existing_count + offset, existing_count + offset + batch_size):
            source = source_rows[index % len(source_rows)]
            source_key = f"{source['collection']}:{source['record_id']}"
            import hashlib

            source_hash = hashlib.sha256(source_key.encode("utf-8")).hexdigest()
            documents.append({
                "_id": f"real-derived:{source_hash}:{index}",
                "derived_from": source_key,
                "source_collection": source["collection"],
                "source_feed": source["source_feed"],
                "timestamp": source["timestamp"],
                "payload": _padded_source_content(source["content"]),
                "content_is_repeated_for_size": True,
            })
        collection.insert_many(documents, ordered=True)
    return collection.count_documents({"derived_from": {"$exists": True}})


def run_working_set_analysis(database=None, populate: bool = True) -> dict[str, Any]:
    owns_database = database is None
    db = database if database is not None else connect_database()
    try:
        collection = db[COLLECTION_NAME]
        if populate:
            populate_working_set(db)
        sample_filter = {"derived_from": {"$exists": True}}
        stats = list(collection.aggregate([
            {"$match": sample_filter},
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
        if not stats:
            raise RuntimeError(f"No documents found in {COLLECTION_NAME}")
        measured = stats[0]
        total_docs = measured["count"]
        total_bytes = measured["totalBytes"]
        index_bytes = db.command({"collStats": COLLECTION_NAME}).get("totalIndexSize", 0)
        status_before = db.client.admin.command({"serverStatus": 1})
        cache_before = (status_before.get("wiredTiger") or {}).get("cache")

        random_reads = 0
        duration_ms = 0.0
        page_reads = 0
        evictions = 0
        if cache_before:
            random_reads = min(500, total_docs)
            selected = list(collection.aggregate([
                {"$match": sample_filter},
                {"$sample": {"size": random_reads}},
                {"$project": {"_id": 1}},
            ]))
            started = time.perf_counter()
            for document in selected:
                collection.find_one({"_id": document["_id"]}, {"_id": 1})
            duration_ms = (time.perf_counter() - started) * 1000
            after_status = db.client.admin.command({"serverStatus": 1})
            cache_after = (after_status.get("wiredTiger") or {}).get("cache", {})
            page_reads = cache_after.get("pages read into cache", 0) - cache_before.get("pages read into cache", 0)
            evictions = (
                cache_after.get("eviction pages evicted by application threads", 0)
                - cache_before.get("eviction pages evicted by application threads", 0)
            )

        report = {
            "collection": COLLECTION_NAME,
            "count": total_docs,
            "avgSize": round(measured["avgSize"]),
            "minSize": measured["minSize"],
            "maxSize": measured["maxSize"],
            "totalBytes": total_bytes,
            "indexBytes": index_bytes,
            "totalFootprintBytes": total_bytes + index_bytes,
            "cacheAvailable": cache_before is not None,
            "randomReads": random_reads,
            "durationMs": round(duration_ms, 2),
            "averageLatencyMs": round(duration_ms / random_reads, 2) if random_reads else None,
            "pageReads": page_reads,
            "evictions": evictions,
        }
        return report
    finally:
        if owns_database:
            close_database()


def run() -> dict[str, Any]:
    database = connect_database()
    try:
        report = run_working_set_analysis(database)
        print(f"Connected to MongoDB database: {settings.mongo_database}")
        print(
            f"{report['count']:,} docs; average BSON {report['avgSize']:,} bytes; "
            f"footprint {format_bytes(report['totalFootprintBytes'])}"
        )
        if report["cacheAvailable"]:
            print(
                f"Random reads: {report['randomReads']:,} in {report['durationMs']:.2f} ms; "
                f"page reads {report['pageReads']:,}; evictions {report['evictions']:,}"
            )
        else:
            print("WiredTiger cache metrics are not exposed by this MongoDB deployment.")
        return report
    finally:
        close_database()


if __name__ == "__main__":
    run()
