from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.database import get_database
from backend.services import cross_source as cs
from backend.utils.serialization import serialize

router = APIRouter(prefix="/api/cross-source")
SORTS = {"source_count": "source_count", "record_count": "record_count", "value": "key", "type": "type"}


def _meta() -> dict[str, Any]:
    meta = get_database()[cs.META].find_one({"_id": "latest"})
    if not meta:
        raise HTTPException(status_code=404, detail="Cross-source analysis has not been run yet. Click Refresh.")
    return meta


def _names() -> dict[str, str]:
    return {s["key"]: s["name"] for s in _meta()["sources"]}


def _row(doc: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return {
        "type": doc["type"], "category": doc["category"], "key": doc["key"], "value": doc["value"],
        "sources": [{"key": s, "name": names.get(s, s), "records": doc["source_counts"].get(s, 0)}
                    for s in doc["sources"]],
        "source_count": doc["source_count"], "record_count": doc["record_count"],
        "match_type": doc["match_method"],
    }


def _filter(category: str, q: str, sources: str, min_sources: int) -> dict[str, Any]:
    query: dict[str, Any] = {"source_count": {"$gte": max(2, min_sources)}}
    if category and category != "all":
        query["category"] = category
    keys = [s for s in sources.split(",") if s]
    if keys:
        query["sources"] = {"$all": keys}
    if q.strip():
        query["$or"] = [{"key": {"$regex": re.escape(q.strip().casefold())}},
                        {"value": {"$regex": re.escape(q.strip()), "$options": "i"}}]
    return query


@router.get("/summary")
def summary() -> dict[str, Any]:
    meta = _meta()
    names = {s["key"]: s["name"] for s in meta["sources"]}
    meta["overlap"] = [{**o, "a_name": names.get(o["a"], o["a"]), "b_name": names.get(o["b"], o["b"])}
                       for o in meta["overlap"]]
    meta["totals"]["total"] = sum(meta["totals"].values())
    return serialize(meta)


@router.get("/sources")
def sources() -> list[dict[str, Any]]:
    return _meta()["sources"]


@router.get("/common")
def common(
    category: str = "all", q: str = "", sources: str = "", min_sources: int = Query(2, ge=2),
    sort: str = "source_count", order: str = "desc",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    col = get_database()[cs.MATCHES]
    query = _filter(category, q, sources, min_sources)
    direction = 1 if order == "asc" else -1
    sort_spec = [(SORTS.get(sort, "source_count"), direction), ("record_count", -1), ("key", 1)]
    total = col.count_documents(query)
    names = _names()
    rows = col.find(query, {"records": 0}).sort(sort_spec).skip((page - 1) * page_size).limit(page_size)
    return {"total": total, "page": page, "page_size": page_size, "items": [_row(d, names) for d in rows]}


@router.get("/compare")
def compare(a: str, b: str, category: str = "all", q: str = "",
            page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict[str, Any]:
    if a == b:
        raise HTTPException(status_code=400, detail="Pick two different sources")
    names = _names()
    if a not in names or b not in names:
        raise HTTPException(status_code=404, detail="Unknown source")
    col = get_database()[cs.MATCHES]
    counts = {c: 0 for c in cs.CATEGORIES}
    for r in col.aggregate([{"$match": {"sources": {"$all": [a, b]}}},
                            {"$group": {"_id": "$category", "n": {"$sum": 1}}}]):
        counts[r["_id"]] = r["n"]
    result = common(category=category, q=q, sources=f"{a},{b}", min_sources=2, page=page, page_size=page_size)
    return {"a": {"key": a, "name": names[a]}, "b": {"key": b, "name": names[b]}, "counts": counts, **result}


@router.post("/refresh")
def refresh() -> dict[str, Any]:
    meta = cs.rebuild(get_database())
    return serialize(meta)


@router.get("/evidence")
def evidence_query(type: str, key: str, source: str = "") -> dict[str, Any]:
    return _evidence(type, key, source)


@router.get("/{kind}/{value:path}")
def evidence(kind: str, value: str, source: str = "") -> dict[str, Any]:
    return _evidence(kind, value, source)


def _evidence(kind: str, value: str, source: str) -> dict[str, Any]:
    db = get_database()
    doc = db[cs.MATCHES].find_one({"_id": f"{kind}:{value}"})
    if not doc:
        raise HTTPException(status_code=404, detail="Common item not found")
    meta = _meta()
    info = {s["key"]: s for s in meta["sources"]}
    groups = []
    for src in doc["sources"]:
        if source and src != source:
            continue
        ids = [r["record_id"] for r in doc["records"] if r["source"] == src]
        url = info.get(src, {}).get("url")
        events = {e["misp_uuid"]: e for e in db["events"].find(
            {"source_feed": url, "misp_uuid": {"$in": ids}},
            {"misp_uuid": 1, "info": 1, "date": 1, "timestamp": 1, "source_reference": 1})}
        groups.append({
            "key": src, "name": info.get(src, {}).get("name", src), "url": url,
            "record_count": doc["source_counts"].get(src, 0),
            "records": [{
                "title": events.get(i, {}).get("info") or "(untitled event)", "misp_uuid": i,
                "record_id": f"{src}:{i}", "date": events.get(i, {}).get("date"),
                "reference": events.get(i, {}).get("source_reference"),
                "field": next((r["field"] for r in doc["records"] if r["record_id"] == i and r["source"] == src), None),
            } for i in ids],
        })
    return serialize({
        "type": doc["type"], "category": doc["category"], "key": doc["key"], "value": doc["value"],
        "source_count": doc["source_count"], "record_count": doc["record_count"], "sources": groups,
        "why": f"Every source listed here has at least one record containing the same {doc['type']}: {doc['value']}.",
        "technical": {
            "database": meta.get("collections"), "collection": "events/attributes → cross_source_matches",
            "fields": doc["fields"], "normalization": doc["normalization"],
            "matching": doc["match_method"], "algorithm_version": doc["algorithm_version"],
            "analyzed_at": doc["analyzed_at"], "stored_records_per_source_cap": cs.MAX_RECORDS_PER_SOURCE,
        },
    })
