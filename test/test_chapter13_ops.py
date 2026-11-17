from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect

from backend.routes import chapter13_ops
from backend.services.chapter13_ops import build_deployment_sample, integration_link_status


def _replica(primary="mongo-0:27017", term=4, now=None):
    now = now or datetime.now(timezone.utc)
    members = [
        {"_id": 0, "name": primary, "stateStr": "PRIMARY", "health": 1, "uptime": 3600, "pingMs": 0, "optimeDate": now, "lastHeartbeat": now},
        {"_id": 1, "name": "mongo-1:27017", "stateStr": "SECONDARY", "health": 1, "uptime": 3500, "pingMs": 2, "optimeDate": now - timedelta(milliseconds=120), "lastHeartbeat": now},
    ]
    return {"set": "rs0", "term": term, "members": members}


def test_deployment_sample_uses_real_server_counters_and_replica_state():
    now = datetime.now(timezone.utc)
    previous = {
        "timestamp": now - timedelta(seconds=10),
        "primary": "mongo-0:27017",
        "opcounter_total": 100,
        "cache_counters": {"requests": 1000, "reads": 100},
    }
    sample = build_deployment_sample(
        _replica(now=now),
        {
            "opcounters": {"insert": 20, "query": 80, "update": 10},
            "connections": {"current": 7, "available": 42, "totalCreated": 99},
            "wiredTiger": {"cache": {
                "pages requested from the cache": 1200,
                "pages read into cache": 120,
                "bytes currently in the cache": 4096,
                "maximum bytes configured": 8192,
                "tracked dirty bytes in the cache": 512,
                "pages evicted by application threads": 5,
            }},
        },
        previous,
        now,
    )
    assert sample["source"] == "live_mongodb_server_status"
    assert sample["primary"] == "mongo-0:27017"
    assert sample["healthy"] is True
    assert sample["operations_per_second"] == 1.0
    assert sample["replication_lag_ms"]["mongo-1:27017"] == 120
    assert sample["cache"]["hit_ratio_interval"] == 0.9
    assert sample["connections"]["current"] == 7
    assert sample["election"] is None


def test_deployment_sample_marks_observed_primary_change():
    now = datetime.now(timezone.utc)
    sample = build_deployment_sample(_replica("mongo-1:27017", now=now), {"opcounters": {}}, {"timestamp": now-timedelta(seconds=10), "primary": "mongo-0:27017", "opcounter_total": 0}, now)
    assert sample["election"] == {
        "previous_primary": "mongo-0:27017",
        "new_primary": "mongo-1:27017",
        "timestamp": now,
    }


def test_monitoring_link_never_returns_url_credentials_or_query_tokens():
    import os

    with patch.dict(os.environ, {
        "LAB13_PROMETHEUS_URL": "",
        "LAB13_PROMETHEUS_PUBLIC_URL": "https://monitor-user:secret@metrics.example/d/overview?token=private",
    }, clear=False):
        result = integration_link_status("PROMETHEUS")
    assert result == {
        "status": "not_configured",
        "url": "https://metrics.example/d/overview",
    }


def test_chapter13_operations_endpoints_require_admin_authentication():
    app = FastAPI()
    app.include_router(chapter13_ops.router)
    with TestClient(app) as client:
        assert client.get("/api/ops/overview").status_code == 401
        assert client.get("/api/ops/metrics/deployment").status_code == 401
        assert client.post("/api/ops/lab13/failover", json={"confirm": True}).status_code == 401
        assert client.post("/api/services/lab13/kubernetes", json={"confirm": True}).status_code == 401


def test_chapter13_rejects_unconfirmed_operations_without_starting_them():
    app = FastAPI()
    app.include_router(chapter13_ops.router)
    app.dependency_overrides[chapter13_ops._admin_user] = lambda: {"user_id": "admin", "role": "admin"}
    with TestClient(app) as client:
        failover = client.post("/api/ops/lab13/failover", json={"confirm": False})
        kubernetes = client.post("/api/services/lab13/kubernetes", json={"confirm": False})
    assert failover.status_code == 400
    assert kubernetes.status_code == 400


def test_failover_requires_observed_new_primary_and_records_audit_markers():
    app = FastAPI()
    app.include_router(chapter13_ops.router)
    app.dependency_overrides[chapter13_ops._admin_user] = lambda: {"user_id": "admin-1", "role": "admin"}
    database = MagicMock()
    database.client.admin.command.side_effect = [
        _replica("mongo-0:27017"),
        AutoReconnect("primary stepped down"),
        _replica("mongo-1:27017", term=5),
    ]
    marker = {"timestamp": datetime.now(timezone.utc).isoformat(), "previous_primary": "mongo-0:27017", "new_primary": "mongo-1:27017", "source": "confirmed_failover_operation"}
    with patch.object(chapter13_ops, "get_database", return_value=database), \
         patch.object(chapter13_ops.service, "mark_election", return_value=marker), \
         patch.object(chapter13_ops, "_audit") as audit, TestClient(app) as client:
        response = client.post("/api/ops/lab13/failover", json={"confirm": True, "seconds": 5})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "verified"
    assert response.json()["new_primary"] == "mongo-1:27017"
    actions = [call.args[2] for call in audit.call_args_list]
    assert "ops.failover" in actions
    assert "lab.run" in actions

