from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pymongo import InsertOne
from pymongo.database import Database
from pymongo.errors import BulkWriteError, PyMongoError

from backend.models.chapter18 import ThreatRecord
from backend.services.chapter18_adapters import (
    AdapterWriteResult,
    write_cassandra_records,
    write_neo4j_records,
)
from backend.services.chapter18_connections import (
    Chapter18ConfigurationError,
    Chapter18Connections,
    Chapter18DependencyError,
)
from backend.services.chapter18_storage import FULL_THREATS, mongo_document

logger = logging.getLogger("aegis.chapter18.ingestion")


class ThreatIdConflictError(ValueError):
    pass


@dataclass
class ImportBatchResult:
    received: int
    unique_records: int
    duplicate_in_batch: int
    mongo: dict[str, Any]
    cassandra: dict[str, Any]
    neo4j: dict[str, Any]
    skipped: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        backends = (self.mongo, self.cassandra, self.neo4j)
        if all(backend.get("status") == "written" for backend in backends):
            return "completed"
        if any(backend.get("status") == "written" for backend in backends):
            return "partial"
        return "failed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "received": self.received,
            "unique_records": self.unique_records,
            "duplicate_in_batch": self.duplicate_in_batch,
            "skipped": self.skipped,
            "mongo": self.mongo,
            "cassandra": self.cassandra,
            "neo4j": self.neo4j,
            "issues": self.issues[:20],
        }


def _unique_records(records: list[ThreatRecord]) -> tuple[list[ThreatRecord], int]:
    unique: dict[str, ThreatRecord] = {}
    duplicates = 0
    for record in records:
        previous = unique.get(record.threat_id)
        if previous is None:
            unique[record.threat_id] = record
            continue
        if previous != record:
            raise ThreatIdConflictError(
                f"Conflicting rows share threat_id {record.threat_id!r}."
            )
        duplicates += 1
    return list(unique.values()), duplicates


def _existing_mongo_records(
    collection, records: list[ThreatRecord]
) -> set[str]:
    existing: set[str] = set()
    ids = [record.threat_id for record in records]
    incoming_by_id = {record.threat_id: record for record in records}
    for offset in range(0, len(ids), 500):
        rows = collection.find(
            {"threat_id": {"$in": ids[offset : offset + 500]}},
            {"_id": 0},
        )
        for row in rows:
            threat_id = row.get("threat_id")
            stored = ThreatRecord.model_validate(row)
            incoming = incoming_by_id[threat_id]
            if stored != incoming:
                raise ThreatIdConflictError(
                    f"Existing threat_id {threat_id!r} has different source data; "
                    "the existing record was not overwritten."
                )
            existing.add(threat_id)
    return existing


def _confirmed_mongo_records(collection, records: list[ThreatRecord]) -> list[ThreatRecord]:
    """Return only validated records present in MongoDB before writing peer stores."""
    confirmed: list[ThreatRecord] = []
    by_id = {record.threat_id: record for record in records}
    ids = list(by_id)
    for offset in range(0, len(ids), 500):
        for row in collection.find(
            {"threat_id": {"$in": ids[offset : offset + 500]}}, {"_id": 0}
        ):
            stored = ThreatRecord.model_validate(row)
            expected = by_id.get(stored.threat_id)
            if expected is None or stored != expected:
                raise ThreatIdConflictError(
                    f"MongoDB contains a conflicting threat_id {stored.threat_id!r}; peer stores were not updated."
                )
            confirmed.append(expected)
    return confirmed


def _adapter_result(result: AdapterWriteResult) -> dict[str, Any]:
    value = result.as_dict()
    value["status"] = "written" if result.failed == 0 else "partial"
    return value


def _unavailable_result(error: Exception) -> dict[str, Any]:
    logger.warning("Chapter 18 database adapter unavailable: %s", type(error).__name__)
    return {
        "status": "unavailable",
        "attempted": 0,
        "written": 0,
        "failed": 0,
        "error_type": type(error).__name__,
    }


def persist_import_batch(
    application_database: Database,
    connections: Chapter18Connections,
    records: list[ThreatRecord],
    adapter_availability: dict[str, dict[str, Any]] | None = None,
) -> ImportBatchResult:
    unique_records, duplicates = _unique_records(records)
    collection = connections.mongo_database(application_database)[FULL_THREATS]
    existing = _existing_mongo_records(collection, unique_records) if unique_records else set()
    mongo_result: dict[str, Any] = {
        "status": "written",
        "attempted": len(unique_records) - len(existing),
        "written": 0,
        "duplicate": len(existing),
        "failed": 0,
    }
    new_records = [record for record in unique_records if record.threat_id not in existing]
    if new_records:
        try:
            result = collection.bulk_write(
                [InsertOne(mongo_document(record)) for record in new_records],
                ordered=False,
            )
            mongo_result["written"] = result.inserted_count
        except BulkWriteError as error:
            details = error.details or {}
            write_errors = details.get("writeErrors", [])
            duplicate_errors = sum(1 for item in write_errors if item.get("code") == 11000)
            other_errors = [
                item for item in write_errors if item.get("code") != 11000
            ]
            mongo_result.update({
                "written": details.get("nInserted", 0),
                "duplicate": mongo_result["duplicate"] + duplicate_errors,
                "failed": len(other_errors),
                "status": "partial" if other_errors else "written",
            })
            if other_errors:
                mongo_result["error_types"] = ["MongoWriteError"]
        except PyMongoError as error:
            logger.exception("MongoDB threat-record write failed")
            mongo_result.update({
                "status": "failed",
                "failed": len(new_records),
                "error_type": type(error).__name__,
            })
    else:
        mongo_result["written"] = 0

    peer_records = _confirmed_mongo_records(collection, unique_records)

    if adapter_availability is not None and adapter_availability.get(
        "cassandra", {}
    ).get("status") != "ready":
        cassandra_result = adapter_availability.get(
            "cassandra",
            {"status": "not_tested", "reason": "Cassandra schema is not ready."},
        )
    else:
        try:
            cassandra_result = _adapter_result(
                write_cassandra_records(connections, peer_records)
            )
        except (
            Chapter18ConfigurationError,
            Chapter18DependencyError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            cassandra_result = _unavailable_result(error)

    if adapter_availability is not None and adapter_availability.get(
        "neo4j", {}
    ).get("status") != "ready":
        neo4j_result = adapter_availability.get(
            "neo4j",
            {"status": "not_tested", "reason": "Neo4j schema is not ready."},
        )
    else:
        try:
            neo4j_result = _adapter_result(
                write_neo4j_records(connections, peer_records, link_related=True)
            )
        except (
            Chapter18ConfigurationError,
            Chapter18DependencyError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            neo4j_result = _unavailable_result(error)

    return ImportBatchResult(
        received=len(records),
        unique_records=len(unique_records),
        duplicate_in_batch=duplicates,
        mongo=mongo_result,
        cassandra=cassandra_result,
        neo4j=neo4j_result,
    )


def normalize_existing_event(event: dict[str, Any]) -> tuple[ThreatRecord | None, str | None]:
    source = event.get("source")
    if not isinstance(source, str) or not source.strip():
        return None, "missing_source"
    source = source.strip()
    source_id = event.get("source_id")
    event_id = str(source_id or event.get("_id") or "").strip()
    if not event_id:
        return None, "missing_identifier"

    timestamp = event.get("timestamp")
    if timestamp is None:
        # MISP public feed summaries commonly retain their source date but have
        # no event timestamp. Preserve that date at UTC midnight rather than
        # dropping valid feed records or substituting the import time.
        source_date = event.get("date")
        if isinstance(source_date, str) and source_date.strip():
            try:
                timestamp = datetime.fromisoformat(source_date.strip().replace("Z", "+00:00"))
            except ValueError:
                timestamp = None
    if not isinstance(timestamp, (datetime, str)):
        return None, "missing_timestamp"
    from_source = event.get("source_feed_name") or event.get("source_feed") or source
    threat_type = (
        event.get("threat_type")
        or event.get("indicator_type")
        or event.get("category")
        or event.get("type")
        or "event"
    )
    indicator = event.get("normalized_indicator") or event.get("indicator") or event.get("value")
    transaction_ids = event.get("transaction_ids")
    if not isinstance(transaction_ids, list):
        transaction_ids = []
    transaction_id = event.get("transaction_id")
    if isinstance(transaction_id, (str, int)) and str(transaction_id).strip():
        transaction_ids.append(str(transaction_id))
    related = event.get("related_threats")
    if not isinstance(related, list):
        related = []
    confidence = event.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None

    try:
        return ThreatRecord.model_validate({
            "threat_id": str(event.get("threat_id") or f"{source}:{event_id}"),
            "source": source,
            "from_source": str(from_source),
            "threat_type": str(threat_type),
            "indicator": indicator if isinstance(indicator, str) else None,
            "target": event.get("target") if isinstance(event.get("target"), str) else None,
            "timestamp": timestamp,
            "severity": event.get("severity"),
            "confidence": confidence,
            "status": event.get("status") or event.get("verdict") or "unreviewed",
            "description": event.get("description") or event.get("summary"),
            "related_threats": related,
            "transaction_ids": transaction_ids,
            "source_reference": event.get("source_reference") or event.get("source_url") or event.get("url"),
            "latitude": event.get("latitude"),
            "longitude": event.get("longitude"),
            "country_code": event.get("country_code"),
            "geolocation_source": event.get("geolocation_source"),
            "provenance": "imported",
        }), None
    except (TypeError, ValueError):
        return None, "invalid_record"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
