from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo.database import Database

from backend.models.chapter18 import AnalystReview
from backend.services.chapter18_connections import Chapter18Connections
from backend.services.chapter18_storage import (
    ANALYST_REVIEWS,
    FULL_THREATS,
    INVESTIGATIONS,
)

MAX_INVESTIGATION_NODES = 100
MAX_INVESTIGATION_DEPTH = 2
MAX_TRANSACTION_REFERENCES = 50


class InvestigationError(ValueError):
    pass


def create_investigation(
    application_database: Database,
    connections: Chapter18Connections,
    seed_threat_id: str,
    analyst_id: str,
) -> dict[str, Any]:
    lab_database = connections.mongo_database(application_database)
    threats = lab_database[FULL_THREATS]
    seed = threats.find_one(
        {"threat_id": seed_threat_id, "provenance": "imported"},
        {"_id": 0},
    )
    if seed is None:
        raise InvestigationError("The requested imported threat record was not found.")

    nodes = {seed_threat_id: seed}
    frontier = [seed_threat_id]
    for _ in range(MAX_INVESTIGATION_DEPTH):
        candidate_ids = {
            related_id
            for threat_id in frontier
            for related_id in nodes[threat_id].get("related_threats", [])
            if related_id not in nodes
        }
        frontier = []
        if not candidate_ids or len(nodes) >= MAX_INVESTIGATION_NODES:
            break
        for offset in range(0, len(candidate_ids), 500):
            batch_ids = list(candidate_ids)[offset : offset + 500]
            for record in threats.find({
                "threat_id": {"$in": batch_ids},
                "provenance": "imported",
            }, {"_id": 0}):
                if len(nodes) >= MAX_INVESTIGATION_NODES:
                    break
                nodes[record["threat_id"]] = record
                frontier.append(record["threat_id"])

    edges = sorted({
        (threat_id, related_id)
        for threat_id, record in nodes.items()
        for related_id in record.get("related_threats", [])
        if related_id in nodes and related_id != threat_id
    })
    seed_indicator = seed.get("indicator")
    seed_target = seed.get("target")
    shared_query = []
    if seed_indicator:
        shared_query.append({"indicator": seed_indicator})
    if seed_target:
        shared_query.append({"target": seed_target})
    shared_candidates = []
    if shared_query:
        shared_candidates = list(threats.find(
            {
                "provenance": "imported",
                "threat_id": {"$ne": seed_threat_id},
                "$or": shared_query,
            },
            {"_id": 0, "threat_id": 1, "indicator": 1, "target": 1},
        ).limit(MAX_INVESTIGATION_NODES))
    shared_indicators = sorted({
        row["indicator"] for row in shared_candidates
        if seed_indicator and row.get("indicator") == seed_indicator
    })
    shared_targets = sorted({
        row["target"] for row in shared_candidates
        if seed_target and row.get("target") == seed_target
    })
    sources = sorted({record.get("from_source", "unknown") for record in nodes.values()})
    reported_confidences = [
        float(record["confidence"])
        for record in nodes.values()
        if isinstance(record.get("confidence"), (int, float))
    ]
    confidence_summary = (
        {
            "mean_source_reported": round(
                sum(reported_confidences) / len(reported_confidences), 4
            ),
            "records_with_confidence": len(reported_confidences),
            "interpretation": "Source-reported values; not calibrated or independently verified.",
        }
        if reported_confidences
        else {
            "mean_source_reported": None,
            "records_with_confidence": 0,
            "interpretation": "No source-reported confidence values were supplied.",
        }
    )

    transaction_ids = sorted({
        str(transaction_id)
        for record in nodes.values()
        for transaction_id in record.get("transaction_ids", [])
        if transaction_id
    })[:MAX_TRANSACTION_REFERENCES]
    transaction_matches = []
    if transaction_ids:
        for row in application_database["wallet_transactions"].find(
            {"transaction_id": {"$in": transaction_ids}},
            {
                "_id": 0,
                "transaction_id": 1,
                "created_at": 1,
                "type": 1,
                "status": 1,
            },
        ):
            transaction_matches.append({
                "transaction_id": row.get("transaction_id"),
                "created_at": row.get("created_at"),
                "type": row.get("type"),
                "status": row.get("status"),
            })

    now = datetime.now(timezone.utc)
    investigation = {
        "investigation_id": str(uuid4()),
        "seed_threat_id": seed_threat_id,
        "created_at": now,
        "created_by": analyst_id,
        "status": "needs_review",
        "classification": "analyst_review_required",
        "nodes": sorted(nodes),
        "edges": [{"from": source, "to": target} for source, target in edges],
        "evidence": {
            "node_count": len(nodes),
            "explicit_relationship_edge_count": len(edges),
            "source_count": len(sources),
            "sources": sources,
            "source_confidence": confidence_summary,
            "unresolved_related_ids": sorted({
                related_id
                for record in nodes.values()
                for related_id in record.get("related_threats", [])
                if related_id not in nodes
            })[:MAX_INVESTIGATION_NODES],
            "shared_indicator_values": shared_indicators,
            "shared_target_values": shared_targets,
            "shared_attribute_candidate_ids": sorted({
                row["threat_id"] for row in shared_candidates
            }),
            "shared_attribute_interpretation": (
                "Exact shared indicators or targets are candidate evidence only. "
                "They do not create graph edges or establish malicious activity."
            ),
        },
        "transaction_matches": transaction_matches,
        "transaction_match_basis": (
            "Exact transaction identifiers provided by imported threat records only."
        ),
    }
    lab_database[INVESTIGATIONS].insert_one(investigation.copy())
    investigation.pop("_id", None)
    for match in investigation["transaction_matches"]:
        value = match.get("created_at")
        if isinstance(value, datetime):
            match["created_at"] = (
                value.replace(tzinfo=timezone.utc)
                if value.tzinfo is None
                else value.astimezone(timezone.utc)
            ).isoformat()
    investigation["created_at"] = now.isoformat()
    return investigation


def list_investigations(
    application_database: Database,
    connections: Chapter18Connections,
    limit: int = 50,
) -> list[dict[str, Any]]:
    lab_database = connections.mongo_database(application_database)
    rows = list(
        lab_database[INVESTIGATIONS]
        .find({}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    for row in rows:
        created_at = row.get("created_at")
        if isinstance(created_at, datetime):
            row["created_at"] = (
                created_at.replace(tzinfo=timezone.utc)
                if created_at.tzinfo is None
                else created_at.astimezone(timezone.utc)
            ).isoformat()
    return rows


def record_analyst_review(
    application_database: Database,
    connections: Chapter18Connections,
    subject_type: str,
    subject_id: str,
    review: AnalystReview,
    analyst_id: str,
) -> dict[str, Any]:
    if subject_type not in {"threat", "investigation"}:
        raise InvestigationError("Unsupported review subject.")
    lab_database = connections.mongo_database(application_database)
    collection_name = FULL_THREATS if subject_type == "threat" else INVESTIGATIONS
    key_name = "threat_id" if subject_type == "threat" else "investigation_id"
    if not lab_database[collection_name].find_one({key_name: subject_id}, {"_id": 1}):
        raise InvestigationError("The review subject was not found.")
    record = {
        "review_id": str(uuid4()),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "decision": review.decision,
        "reason": review.reason,
        "analyst_id": analyst_id,
        "created_at": datetime.now(timezone.utc),
    }
    lab_database[ANALYST_REVIEWS].insert_one(record.copy())
    record["created_at"] = record["created_at"].isoformat()
    return record
