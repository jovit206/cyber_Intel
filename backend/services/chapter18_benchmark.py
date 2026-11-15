from __future__ import annotations

import logging
import math
import time
from collections import Counter, deque
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any, Callable
from uuid import uuid4

from pymongo.database import Database
from pymongo.errors import PyMongoError

from backend.models.chapter18 import BenchmarkRequest
from backend.services.chapter18_connections import Chapter18Connections
from backend.services.chapter18_storage import BENCHMARK_RUNS, FULL_THREATS

logger = logging.getLogger("aegis.chapter18.benchmark")
REQUIRED_IMPORT_RECORDS = 100_000
WARMUP_RUNS = 1
MEASURED_RUNS = 10
RESULT_LIMIT = 1000
GRAPH_MAX_DEPTH = 2
GRAPH_MAX_NODES = 100
SHARD_COUNT = 16


class BenchmarkPrerequisiteError(RuntimeError):
    pass


def _day_buckets(start: datetime, end: datetime) -> list[date]:
    current = start.date()
    last = end.date()
    days: list[date] = []
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return days


def _time_query(request: BenchmarkRequest) -> dict[str, Any]:
    return {
        "timestamp": {
            "$gte": request.from_datetime,
            "$lte": request.to_datetime,
        }
    }


def _mongo_source_query(collection, request: BenchmarkRequest) -> dict[str, int]:
    query = _time_query(request)
    query["from_source"] = request.source
    query["provenance"] = "imported"
    result = list(
        collection.find(query, {"_id": 0, "threat_id": 1})
        .sort([("timestamp", -1), ("threat_id", 1)])
        .limit(RESULT_LIMIT)
    )
    return {"matched_count": len(result)}


def _mongo_indicator_query(collection, request: BenchmarkRequest) -> dict[str, int]:
    terms = []
    if request.target:
        terms.append({"target": request.target})
    if request.indicator:
        terms.append({"indicator": request.indicator})
    query = _time_query(request)
    query["$or"] = terms
    query["provenance"] = "imported"
    result = list(
        collection.find(query, {"_id": 0, "threat_id": 1})
        .sort([("timestamp", -1), ("threat_id", 1)])
        .limit(RESULT_LIMIT)
    )
    return {"matched_count": len(result)}


def _mongo_type_aggregation(collection, request: BenchmarkRequest) -> dict[str, Any]:
    rows = list(collection.aggregate([
        {"$match": {**_time_query(request), "provenance": "imported"}},
        {"$group": {"_id": "$threat_type", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    return {
        "group_count": len(rows),
        "record_count": sum(row["count"] for row in rows),
    }


def _mongo_graph_query(
    collection, seed_threat_id: str
) -> dict[str, int]:
    rows = list(collection.aggregate([
        {"$match": {"threat_id": seed_threat_id}},
        {"$graphLookup": {
            "from": FULL_THREATS,
            "startWith": "$related_threats",
            "connectFromField": "related_threats",
            "connectToField": "threat_id",
            "as": "related",
            "maxDepth": GRAPH_MAX_DEPTH - 1,
            "restrictSearchWithMatch": {
                "provenance": "imported",
                "threat_id": {"$ne": seed_threat_id},
            },
        }},
        {"$project": {"_id": 0, "related_ids": {"$slice": ["$related.threat_id", GRAPH_MAX_NODES]}}},
    ]))
    related = rows[0].get("related_ids", []) if rows else []
    return {"related_count": len(set(related))}


def _cassandra_statements(connections: Chapter18Connections) -> dict[str, Any]:
    from cassandra import DriverException
    from cassandra.cluster import NoHostAvailable

    session = connections.cassandra_session()
    keyspace = connections.settings.cassandra_keyspace
    return {
        "session": session,
        "errors": (DriverException, NoHostAvailable, OSError, RuntimeError, ValueError),
        "source": session.prepare(
            f"SELECT threat_id, timestamp, provenance FROM {keyspace}.threats_by_source "
            "WHERE from_source = ? AND day_bucket = ? AND shard = ? "
            "AND timestamp >= ? AND timestamp <= ?"
        ),
        "target": session.prepare(
            f"SELECT threat_id, timestamp, provenance FROM {keyspace}.threats_by_target "
            "WHERE target = ? AND day_bucket = ? AND shard = ? "
            "AND timestamp >= ? AND timestamp <= ?"
        ),
        "indicator": session.prepare(
            f"SELECT threat_id, timestamp, provenance FROM {keyspace}.threats_by_indicator "
            "WHERE indicator = ? AND day_bucket = ? AND shard = ? "
            "AND timestamp >= ? AND timestamp <= ?"
        ),
        "day": session.prepare(
            f"SELECT threat_type, provenance FROM {keyspace}.threats_by_day "
            "WHERE day_bucket = ? AND shard = ? "
            "AND timestamp >= ? AND timestamp <= ?"
        ),
        "id": session.prepare(
            f"SELECT threat_id, related_threats, provenance FROM {keyspace}.threats_by_id "
            "WHERE threat_id = ?"
        ),
    }


def _cassandra_partition_rows(
    prepared: dict[str, Any],
    statement: Any,
    partition_value: str | None,
    request: BenchmarkRequest,
) -> list[Any]:
    from cassandra.concurrent import execute_concurrent_with_args

    args = []
    for day in _day_buckets(request.from_datetime, request.to_datetime):
        for shard in range(SHARD_COUNT):
            if partition_value is None:
                args.append((
                    day,
                    shard,
                    request.from_datetime,
                    request.to_datetime,
                ))
            else:
                args.append((
                    partition_value,
                    day,
                    shard,
                    request.from_datetime,
                    request.to_datetime,
                ))
    rows: list[Any] = []
    for offset in range(0, len(args), 500):
        outcomes = execute_concurrent_with_args(
            prepared["session"],
            statement,
            args[offset : offset + 500],
        )
        for succeeded, outcome in outcomes:
            if not succeeded:
                raise outcome
            rows.extend(outcome)
    return rows


def _top_unique(rows: list[Any]) -> list[Any]:
    unique = {row.threat_id: row for row in rows}
    return sorted(
        unique.values(),
        key=lambda row: (row.timestamp, row.threat_id),
        reverse=True,
    )[:RESULT_LIMIT]


def _cassandra_source_query(
    prepared: dict[str, Any], request: BenchmarkRequest
) -> dict[str, int]:
    rows = _cassandra_partition_rows(
        prepared, prepared["source"], request.source, request
    )
    rows = [row for row in rows if row.provenance == "imported"]
    return {"matched_count": len(_top_unique(rows))}


def _cassandra_indicator_query(
    prepared: dict[str, Any], request: BenchmarkRequest
) -> dict[str, int]:
    rows: list[Any] = []
    if request.target:
        rows.extend(_cassandra_partition_rows(
            prepared, prepared["target"], request.target, request
        ))
    if request.indicator:
        rows.extend(_cassandra_partition_rows(
            prepared, prepared["indicator"], request.indicator, request
        ))
    rows = [row for row in rows if row.provenance == "imported"]
    return {"matched_count": len(_top_unique(rows))}


def _cassandra_type_aggregation(
    prepared: dict[str, Any], request: BenchmarkRequest
) -> dict[str, Any]:
    rows = _cassandra_partition_rows(prepared, prepared["day"], None, request)
    rows = [row for row in rows if row.provenance == "imported"]
    groups = Counter(row.threat_type for row in rows)
    return {"group_count": len(groups), "record_count": sum(groups.values())}


def _cassandra_graph_query(
    prepared: dict[str, Any], seed_threat_id: str
) -> dict[str, int]:
    from cassandra.concurrent import execute_concurrent_with_args

    visited = {seed_threat_id}
    frontier = deque([(seed_threat_id, 0)])
    related_count = 0
    while frontier and related_count < GRAPH_MAX_NODES:
        threat_id, depth = frontier.popleft()
        if depth >= GRAPH_MAX_DEPTH:
            continue
        outcomes = execute_concurrent_with_args(
            prepared["session"], prepared["id"], [(threat_id,)]
        )
        succeeded, outcome = outcomes[0]
        if not succeeded:
            raise outcome
        row = next(iter(outcome), None)
        if row is None or row.provenance != "imported":
            continue
        for related_id in row.related_threats or []:
            if related_id in visited:
                continue
            visited.add(related_id)
            related_count += 1
            if related_count >= GRAPH_MAX_NODES:
                break
            frontier.append((related_id, depth + 1))
    return {"related_count": related_count}


def _neo4j_errors() -> tuple[type[BaseException], ...]:
    from neo4j.exceptions import DriverError, Neo4jError

    return DriverError, Neo4jError, OSError, RuntimeError, ValueError


def _neo4j_source_query(driver, database: str, request: BenchmarkRequest) -> dict[str, int]:
    with driver.session(database=database) as session:
        rows = list(session.run(
            """
            MATCH (t:Threat {from_source: $source})
            WHERE t.timestamp >= datetime($from_datetime)
              AND t.timestamp <= datetime($to_datetime)
              AND t.provenance = 'imported'
            RETURN t.threat_id AS threat_id, t.timestamp AS timestamp
            ORDER BY timestamp DESC, threat_id ASC
            LIMIT $limit
            """,
            source=request.source,
            from_datetime=request.from_datetime.isoformat(),
            to_datetime=request.to_datetime.isoformat(),
            limit=RESULT_LIMIT,
        ))
    return {"matched_count": len(rows)}


def _neo4j_indicator_query(driver, database: str, request: BenchmarkRequest) -> dict[str, int]:
    with driver.session(database=database) as session:
        rows = list(session.run(
            """
            MATCH (t:Threat)
            WHERE (t.target = $target OR t.indicator = $indicator)
              AND t.timestamp >= datetime($from_datetime)
              AND t.timestamp <= datetime($to_datetime)
              AND t.provenance = 'imported'
            RETURN t.threat_id AS threat_id, t.timestamp AS timestamp
            ORDER BY timestamp DESC, threat_id ASC
            LIMIT $limit
            """,
            target=request.target,
            indicator=request.indicator,
            from_datetime=request.from_datetime.isoformat(),
            to_datetime=request.to_datetime.isoformat(),
            limit=RESULT_LIMIT,
        ))
    return {"matched_count": len(rows)}


def _neo4j_type_aggregation(driver, database: str, request: BenchmarkRequest) -> dict[str, Any]:
    with driver.session(database=database) as session:
        rows = list(session.run(
            """
            MATCH (t:Threat)
            WHERE t.timestamp >= datetime($from_datetime)
              AND t.timestamp <= datetime($to_datetime)
              AND t.provenance = 'imported'
            RETURN t.threat_type AS threat_type, count(t) AS count
            ORDER BY threat_type
            """,
            from_datetime=request.from_datetime.isoformat(),
            to_datetime=request.to_datetime.isoformat(),
        ))
    return {
        "group_count": len(rows),
        "record_count": sum(row["count"] for row in rows),
    }


def _neo4j_graph_query(driver, database: str, seed_threat_id: str) -> dict[str, int]:
    with driver.session(database=database) as session:
        row = session.run(
            """
            MATCH (seed:Threat {threat_id: $threat_id})
            OPTIONAL MATCH path=(seed)-[:RELATED_TO*1..2]->(related:Threat)
            WHERE related.threat_id <> $threat_id
              AND related.provenance = 'imported'
            WITH collect(DISTINCT related) AS related_nodes
            RETURN CASE WHEN size(related_nodes) > $limit THEN $limit
                        ELSE size(related_nodes) END AS related_count
            """,
            threat_id=seed_threat_id,
            limit=GRAPH_MAX_NODES,
        ).single()
    return {"related_count": row["related_count"] if row else 0}


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 4)


def _measure(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    last_result = operation()
    samples: list[float] = []
    for _ in range(MEASURED_RUNS):
        started = time.perf_counter()
        last_result = operation()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "status": "measured",
        "warmup_runs": WARMUP_RUNS,
        "measured_runs": MEASURED_RUNS,
        "result": last_result,
        "latency_ms": {
            "min": round(min(samples), 4),
            "mean": round(mean(samples), 4),
            "p50": _percentile(samples, 0.50),
            "p95": _percentile(samples, 0.95),
            "p99": _percentile(samples, 0.99),
            "max": round(max(samples), 4),
            "samples": [round(sample, 4) for sample in samples],
        },
    }


def _backend_error_types(backend: str) -> tuple[type[BaseException], ...]:
    common: tuple[type[BaseException], ...] = (
        PyMongoError,
        OSError,
        RuntimeError,
        ValueError,
    )
    if backend == "cassandra":
        from cassandra import DriverException, OperationTimedOut
        from cassandra.cluster import NoHostAvailable

        return common + (DriverException, NoHostAvailable, OperationTimedOut)
    if backend == "neo4j":
        return common + _neo4j_errors()
    return common


def _operation_functions(
    backend: str,
    database: Database,
    connections: Chapter18Connections,
    request: BenchmarkRequest,
    seed_threat_id: str | None,
) -> dict[str, Callable[[], dict[str, Any]]]:
    if backend == "mongodb":
        collection = connections.mongo_database(database)[FULL_THREATS]
        functions = {
            "source_time_search": lambda: _mongo_source_query(collection, request),
            "target_indicator_search": lambda: _mongo_indicator_query(collection, request),
            "type_time_aggregation": lambda: _mongo_type_aggregation(collection, request),
        }
        if seed_threat_id:
            functions["explicit_relationship_traversal"] = lambda: _mongo_graph_query(
                collection, seed_threat_id
            )
        return functions

    if backend == "cassandra":
        prepared = _cassandra_statements(connections)
        functions = {
            "source_time_search": lambda: _cassandra_source_query(prepared, request),
            "target_indicator_search": lambda: _cassandra_indicator_query(prepared, request),
            "type_time_aggregation": lambda: _cassandra_type_aggregation(prepared, request),
        }
        if seed_threat_id:
            functions["explicit_relationship_traversal"] = lambda: _cassandra_graph_query(
                prepared, seed_threat_id
            )
        return functions

    driver = connections.neo4j_driver()
    database_name = connections.settings.neo4j_database
    functions = {
        "source_time_search": lambda: _neo4j_source_query(driver, database_name, request),
        "target_indicator_search": lambda: _neo4j_indicator_query(driver, database_name, request),
        "type_time_aggregation": lambda: _neo4j_type_aggregation(driver, database_name, request),
    }
    if seed_threat_id:
        functions["explicit_relationship_traversal"] = lambda: _neo4j_graph_query(
            driver, database_name, seed_threat_id
        )
    return functions


def run_benchmark(
    application_database: Database,
    connections: Chapter18Connections,
    request: BenchmarkRequest,
    database_status: dict[str, Any],
) -> dict[str, Any]:
    mongo = connections.mongo_database(application_database)
    imported_count = mongo[FULL_THREATS].count_documents({"provenance": "imported"})
    if imported_count < REQUIRED_IMPORT_RECORDS:
        raise BenchmarkPrerequisiteError(
            f"At least {REQUIRED_IMPORT_RECORDS:,} imported records are required; "
            f"the database currently contains {imported_count:,}."
        )
    date_filter = _time_query(request)
    if not mongo[FULL_THREATS].find_one({
        **date_filter,
        "from_source": request.source,
        "provenance": "imported",
    }, {"_id": 1}):
        raise BenchmarkPrerequisiteError(
            "No imported record matches the selected source and time range."
        )
    terms = []
    if request.target:
        terms.append({"target": request.target})
    if request.indicator:
        terms.append({"indicator": request.indicator})
    if not mongo[FULL_THREATS].find_one({
        **date_filter,
        "$or": terms,
        "provenance": "imported",
    }, {"_id": 1}):
        raise BenchmarkPrerequisiteError(
            "No imported record matches the selected target or indicator and time range."
        )
    seed_id = request.seed_threat_id
    if seed_id and not mongo[FULL_THREATS].find_one(
        {"threat_id": seed_id, "provenance": "imported"}, {"_id": 1}
    ):
        raise BenchmarkPrerequisiteError("The selected graph seed is not an imported record.")
    if not seed_id:
        seed = mongo[FULL_THREATS].find_one(
            {"related_threats.0": {"$exists": True}, "provenance": "imported"},
            {"_id": 0, "threat_id": 1},
        )
        seed_id = seed.get("threat_id") if seed else None

    results: dict[str, Any] = {}
    operation_names = (
        "source_time_search",
        "target_indicator_search",
        "type_time_aggregation",
        "explicit_relationship_traversal",
    )
    for backend in ("mongodb", "cassandra", "neo4j"):
        state = database_status.get(backend, {})
        if state.get("status") != "connected" or not state.get("schema_ready"):
            results[backend] = {
                name: {
                    "status": "not_tested",
                    "reason": "Database is not connected or its Chapter 18 schema is not ready.",
                }
                for name in operation_names
            }
            continue
        if backend == "cassandra" and not connections.settings.cassandra_hosts:
            results[backend] = {
                name: {"status": "not_tested", "reason": "Cassandra is not configured."}
                for name in operation_names
            }
            continue
        if backend == "neo4j" and not connections.settings.neo4j_uri:
            results[backend] = {
                name: {"status": "not_tested", "reason": "Neo4j is not configured."}
                for name in operation_names
            }
            continue
        try:
            functions = _operation_functions(
                backend,
                application_database,
                connections,
                request,
                seed_id,
            )
        except _backend_error_types(backend) as error:
            logger.exception("%s benchmark preparation failed", backend)
            results[backend] = {
                name: {
                    "status": "not_tested",
                    "reason": f"Database preparation failed ({type(error).__name__}).",
                }
                for name in operation_names
            }
            continue

        backend_results: dict[str, Any] = {}
        for name in operation_names:
            operation = functions.get(name)
            if operation is None:
                backend_results[name] = {
                    "status": "not_tested",
                    "reason": "No imported explicit related-threat identifiers are available.",
                }
                continue
            try:
                backend_results[name] = _measure(operation)
            except _backend_error_types(backend) as error:
                logger.exception("%s benchmark operation %s failed", backend, name)
                backend_results[name] = {
                    "status": "failed",
                    "error_type": type(error).__name__,
                }
        results[backend] = backend_results

    run_id = str(uuid4())
    completed_at = datetime.now(timezone.utc)
    statuses = [
        operation.get("status")
        for backend in results.values()
        for operation in backend.values()
    ]
    overall_status = (
        "completed"
        if statuses and all(status == "measured" for status in statuses)
        else "partial"
        if any(status == "measured" for status in statuses)
        else "not_tested"
    )
    run = {
        "run_id": run_id,
        "status": overall_status,
        "dataset": {
            "provenance": "imported",
            "record_count": imported_count,
            "minimum_required": REQUIRED_IMPORT_RECORDS,
        },
        "filters": request.model_dump(mode="json"),
        "seed_threat_id": seed_id,
        "warmup_runs": WARMUP_RUNS,
        "measured_runs": MEASURED_RUNS,
        "created_at": completed_at,
        "completed_at": completed_at,
        "results": results,
    }
    mongo[BENCHMARK_RUNS].insert_one(run.copy())
    run.pop("_id", None)
    return run
