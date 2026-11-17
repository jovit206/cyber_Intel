"""Read-only structured retrieval over the platform's existing CTI collections.

Threat records remain in their source collections. This module normalizes matching
rows into evidence chunks for the existing RAG pipeline; it does not create or
update MongoDB collections.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from pymongo.database import Database


MAX_TERMS = 12
MAX_EVIDENCE_CHARS = 4_000

# Collection, searchable fields, evidence fields, and source-reference fields.
# Keep this aligned with the collectors and the Chapter 18 ThreatRecord schema.
_SOURCES = (
    ("events", ("info", "tags", "threat_level", "source_feed_name", "source"),
     ("info", "tags", "threat_level", "source_feed_name", "timestamp"), ("_id", "source_id")),
    ("attributes", ("indicator", "normalized_indicator", "indicator_type", "category", "comment"),
     ("indicator", "normalized_indicator", "indicator_type", "category", "comment", "source_feed_name", "timestamp"), ("_id", "event_id")),
    ("threats", ("name", "aliases", "description", "type", "country", "meta.platform"),
     ("name", "aliases", "description", "type", "country", "source_url"), ("_id", "cluster_uuid")),
    ("reports", ("title", "name", "description", "source_repository", "source_pdf"),
     ("title", "name", "description", "source_repository", "source_pdf"), ("_id", "report_id")),
    ("passages", ("text", "entities", "threat_ids", "source_pdf"),
     ("text", "entities", "threat_ids", "source_pdf", "page", "report_id"), ("_id", "report_id")),
)

_CHAPTER18_SEARCH_FIELDS = (
    "threat_id", "source", "from_source", "threat_type", "indicator", "target", "description",
)
_PLATFORM_FIELDS = ("platform", "platforms", "affected_platforms", "operating_system", "target", "meta.platform")
_TEXT_FIELDS = ("info", "tags", "text", "description", "indicator", "normalized_indicator", "name", "aliases", "entities", "threat_ids", "target", "source_reference", "source_pdf")


def _terms(question: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[\w.-]{2,}", question.strip(), flags=re.UNICODE)))[:MAX_TERMS]


def _regex_any(fields: tuple[str, ...], value: str) -> dict[str, Any]:
    escaped = re.escape(value.strip())
    return {"$or": [{field: {"$regex": escaped, "$options": "i"}} for field in fields]}


def _text_query(terms: list[str], fields: tuple[str, ...]) -> dict[str, Any]:
    if not terms:
        return {"_id": {"$exists": False}}
    return {"$or": [
        {field: {"$regex": re.escape(term), "$options": "i"}}
        for term in terms for field in fields
    ]}


def _combine(*conditions: dict[str, Any]) -> dict[str, Any]:
    active = [condition for condition in conditions if condition]
    if not active:
        return {}
    if len(active) == 1:
        return active[0]
    return {"$and": active}


def build_source_query(
    collection_name: str,
    terms: list[str],
    *,
    platform: str | None = None,
    threat_status: str | None = None,
) -> dict[str, Any]:
    """Build a bounded, escaped query using fields supported by each source."""
    if collection_name == "full_threats":
        conditions = [
            {"provenance": "imported"},
            _text_query(terms, _CHAPTER18_SEARCH_FIELDS),
        ]
        if platform:
            conditions.append(_regex_any(_PLATFORM_FIELDS + ("description",), platform))
        if threat_status:
            conditions.append({"status": threat_status.strip()})
        return _combine(*conditions)

    if threat_status:
        # Only Chapter 18's imported ThreatRecord schema defines a threat status.
        # Don't misinterpret feed freshness, attribute currency, or report currentness.
        return {"_id": {"$exists": False}}

    fields = next((entry[1] for entry in _SOURCES if entry[0] == collection_name), None)
    if fields is None:
        return {"_id": {"$exists": False}}
    conditions = [_text_query(terms, fields)]
    if collection_name == "attributes":
        conditions.append({"is_current": {"$ne": False}})
    if collection_name == "passages":
        conditions.append({"is_current": {"$ne": False}})
    if platform:
        # Match explicit platform metadata/targets and textual evidence fields;
        # never fabricate a platform classification from unrelated records.
        platform_fields = _PLATFORM_FIELDS + tuple(field for field in fields if field in _TEXT_FIELDS)
        conditions.append(_regex_any(platform_fields, platform))
    return _combine(*conditions)


def _evidence_text(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    values = {field: row[field] for field in fields if field in row and row[field] is not None}
    rendered = json.dumps(values, ensure_ascii=False, default=str, separators=(",", ": "))
    return rendered[:MAX_EVIDENCE_CHARS]


def _as_evidence(collection_name: str, row: dict[str, Any], evidence_fields: tuple[str, ...], id_fields: tuple[str, ...]) -> dict[str, Any]:
    source_id = next((row.get(field) for field in id_fields if row.get(field) is not None), "record")
    reference = row.get("source_pdf") or row.get("source_url") or row.get("source_reference") or row.get("report_id")
    source = row.get("source_feed_name") or row.get("source") or row.get("from_source") or collection_name
    return {
        "text": _evidence_text(row, evidence_fields),
        "source": str(source),
        "chunk_id": f"{collection_name}:{source_id}",
        "source_reference": str(reference) if reference else f"mongodb:{collection_name}/{source_id}",
        "evidence_type": collection_name,
        "score": None,
    }


def _chapter18_database(database: Database) -> Database | None:
    name = os.getenv("LAB18_MONGO_DATABASE", "threat_intel_lab18").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name) or name == database.name:
        return None
    return database.client[name]


def retrieve_structured_threat_evidence(
    database: Database,
    question: str,
    *,
    limit: int = 5,
    platform: str | None = None,
    threat_status: str | None = None,
) -> dict[str, Any]:
    """Search existing CTI records and return evidence plus sanitized availability.

    Each source is independently bounded and failure-isolated. Chapter 18 records
    are read from their configured, separate database only when that collection
    already exists. No source is initialized or mutated here.
    """
    terms = _terms(question)
    if not terms:
        return {"items": [], "sources": [], "unavailable_sources": []}
    limit = min(max(int(limit), 1), 10)
    selected_sources = _SOURCES
    if threat_status:
        selected_sources = ()

    items: list[dict[str, Any]] = []
    sources: list[str] = []
    unavailable: list[str] = []
    for name, search_fields, evidence_fields, id_fields in selected_sources:
        try:
            collection = database[name]
            cursor = collection.find(
                build_source_query(name, terms, platform=platform, threat_status=threat_status),
                {field: 1 for field in set(search_fields + evidence_fields + id_fields + ("source", "source_feed_name", "source_reference", "source_url", "report_id"))},
            )
            rows = cursor.max_time_ms(3_000).limit(limit)
            found = [_as_evidence(name, row, evidence_fields, id_fields) for row in rows]
            if found:
                items.extend(found)
                sources.append(name)
        except Exception as error:  # One optional collection must not break RAG.
            unavailable.append(f"{name}:{type(error).__name__}")

    chapter18 = _chapter18_database(database)
    if chapter18 is not None:
        try:
            names = set(chapter18.list_collection_names())
            if "full_threats" in names:
                projection = {field: 1 for field in set(_CHAPTER18_SEARCH_FIELDS + _PLATFORM_FIELDS + ("status", "timestamp", "severity", "confidence", "provenance", "source_reference"))}
                cursor = chapter18["full_threats"].find(
                    build_source_query("full_threats", terms, platform=platform, threat_status=threat_status),
                    projection,
                )
                rows = cursor.max_time_ms(3_000).limit(limit)
                found = [_as_evidence("full_threats", row, tuple(projection), ("threat_id", "_id")) for row in rows]
                if found:
                    items.extend(found)
                    sources.append("threat_intel_lab18.full_threats")
        except Exception as error:
            unavailable.append(f"threat_intel_lab18.full_threats:{type(error).__name__}")

    # Stable de-duplication and a hard total limit bound prompt size.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if item["chunk_id"] not in seen:
            seen.add(item["chunk_id"])
            unique.append(item)
        if len(unique) >= limit:
            break
    return {"items": unique, "sources": sources, "unavailable_sources": unavailable}
