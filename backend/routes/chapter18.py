from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from backend.models.chapter18 import AnalystReview, BenchmarkRequest, ThreatRecord
from backend.routes.admin import _admin_user
from backend.services.chapter18_adapters import ensure_cassandra_schema, ensure_neo4j_schema
from backend.services.chapter18_analytics import analytics_snapshot
from backend.services.chapter18_benchmark import BenchmarkPrerequisiteError, run_benchmark
from backend.services.chapter18_connections import Chapter18Connections
from backend.services.chapter18_detection import (
    InvestigationError, create_investigation, list_investigations, record_analyst_review,
)
from backend.services.chapter18_ingestion import ThreatIdConflictError, persist_import_batch
from backend.services.chapter18_reports import ReportDependencyError, benchmark_csv, benchmark_pdf
from backend.services.chapter18_status import chapter18_status
from backend.services.chapter18_storage import BENCHMARK_RUNS, FULL_THREATS, IMPORT_RUNS, ensure_mongo_schema

router = APIRouter(prefix="/api/chapter18", tags=["Chapter 18 Multi-Database Threat Intelligence"])
MAX_IMPORT_RECORDS = 10_000


def _connections(request: Request) -> Chapter18Connections:
    connections = getattr(request.app.state, "chapter18_connections", None)
    if connections is None:
        connections = Chapter18Connections()
        request.app.state.chapter18_connections = connections
    return connections


def _db(request: Request):
    return request.app.state.database


def _safe_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()
    return value


class ImportBatch(BaseModel):
    records: list[ThreatRecord] = Field(min_length=1, max_length=MAX_IMPORT_RECORDS)


class InvestigationRequest(BaseModel):
    threat_id: str = Field(min_length=1, max_length=256)


class ReviewRequest(AnalystReview):
    subject_type: str = Field(pattern="^(threat|investigation)$")
    subject_id: str = Field(min_length=1, max_length=256)


@router.get("/status")
def status(request: Request, actor: dict = Depends(_admin_user)) -> dict:
    del actor
    connections = _connections(request)
    return {"project": 7, "databases": chapter18_status(_db(request), connections)}


@router.post("/schema")
def prepare_schema(request: Request, actor: dict = Depends(_admin_user)) -> dict:
    del actor
    try:
        connections = _connections(request)
        mongo_schema = ensure_mongo_schema(connections.mongo_database(_db(request)))
        optional: dict[str, Any] = {}
        for name, setup in (("cassandra", ensure_cassandra_schema), ("neo4j", ensure_neo4j_schema)):
            try:
                optional[name] = {"status": "ready", **setup(connections)}
            except Exception as error:
                optional[name] = {"status": "unavailable", "error_type": type(error).__name__}
        return {"mongodb": mongo_schema, "optional_databases": chapter18_status(_db(request), connections), "schema_setup": optional}
    except (PyMongoError, RuntimeError, ValueError) as error:
        raise HTTPException(503, "Chapter 18 MongoDB schema could not be prepared.") from error


@router.post("/imports", status_code=201)
def import_records(payload: ImportBatch, request: Request, actor: dict = Depends(_admin_user)) -> dict:
    connections = _connections(request)
    try:
        ensure_mongo_schema(connections.mongo_database(_db(request)))
        available = chapter18_status(_db(request), connections)
        readiness = {
            name: {"status": "ready" if item.get("status") == "connected" and item.get("schema_ready") else item.get("status"), "reason": item.get("message")}
            for name, item in available.items()
        }
        result = persist_import_batch(_db(request), connections, payload.records, readiness).as_dict()
        import_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        connections.mongo_database(_db(request))[IMPORT_RUNS].insert_one({
            "import_id": import_id,
            "created_at": created_at,
            "created_by": actor.get("user_id", "admin"),
            **result,
        })
        return {"import_id": import_id, "created_at": created_at.isoformat(), **result}
    except ThreatIdConflictError as error:
        raise HTTPException(409, str(error)) from error
    except (PyMongoError, RuntimeError, ValueError) as error:
        raise HTTPException(503, "Chapter 18 import could not be completed.") from error


@router.get("/threats")
def threats(
    request: Request,
    actor: dict = Depends(_admin_user),
    q: str | None = Query(None, max_length=256),
    source: str | None = Query(None, max_length=120),
    status_filter: str | None = Query(None, alias="status", max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
) -> dict:
    del actor
    collection = _connections(request).mongo_database(_db(request))[FULL_THREATS]
    query: dict[str, Any] = {"provenance": "imported"}
    if source:
        query["from_source"] = source.strip()
    if status_filter:
        query["status"] = status_filter.strip()
    if q and q.strip():
        term = q.strip()
        query["$or"] = [{field: {"$regex": re.escape(term), "$options": "i"}} for field in ("threat_id", "indicator", "target", "description")]
    total = collection.count_documents(query)
    rows = list(collection.find(query, {"_id": 0}).sort("timestamp", DESCENDING).skip(offset).limit(limit))
    for row in rows:
        row["timestamp"] = _safe_datetime(row.get("timestamp"))
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/analytics")
def analytics(request: Request, actor: dict = Depends(_admin_user)) -> dict:
    del actor
    return analytics_snapshot(_db(request), _connections(request))


@router.get("/investigations")
def investigations(request: Request, actor: dict = Depends(_admin_user), limit: int = Query(50, ge=1, le=200)) -> dict:
    del actor
    return {"items": list_investigations(_db(request), _connections(request), limit)}


@router.post("/investigations", status_code=201)
def investigate(payload: InvestigationRequest, request: Request, actor: dict = Depends(_admin_user)) -> dict:
    try:
        return create_investigation(_db(request), _connections(request), payload.threat_id, actor.get("user_id", "admin"))
    except InvestigationError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/reviews", status_code=201)
def review(payload: ReviewRequest, request: Request, actor: dict = Depends(_admin_user)) -> dict:
    try:
        return record_analyst_review(
            _db(request), _connections(request), payload.subject_type, payload.subject_id,
            AnalystReview(decision=payload.decision, reason=payload.reason), actor.get("user_id", "admin"),
        )
    except InvestigationError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/reviews")
def review_history(
    request: Request,
    actor: dict = Depends(_admin_user),
    subject_type: str | None = Query(None, pattern="^(threat|investigation)$"),
    subject_id: str | None = Query(None, max_length=256),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    del actor
    query: dict[str, Any] = {}
    if subject_type:
        query["subject_type"] = subject_type
    if subject_id:
        query["subject_id"] = subject_id
    rows = list(
        _connections(request).mongo_database(_db(request))["analyst_reviews"]
        .find(query, {"_id": 0})
        .sort("created_at", DESCENDING)
        .limit(limit)
    )
    for row in rows:
        row["created_at"] = _safe_datetime(row.get("created_at"))
    return {"items": rows}


@router.post("/benchmarks", status_code=201)
def benchmark(payload: BenchmarkRequest, request: Request, actor: dict = Depends(_admin_user)) -> dict:
    del actor
    try:
        return run_benchmark(_db(request), _connections(request), payload, chapter18_status(_db(request), _connections(request)))
    except BenchmarkPrerequisiteError as error:
        raise HTTPException(409, str(error)) from error
    except (PyMongoError, RuntimeError, ValueError) as error:
        raise HTTPException(503, "Benchmark could not be completed.") from error


@router.get("/benchmarks")
def benchmark_history(request: Request, actor: dict = Depends(_admin_user), limit: int = Query(20, ge=1, le=100)) -> dict:
    del actor
    rows = list(_connections(request).mongo_database(_db(request))[BENCHMARK_RUNS].find({}, {"_id": 0}).sort("created_at", DESCENDING).limit(limit))
    for row in rows:
        for key in ("created_at", "completed_at"):
            row[key] = _safe_datetime(row.get(key))
    return {"items": rows}


@router.get("/benchmarks/{run_id}/report")
def benchmark_report(run_id: str, request: Request, actor: dict = Depends(_admin_user), format: str = Query("csv", pattern="^(csv|pdf)$")):
    del actor
    run = _connections(request).mongo_database(_db(request))[BENCHMARK_RUNS].find_one({"run_id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, "Benchmark run not found.")
    try:
        content = benchmark_csv(run) if format == "csv" else benchmark_pdf(run)
    except ReportDependencyError as error:
        raise HTTPException(503, str(error)) from error
    from fastapi.responses import Response
    return Response(content, media_type="text/csv" if format == "csv" else "application/pdf", headers={"Content-Disposition": f"attachment; filename=chapter18-{run_id}.{format}"})
