from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from app.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.fraud_query_service import FraudQueryService


class FakeNeoSession:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, query, **params):
        assert params["user_id"] == "USER001"
        return [{"path": row} for row in self.rows]


class FakeNeoDriver:
    def __init__(self, rows):
        self.rows = rows

    def session(self, **_):
        return FakeNeoSession(self.rows)


def make_service():
    mongo = Mock()
    cassandra = Mock()
    cassandra.prepare.return_value = "prepared"
    cassandra.execute.return_value = [
        {"user_id": "USER001", "event_type": "login", "amount": 20}
    ]
    return FraudQueryService(mongo, cassandra, FakeNeoDriver([["USER001", "USER002"]]))


def test_mongodb_transaction_lookup_and_missing_transaction():
    service = make_service()
    service.mongo_transactions.find_one.return_value = {"txn_id": "TXN001", "amount": 25}
    assert service.get_transaction("TXN001") == {"txn_id": "TXN001", "amount": 25}
    service.mongo_transactions.find_one.assert_called_once_with({"txn_id": "TXN001"}, {"_id": 0})
    service.mongo_transactions.find_one.return_value = None
    assert service.get_transaction("missing") is None


def test_cassandra_profile_uses_prepared_query_and_limits_rows():
    service = make_service()
    events = service.get_user_profile("USER001")
    assert events == [{"user_id": "USER001", "event_type": "login", "amount": 20}]
    assert "LIMIT 10" in service.cassandra_session.prepare.call_args.args[0]
    service.cassandra_session.execute.assert_called_once_with("prepared", ("USER001",))


def test_neo4j_fraud_ring_query_is_bounded_and_parameterized():
    service = make_service()
    assert service.detect_fraud_rings("USER001") == [{
        "path": ["USER001", "USER002"], "connected_user_id": "USER002"
    }]
    with pytest.raises(ValueError):
        service.detect_fraud_rings("USER001", max_hops=100)


def test_combined_analysis_merges_actual_backend_results_and_indicators():
    service = make_service()
    service.mongo_transactions.find_one.return_value = {
        "txn_id": "TXN001", "user_id": "USER001", "risk_score": 0.9, "status": "flagged"
    }
    result = service.analyze_user("USER001", "TXN001")
    assert result["analysis_status"] == "completed"
    assert result["transaction"]["txn_id"] == "TXN001"
    assert len(result["recent_events"]) == 1
    assert result["connected_accounts"] == ["USER002"]
    assert set(result["fraud_indicators"]) == {
        "transaction_flagged_or_high_risk", "connected_transfer_paths_found"
    }


def test_invalid_hop_limit_is_rejected_before_backend_queries():
    service = make_service()
    with pytest.raises(ValueError):
        service.analyze_user("USER001", "TXN001", max_hops=6)
    service.mongo_transactions.find_one.assert_not_called()


def test_partial_backend_failure_is_reported_without_fabricated_data():
    service = make_service()
    service.mongo_transactions.find_one.return_value = None
    service.cassandra_session.prepare.side_effect = TimeoutError("backend unavailable")
    result = service.analyze_user("USER001", "TXN001")
    assert result["analysis_status"] == "partial"
    assert result["unavailable_backends"] == ["cassandra"]
    assert result["backend_errors"]["cassandra"] == "TimeoutError"
    assert result["recent_events"] == []


@pytest.mark.parametrize("backend", ["mongodb", "cassandra", "neo4j"])
def test_each_backend_connection_failure_yields_a_partial_analysis(backend):
    service = make_service()
    if backend == "mongodb":
        service.mongo_transactions.find_one.side_effect = TimeoutError("MongoDB unavailable")
    elif backend == "cassandra":
        service.cassandra_session.prepare.side_effect = TimeoutError("Cassandra unavailable")
    else:
        service.neo4j_driver = Mock()
        service.neo4j_driver.session.side_effect = TimeoutError("Neo4j unavailable")
    result = service.analyze_user("USER001", "TXN001")
    assert backend in result["unavailable_backends"]
    assert result["analysis_status"] == "partial"
    assert result["backend_errors"][backend] == "TimeoutError"


def test_backend_circuit_open_is_reported_as_unavailable():
    breaker = CircuitBreaker()
    for _ in range(3):
        with pytest.raises(ConnectionError):
            breaker.call("mongodb", lambda: (_ for _ in ()).throw(ConnectionError()))
    service = FraudQueryService(Mock(), Mock(), FakeNeoDriver([]), circuit_breaker=breaker)
    with pytest.raises(CircuitOpenError):
        service.get_transaction("TXN001")


def test_neo4j_max_hops_and_path_cap_are_enforced():
    service = FraudQueryService(Mock(), Mock(), FakeNeoDriver([
        ["USER001", f"USER{i:03d}"] for i in range(100)
    ]))
    assert len(service.detect_fraud_rings("USER001", max_hops=2)) == 50
