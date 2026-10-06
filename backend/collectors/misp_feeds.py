from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from pymongo import UpdateOne
from pymongo.database import Database

from backend.database import close_database, connect_database
from backend.services.normalization import normalize_indicator, normalize_timestamp

logger = logging.getLogger("aegis.misp_feeds")
MAX_WORKERS = 8
MAX_RETRIES = 3
EVENT_BATCH_SIZE = 500
FEEDS = {
    "circl": "https://www.circl.lu/doc/misp/feed-osint",
    "botvrij": "https://www.botvrij.eu/data/feed-osint",
    "threatfox": "https://threatfox.abuse.ch/downloads/misp",
}


class FeedSyncError(RuntimeError):
    pass


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(60.0, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    return min(60.0, max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds()))
                except (TypeError, ValueError, OverflowError):
                    pass
    return min(30.0, 2.0 ** attempt)


def _get_json(
    client: httpx.Client,
    url: str,
    *,
    sleep=time.sleep,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        response: httpx.Response | None = None
        try:
            response = client.get(url)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_RETRIES:
                    delay = _retry_delay(response, attempt)
                    logger.warning("Retrying %s after HTTP %d in %.1f seconds", url, response.status_code, delay)
                    sleep(delay)
                    continue
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as error:
                raise FeedSyncError(f"Invalid JSON from {url}") from error
        except httpx.TimeoutException as error:
            last_error = error
            if attempt == MAX_RETRIES:
                break
            delay = _retry_delay(response, attempt)
            logger.warning("Retrying timed-out request %s in %.1f seconds", url, delay)
            sleep(delay)
        except httpx.RequestError as error:
            last_error = error
            if attempt == MAX_RETRIES:
                break
            delay = _retry_delay(response, attempt)
            logger.warning("Retrying failed request %s in %.1f seconds", url, delay)
            sleep(delay)
        except httpx.HTTPStatusError as error:
            raise FeedSyncError(f"HTTP {error.response.status_code} from {url}") from error
    raise FeedSyncError(f"Request failed after {MAX_RETRIES + 1} attempts: {url}") from last_error


def _manifest_items(payload: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise FeedSyncError("MISP manifest must be a JSON object")
    items: list[tuple[str, dict[str, Any]]] = []
    for event_uuid, metadata in payload.items():
        if not isinstance(event_uuid, str) or not isinstance(metadata, dict):
            continue
        items.append((event_uuid, metadata))
    return items


def _manifest_fingerprint(metadata: dict[str, Any]) -> str:
    packed = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _unwrap_event(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FeedSyncError("MISP event response must be a JSON object")
    event = payload.get("Event", payload)
    if isinstance(event, dict):
        return event
    response = payload.get("response")
    if isinstance(response, list) and response:
        row = response[0]
        if isinstance(row, dict) and isinstance(row.get("Event", row), dict):
            return row.get("Event", row)
    raise FeedSyncError("MISP event response did not contain an Event object")


def _string_tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(
        value if isinstance(value, str) else value["name"]
        for value in values
        if isinstance(value, str) or (isinstance(value, dict) and isinstance(value.get("name"), str))
    ))


def _galaxy_clusters(event: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    galaxies = event.get("Galaxy")
    if not isinstance(galaxies, list):
        return result
    for galaxy in galaxies:
        if not isinstance(galaxy, dict):
            continue
        clusters = galaxy.get("GalaxyCluster")
        if not isinstance(clusters, list):
            continue
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            result.append({
                "uuid": cluster.get("uuid"),
                "value": cluster.get("value"),
                "type": cluster.get("type") or galaxy.get("type"),
                "description": cluster.get("description"),
                "meta": cluster.get("meta") if isinstance(cluster.get("meta"), dict) else {},
            })
    return result


def _event_attributes(event: dict[str, Any]) -> list[dict[str, Any]]:
    attributes: list[dict[str, Any]] = []
    direct = event.get("Attribute")
    if isinstance(direct, list):
        attributes.extend(item for item in direct if isinstance(item, dict))
    objects = event.get("Object")
    if isinstance(objects, list):
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            nested = obj.get("Attribute")
            if isinstance(nested, list):
                attributes.extend(
                    {**item, "object_name": obj.get("name")}
                    for item in nested if isinstance(item, dict)
                )
    return attributes


def normalize_misp_feed_event(
    payload: Any,
    *,
    feed_name: str,
    feed_url: str,
    event_uuid: str,
    manifest_metadata: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event = _unwrap_event(payload)
    actual_uuid = event.get("uuid")
    if actual_uuid and actual_uuid != event_uuid:
        raise FeedSyncError(f"Event UUID mismatch: manifest {event_uuid}, body {actual_uuid}")

    event_id = f"{feed_name}:{event_uuid}"
    event_timestamp = event.get("timestamp", manifest_metadata.get("timestamp"))
    event_tags = list(dict.fromkeys(
        _string_tags(manifest_metadata.get("Tag")) + _string_tags(event.get("Tag"))
    ))
    attributes = _event_attributes(event)
    galaxies = _galaxy_clusters(event)
    threat_ids = [
        f"{cluster['type']}:{cluster['uuid']}"
        for cluster in galaxies
        if cluster.get("type") and cluster.get("uuid")
    ]
    doc = {
        "_id": event_id,
        "source": "misp",
        "source_feed": feed_url.rstrip("/"),
        "source_feed_name": feed_name,
        "misp_uuid": event_uuid,
        "source_id": f"{feed_name}:{event_uuid}",
        "info": event.get("info") or manifest_metadata.get("info"),
        "date": event.get("date") or manifest_metadata.get("date"),
        "timestamp": normalize_timestamp(event_timestamp),
        "manifest_timestamp": manifest_metadata.get("timestamp"),
        "manifest_fingerprint": _manifest_fingerprint(manifest_metadata),
        "publisher": (event.get("Orgc") or manifest_metadata.get("Orgc") or {}).get("name"),
        "threat_level": event.get("threat_level_id", manifest_metadata.get("threat_level_id")),
        "threat_level_id": event.get("threat_level_id", manifest_metadata.get("threat_level_id")),
        "published": event.get("published") if isinstance(event.get("published"), bool) else None,
        "tags": list(dict.fromkeys(event_tags + _string_tags(event.get("tags")))),
        "galaxies": galaxies,
        "attribute_count": len(attributes),
        "source_reference": urljoin(f"{feed_url.rstrip('/')}/", f"{event_uuid}.json"),
        "feed_synced_at": datetime.now(timezone.utc),
    }
    attribute_docs = []
    event_time = normalize_timestamp(event_timestamp)
    for attribute in attributes:
        attribute_uuid = attribute.get("uuid")
        if not isinstance(attribute_uuid, str) or not attribute_uuid:
            material = json.dumps(attribute, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            attribute_uuid = hashlib.sha256(f"{event_uuid}\n{material}".encode("utf-8")).hexdigest()
        value = attribute.get("value")
        indicator_type = attribute.get("type")
        value_text = str(value) if value is not None else None
        type_text = str(indicator_type) if indicator_type is not None else None
        attr_id = hashlib.sha256(
            f"{feed_url.rstrip('/')}\n{event_uuid}\n{attribute_uuid}".encode("utf-8")
        ).hexdigest()
        attribute_docs.append({
            "_id": f"{feed_name}:{attr_id}",
            "source": "misp",
            "source_feed": feed_url.rstrip("/"),
            "source_feed_name": feed_name,
            "event_uuid": event_uuid,
            "event_id": event_id,
            "attribute_uuid": attribute_uuid,
            "attribute_id": attribute.get("id"),
            "type": type_text,
            "indicator_type": type_text,
            "value": value_text,
            "indicator": value_text,
            "normalized_indicator": normalize_indicator(type_text, value_text),
            "category": attribute.get("category"),
            "to_ids": attribute.get("to_ids") if isinstance(attribute.get("to_ids"), bool) else None,
            "comment": attribute.get("comment"),
            "timestamp": normalize_timestamp(attribute.get("timestamp")) or event_time,
            "first_seen": normalize_timestamp(attribute.get("first_seen")),
            "last_seen": normalize_timestamp(attribute.get("last_seen")),
            "tags": list(dict.fromkeys(event_tags + _string_tags(attribute.get("Tag")))),
            "references": attribute.get("references") if isinstance(attribute.get("references"), list) else [],
            "object_name": attribute.get("object_name"),
            "threat_ids": threat_ids,
            "is_current": True,
        })
    return doc, attribute_docs


def _event_needs_fetch(existing: dict[str, Any] | None, metadata: dict[str, Any]) -> bool:
    if existing is None:
        return True
    return (
        existing.get("manifest_timestamp") != metadata.get("timestamp")
        or existing.get("manifest_fingerprint") != _manifest_fingerprint(metadata)
    )


def _store_event(
    database: Database,
    event_doc: dict[str, Any],
    attribute_docs: list[dict[str, Any]],
) -> None:
    database["events"].update_one(
        {"source_feed": event_doc["source_feed"], "misp_uuid": event_doc["misp_uuid"]},
        {
            "$set": {key: value for key, value in event_doc.items() if key != "_id"},
            "$setOnInsert": {"_id": event_doc["_id"]},
        },
        upsert=True,
    )
    attributes = database["attributes"]
    attributes.update_many(
        {
            "source_feed": event_doc["source_feed"],
            "event_uuid": event_doc["misp_uuid"],
        },
        {"$set": {"is_current": False}},
    )
    if attribute_docs:
        attributes.bulk_write([
            UpdateOne(
                {
                    "source_feed": row["source_feed"],
                    "event_uuid": row["event_uuid"],
                    "attribute_uuid": row["attribute_uuid"],
                },
                {
                    "$set": {key: value for key, value in row.items() if key != "_id"},
                    "$setOnInsert": {"_id": row["_id"]},
                },
                upsert=True,
            )
            for row in attribute_docs
        ], ordered=False)


def sync_feed(
    database: Database,
    feed_name: str,
    feed_url: str,
    *,
    max_events: int | None = None,
    workers: int = MAX_WORKERS,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    if feed_name not in FEEDS or FEEDS[feed_name] != feed_url.rstrip("/"):
        raise ValueError(f"Unsupported MISP feed: {feed_name}")
    if max_events is not None and max_events < 1:
        raise ValueError("max_events must be at least 1")
    if workers < 1 or workers > 32:
        raise ValueError("workers must be between 1 and 32")

    owns_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
    collection = database["events"]
    state_collection = database["cti_sync_state"]
    try:
        manifest_url = urljoin(f"{feed_url.rstrip('/')}/", "manifest.json")
        logger.info("[%s] Fetching manifest %s", feed_name, manifest_url)
        manifest = _manifest_items(_get_json(http, manifest_url))
        if max_events is not None:
            manifest.sort(key=lambda item: float(item[1].get("timestamp", 0) or 0), reverse=True)
            manifest = manifest[:max_events]

        pending: list[tuple[str, dict[str, Any]]] = []
        uuids = [event_uuid for event_uuid, _ in manifest]
        for offset in range(0, len(uuids), EVENT_BATCH_SIZE):
            batch_uuids = uuids[offset:offset + EVENT_BATCH_SIZE]
            existing_rows = collection.find(
                {"source_feed": feed_url.rstrip("/"), "misp_uuid": {"$in": batch_uuids}},
                {"misp_uuid": 1, "manifest_timestamp": 1, "manifest_fingerprint": 1},
            )
            existing_by_uuid = {row["misp_uuid"]: row for row in existing_rows}
            for event_uuid, metadata in manifest[offset:offset + EVENT_BATCH_SIZE]:
                if _event_needs_fetch(existing_by_uuid.get(event_uuid), metadata):
                    pending.append((event_uuid, metadata))

        logger.info(
            "[%s] Manifest contains %d events; %d new or changed events will be fetched",
            feed_name,
            len(manifest),
            len(pending),
        )
        completed = 0
        inserted = 0
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _get_json,
                    http,
                    urljoin(f"{feed_url.rstrip('/')}/", f"{event_uuid}.json"),
                ): (event_uuid, metadata)
                for event_uuid, metadata in pending
            }
            for future in as_completed(futures):
                event_uuid, metadata = futures[future]
                try:
                    body = future.result()
                    event_doc, attribute_docs = normalize_misp_feed_event(
                        body,
                        feed_name=feed_name,
                        feed_url=feed_url,
                        event_uuid=event_uuid,
                        manifest_metadata=metadata,
                    )
                    was_present = collection.find_one(
                        {"source_feed": feed_url.rstrip("/"), "misp_uuid": event_uuid},
                        {"_id": 1},
                    ) is not None
                    _store_event(database, event_doc, attribute_docs)
                    inserted += int(not was_present)
                except Exception as error:
                    failures.append(f"{event_uuid}: {error}")
                    logger.error("[%s] Failed event %s: %s", feed_name, event_uuid, error)
                completed += 1
                if completed % 25 == 0 or completed == len(pending):
                    logger.info("[%s] Progress: %d/%d changed events processed", feed_name, completed, len(pending))

        if failures:
            state_collection.update_one(
                {"_id": feed_name},
                {"$set": {
                    "source_feed": feed_url,
                    "last_error": f"{len(failures)} event(s) failed",
                    "failed_events": failures[:20],
                }},
                upsert=True,
            )
            raise FeedSyncError(f"{feed_name}: {len(failures)} event(s) failed; see logs")

        completed_at = datetime.now(timezone.utc)
        result = {
            "manifest_events": len(manifest),
            "fetched_events": len(pending),
            "inserted_events": inserted,
            "updated_events": len(pending) - inserted,
        }
        state_collection.update_one(
            {"_id": feed_name},
            {"$set": {
                "source_feed": feed_url,
                "last_successful_sync": completed_at,
                "last_manifest_event_count": len(manifest),
                "last_fetched_event_count": len(pending),
                "last_inserted_event_count": inserted,
                "last_error": None,
            }},
            upsert=True,
        )
        logger.info("[%s] Sync complete: %s", feed_name, result)
        return result
    finally:
        if owns_client:
            http.close()


def sync_all_feeds(
    database: Database,
    *,
    max_events: int | None = None,
    workers: int = MAX_WORKERS,
) -> dict[str, dict[str, int]]:
    return {
        name: sync_feed(
            database,
            name,
            url,
            max_events=max_events,
            workers=workers,
        )
        for name, url in FEEDS.items()
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Incrementally sync public MISP OSINT feeds.")
    parser.add_argument("--max-events", type=int, help="Limit each feed to its newest N events for a quick run.")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent event downloads (1-32).")
    parser.add_argument("--feeds", help="Comma-separated feed names to sync (default: all).")
    args = parser.parse_args()
    feed_names = [name.strip() for name in args.feeds.split(",") if name.strip()] if args.feeds else list(FEEDS)
    unknown = [name for name in feed_names if name not in FEEDS]
    if unknown:
        parser.error(f"Unknown feed(s): {', '.join(unknown)}; choose from {', '.join(FEEDS)}")
    database = connect_database()
    failures: list[str] = []
    try:
        for name in feed_names:
            try:
                sync_feed(database, name, FEEDS[name], max_events=args.max_events, workers=args.workers)
            except Exception as error:
                failures.append(name)
                logger.error("[%s] Sync failed; continuing to next feed: %s", name, error)
    finally:
        close_database()
    if failures:
        raise SystemExit(f"Failed feed(s): {', '.join(failures)}")


if __name__ == "__main__":
    main()
