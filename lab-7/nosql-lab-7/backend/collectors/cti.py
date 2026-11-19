from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from pymongo import UpdateOne
from pymongo.database import Database

from backend.config import settings
from backend.services import status as source_status
from backend.services.normalization import (
    normalize_malwarebazaar_records,
    normalize_misp_events,
    normalize_virustotal_report,
    virustotal_endpoint,
)

logger = logging.getLogger("aegis.cti")
MISP_PAGE_SIZE = 100
MALWAREBAZAAR_API_URL = "https://mb-api.abuse.ch/api/v1/"
BULK_WRITE_SIZE = 500


class SourceApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _request_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException as error:
        raise SourceApiError("request timed out") from error
    except httpx.RequestError as error:
        raise SourceApiError(f"network failure: {error}") from error
    if response.status_code < 200 or response.status_code >= 300:
        raise SourceApiError(
            f"HTTP {response.status_code}",
            status_code=response.status_code,
            retry_after=response.headers.get("Retry-After"),
        )
    try:
        return response.json()
    except ValueError as error:
        raise SourceApiError("invalid JSON response", status_code=response.status_code) from error


def _record_id(source: str, source_id: str) -> str:
    import hashlib

    key = f"{source}\n{source_id}".encode("utf-8")
    return f"{source}:{hashlib.sha256(key).hexdigest()}"


def store_records(database: Database, records: list[dict[str, Any]]) -> dict[str, int]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        source = record.get("source")
        source_id = record.get("source_id")
        if not source or not source_id:
            continue
        if not record.get("indicator_type") and source != "misp":
            continue
        unique[(source, str(source_id))] = record

    values = list(unique.values())
    inserted = 0
    collection = database["events"]
    for offset in range(0, len(values), BULK_WRITE_SIZE):
        batch = values[offset:offset + BULK_WRITE_SIZE]
        operations = []
        for record in batch:
            fields = {key: value for key, value in record.items() if key != "_id"}
            operations.append(UpdateOne(
                {"source": record["source"], "source_id": record["source_id"]},
                {
                    "$set": fields,
                    "$setOnInsert": {
                        "_id": _record_id(record["source"], str(record["source_id"]))
                    },
                },
                upsert=True,
            ))
        result = collection.bulk_write(operations, ordered=False)
        inserted += result.upserted_count
    return {
        "records_received": len(records),
        "records_inserted": inserted,
        "duplicates_skipped": max(0, len(records) - inserted),
    }


def _record_source_result(name: str, database: Database, records: list[dict[str, Any]]) -> dict[str, int]:
    result = store_records(database, records)
    source_status.finish_fetch(
        name.lower(),
        result["records_received"],
        result["records_inserted"],
        result["duplicates_skipped"],
    )
    logger.info(
        "[%s] Records received: %d; new records inserted: %d; duplicates skipped: %d",
        name,
        result["records_received"],
        result["records_inserted"],
        result["duplicates_skipped"],
    )
    return result


def collect_misp(database: Database, client: httpx.Client | None = None) -> dict[str, int]:
    if not settings.misp_api_url or not settings.misp_api_key:
        raise SourceApiError("MISP_API_URL and MISP_API_KEY are required")
    parsed = urlparse(settings.misp_api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query:
        raise SourceApiError("MISP_API_URL must be an HTTP(S) base URL without credentials or query parameters")

    logger.info("[MISP] Fetch started")
    state = database["cti_fetch_state"].find_one({"_id": "misp"})
    if state and state.get("last_successful_timestamp"):
        last_success = state["last_successful_timestamp"]
        if isinstance(last_success, datetime):
            since = max(0, int(last_success.timestamp()) - 120)
        else:
            since = max(0, int(datetime.fromisoformat(last_success).timestamp()) - 120)
    else:
        since = settings.misp_initial_lookback or "7d"

    owns_client = client is None
    http = client or httpx.Client(timeout=15)
    try:
        records = []
        page = 1
        endpoint = f"{settings.misp_api_url.rstrip('/')}/events/restSearch"
        while True:
            query: dict[str, Any] = {
                "returnFormat": "json",
                "published": True,
                "limit": MISP_PAGE_SIZE,
                "page": page,
                "timestamp": since,
            }
            payload = _request_json(
                http,
                "POST",
                endpoint,
                headers={
                    "Authorization": settings.misp_api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=query,
            )
            if not isinstance(payload, dict) or "response" not in payload or payload.get("errors"):
                raise SourceApiError("unexpected MISP REST search response")
            response = payload["response"]
            response_rows = response if isinstance(response, list) else [response] if isinstance(response, dict) else []
            events = [row.get("Event", row) for row in response_rows if isinstance(row, dict)]
            records.extend(normalize_misp_events({"response": events}))
            if len(events) < MISP_PAGE_SIZE:
                break
            page += 1

        result = _record_source_result("MISP", database, records)
        database["cti_fetch_state"].update_one(
            {"_id": "misp"},
            {"$set": {"last_successful_timestamp": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return result
    finally:
        if owns_client:
            http.close()


def collect_malwarebazaar(database: Database, client: httpx.Client | None = None) -> dict[str, int]:
    if not settings.malwarebazaar_auth_key:
        raise SourceApiError("MALWAREBAZAAR_AUTH_KEY is required")
    logger.info("[MalwareBazaar] Fetch started")
    owns_client = client is None
    http = client or httpx.Client(timeout=15)
    try:
        payload = _request_json(
            http,
            "POST",
            MALWAREBAZAAR_API_URL,
            headers={
                "Auth-Key": settings.malwarebazaar_auth_key,
                "Accept": "application/json",
            },
            data={"query": "recent_detections"},
        )
        if not isinstance(payload, dict) or payload.get("query_status") not in {"ok", "no_results"}:
            status = payload.get("query_status", "missing") if isinstance(payload, dict) else "invalid"
            raise SourceApiError(f"API query status: {status}")
        if payload["query_status"] == "ok" and not isinstance(payload.get("data"), list):
            raise SourceApiError("response is missing its data array")
        return _record_source_result(
            "MalwareBazaar",
            database,
            normalize_malwarebazaar_records(payload),
        )
    finally:
        if owns_client:
            http.close()


def _virustotal_candidate(database: Database) -> dict[str, Any] | None:
    types = ["sha256", "sha1", "md5", "domain", "hostname", "ip", "ip-src", "ip-dst", "ipv4", "ipv6", "url"]
    return database["attributes"].find_one(
        {
            "source": "misp",
            "is_current": {"$ne": False},
            "indicator_type": {"$in": types},
            "normalized_indicator": {"$type": "string"},
            "vt_lookup_checked_at": {"$exists": False},
        },
        sort=[("first_seen", 1), ("_id", 1)],
    )


def _virustotal_cache_id(candidate: dict[str, Any]) -> str:
    import hashlib

    key = f"{candidate['indicator_type']}\n{candidate['normalized_indicator']}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _store_virustotal_cache(
    database: Database,
    candidate: dict[str, Any],
    result: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc)
    cache_id = _virustotal_cache_id(candidate)
    cache_doc: dict[str, Any] = {
        "_id": cache_id,
        "source": "virustotal",
        "indicator_type": candidate["indicator_type"],
        "indicator": candidate.get("indicator") or candidate.get("value"),
        "normalized_indicator": candidate["normalized_indicator"],
        "result": result,
        "checked_at": checked_at,
    }
    if payload is not None:
        cache_doc["response"] = payload
    database["virustotal_cache"].update_one(
        {"_id": cache_id},
        {
            "$set": {key: value for key, value in cache_doc.items() if key != "_id"},
            "$setOnInsert": {"_id": cache_id},
        },
        upsert=True,
    )
    database["attributes"].update_one(
        {"_id": candidate["_id"]},
        {"$set": {
            "vt_lookup_checked_at": checked_at,
            "vt_lookup_result": result,
            "vt_cache_id": cache_id,
        }},
    )
    return cache_doc


def collect_virustotal(database: Database, client: httpx.Client | None = None) -> dict[str, int]:
    if not settings.virustotal_api_key:
        raise SourceApiError("VIRUSTOTAL_API_KEY is required")
    candidate = _virustotal_candidate(database)
    if not candidate:
        logger.info("[VirusTotal] No eligible new indicators are waiting for a lookup")
        source_status.finish_fetch("virustotal", 0, 0, 0)
        return {"records_received": 0, "records_inserted": 0, "duplicates_skipped": 0}
    cached = database["virustotal_cache"].find_one({
        "_id": _virustotal_cache_id(candidate),
    })
    if cached:
        database["attributes"].update_one(
            {"_id": candidate["_id"]},
            {"$set": {
                "vt_lookup_checked_at": cached["checked_at"],
                "vt_lookup_result": cached["result"],
                "vt_cache_id": cached["_id"],
            }},
        )
        source_status.finish_fetch("virustotal", 0, 0, 0)
        logger.info("[VirusTotal] Reused cached report for %s", candidate["indicator_type"])
        return {"records_received": 0, "records_inserted": 0, "duplicates_skipped": 0}

    indicator = candidate.get("indicator") or candidate.get("value")
    endpoint = virustotal_endpoint(candidate["indicator_type"], indicator)
    if not endpoint:
        _store_virustotal_cache(database, candidate, "unsupported_type")
        return {"records_received": 0, "records_inserted": 0, "duplicates_skipped": 0}

    logger.info("[VirusTotal] Fetch started (one %s indicator per cycle)", candidate["source"])
    owns_client = client is None
    http = client or httpx.Client(timeout=15)
    try:
        try:
            payload = _request_json(
                http,
                "GET",
                endpoint,
                headers={"x-apikey": settings.virustotal_api_key, "Accept": "application/json"},
            )
        except SourceApiError as error:
            if error.status_code == 404:
                _store_virustotal_cache(database, candidate, "not_found")
                logger.info("[VirusTotal] Indicator report not found (404); no record created")
                source_status.finish_fetch("virustotal", 0, 0, 0)
                return {"records_received": 0, "records_inserted": 0, "duplicates_skipped": 0}
            raise
        record = normalize_virustotal_report(payload, candidate)
        if not record:
            raise SourceApiError("VirusTotal response did not contain a usable report object")
        result = _record_source_result("VirusTotal", database, [record])
        _store_virustotal_cache(database, candidate, "found", payload)
        return result
    finally:
        if owns_client:
            http.close()


def _run_collector(name: str, database: Database, collector: Callable[[Database], dict[str, int]]) -> None:
    source_status.start_fetch(name.lower())
    try:
        collector(database)
    except Exception as error:
        message = str(error)
        source_status.fail_fetch(name.lower(), message)
        if isinstance(error, SourceApiError) and error.status_code == 429:
            logger.error("[%s] API unavailable: 429; respecting Retry-After=%s. Skipping this cycle.", name, error.retry_after or "not provided")
        else:
            logger.error("[%s] %s. Skipping this cycle; no fallback records will be generated.", name, message)


def start_collectors(scheduler: Any, database: Database) -> None:
    source_configs: list[tuple[str, str, bool, int, Callable[[Database], dict[str, int]]]] = [
        (
            "virustotal",
            "VirusTotal",
            bool(settings.virustotal_api_key),
            max(900, settings.virustotal_poll_interval_seconds),
            collect_virustotal,
        ),
    ]
    for name, label, configured, interval, collector in source_configs:
        source_status.set_configured(name, configured)
        if not configured:
            logger.warning("[%s] Collector disabled: missing required API configuration.", label)
            continue
        scheduler.add_job(
            _run_collector,
            "interval",
            seconds=interval,
            args=[label, database, collector],
            id=f"cti-{name}",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
            max_instances=1,
            coalesce=True,
        )
