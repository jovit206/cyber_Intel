"""Cross-source common data: values that appear in 2+ distinct CTI source feeds.

Reads `events` (+ `attributes`), writes only compact references to
`cross_source_matches` and a summary doc to `cross_source_meta`.
Not duplicate detection: same value in different feeds is common data, not a duplicate record.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Any
from urllib.parse import urlsplit

from pymongo.database import Database

logger = logging.getLogger("aegis.cross_source")

ALGORITHM_VERSION = "v1"
MATCHES = "cross_source_matches"
META = "cross_source_meta"
MAX_RECORDS_PER_SOURCE = 100
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
GALAXY_TAG_RE = re.compile(r'^misp-galaxy:([^=]+)="(.+)"$')

SOURCE_NAMES = {
    "circl": "CIRCL MISP",
    "botvrij": "Botvrij.eu",
    "threatfox": "ThreatFox",
}
# MISP attribute type -> specific type; specific type -> category
ATTRIBUTE_TYPES = {
    "ip-src": "ip", "ip-dst": "ip", "domain": "domain", "hostname": "domain", "url": "url",
    "md5": "hash", "sha1": "hash", "sha256": "hash", "sha512": "hash",
    "email-src": "email", "email-dst": "email", "email": "email",
    "vulnerability": "cve",
}
CATEGORY_OF = {
    "cve": "cve", "malware": "malware", "actor": "actor", "tool": "tool", "galaxy": "galaxy",
    "ip": "ioc", "domain": "ioc", "url": "ioc", "hash": "ioc", "email": "ioc",
}
CATEGORIES = ["cve", "malware", "actor", "ioc", "tool", "galaxy"]
_lock = threading.Lock()


def source_name(key: str, url: str | None = None) -> str:
    if key in SOURCE_NAMES:
        return SOURCE_NAMES[key]
    host = urlsplit(url or "").hostname or key
    return host.removeprefix("www.")


def galaxy_kind(cluster_type: str) -> str:
    t = (cluster_type or "").lower()
    if "actor" in t or "intrusion-set" in t:
        return "actor"
    if "tool" in t:
        return "tool"
    if any(w in t for w in ("malware", "ransomware", "botnet", "malpedia")):
        return "malware"
    return "galaxy"


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _attribute_pipeline() -> list[dict[str, Any]]:
    branches = [{"case": {"$eq": ["$indicator_type", k]}, "then": v} for k, v in ATTRIBUTE_TYPES.items()]
    value = {"$trim": {"input": {"$toString": "$normalized_indicator"}}}
    return [
        {"$match": {"is_current": True, "source_feed": {"$type": "string"},
                    "normalized_indicator": {"$type": "string", "$ne": ""},
                    "indicator_type": {"$in": list(ATTRIBUTE_TYPES)}}},
        {"$project": {"event_uuid": 1, "source_feed": 1, "indicator_type": 1,
                      "s": {"$ifNull": ["$source_feed_name", "$source_feed"]},
                      "v": value}},
        {"$addFields": {"t": {"$switch": {"branches": branches, "default": "other"}}}},
        # lowercase except urls (path is case-sensitive; host already lowered at ingest); CVEs upper
        {"$addFields": {"v": {"$switch": {"branches": [
            {"case": {"$eq": ["$t", "url"]}, "then": "$v"},
            {"case": {"$eq": ["$t", "cve"]}, "then": {"$toUpper": "$v"}},
            {"case": {"$eq": ["$t", "domain"]},
             "then": {"$rtrim": {"input": {"$toLower": "$v"}, "chars": "."}}},
        ], "default": {"$toLower": "$v"}}}}},
        {"$group": {"_id": {"t": "$t", "v": "$v", "s": "$s"},
                    "url": {"$first": "$source_feed"},
                    "ids": {"$addToSet": "$event_uuid"},
                    "types": {"$addToSet": "$indicator_type"}}},
        {"$group": {"_id": {"t": "$_id.t", "v": "$_id.v"},
                    "srcs": {"$push": {"s": "$_id.s", "url": "$url", "ids": "$ids", "types": "$types"}}}},
        # CVEs may also match via events (info/tags), so keep single-source CVEs for merging
        {"$match": {"$or": [{"_id.t": "cve"}, {"$expr": {"$gte": [{"$size": "$srcs"}, 2]}}]}},
    ]


def rebuild(database: Database) -> dict[str, Any]:
    """Full recompute over the whole dataset. Safe to call concurrently (second caller waits out)."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "rebuild already running"}
    try:
        return _rebuild(database)
    finally:
        _lock.release()


def _rebuild(database: Database) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    # (type, key) -> {"display", "src": {slug: {"ids": set, "fields": set}}}
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    source_urls: dict[str, str] = {}

    def add(kind: str, key: str, display: str, src: str, rid: str, field: str) -> None:
        entry = acc.setdefault((kind, key), {"display": display, "src": {}})
        slot = entry["src"].setdefault(src, {"ids": set(), "fields": set()})
        slot["ids"].add(rid)
        slot["fields"].add(field)

    for row in database["attributes"].aggregate(_attribute_pipeline(), allowDiskUse=True):
        kind, value = row["_id"]["t"], row["_id"]["v"]
        if kind == "cve":
            if not CVE_RE.fullmatch(value):
                continue
        for s in row["srcs"]:
            source_urls.setdefault(s["s"], s["url"])
            for rid in s["ids"]:
                add(kind, value, value, s["s"], rid, "attributes." + "/".join(sorted(s["types"])))

    events_analyzed = 0
    cursor = database["events"].find(
        {"source_feed": {"$type": "string"}},
        {"source_feed": 1, "source_feed_name": 1, "misp_uuid": 1, "info": 1, "tags": 1, "galaxies": 1},
    )
    for ev in cursor:
        events_analyzed += 1
        src = ev.get("source_feed_name") or ev["source_feed"]
        source_urls.setdefault(src, ev["source_feed"])
        rid = ev.get("misp_uuid") or str(ev["_id"])
        tags = [t for t in (ev.get("tags") or []) if isinstance(t, str)]
        for text, field in [(ev.get("info") or "", "events.info")] + [(t, "events.tags") for t in tags]:
            for m in CVE_RE.findall(str(text)):
                add("cve", m.upper(), m.upper(), src, rid, field)
        clusters = [(g.get("type"), g.get("value")) for g in (ev.get("galaxies") or []) if isinstance(g, dict)]
        for t in tags:
            m = GALAXY_TAG_RE.match(t)
            if m:
                clusters.append((m.group(1), m.group(2)))
        for ctype, cvalue in clusters:
            if isinstance(cvalue, str) and cvalue.strip() and ctype:
                add(galaxy_kind(str(ctype)), norm_text(cvalue), cvalue.strip(), src, rid, f"events.galaxies[{ctype}]")

    now = datetime.now(timezone.utc)
    docs = []
    totals = {c: 0 for c in CATEGORIES}
    overlap: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (kind, key), entry in acc.items():
        slugs = sorted(entry["src"])
        if len(slugs) < 2:  # same-source repeats are NOT cross-source
            continue
        category = CATEGORY_OF.get(kind, "galaxy")
        totals[category] += 1
        for a, b in combinations(slugs, 2):
            overlap[(a, b)][category] += 1
        docs.append({
            "_id": f"{kind}:{key}", "type": kind, "category": category, "key": key,
            "value": entry["display"], "sources": slugs, "source_count": len(slugs),
            "record_count": sum(len(s["ids"]) for s in entry["src"].values()),
            "source_counts": {k: len(v["ids"]) for k, v in entry["src"].items()},
            "records": [{"source": k, "record_id": r, "field": "/".join(sorted(v["fields"]))}
                        for k, v in entry["src"].items() for r in sorted(v["ids"])[:MAX_RECORDS_PER_SOURCE]],
            "fields": sorted({f for v in entry["src"].values() for f in v["fields"]}),
            "match_method": "exact match on normalized value",
            "normalization": "trim; lowercase (ip/domain/hash/email/names); uppercase (CVE); "
                             "URL host lowercased; domain trailing dot removed",
            "analyzed_at": now, "algorithm_version": ALGORITHM_VERSION,
        })
    matches = database[MATCHES]
    matches.delete_many({})
    for i in range(0, len(docs), 1000):
        matches.insert_many(docs[i:i + 1000], ordered=False)
    _ensure_indexes(database)

    per_source = {row["_id"]: row["n"] for row in database["events"].aggregate([
        {"$match": {"source_feed": {"$type": "string"}}},
        {"$group": {"_id": {"$ifNull": ["$source_feed_name", "$source_feed"]}, "n": {"$sum": 1}}}])}
    meta = {
        "records_analyzed": events_analyzed,
        "attributes_analyzed": database["attributes"].count_documents(
            {"is_current": True, "source_feed": {"$type": "string"}}),
        "collections": ["events", "attributes"],
        "sources": [{"key": k, "name": source_name(k, source_urls.get(k)), "url": source_urls.get(k),
                     "records": per_source.get(k, 0)} for k in sorted(source_urls)],
        "totals": totals,
        "overlap": [{"a": a, "b": b, "counts": dict(c)} for (a, b), c in sorted(overlap.items())],
        "analyzed_at": now, "duration_seconds": round((now - started).total_seconds(), 2),
        "algorithm_version": ALGORITHM_VERSION,
    }
    database[META].replace_one({"_id": "latest"}, {"_id": "latest", **meta}, upsert=True)
    logger.info("Cross-source rebuild: %s events, %s matches", events_analyzed, len(docs))
    return meta


def _ensure_indexes(database: Database) -> None:
    m = database[MATCHES]
    m.create_index([("category", 1), ("source_count", -1), ("record_count", -1)])
    m.create_index([("type", 1), ("key", 1)])
    m.create_index([("sources", 1)])
    m.create_index([("key", 1)])
    # source/feed lookups used by the aggregation and evidence view
    database["attributes"].create_index([("is_current", 1), ("indicator_type", 1), ("normalized_indicator", 1)])
    database["events"].create_index([("source_feed", 1), ("misp_uuid", 1)], name="cross_source_feed_uuid")
