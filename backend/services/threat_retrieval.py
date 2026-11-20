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
    ("attributes", ("indicator", "normalized_indicator", "value", "references", "indicator_type", "category", "comment"),
     ("indicator", "normalized_indicator", "value", "references", "indicator_type", "category", "comment", "source_feed_name", "timestamp"), ("_id", "event_id")),
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


_QUESTION_STOP_TERMS = frozenset({
    "a", "about", "all", "also", "an", "and", "any", "are", "as", "at", "be", "been", "being", "by",
    "can", "could", "did", "do", "does", "doing", "for", "from", "had", "has", "have", "how", "i",
    "if", "in", "into", "is", "it", "its", "me", "my", "no", "not", "of", "on", "or", "our", "please",
    "should", "so", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "us", "was", "we", "were", "what", "when", "where", "which", "who", "whom", "whose",
    "why", "will", "with", "would", "you", "your",
    # Generic request phrasing in this application; never a discriminating CTI term on its own.
    "tell", "show", "list", "give", "explain", "describe", "summarize", "summarise", "find", "know",
    "platform", "application", "app", "recorded", "stored", "available", "mention", "mentions",
    "mentioned", "related", "associated", "threat", "threats", "today", "tomorrow", "yesterday",
})


# Ranking words ("top", "latest") describe which records to return, not what they contain. Searching for them as
# text matches unrelated values such as ".top" domains, so they are removed from the search terms.
_RANKING_TERMS = frozenset({
    "top", "latest", "recent", "newest", "current", "currently", "biggest", "main", "major", "most", "active",
    "now", "new", "important", "significant", "notable", "key", "seen", "observed", "known", "ingested",
})
# The dashboard's Top Threats view (GET /api/threats/top) shows the newest events from these live CTI sources.
_LIVE_CTI_SOURCES = ("misp", "virustotal")
# Evidence for those events. VirusTotal events have no "info"; their facts are the indicator, verdict, severity and
# detection counts. Ordered so the most useful fields survive prompt truncation; missing (None) fields are skipped.
_LATEST_EVIDENCE_FIELDS = (
    "info", "indicator", "indicator_type", "verdict", "severity", "threat_level", "malicious_count",
    "suspicious_count", "reputation", "source", "source_feed_name", "timestamp", "tags", "categories",
)


def _terms(question: str) -> list[str]:
    # Exact vulnerability identifiers are stronger than prose around them. Search
    # only those IDs so generic question words cannot outrank the requested CVE.
    cve_ids = re.findall(r"\bCVE-\d{4}-\d{4,}\b", question, flags=re.IGNORECASE)
    if cve_ids:
        return list(dict.fromkeys(cve_ids))[:MAX_TERMS]
    # Terms are matched as case-insensitive substrings, so function words such as
    # "is" or "the" would match nearly every record and turn any question (even an
    # unrelated one) into apparent evidence. Search only the discriminating words.
    words = re.findall(r"[\w.-]{2,}", question.strip(), flags=re.UNICODE)
    terms = [word for word in words
             if word.casefold() not in _QUESTION_STOP_TERMS and word.casefold() not in _RANKING_TERMS]
    return list(dict.fromkeys(terms))[:MAX_TERMS]


def _asks_for_ranking(question: str) -> bool:
    """"top/latest threats" or a bare "what are the threats?": a request for the platform's current threats."""
    return (any(word.casefold() in _RANKING_TERMS for word in re.findall(r"[\w-]+", question))
            or bool(re.search(r"\b(are|show|list|give)\b(?:\s+me)?(?:\s+all)?(?:\s+the)?\s+threats?\s*[?.!]*\s*$", question, re.I)))


def latest_threat_evidence(database: Database, limit: int = 5) -> dict[str, Any]:
    """The newest live CTI events, ranked exactly like the dashboard's Top Threats view."""
    name, _, _, id_fields = _SOURCES[0]
    evidence_fields = _LATEST_EVIDENCE_FIELDS
    try:
        rows = database[name].find(
            {"source": {"$in": list(_LIVE_CTI_SOURCES)}},
            {field: 1 for field in set(evidence_fields + id_fields + ("source_reference", "source_url", "report_id"))},
        ).sort([("timestamp", -1), ("first_seen", -1), ("_id", 1)]).max_time_ms(3_000).limit(limit)
        items = [_as_evidence(name, row, evidence_fields, id_fields) for row in rows]
    except Exception as error:
        return {"items": [], "sources": [], "unavailable_sources": [f"{name}:{type(error).__name__}"]}
    return {"items": items, "sources": [name] if items else [], "unavailable_sources": [], "ranking": True}


def _regex_any(fields: tuple[str, ...], value: str) -> dict[str, Any]:
    escaped = re.escape(value.strip())
    return {"$or": [{field: {"$regex": escaped, "$options": "i"}} for field in fields]}


def _text_query(terms: list[str], fields: tuple[str, ...]) -> dict[str, Any]:
    if not terms:
        return {"_id": {"$exists": False}}
    # Anchor at a word start: "ltd" must not match inside "9sltd81l.example", while
    # prefixes/plurals ("ransomware-as-a-service", "LockBit3") and dotted indicator parts still match.
    return {"$or": [
        {field: {"$regex": r"\b" + re.escape(term), "$options": "i"}}
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
    if collection_name == "attributes" and terms and all(
        re.fullmatch(r"CVE-\d{4}-\d{4,}", term, flags=re.IGNORECASE) for term in terms
    ):
        # MISP CVE attributes are stored as exact indicator values. Use the
        # indexed identity fields instead of a multi-field regex scan over the
        # large attributes collection.
        # Only normalized_indicator is indexed (normalized_indicator_1_is_current_1); an $or that also matched the
        # unindexed raw indicator field forced a scan of every attribute and hit the 3 s query limit. Stored CVE
        # attributes carry the upper-case identifier in normalized_indicator.
        exact_ids = list(dict.fromkeys(term.upper() for term in terms))
        return _combine(
            {"normalized_indicator": {"$in": exact_ids}},
            {"is_current": {"$ne": False}},
        )
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
    source = row.get("source_feed_name") or row.get("from_source") or row.get("source") or collection_name
    # Attribute citations carry their originating feed so an indicator is traceable to the exact source feed.
    # Stored attribute ids are usually already feed-qualified ("botvrij:<hash>"); never qualify twice.
    feed = row.get("source_feed_name") if collection_name == "attributes" else None
    if feed and not str(source_id).startswith(f"{feed}:"):
        chunk_id = f"{collection_name}:{feed}:{source_id}"
    else:
        chunk_id = f"{collection_name}:{source_id}"
    return {
        "text": _evidence_text(row, evidence_fields),
        "source": str(source),
        "chunk_id": chunk_id,
        "source_reference": str(reference) if reference else f"mongodb:{collection_name}/{source_id}",
        "evidence_type": collection_name,
        "score": None,
    }


def _chapter18_database(database: Database) -> Database | None:
    name = os.getenv("LAB18_MONGO_DATABASE", "threat_intel_lab18").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name) or name == database.name:
        return None
    return database.client[name]


_IOC_INTENT = re.compile(r"\biocs?\b|indicators? of compromise", flags=re.IGNORECASE)
_IOC_TYPE_PATTERN = r"^(?:ip(?:v[46])?(?:[-:/|].*)?|domain(?:\|ip)?|hostname|host|url|uri|md5|sha1|sha256|sha512|email(?:[-_].*)?|filename|filepath|file-path)$"
_IOC_STOP_TERMS = {
    "what", "which", "who", "is", "are", "was", "were", "the", "a", "an", "of", "and", "or",
    "to", "in", "on", "for", "with", "from", "me", "show", "shown", "list", "give", "provide",
    "concrete", "value", "values", "present", "available", "explicit", "explicitly", "evidence",
    "source", "sources", "record", "records", "retrieved", "ioc", "iocs", "indicator", "indicators",
    "compromise", "cite", "only", "any", "these", "those", "about", "please",
}


def _indicator_source_query(
    collection_name: str,
    context_terms: list[str],
    *,
    platform: str | None = None,
    threat_status: str | None = None,
) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = []
    if collection_name == "attributes":
        conditions.extend([
            {"indicator": {"$exists": True}},
            {"indicator": {"$ne": None}},
            {"indicator": {"$ne": ""}},
            {"is_current": {"$ne": False}},
        ])
        conditions.append({"$or": [
            {"indicator_type": {"$regex": _IOC_TYPE_PATTERN, "$options": "i"}},
            {"category": {"$regex": _IOC_TYPE_PATTERN, "$options": "i"}},
        ]})
        if context_terms:
            conditions.append(_text_query(context_terms, ("indicator", "normalized_indicator", "indicator_type", "category", "comment", "source_feed_name")))
        if platform:
            conditions.append(_regex_any(("platform", "platforms", "affected_platforms", "operating_system", "tags", "comment"), platform))
    else:
        conditions.extend([
            {"provenance": "imported"},
            {"indicator": {"$exists": True}},
            {"indicator": {"$ne": None}},
            {"indicator": {"$ne": ""}},
        ])
        conditions.append({"threat_type": {"$regex": _IOC_TYPE_PATTERN, "$options": "i"}})
        if context_terms:
            conditions.append(_text_query(context_terms, _CHAPTER18_SEARCH_FIELDS + ("source_feed_name",)))
        if threat_status:
            conditions.append({"status": threat_status.strip()})
        if platform:
            conditions.append(_regex_any(("platform", "platforms", "affected_platforms", "operating_system", "target"), platform))
    return _combine(*conditions)


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
    limit = min(max(int(limit), 1), 10)
    if not terms and not _IOC_INTENT.search(question):
        if _asks_for_ranking(question) and not platform and not threat_status:
            # "What are the top/latest threats?" names no topic: answer with the dashboard's Top Threats records.
            return latest_threat_evidence(database, limit)
        return {"items": [], "sources": [], "unavailable_sources": []}
    is_ioc_query = bool(_IOC_INTENT.search(question))
    ioc_context_terms = [term for term in terms if term.casefold() not in _IOC_STOP_TERMS] if is_ioc_query else []
    selected_sources = _SOURCES
    if threat_status:
        selected_sources = ()
    elif is_ioc_query:
        selected_sources = tuple(source for source in _SOURCES if source[0] == "attributes")

    items: list[dict[str, Any]] = []
    sources: list[str] = []
    unavailable: list[str] = []
    for name, search_fields, evidence_fields, id_fields in selected_sources:
        try:
            collection = database[name]
            source_query = (
                _indicator_source_query(name, ioc_context_terms, platform=platform, threat_status=threat_status)
                if is_ioc_query
                else build_source_query(name, terms, platform=platform, threat_status=threat_status)
            )
            cursor = collection.find(
                source_query,
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
                chapter18_query = (
                    _indicator_source_query("full_threats", ioc_context_terms, platform=platform, threat_status=threat_status)
                    if is_ioc_query
                    else build_source_query("full_threats", terms, platform=platform, threat_status=threat_status)
                )
                cursor = chapter18["full_threats"].find(chapter18_query, projection)
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
    if not unique and not is_ioc_query and _asks_for_ranking(question) and not platform and not threat_status:
        # "wahat is the top threat": the leftover word (often a typo) matched nothing. Show the current Top Threats and
        # report the unmatched words, so a real topic with no records is never presented as matching.
        latest = latest_threat_evidence(database, limit)
        if latest["items"]:
            return {**latest, "unmatched_terms": terms}
    return {"items": unique, "sources": sources, "unavailable_sources": unavailable}
