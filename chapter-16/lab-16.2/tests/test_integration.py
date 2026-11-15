from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.database import DatabaseConnections, ensure_fraud_schema
from app.fraud_query_service import FraudQueryService
from app.seed_demo import seed_demo
from app.config import Settings


def test_complete_fraud_analysis_workflow(real_connections):
    ensure_fraud_schema(
        real_connections.cassandra_session,
        real_connections.neo4j_driver,
        real_connections.neo4j_database,
    )
    seed_demo(real_connections)
    service = FraudQueryService(
        real_connections.transactions,
        real_connections.cassandra_session,
        real_connections.neo4j_driver,
        real_connections.neo4j_database,
    )
    result = service.analyze_user("USER001", "TXN001", max_hops=3)
    assert result["analysis_status"] == "completed"
    assert result["transaction"]["txn_id"] == "TXN001"
    assert len(result["recent_events"]) >= 1
    assert {"USER002", "USER003", "USER004"} <= set(result["connected_accounts"])
    assert "transaction_flagged_or_high_risk" in result["fraud_indicators"]


def test_polyglot_enrollment_queries_return_consistent_sets(
    mongo_database, cassandra_session, neo4j_session
):
    from conftest import LAB_161, load_module

    mongo_seed = load_module("integ_mongo_seed", LAB_161 / "mongodb" / "seed_data.py")
    mongo_queries = load_module("integ_mongo_queries", LAB_161 / "mongodb" / "queries.py")
    cassandra_seed = load_module("integ_cassandra_seed", LAB_161 / "cassandra" / "seed_data.py")
    cassandra_query_module = load_module("integ_cassandra_queries", LAB_161 / "cassandra" / "queries.py")
    neo_seed = load_module("integ_neo_seed", LAB_161 / "neo4j" / "seed_data.py")
    neo_queries = load_module("integ_neo_queries", LAB_161 / "neo4j" / "queries.py")
    mongo_seed.seed(mongo_database)
    cassandra_seed.seed(cassandra_session)
    neo_seed.seed(neo4j_session)
    cassandra_queries = cassandra_query_module.EnrollmentQueries(cassandra_session)

    for mongo_result, cassandra_result, neo_result, key in (
        (mongo_queries.courses_for_student(mongo_database, "S001"),
         cassandra_queries.courses_for_student("S001"),
         neo_queries.courses_for_student(neo4j_session, "S001"), "course_id"),
        (mongo_queries.students_in_course(mongo_database, "C101"),
         cassandra_queries.students_in_course("C101"),
         neo_queries.students_in_course(neo4j_session, "C101"), "student_id"),
        (mongo_queries.students_sharing_course(mongo_database, "S001"),
         cassandra_queries.students_sharing_course("S001"),
         neo_queries.students_sharing_course(neo4j_session, "S001"), "student_id"),
    ):
        assert {row[key] for row in mongo_result} == {row[key] for row in cassandra_result}
        assert {row[key] for row in mongo_result} == {row[key] for row in neo_result}


def test_database_resource_close_closes_each_backend():
    from unittest.mock import Mock

    mongo, cassandra_cluster, cassandra_session, neo = Mock(), Mock(), Mock(), Mock()
    connections = DatabaseConnections(
        mongo_client=mongo, mongo_database=Mock(),
        cassandra_cluster=cassandra_cluster, cassandra_session=cassandra_session,
        neo4j_driver=neo, neo4j_database="neo4j",
    )
    connections.close()
    neo.close.assert_called_once()
    cassandra_session.shutdown.assert_called_once()
    cassandra_cluster.shutdown.assert_called_once()
    mongo.close.assert_called_once()
