from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.chapter18 import AnalystReview, BenchmarkRequest, ThreatRecord
from backend.routes.chapter18 import router
from backend.services.chapter18_benchmark import MEASURED_RUNS, _measure, _percentile
from backend.services.chapter18_connections import Chapter18Connections
from backend.services.chapter18_adapters import _cassandra_args, _shard_for
from backend.services.chapter18_ingestion import _confirmed_mongo_records, ThreatIdConflictError


def test_threat_record_normalizes_time_confidence_and_identifiers():
    record = ThreatRecord.model_validate({
        "threat_id": " t-1 ", "source": " feed ", "from_source": " feed-a ",
        "threat_type": " malware ", "timestamp": "2026-01-01T00:00:00Z",
        "confidence": 75, "related_threats": ["t-2", "t-2", " t-3 "],
    })
    assert record.threat_id == "t-1"
    assert record.confidence == 0.75
    assert record.timestamp.tzinfo == timezone.utc
    assert record.related_threats == ["t-2", "t-3"]


def test_threat_record_rejects_unpaired_coordinates_and_extra_fields():
    base = {"threat_id": "t", "source": "s", "from_source": "f", "threat_type": "x", "timestamp": datetime.now(timezone.utc)}
    with pytest.raises(ValueError):
        ThreatRecord.model_validate({**base, "latitude": 1.0})
    with pytest.raises(ValueError):
        ThreatRecord.model_validate({**base, "unexpected": True})


def test_benchmark_request_requires_exact_filter_and_bounded_time_range():
    with pytest.raises(ValueError):
        BenchmarkRequest(source="feed", from_datetime="2026-01-01T00:00:00Z", to_datetime="2026-01-02T00:00:00Z")
    with pytest.raises(ValueError):
        BenchmarkRequest(source="feed", target="example.org", from_datetime="2026-01-01T00:00:00Z", to_datetime="2026-03-01T00:00:00Z")


def test_benchmark_calculations_use_ten_measured_samples_and_nearest_rank_percentiles():
    assert MEASURED_RUNS == 10
    assert _percentile([10, 1, 9, 2, 8, 3, 7, 4, 6, 5], .50) == 5
    calls = []
    result = _measure(lambda: calls.append(len(calls)) or {"record_count": 1})
    assert result["status"] == "measured"
    assert result["measured_runs"] == 10
    assert len(result["latency_ms"]["samples"]) == 10
    assert len(calls) == 11  # one warm-up followed by ten timed calls


def test_chapter18_endpoints_reject_anonymous_users():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for method, path, body in (
            ("GET", "/api/chapter18/status", None),
            ("POST", "/api/chapter18/imports", {"records": []}),
            ("POST", "/api/chapter18/benchmarks", {}),
            ("GET", "/api/chapter18/reviews", None),
        ):
            response = client.request(method, path, json=body)
            assert response.status_code == 401


def test_analyst_review_requires_a_rationale():
    with pytest.raises(ValueError):
        AnalystReview(decision="false_positive", reason="  ")


def test_invalid_optional_database_configuration_is_reported_without_exposing_secrets(monkeypatch):
    monkeypatch.setenv("LAB18_CASSANDRA_PORT", "not-a-port")
    connections = Chapter18Connections()
    assert connections.configuration_error
    assert "not-a-port" not in connections.configuration_error
    assert "password" not in repr(connections.settings).lower()


def test_database_adapter_partitioning_is_stable_and_timestamp_bucketed():
    record = ThreatRecord.model_validate({
        "threat_id": "threat-42", "source": "feed", "from_source": "feed-a",
        "threat_type": "malware", "timestamp": "2026-04-05T12:30:00Z",
        "target": "example.org", "indicator": "192.0.2.1",
    })
    assert _shard_for(record.threat_id) == _shard_for(record.threat_id)
    assert 0 <= _shard_for(record.threat_id) < 16
    args = _cassandra_args(record, "from_source")
    assert args[:4] == ("feed-a", record.timestamp.date(), _shard_for(record.threat_id), record.timestamp)


def test_peer_adapters_only_receive_records_confirmed_in_mongodb():
    record = ThreatRecord.model_validate({
        "threat_id": "persisted", "source": "feed", "from_source": "feed-a",
        "threat_type": "malware", "timestamp": "2026-04-05T12:30:00Z",
    })
    collection = MagicMock()
    collection.find.return_value = [record.model_dump(mode="python")]
    assert _confirmed_mongo_records(collection, [record])[0] == record
    collection.find.return_value = [{**record.model_dump(mode="python"), "source": "other"}]
    with pytest.raises(ThreatIdConflictError):
        _confirmed_mongo_records(collection, [record])
