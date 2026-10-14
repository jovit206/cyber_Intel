from __future__ import annotations

import ipaddress
import logging
import re
import time

import httpx
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from pymongo.database import Database
from pymongo.errors import PyMongoError

from backend.config import settings, virustotal_key_diagnosis, virustotal_key_status
from backend.collectors.cti import SourceApiError, _request_json
from backend.database import get_database
from backend.services import status as source_status
from backend.services.normalization import (
    normalize_indicator,
    normalize_virustotal_report,
    virustotal_endpoint,
)
from backend.utils.serialization import serialize, serialize_document

router = APIRouter(prefix="/api")
logger = logging.getLogger("aegis.api")
LIVE_CTI_SOURCES = ["misp", "virustotal"]
MISP_FEED_NAMES = ["circl", "botvrij", "threatfox"]
WORKING_SET_COLLECTION = "threat_reports_real"
metrics_history: list[dict[str, Any]] = []
previous_cache_sample: dict[str, Any] | None = None


EVICTION_STAT_NAMES = (
    "pages evicted by application threads",
    "eviction pages evicted by application threads",
)


def _cache_stat(cache: dict[str, Any], *names: str) -> Any:
    """First WiredTiger statistic that exists under any of the given names, else None."""
    for name in names:
        if name in cache:
            return cache[name]
    return None


def sample_cache_metrics(database: Database) -> dict[str, Any] | None:
    global previous_cache_sample
    status = database.client.admin.command({"serverStatus": 1})
    cache = (status.get("wiredTiger") or {}).get("cache")
    if not cache:
        return None
    current = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "max_bytes": cache.get("maximum bytes configured", 1),
        "bytes_in_cache": cache.get("bytes currently in the cache", 0),
        "dirty_bytes": cache.get("tracked dirty bytes in the cache", 0),
        "pages_read": cache.get("pages read into cache", 0),
        "pages_evicted": _cache_stat(cache, *EVICTION_STAT_NAMES),
        "pages_requested": cache.get("pages requested from the cache", 0),
    }
    hit_ratio = 1.0
    if previous_cache_sample:
        delta_requests = current["pages_requested"] - previous_cache_sample["pages_requested"]
        delta_reads = current["pages_read"] - previous_cache_sample["pages_read"]
        if delta_requests > 0:
            hit_ratio = max(0, 1 - delta_reads / delta_requests)
    previous_cache_sample = current
    point = {**current, "hit_ratio_interval": hit_ratio, "available": True}
    metrics_history.append(point)
    del metrics_history[:-60]
    return point


@router.get("/meta")
def get_meta() -> dict[str, Any]:
    database = get_database()
    event_count = database["events"].count_documents({"source": {"$in": LIVE_CTI_SOURCES}})
    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "events": event_count,
        "public": True,
        "database": settings.mongo_database,
        "version": "MongoDB / WiredTiger",
    }


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    database = get_database()
    counts = {
        "events": database["events"].count_documents({"source": {"$in": LIVE_CTI_SOURCES}}),
        "attributes": database["attributes"].count_documents({"source_feed": {"$exists": True}}),
        "threats": database["threats"].count_documents({"source": "misp-galaxy"}),
        "reports": database["reports"].count_documents({"source_repository": {"$exists": True}}),
    }
    events_by_source = list(database["events"].aggregate([
        {"$match": {"source": {"$in": LIVE_CTI_SOURCES}}},
        {"$group": {
            "_id": {"$ifNull": ["$source_feed_name", "$source"]},
            "count": {"$sum": 1},
        }},
    ]))
    events_by_threat_level = list(database["events"].aggregate([
        {"$match": {
            "source": {"$in": LIVE_CTI_SOURCES},
            "threat_level": {"$exists": True, "$ne": None},
        }},
        {"$group": {"_id": "$threat_level", "count": {"$sum": 1}}},
    ]))
    latest_event = database["events"].find_one(
        {"source": {"$in": LIVE_CTI_SOURCES}},
        {"timestamp": 1},
        sort=[("timestamp", -1)],
    )
    return {
        **counts,
        "events_by_source": {str(row["_id"]): row["count"] for row in events_by_source},
        "events_by_threat_level": {
            str(row["_id"]): row["count"] for row in events_by_threat_level
        },
        "events_by_reliability": {},
        "events_by_verdict": {},
        "last_event": serialize_document(latest_event.get("timestamp")) if latest_event else None,
    }


@router.get("/sources")
def get_sources() -> dict[str, Any]:
    database = get_database()
    feed_stats = list(database["events"].aggregate([
        {"$match": {"source": {"$in": LIVE_CTI_SOURCES}}},
        {"$group": {
            "_id": {"$ifNull": ["$source_feed_name", "$source"]},
            "events": {"$sum": 1},
            "indicators": {"$sum": {
                "$cond": [
                    {"$eq": ["$source", "misp"]},
                    {"$ifNull": ["$attribute_count", 0]},
                    {"$cond": [{"$ne": ["$indicator", None]}, 1, 0]},
                ],
            }},
            "last": {"$max": "$timestamp"},
        }},
        {"$project": {"_id": 1, "events": 1, "indicators": 1, "last": 1}},
    ]))
    return {"feeds": serialize_document(feed_stats)}


@router.get("/threats/top")
def get_top_threats(
    limit: int = Query(default=12, ge=1, le=100),
    verdict: str | None = None,
) -> list[dict[str, Any]]:
    database = get_database()
    filters: dict[str, Any] = {"source": {"$in": LIVE_CTI_SOURCES}}
    if verdict:
        filters["verdict"] = verdict
    results = database["events"].find(filters).sort(
        [("timestamp", -1), ("first_seen", -1), ("_id", 1)]
    ).limit(limit)
    return serialize_document(list(results))


@router.get("/threats/{threat_id}")
def get_threat(threat_id: str) -> dict[str, Any]:
    database = get_database()
    event = database["events"].find_one({
        "_id": threat_id,
        "source": {"$in": LIVE_CTI_SOURCES},
    })
    if not event:
        raise HTTPException(status_code=404, detail="Threat not found")
    attributes = list(database["attributes"].find(
        {"event_id": threat_id, "is_current": {"$ne": False}}
    ).limit(50))
    return serialize_document({"event": event, "attributes": attributes})


@router.get("/events")
def get_events(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    source: str | None = None,
) -> dict[str, Any]:
    database = get_database()
    filters: dict[str, Any] = {"source": {"$in": LIVE_CTI_SOURCES}}
    if source:
        if source in LIVE_CTI_SOURCES:
            filters["source"] = source
        elif source in MISP_FEED_NAMES:
            filters.update({"source": "misp", "source_feed_name": source})
        else:
            filters["source"] = {"$in": []}
    collection = database["events"]
    events = list(collection.find(filters)
                   .sort([("timestamp", -1), ("first_seen", -1), ("_id", 1)])
                   .skip((page - 1) * limit)
                   .limit(limit))
    total = collection.count_documents(filters)

    event_ids = [event["_id"] for event in events]
    attributes_by_event: dict[str, list[dict[str, Any]]] = {}
    if event_ids:
        for attribute in database["attributes"].find(
            {"event_id": {"$in": event_ids}, "is_current": {"$ne": False}},
            {"_id": 1, "event_id": 1, "indicator": 1, "indicator_type": 1,
             "normalized_indicator": 1, "category": 1, "value": 1, "comment": 1,
             "to_ids": 1, "timestamp": 1, "source_feed_name": 1},
        ).sort([("timestamp", -1), ("_id", 1)]).limit(5000):
            attributes_by_event.setdefault(attribute["event_id"], []).append(attribute)

    indicator_values = {
        value
        for event in events
        for value in [
            event.get("normalized_indicator"),
            *(attribute.get("normalized_indicator")
              for attribute in attributes_by_event.get(event["_id"], [])),
        ]
        if value
    }
    source_by_indicator: dict[str, set[str]] = {}
    if indicator_values:
        for attribute in database["attributes"].find(
            {
                "normalized_indicator": {"$in": list(indicator_values)},
                "is_current": {"$ne": False},
            },
            {"normalized_indicator": 1, "source_feed_name": 1},
        ).limit(10000):
            source_by_indicator.setdefault(attribute["normalized_indicator"], set()).add(
                attribute.get("source_feed_name", "misp")
            )
        for event in collection.find(
            {
                "source": "virustotal",
                "normalized_indicator": {"$in": list(indicator_values)},
            },
            {"normalized_indicator": 1, "source": 1},
        ).limit(10000):
            source_by_indicator.setdefault(event["normalized_indicator"], set()).add("virustotal")

    for event in events:
        attributes = attributes_by_event.get(event["_id"], [])
        if attributes:
            event.setdefault("indicator", attributes[0].get("indicator"))
            event.setdefault("indicator_type", attributes[0].get("indicator_type"))
            event.setdefault("category", attributes[0].get("category"))
            event.setdefault("value", attributes[0].get("value"))
            event.setdefault("comment", attributes[0].get("comment"))
            event.setdefault("to_ids", attributes[0].get("to_ids"))
        event_indicators = {
            value for value in [
                event.get("normalized_indicator"),
                *(attribute.get("normalized_indicator") for attribute in attributes),
            ] if value
        }
        event["correlated_sources"] = sorted({
            source
            for value in event_indicators
            for source in source_by_indicator.get(value, set())
        })

    return {
        "events": serialize_document(events),
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


@router.get("/events/{event_id}")
def get_event(event_id: str) -> dict[str, Any]:
    database = get_database()
    event = database["events"].find_one({
        "_id": event_id,
        "source": {"$in": LIVE_CTI_SOURCES},
    })
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    attributes = list(database["attributes"].find(
        {"event_id": event_id, "is_current": {"$ne": False}}
    ))
    return serialize_document({"event": event, "attributes": attributes})


@router.get("/reports")
def get_reports() -> list[dict[str, Any]]:
    return serialize_document(list(get_database()["reports"].find({
        "source_repository": {"$exists": True},
    })))


@router.get("/search")
def search(q: str = "") -> dict[str, Any]:
    if not q.strip():
        return {"query": "", "threats": [], "iocs": [], "passages": []}
    database = get_database()
    pattern = re.escape(q.strip())
    events = list(database["events"].find({
        "source": {"$in": LIVE_CTI_SOURCES},
        "$or": [
            {"info": {"$regex": pattern, "$options": "i"}},
            {"indicator": {"$regex": pattern, "$options": "i"}},
            {"description": {"$regex": pattern, "$options": "i"}},
            {"comment": {"$regex": pattern, "$options": "i"}},
        ]
    }).limit(10))
    indicators = list(database["attributes"].find({
        "source_feed": {"$exists": True},
        "is_current": {"$ne": False},
        "$or": [
            {"value": {"$regex": pattern, "$options": "i"}},
            {"comment": {"$regex": pattern, "$options": "i"}},
        ],
    }).limit(10))
    passages = list(database["passages"].find({
        "source_pdf": {"$exists": True},
        "is_current": {"$ne": False},
        "text": {"$regex": pattern, "$options": "i"},
    }).limit(10))
    return serialize_document({
        "query": q,
        "threats": events,
        "iocs": indicators,
        "passages": passages,
    })


@router.get("/metrics")
def get_metrics(request: Request) -> dict[str, Any]:
    database = request.app.state.database
    try:
        server_status = database.client.admin.command({"serverStatus": 1})
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail=f"MongoDB serverStatus failed: {error}") from error
    cache = (server_status.get("wiredTiger") or {}).get("cache")
    if not cache:
        return {"available": False, "reason": "WiredTiger statistics not exposed."}
    latest = metrics_history[-1] if metrics_history else {}
    return {
        "available": True,
        "max_bytes": cache.get("maximum bytes configured"),
        "bytes_in_cache": cache.get("bytes currently in the cache"),
        "dirty_bytes": cache.get("tracked dirty bytes in the cache"),
        "pages_read": cache.get("pages read into cache"),
        "pages_evicted": _cache_stat(cache, *EVICTION_STAT_NAMES),
        "pages_requested": cache.get("pages requested from the cache"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hit_ratio_interval": latest.get("hit_ratio_interval"),
        "sampling_interval_seconds": 10,
        "history": [
            {
                "timestamp": point["timestamp"],
                "hit_ratio": point["hit_ratio_interval"],
                "bytes_in_cache": point["bytes_in_cache"],
                "max_bytes": point["max_bytes"],
            }
            for point in list(metrics_history)
        ],
    }


@router.get("/lab72/analysis")
def get_working_set_analysis() -> dict[str, Any]:
    database = get_database()
    stats = list(database[WORKING_SET_COLLECTION].aggregate([
        {"$match": {"derived_from": {"$exists": True}}},
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
        return {
            "collection": WORKING_SET_COLLECTION,
            "count": 0,
            "avgSize": 0,
            "minSize": 0,
            "maxSize": 0,
            "totalBytes": 0,
            "indexBytes": 0,
            "totalFootprintBytes": 0,
        }
    measured = stats[0]
    index_bytes = database.command({"collStats": WORKING_SET_COLLECTION}).get("totalIndexSize", 0)
    total_bytes = measured["totalBytes"]
    return {
        "collection": WORKING_SET_COLLECTION,
        "count": measured["count"],
        "avgSize": round(measured["avgSize"]),
        "minSize": measured["minSize"],
        "maxSize": measured["maxSize"],
        "totalBytes": total_bytes,
        "indexBytes": index_bytes,
        "totalFootprintBytes": total_bytes + index_bytes,
    }


@router.post("/lab72/simulate-reads")
def simulate_random_reads(request: Request) -> dict[str, Any]:
    database = get_database()
    collection = database[WORKING_SET_COLLECTION]
    benchmark_filter = {"derived_from": {"$exists": True}}
    total_docs = collection.count_documents(benchmark_filter)
    if not total_docs:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The benchmark collection '{WORKING_SET_COLLECTION}' is empty, so there is nothing to read. "
                "Build the working-set workload first: docker exec lab7-backend python -m backend.lab_7_2_working_set"
            ),
        )
    count = min(500, total_docs)
    documents = list(collection.aggregate([
        {"$match": benchmark_filter},
        {"$sample": {"size": count}},
        {"$project": {"_id": 1}},
    ]))
    try:
        before_status = database.client.admin.command({"serverStatus": 1})
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail=f"MongoDB serverStatus failed: {error}") from error
    cache_before = (before_status.get("wiredTiger") or {}).get("cache")
    if not cache_before:
        raise HTTPException(
            status_code=503,
            detail="WiredTiger cache statistics are not available on this MongoDB deployment.",
        )
    started = time.perf_counter()
    for document in documents:
        collection.find_one({"_id": document["_id"]}, {"_id": 1})
    duration_ms = (time.perf_counter() - started) * 1000
    after_status = database.client.admin.command({"serverStatus": 1})
    cache_after = (after_status.get("wiredTiger") or {}).get("cache", {})
    page_reads = cache_after.get("pages read into cache", 0) - cache_before.get("pages read into cache", 0)
    evicted_before = _cache_stat(cache_before, *EVICTION_STAT_NAMES)
    evicted_after = _cache_stat(cache_after, *EVICTION_STAT_NAMES)
    evictions = (
        evicted_after - evicted_before
        if evicted_before is not None and evicted_after is not None
        else None
    )
    sample_cache_metrics(request.app.state.database)
    return {
        "success": True,
        "queriesExecuted": len(documents),
        "durationMs": round(duration_ms, 2),
        "avgLatencyMs": round(duration_ms / len(documents), 2) if documents else 0,
        "pageReads": page_reads,
        "evictions": evictions,
    }


@router.get("/data-source-status")
def get_data_source_status(request: Request) -> dict[str, Any]:
    database_status: dict[str, Any] = {"connected": False, "last_error": None}
    try:
        request.app.state.database.client.admin.command({"ping": 1})
        database_status["connected"] = True
    except Exception as error:
        database_status["last_error"] = str(error)

    snapshot = source_status.snapshot()
    database = request.app.state.database
    feed_syncs = list(database["cti_sync_state"].find(
        {"_id": {"$in": [*MISP_FEED_NAMES, "misp-galaxy", "annual-reports"]}},
        {"_id": 1, "source_feed": 1, "last_successful_sync": 1, "last_error": 1,
         "last_manifest_event_count": 1, "last_fetched_event_count": 1},
    ))
    scheduler = getattr(request.app.state, "scheduler", None)
    for name, details in snapshot.items():
        job = scheduler.get_job(f"cti-{name}") if scheduler else None
        details["next_scheduled_fetch"] = (
            job.next_run_time.astimezone(timezone.utc).isoformat()
            if job and job.next_run_time else None
        )
    return {
        "mongodb": database_status,
        "misp_feeds": serialize_document(feed_syncs),
        "virustotal": snapshot["virustotal"],
        "virustotal_configured": bool(settings.virustotal_api_key),
        "virustotal_key_status": virustotal_key_status(),
        "virustotal_key_hint": {
            key: value
            for key, value in virustotal_key_diagnosis().items()
            if key != "status"
        },
    }


VT_KINDS = {"sha256", "sha1", "md5", "ip", "ip-src", "ip-dst", "ipv4", "ipv6", "domain", "hostname", "url"}
_HASH_LENGTHS = {"md5": 32, "sha1": 40, "sha256": 64}
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$", re.IGNORECASE)
MAX_INDICATOR_LENGTH = 2048


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _detect_indicator_type(value: str, provided: str | None) -> str | None:
    if provided:
        return provided.strip().lower()
    text = value.strip().lower()
    for kind, length in (("sha256", 64), ("sha1", 40), ("md5", 32)):
        if re.fullmatch(rf"[0-9a-f]{{{length}}}", text):
            return kind
    if _is_ip(text) or re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", text):
        return "ip"
    if text.startswith(("http://", "https://")):
        return "url"
    if "." in text and not re.search(r"\s", text):
        return "domain"
    return None


def _validate_indicator(kind: str, value: str) -> str | None:
    """Return a human-readable problem with the indicator, or None if it is acceptable."""
    if kind not in VT_KINDS:
        return f"Unsupported indicator type '{kind}'. Use an IP address, domain, URL, MD5, SHA-1 or SHA-256."
    if kind in _HASH_LENGTHS:
        if not re.fullmatch(rf"[0-9a-fA-F]{{{_HASH_LENGTHS[kind]}}}", value):
            return f"'{value[:40]}' is not a valid {kind.upper()} hash ({_HASH_LENGTHS[kind]} hexadecimal characters)."
    elif kind in {"ip", "ip-src", "ip-dst", "ipv4", "ipv6"}:
        if not _is_ip(value):
            return f"'{value[:60]}' is not a valid IP address."
    elif kind in {"domain", "hostname"}:
        if not _DOMAIN_RE.fullmatch(value.rstrip(".")):
            return f"'{value[:80]}' is not a valid domain name."
    elif kind == "url":
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "A URL must start with http:// or https:// and include a host."
    return None


def _vt_cache_id(indicator_type: str, normalized: str) -> str:
    import hashlib

    return hashlib.sha256(f"{indicator_type}\n{normalized}".encode("utf-8")).hexdigest()


def _write_vt_cache(
    database,
    cache_id: str,
    indicator_type: str,
    indicator: str,
    normalized: str,
    result: str,
    payload: dict[str, Any] | None = None,
) -> None:
    checked_at = datetime.now(timezone.utc)
    cache_doc: dict[str, Any] = {
        "_id": cache_id,
        "source": "virustotal",
        "indicator_type": indicator_type,
        "indicator": indicator,
        "normalized_indicator": normalized,
        "result": result,
        "checked_at": checked_at,
        "expires_at": checked_at + timedelta(hours=24),
    }
    if payload is not None:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
            stats = attributes.get("last_analysis_stats") if isinstance(attributes.get("last_analysis_stats"), dict) else {}
            cache_doc["report_id"] = str(data.get("id") or cache_id)
            cache_doc["country"] = attributes.get("country") or attributes.get("country_name") or "Unknown"
            cache_doc["owner"] = attributes.get("as_owner") or attributes.get("owner") or attributes.get("network") or "Unknown"
            cache_doc["verdict"] = _vt_verdict_from_stats(stats)
            cache_doc["severity"] = _vt_severity_from_stats(stats)
            cache_doc["virustotal_url"] = _vt_gui_url(indicator, indicator_type)
            cache_doc["engine_results"] = attributes.get("last_analysis_results") if isinstance(attributes.get("last_analysis_results"), dict) else {}
        cache_doc["response"] = payload
    database["virustotal_cache"].update_one(
        {"_id": cache_id},
        {
            "$set": {key: value for key, value in cache_doc.items() if key != "_id"},
            "$setOnInsert": {"_id": cache_id},
        },
        upsert=True,
    )


def _vt_verdict_from_stats(stats: dict[str, Any] | None) -> str:
    if not isinstance(stats, dict):
        return "clean"
    malicious = stats.get("malicious")
    suspicious = stats.get("suspicious")
    if isinstance(malicious, int) and malicious > 0:
        return "malicious"
    if isinstance(suspicious, int) and suspicious > 0:
        return "suspicious"
    return "clean"


def _vt_severity_from_stats(stats: dict[str, Any] | None) -> str:
    return "High" if _vt_verdict_from_stats(stats) == "malicious" else "Low"


def _vt_gui_url(indicator: str, indicator_type: str | None = None) -> str:
    target = str(indicator or "").strip()
    kind = (indicator_type or "").lower()
    if kind in {"sha256", "sha1", "md5"}:
        return f"https://www.virustotal.com/gui/file/{target}"
    if kind in {"ip", "ip-src", "ip-dst", "ipv4", "ipv6"}:
        return f"https://www.virustotal.com/gui/search/{target}"
    if kind in {"domain", "hostname"}:
        return f"https://www.virustotal.com/gui/domain/{target}"
    return f"https://www.virustotal.com/gui/search/{target}"


def _vt_cache_response(doc: dict[str, Any]) -> dict[str, Any]:
    payload = doc.get("response")
    record = (
        normalize_virustotal_report(payload, doc)
        if isinstance(payload, dict)
        else None
    )
    if not record:
        return {"error": "No real VirusTotal result available."}
    view: dict[str, Any] = dict(record)
    view["cached"] = True
    view["report_id"] = record.get("report_id") or doc.get("report_id") or doc.get("_id")
    view["verdict"] = record.get("verdict") or _vt_verdict_from_stats(record.get("analysis_stats"))
    view["severity"] = record.get("severity") or _vt_severity_from_stats(record.get("analysis_stats"))
    view["country"] = record.get("country") or doc.get("country") or "Unknown"
    view["owner"] = record.get("owner") or doc.get("owner") or "Unknown"
    view["engine_results"] = record.get("engine_results") or doc.get("engine_results") or {}
    view["virustotal_url"] = record.get("virustotal_url") or _vt_gui_url(str(doc.get("indicator", "")), doc.get("indicator_type"))
    return serialize(view)


def _as_utc(value: Any) -> datetime | None:
    """MongoDB returns naive UTC datetimes; make them comparable with timezone-aware ones."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _vt_failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Uniform error payload. Never includes credentials or request headers."""
    return {"error": message, "error_code": code, **extra}


def _vt_not_found(indicator: str, kind: str, cached: bool) -> dict[str, Any]:
    return {
        "found": False,
        "result": "not_found",
        "indicator": indicator,
        "indicator_type": kind,
        "cached": cached,
        "message": (
            "VirusTotal has no existing report for this indicator. A lookup does not submit it "
            "for analysis, so there is no verdict to show."
        ),
    }


def _vt_auth_message() -> str:
    message = (
        "VirusTotal rejected the API key (HTTP 401). The key configured for the backend is invalid, "
        "revoked or malformed. Update VIRUSTOTAL_API_KEY in the backend .env file and restart the backend."
    )
    diagnosis = virustotal_key_diagnosis()
    if diagnosis["status"] == "unexpected_format":
        message += (
            " The configured value does not look like a VirusTotal key: expected exactly 64 hexadecimal "
            f"characters, found {diagnosis['length']}."
        )
        if diagnosis["contains_64_hex"]:
            message += (
                f" It contains a 64-character hexadecimal sequence plus {diagnosis['extra_before']} extra "
                f"character(s) before it and {diagnosis['extra_after']} after it. Copy the key again exactly "
                "as VirusTotal shows it, with no prefix, suffix, quotes or spaces."
            )
        elif diagnosis["has_whitespace"] or diagnosis["has_quotes"]:
            message += " It contains whitespace or quotation marks that should be removed."
    return message


def _scan_virustotal_indicator(
    indicator: str,
    indicator_type: str | None = None,
) -> dict[str, Any]:
    indicator = indicator.strip()
    if not indicator:
        return _vt_failure("invalid_indicator", "Enter an IP address, domain, URL or file hash.")
    if len(indicator) > MAX_INDICATOR_LENGTH:
        return _vt_failure("invalid_indicator", f"The indicator is longer than {MAX_INDICATOR_LENGTH} characters.")
    kind = _detect_indicator_type(indicator, indicator_type)
    if kind is None:
        return _vt_failure(
            "invalid_indicator",
            "Could not determine the indicator type. Enter an IP address, domain, URL, MD5, SHA-1 or SHA-256.",
        )
    problem = _validate_indicator(kind, indicator)
    if problem:
        return _vt_failure("invalid_indicator", problem)

    database = get_database()
    normalized = normalize_indicator(kind, indicator) or indicator
    cache_id = _vt_cache_id(kind, normalized)
    cached = database["virustotal_cache"].find_one({"_id": cache_id})
    if cached:
        expires_at = _as_utc(cached.get("expires_at"))
        if expires_at is None or expires_at > datetime.now(timezone.utc):
            if cached.get("result") == "not_found":
                return _vt_not_found(indicator, kind, cached=True)
            return _vt_cache_response(cached)

    if virustotal_key_status() == "missing":
        return _vt_failure(
            "not_configured",
            "VirusTotal is not configured. Set VIRUSTOTAL_API_KEY in the backend .env file and restart the backend.",
            configured=False,
        )
    endpoint = virustotal_endpoint(kind, indicator)
    if not endpoint:
        return _vt_failure("invalid_indicator", f"Unsupported indicator type: {kind}")
    try:
        with httpx.Client(timeout=15) as client:
            payload = _request_json(
                client,
                "GET",
                endpoint,
                headers={"x-apikey": settings.virustotal_api_key, "Accept": "application/json"},
            )
    except SourceApiError as error:
        status = error.status_code
        if status == 404:
            _write_vt_cache(database, cache_id, kind, indicator, normalized, "not_found")
            return _vt_not_found(indicator, kind, cached=False)
        if status == 401:
            return _vt_failure("auth_failed", _vt_auth_message())
        if status == 403:
            return _vt_failure(
                "forbidden",
                "VirusTotal refused the request (HTTP 403). The key may lack permission for this lookup or the quota is exhausted.",
            )
        if status == 400:
            return _vt_failure("bad_request", "VirusTotal could not process this indicator (HTTP 400).")
        if status == 429:
            return _vt_failure("rate_limited", "VirusTotal rate limit or quota reached. Cached results remain available; try again later.")
        if status is None and "timed out" in str(error):
            return _vt_failure("timeout", "VirusTotal did not respond within 15 seconds. Try again.")
        if status is None:
            return _vt_failure("unavailable", "Could not reach VirusTotal from the backend. Check its network access and try again.")
        return _vt_failure("upstream_error", f"VirusTotal returned an unexpected response (HTTP {status}).")

    record = normalize_virustotal_report(payload, {"indicator_type": kind, "indicator": indicator})
    if not record:
        return _vt_failure("unexpected_response", "VirusTotal returned a response without a usable report.")
    _write_vt_cache(database, cache_id, kind, indicator, normalized, "found", payload)
    return serialize({**record, "cached": False})


@router.get("/virustotal/lookup")
def virustotal_lookup(
    indicator: str = Query(..., min_length=1),
    indicator_type: str | None = None,
) -> dict[str, Any]:
    return _scan_virustotal_indicator(indicator, indicator_type)


@router.post("/virustotal/scan")
def virustotal_scan(payload: dict[str, Any]) -> dict[str, Any]:
    target = str(payload.get("target", "")).strip()
    if not target:
        raise HTTPException(status_code=400, detail="Target is required.")
    return _scan_virustotal_indicator(target)


@router.get("/virustotal/recent")
def virustotal_recent(limit: int = Query(8, ge=1, le=50)) -> dict[str, Any]:
    database = get_database()
    now = datetime.now(timezone.utc)
    docs = list(database["virustotal_cache"].find({"result": "found"}).sort("checked_at", -1).limit(limit))
    items: list[dict[str, Any]] = []
    for doc in docs:
        expires_at = _as_utc(doc.get("expires_at"))
        if expires_at is not None and expires_at < now:
            continue
        report = _vt_cache_response(doc)
        if report.get("error"):
            continue
        items.append({
            "indicator": report.get("indicator"),
            "indicator_type": report.get("indicator_type"),
            "verdict": report.get("verdict") or "clean",
            "country": report.get("country") or "Unknown",
            "owner": report.get("owner") or "Unknown",
            "scanned_at": report.get("last_analysis_date") or doc.get("checked_at"),
            "report_id": report.get("report_id") or doc.get("_id"),
            "malicious_count": report.get("malicious_count") or 0,
            "virustotal_url": report.get("virustotal_url"),
            "engine_results": report.get("engine_results") or {},
        })
    return {"items": items, "count": len(items), "message": "No real VirusTotal scans yet." if not items else None}


@router.get("/virustotal/report")
def virustotal_report(
    indicator: str = Query(..., min_length=1),
    indicator_type: str | None = None,
) -> dict[str, Any]:
    report = _scan_virustotal_indicator(indicator, indicator_type)
    if report.get("error"):
        return {"error": report["error"], "result": None}
    return {"report": report, "engine_results": report.get("engine_results") or {}}
