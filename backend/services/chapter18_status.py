from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import PyMongoError

from backend.services.chapter18_connections import (
    Chapter18ConfigurationError,
    Chapter18Connections,
    Chapter18DependencyError,
)
from backend.services.chapter18_storage import FULL_THREATS


def _unavailable(error: Exception) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "error_type": type(error).__name__,
    }


def _not_configured(message: str) -> dict[str, Any]:
    return {
        "status": "not_configured",
        "message": message,
    }


def mongodb_status(
    application_database: Database,
    connections: Chapter18Connections,
) -> dict[str, Any]:
    try:
        database = connections.mongo_database(application_database)
        ping = application_database.client.admin.command({"ping": 1})
        build_info = application_database.client.admin.command({"buildInfo": 1})
        collections = set(database.list_collection_names())
        record_count = (
            database[FULL_THREATS].count_documents({})
            if FULL_THREATS in collections
            else 0
        )
        index_names = (
            {index["name"] for index in database[FULL_THREATS].list_indexes()}
            if FULL_THREATS in collections
            else set()
        )
        required_indexes = {
            "lab18_threat_id_unique",
            "lab18_source_provenance_time",
            "lab18_provenance_time_type",
            "lab18_target_provenance_time",
            "lab18_indicator_provenance_time",
        }
        return {
            "status": "connected",
            "version": build_info.get("version"),
            "database": database.name,
            "record_count": record_count,
            "schema_ready": (
                FULL_THREATS in collections and required_indexes.issubset(index_names)
            ),
            "ping": bool(ping.get("ok")),
        }
    except (Chapter18ConfigurationError, PyMongoError, OSError) as error:
        return _unavailable(error)


def cassandra_status(connections: Chapter18Connections) -> dict[str, Any]:
    if connections.configuration_error:
        return _unavailable(Chapter18ConfigurationError(connections.configuration_error))
    if not connections.settings.cassandra_hosts:
        return _not_configured("Set LAB18_CASSANDRA_HOSTS to check Cassandra.")
    try:
        from cassandra import DriverException, OperationTimedOut
        from cassandra.cluster import NoHostAvailable
    except ImportError as error:
        return _unavailable(Chapter18DependencyError(str(error)))
    try:
        session = connections.cassandra_session()
        local = session.execute("SELECT release_version, data_center FROM system.local").one()
        tables = session.execute(
            "SELECT table_name FROM system_schema.tables WHERE keyspace_name = %s",
            [connections.settings.cassandra_keyspace],
        )
        table_names = {row.table_name for row in tables}
        required_tables = {
            "threats_by_id",
            "threats_by_source",
            "threats_by_type",
            "threats_by_target",
            "threats_by_indicator",
            "threats_by_day",
        }
        return {
            "status": "connected",
            "version": local.release_version,
            "datacenter": local.data_center,
            "keyspace": connections.settings.cassandra_keyspace,
            "schema_ready": required_tables.issubset(table_names),
        }
    except (
        Chapter18ConfigurationError,
        Chapter18DependencyError,
        DriverException,
        NoHostAvailable,
        OperationTimedOut,
        OSError,
        RuntimeError,
    ) as error:
        return _unavailable(error)


def neo4j_status(connections: Chapter18Connections) -> dict[str, Any]:
    if connections.configuration_error:
        return _unavailable(Chapter18ConfigurationError(connections.configuration_error))
    if not connections.settings.neo4j_uri:
        return _not_configured("Set LAB18_NEO4J_URI to check Neo4j.")
    try:
        from neo4j.exceptions import DriverError, Neo4jError
    except ImportError as error:
        return _unavailable(Chapter18DependencyError(str(error)))
    try:
        driver = connections.neo4j_driver()
        driver.verify_connectivity()
        with driver.session(database=connections.settings.neo4j_database) as session:
            components = session.run("CALL dbms.components()").single()
            constraints = {
                row["name"]
                for row in session.run("SHOW CONSTRAINTS YIELD name RETURN name")
            }
            indexes = {
                row["name"]
                for row in session.run(
                    "SHOW INDEXES YIELD name WHERE name IN "
                    "['threat_source_time', 'threat_indicator_time', "
                    "'threat_target_time', 'threat_provenance_time'] RETURN name"
                )
            }
        component_versions = components.get("versions", []) if components else []
        expected_constraints = {
            "threat_id_unique",
            "source_key_unique",
            "target_key_unique",
            "threat_type_key_unique",
            "indicator_value_unique",
        }
        expected_indexes = {
            "threat_source_time",
            "threat_indicator_time",
            "threat_target_time",
            "threat_provenance_time",
        }
        return {
            "status": "connected",
            "version": component_versions[0] if component_versions else None,
            "database": connections.settings.neo4j_database,
            "schema_ready": (
                expected_constraints.issubset(constraints)
                and expected_indexes.issubset(indexes)
            ),
        }
    except (
        Chapter18ConfigurationError,
        Chapter18DependencyError,
        DriverError,
        Neo4jError,
        OSError,
        RuntimeError,
    ) as error:
        return _unavailable(error)


def chapter18_status(
    application_database: Database,
    connections: Chapter18Connections,
) -> dict[str, Any]:
    return {
        "mongodb": mongodb_status(application_database, connections),
        "cassandra": cassandra_status(connections),
        "neo4j": neo4j_status(connections),
    }
