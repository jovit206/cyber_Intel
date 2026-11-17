"""Chapter 18 faculty route aliases backed by imported threat records and decisions."""
from datetime import datetime, timezone
import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from backend.routes.admin import _admin_user, _audit
from backend.services.chapter18_reports import ReportDependencyError, benchmark_pdf
from backend.services.chapter18_storage import BENCHMARK_RUNS, FULL_THREATS

router = APIRouter(tags=["Chapter 18 false-positive review"])


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threat_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    decision: Literal["false_positive", "true_positive", "clear"]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] | None = None


def _database(request: Request):
    return request.app.state.database


@router.get("/api/moderation/fp")
def false_positive_candidates(request: Request, actor: dict = Depends(_admin_user), limit: int = Query(100, ge=1, le=500), source: str | None = Query(None, max_length=120)):
    del actor
    db = _database(request)
    decisions = {row["threat_id"]: row for row in db.fp_decisions.find({}, {"_id": 0})}
    query = {"provenance": "imported"}
    if source:
        query["from_source"] = source.strip()
    rows = list(db[FULL_THREATS].find(query, {"_id": 0}).sort("timestamp", -1).limit(limit))
    result = []
    for row in rows:
        decision = decisions.get(row.get("threat_id"))
        if decision and decision.get("decision") == "false_positive":
            continue
        row["review_decision"] = decision.get("decision") if decision else "needs_review"
        row["review_reason"] = decision.get("reason") if decision else None
        row["evidence_note"] = "Imported record shown for analyst review; no automatic false-positive verdict is inferred."
        result.append(row)
    return {"items": result, "count": len(result), "source": "full_threats", "data_note": "Only imported records are returned; no synthetic candidates are generated."}


@router.get("/api/moderation/fp/value")
def false_positive_value(
    request: Request,
    threat_id: str | None = Query(None, min_length=1, max_length=256),
    value: str | None = Query(None, min_length=1, max_length=2048),
    actor: dict = Depends(_admin_user),
):
    del actor
    if not threat_id and not value:
        raise HTTPException(422, "Specify a threat_id or indicator value.")
    db = _database(request)
    if threat_id:
        records = list(db[FULL_THREATS].find({"threat_id": threat_id, "provenance": "imported"}, {"_id": 0}).limit(1))
    else:
        records = list(db[FULL_THREATS].find({"provenance": "imported", "$or": [{"indicator": value}, {"target": value}]}, {"_id": 0}).limit(100))
    if not records:
        raise HTTPException(404, "Imported threat record not found.")
    decisions = {row["threat_id"]: row for row in db.fp_decisions.find({"threat_id": {"$in": [item["threat_id"] for item in records]}}, {"_id": 0})}
    if threat_id:
        return {"record": records[0], "decision": decisions.get(records[0]["threat_id"])}
    return {"value": value, "records": [{"record": row, "decision": decisions.get(row["threat_id"])} for row in records], "count": len(records)}


@router.get("/api/moderation/fp/export")
def export_reviewable_records(request: Request, actor: dict = Depends(_admin_user)):
    del actor
    db = _database(request)
    excluded = [row["threat_id"] for row in db.fp_decisions.find({"decision": "false_positive"}, {"_id": 0, "threat_id": 1})]
    query = {"provenance": "imported", "threat_id": {"$nin": excluded}}
    fields = ["threat_id", "from_source", "source", "threat_type", "indicator", "target", "timestamp", "severity", "confidence", "status", "description"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in db[FULL_THREATS].find(query, {"_id": 0, **{name: 1 for name in fields}}).sort("timestamp", -1):
        writer.writerow(row)
    from fastapi.responses import Response
    return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=chapter18-reviewable-threats.csv"})


@router.post("/api/moderation/fp/decision")
def save_false_positive_decision(payload: Decision, request: Request, actor: dict = Depends(_admin_user)):
    db = _database(request)
    record = db[FULL_THREATS].find_one({"threat_id": payload.threat_id, "provenance": "imported"}, {"_id": 1})
    if not record:
        raise HTTPException(404, "Imported threat record not found.")
    if payload.decision != "clear" and not payload.reason:
        raise HTTPException(422, "A reason is required for analyst decisions.")
    db.fp_decisions.create_index("threat_id", unique=True, name="uq_fp_decision_threat")
    now = datetime.now(timezone.utc)
    try:
        _audit(db, actor["user_id"], "fp.decision", payload.threat_id, "started",
               {"decision": payload.decision, "reason": payload.reason})
    except Exception as error:
        raise HTTPException(503, "The decision was not changed because its audit record could not be stored.") from error
    if payload.decision == "clear":
        db.fp_decisions.delete_one({"threat_id": payload.threat_id})
    else:
        db.fp_decisions.update_one({"threat_id": payload.threat_id}, {"$set": {
            "threat_id": payload.threat_id, "decision": payload.decision,
            "reason": payload.reason, "analyst_id": actor["user_id"], "updated_at": now,
        }, "$setOnInsert": {"created_at": now}}, upsert=True)
    _audit(db, actor["user_id"], "fp.decision", payload.threat_id, "cleared" if payload.decision == "clear" else "recorded",
           {"decision": payload.decision, "reason": payload.reason})
    return {"threat_id": payload.threat_id, "decision": payload.decision, "updated_at": now.isoformat()}


@router.get("/api/ops/sources")
def imported_sources(request: Request, actor: dict = Depends(_admin_user)):
    del actor
    rows = list(_database(request)[FULL_THREATS].aggregate([
        {"$match": {"provenance": "imported"}},
        {"$group": {"_id": "$from_source", "count": {"$sum": 1}, "latest": {"$max": "$timestamp"}}},
        {"$sort": {"count": -1}},
    ]))
    for row in rows:
        row["source"] = row.pop("_id") or "Not recorded"
        if isinstance(row.get("latest"), datetime):
            row["latest"] = row["latest"].isoformat()
    return {"sources": rows, "source": FULL_THREATS}


@router.get("/api/ops/report.pdf")
def comparative_report(request: Request, run_id: str = Query(..., min_length=1, max_length=128), actor: dict = Depends(_admin_user)):
    del actor
    run = _database(request)[BENCHMARK_RUNS].find_one({"run_id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, "A completed measured benchmark run is required before a PDF report can be generated.")
    try:
        body = benchmark_pdf(run)
    except ReportDependencyError as error:
        raise HTTPException(503, str(error)) from error
    from fastapi.responses import Response
    return Response(body, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=chapter18-{run_id}.pdf"})
