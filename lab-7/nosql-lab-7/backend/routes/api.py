from __future__ import annotations

import logging
import re
import time

import httpx
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pymongo.database import Database

from backend.config import settings
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
        "pages_evicted": cache.get("eviction pages evicted by application threads", 0),
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
    server_status = database.client.admin.command({"serverStatus": 1})
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
        "pages_evicted": cache.get("eviction pages evicted by application threads"),
        "pages_requested": cache.get("pages requested from the cache"),
        "timestamp": latest.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "hit_ratio_interval": latest.get("hit_ratio_interval", 1.0),
        "history": [
            {
                "timestamp": point["timestamp"],
                "hit_ratio": point["hit_ratio_interval"],
                "bytes_in_cache": point["bytes_in_cache"],
                "max_bytes": point["max_bytes"],
            }
            for point in metrics_history[-20:]
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
            detail=f"No documents found in {WORKING_SET_COLLECTION}; seed the Lab 7.2 dataset first.",
        )
    count = min(500, total_docs)
    documents = list(collection.aggregate([
        {"$match": benchmark_filter},
        {"$sample": {"size": count}},
        {"$project": {"_id": 1}},
    ]))
    before_status = database.client.admin.command({"serverStatus": 1})
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
    evictions = (
        cache_after.get("eviction pages evicted by application threads", 0)
        - cache_before.get("eviction pages evicted by application threads", 0)
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
    }


def _detect_indicator_type(value: str, provided: str | None) -> str | None:
    if provided:
        return provided.strip().lower()
    text = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return "sha256"
    if re.fullmatch(r"[0-9a-f]{40}", text):
        return "sha1"
    if re.fullmatch(r"[0-9a-f]{32}", text):
        return "md5"
    if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", text):
        return "ip"
    if text.startswith(("http://", "https://")):
        return "url"
    if "." in text and " " not in text:
        return "domain"
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


def _scan_virustotal_indicator(
    indicator: str,
    indicator_type: str | None = None,
) -> dict[str, Any]:
    indicator = indicator.strip()
    kind = _detect_indicator_type(indicator, indicator_type)
    if kind is None:
        return {
            "error": "Could not determine the indicator type; pass an IP, domain, URL, MD5, SHA-1 or SHA-256."
        }
    database = get_database()
    normalized = normalize_indicator(kind, indicator) or indicator
    cache_id = _vt_cache_id(kind, normalized)
    cached = database["virustotal_cache"].find_one({"_id": cache_id})
    if cached:
        expires_at = cached.get("expires_at")
        if expires_at is None or expires_at > datetime.now(timezone.utc):
            return _vt_cache_response(cached)
    if not settings.virustotal_api_key:
        return {
            "configured": False,
            "error": "VirusTotal API key not configured. Set VIRUSTOTAL_API_KEY in .env.",
        }
    endpoint = virustotal_endpoint(kind, indicator)
    if not endpoint:
        return {"error": f"Unsupported indicator type: {kind}"}
    try:
        with httpx.Client(timeout=15) as client:
            payload = _request_json(
                client,
                "GET",
                endpoint,
                headers={"x-apikey": settings.virustotal_api_key, "Accept": "application/json"},
            )
    except SourceApiError as error:
        if error.status_code == 404:
            _write_vt_cache(database, cache_id, kind, indicator, normalized, "not_found")
            return {
                "found": False,
                "result": "not_found",
                "indicator": indicator,
                "indicator_type": kind,
                "message": "No real VirusTotal result available.",
            }
        if error.status_code == 401:
            return {"error": "VirusTotal API key rejected (401). Check VIRUSTOTAL_API_KEY in .env."}
        if error.status_code == 429:
            return {"error": "VirusTotal rate limit/quota reached; cached results remain available."}
        return {"error": f"VirusTotal scan failed: {error}"}

    record = normalize_virustotal_report(payload, {"indicator_type": kind, "indicator": indicator})
    if not record:
        return {"error": "No real VirusTotal result available."}
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
        expires_at = doc.get("expires_at")
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
