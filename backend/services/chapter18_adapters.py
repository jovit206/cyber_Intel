from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pymongo.database import Database

from backend.models.chapter18 import ThreatRecord
from backend.services.chapter18_connections import Chapter18Connections
from backend.services.chapter18_storage import ensure_mongo_schema

logger = logging.getLogger("aegis.chapter18.adapters")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARD_COUNT = 16
WRITE_BATCH_SIZE = 500
_CASSANDRA_FIELDS = (
    "threat_id",
    "source",
    "from_source",
    "threat_type",
    "indicator",
    "target",
    "severity",
    "confidence",
    "status",
    "description",
    "related_threats",
    "transaction_ids",
    "source_reference",
    "latitude",
    "longitude",
    "country_code",
    "geolocation_source",
    "provenance",
)


@dataclass
class AdapterWriteResult:
    attempted: int = 0
    written: int = 0
    failed: int = 0
    duplicate: int = 0
    error_types: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "written": self.written,
            "failed": self.failed,
            "duplicate": self.duplicate,
            "error_types": self.error_types[:5],
        }


def _chunks(items: list[Any], size: int = WRITE_BATCH_SIZE) -> Iterable[list[Any]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def ensure_cassandra_schema(
    connections: Chapter18Connections,
) -> dict[str, Any]:
    settings = connections.settings
    session = connections.cassandra_session()
    schema_path = PROJECT_ROOT / "chapter-18" / "cassandra_schema.cql"
    statements = [statement.strip() for statement in schema_path.read_text(encoding="utf-8").split(";")]
    keyspace = settings.cassandra_keyspace
    datacenter = settings.cassandra_datacenter
    replication = settings.cassandra_replication_factor
    statements = [
        (
            f"CREATE KEYSPACE IF NOT EXISTS {keyspace} WITH replication = "
            f"{{'class': 'NetworkTopologyStrategy', '{datacenter}': {replication}}}"
            if statement.startswith("CREATE KEYSPACE")
            else statement.replace("threat_intel_lab18.", f"{keyspace}.")
        )
        for statement in statements
        if statement
    ]
    for statement in statements:
        session.execute(statement)
    return {
        "keyspace": keyspace,
        "tables": [
            "threats_by_id",
            "threats_by_source",
            "threats_by_type",
            "threats_by_target",
            "threats_by_indicator",
            "threats_by_day",
        ],
    }


def ensure_neo4j_schema(
    connections: Chapter18Connections,
) -> dict[str, Any]:
    schema_path = PROJECT_ROOT / "chapter-18" / "neo4j_schema.cypher"
    statements = [statement.strip() for statement in schema_path.read_text(encoding="utf-8").split(";")]
    driver = connections.neo4j_driver()
    with driver.session(database=connections.settings.neo4j_database) as session:
        for statement in statements:
            if statement:
                session.run(statement).consume()
    return {
        "database": connections.settings.neo4j_database,
        "constraints_and_indexes": len([statement for statement in statements if statement]),
    }


def ensure_schema(
    application_database: Database,
    connections: Chapter18Connections,
    include_optional_databases: bool = True,
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "mongodb": ensure_mongo_schema(connections.mongo_database(application_database))
    }
    if not include_optional_databases:
        return results
    results["cassandra"] = ensure_cassandra_schema(connections)
    results["neo4j"] = ensure_neo4j_schema(connections)
    return results


def _record_data(record: ThreatRecord) -> dict[str, Any]:
    return record.model_dump(mode="python")


def _shard_for(threat_id: str) -> int:
    digest = hashlib.sha256(threat_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % SHARD_COUNT


def _cassandra_args(
    record: ThreatRecord, partition_field: str
) -> tuple[Any, ...]:
    data = _record_data(record)
    if partition_field == "threat_id":
        return (
            data["threat_id"],
            *(data[column] for column in _CASSANDRA_FIELDS if column != "threat_id"),
            data["timestamp"],
        )
    day_bucket = data["timestamp"].date()
    key_values = (
        (day_bucket,)
        if partition_field == "day_bucket"
        else (data[partition_field], day_bucket)
    )
    payload_fields = [
        column for column in _CASSANDRA_FIELDS if column != partition_field
    ]
    return (
        *key_values,
        _shard_for(record.threat_id),
        data["timestamp"],
        *(data[column] for column in payload_fields),
    )


def write_cassandra_records(
    connections: Chapter18Connections,
    records: list[ThreatRecord],
) -> AdapterWriteResult:
    from cassandra.concurrent import execute_concurrent_with_args

    session = connections.cassandra_session()
    keyspace = connections.settings.cassandra_keyspace
    partitions = (
        ("threats_by_id", "threat_id"),
        ("threats_by_source", "from_source"),
        ("threats_by_type", "threat_type"),
        ("threats_by_target", "target"),
        ("threats_by_indicator", "indicator"),
        ("threats_by_day", "day_bucket"),
    )
    result = AdapterWriteResult()
    for table, partition_field in partitions:
        eligible = [
            record
            for record in records
            if partition_field in {"day_bucket", "threat_id"}
            or getattr(record, partition_field) is not None
        ]
        if table in {"threats_by_target", "threats_by_indicator"}:
            eligible = [
                record
                for record in eligible
                if getattr(record, partition_field)
            ]
        if partition_field == "threat_id":
            payload_fields = [
                column for column in _CASSANDRA_FIELDS if column != "threat_id"
            ] + ["timestamp"]
            columns = ", ".join(["threat_id", *payload_fields])
            parameter_count = len(payload_fields) + 1
        else:
            key_columns = (
            [partition_field]
            if partition_field == "day_bucket"
            else [partition_field, "day_bucket"]
            ) + ["shard", "timestamp"]
            payload_fields = [
                column for column in _CASSANDRA_FIELDS if column != partition_field
            ]
            columns = ", ".join([*key_columns, *payload_fields])
            parameter_count = len(key_columns) + len(payload_fields)
        placeholders = ", ".join("?" for _ in range(parameter_count))
        statement = session.prepare(
            f"INSERT INTO {keyspace}.{table} ({columns}) VALUES ({placeholders})"
        )
        args = [
            _cassandra_args(record, partition_field)
            for record in eligible
        ]
        result.attempted += len(args)
        for batch in _chunks(args):
            outcomes = execute_concurrent_with_args(
                session, statement, batch, raise_on_first_error=False
            )
            for succeeded, outcome in outcomes:
                if succeeded:
                    result.written += 1
                else:
                    result.failed += 1
                    error_type = type(outcome).__name__
                    if error_type not in result.error_types:
                        result.error_types.append(error_type)
    return result


_NEO4J_UPSERT = """
UNWIND $rows AS row
MERGE (t:Threat {threat_id: row.threat_id})
SET t.source = row.source,
    t.from_source = row.from_source,
    t.threat_type = row.threat_type,
    t.indicator = row.indicator,
    t.target = row.target,
    t.timestamp = row.timestamp,
    t.severity = row.severity,
    t.confidence = row.confidence,
    t.status = row.status,
    t.description = row.description,
    t.related_threats = row.related_threats,
    t.transaction_ids = row.transaction_ids,
    t.source_reference = row.source_reference,
    t.latitude = row.latitude,
    t.longitude = row.longitude,
    t.country_code = row.country_code,
    t.geolocation_source = row.geolocation_source,
    t.provenance = row.provenance
MERGE (s:Source {source_key: row.from_source})
MERGE (s)-[:REPORTED]->(t)
MERGE (kind:ThreatType {type_key: row.threat_type})
MERGE (t)-[:HAS_TYPE]->(kind)
FOREACH (target_value IN CASE WHEN row.target IS NULL THEN [] ELSE [row.target] END |
    MERGE (target:Target {target_key: target_value})
    MERGE (t)-[:TARGETS]->(target)
)
FOREACH (indicator_value IN CASE WHEN row.indicator IS NULL THEN [] ELSE [row.indicator] END |
    MERGE (indicator:Indicator {value: indicator_value})
    MERGE (t)-[:HAS_INDICATOR]->(indicator)
)
"""

_NEO4J_LINK_RELATED = """
UNWIND $rows AS row
MATCH (t:Threat {threat_id: row.threat_id})
UNWIND row.related_threats AS related_id
MATCH (related:Threat {threat_id: related_id})
MERGE (t)-[:RELATED_TO]->(related)
"""


def _neo4j_row(record: ThreatRecord) -> dict[str, Any]:
    return _record_data(record)


def write_neo4j_records(
    connections: Chapter18Connections,
    records: list[ThreatRecord],
    link_related: bool = False,
) -> AdapterWriteResult:
    from neo4j.exceptions import DriverError, Neo4jError

    driver = connections.neo4j_driver()
    result = AdapterWriteResult(attempted=len(records))
    try:
        with driver.session(database=connections.settings.neo4j_database) as session:
            for batch in _chunks([_neo4j_row(record) for record in records]):
                session.execute_write(lambda tx: tx.run(_NEO4J_UPSERT, rows=batch).consume())
                result.written += len(batch)
            if link_related:
                for batch in _chunks([_neo4j_row(record) for record in records]):
                    session.execute_write(
                        lambda tx: tx.run(_NEO4J_LINK_RELATED, rows=batch).consume()
                    )
    except (DriverError, Neo4jError, OSError):
        logger.exception("Neo4j threat-record write failed after %d records", result.written)
        result.failed = result.attempted - result.written
        result.error_types.append("Neo4jWriteError")
    return result


def link_neo4j_relationships(
    connections: Chapter18Connections,
    records: list[ThreatRecord],
) -> AdapterWriteResult:
    from neo4j.exceptions import DriverError, Neo4jError

    related_records = [record for record in records if record.related_threats]
    result = AdapterWriteResult(attempted=len(related_records))
    if not related_records:
        return result
    driver = connections.neo4j_driver()
    try:
        with driver.session(database=connections.settings.neo4j_database) as session:
            for batch in _chunks([_neo4j_row(record) for record in related_records]):
                session.execute_write(
                    lambda tx: tx.run(_NEO4J_LINK_RELATED, rows=batch).consume()
                )
                result.written += len(batch)
    except (DriverError, Neo4jError, OSError):
        logger.exception("Neo4j related-threat linking failed after %d records", result.written)
        result.failed = result.attempted - result.written
        result.error_types.append("Neo4jLinkError")
    return result
