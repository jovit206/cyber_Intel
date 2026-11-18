from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import chapter14_ops
from backend.services import chapter14_ops as service


def test_monitoring_window_derives_operations_rate_from_actual_counter_samples():
    now = datetime.now(timezone.utc)
    history = [
        {"timestamp": now - timedelta(seconds=30), "opcounters": {"insert": 10, "query": 40}},
        {"timestamp": now, "opcounters": {"insert": 20, "query": 60}},
    ]
    database = MagicMock()
    database[service.METRICS].find.return_value = history
    database[service.ALERTS].count_documents.return_value = 2
    database[service.BACKUP_RUNS].find_one.side_effect = [None, None, None]
    database[service.WORKER_STATUS].find_one.return_value = None

    result = service.metric_window(database, 60)

    assert result["source"] == "live_mongodb_server_status"
    assert result["sample_count"] == 2
    assert result["history"][0]["operations_per_second"] is None
    assert result["latest"]["operations_per_second"] == 1.0
    assert result["alert_count"] == 2
    assert result["available"] is True


def test_chapter14_routes_require_admin_session():
    app = FastAPI()
    app.include_router(chapter14_ops.router)
    with TestClient(app) as client:
        assert client.get("/api/ops/metrics/monitoring").status_code == 401
        assert client.get("/api/moderation/alerts").status_code == 401
        assert client.get("/api/ops/lab14/backups").status_code == 401
        assert client.post("/api/ops/lab14/backup", json={"confirm": True}).status_code == 401
        assert client.post("/api/ops/lab14/verify", json={"confirm": True}).status_code == 401
        assert client.post("/api/moderation/alerts/ack", json={"alert_id": "alert-x"}).status_code == 401


def test_backup_and_restore_verification_require_explicit_confirmation():
    app = FastAPI()
    app.include_router(chapter14_ops.router)
    app.dependency_overrides[chapter14_ops._admin_user] = lambda: {"user_id": "admin", "role": "admin"}
    with TestClient(app) as client:
        backup = client.post("/api/ops/lab14/backup", json={"confirm": False})
        verify = client.post("/api/ops/lab14/verify", json={"confirm": False})
    assert backup.status_code == 400
    assert verify.status_code == 400


def test_acknowledgement_is_audited_before_alert_status_changes():
    app = FastAPI()
    app.include_router(chapter14_ops.router)
    app.dependency_overrides[chapter14_ops._admin_user] = lambda: {"user_id": "admin-1", "role": "admin"}
    database = MagicMock()
    database[service.ALERTS].find_one.return_value = {"alert_id": "alert-1", "status": "active"}
    with patch.object(chapter14_ops, "get_database", return_value=database), \
         patch.object(chapter14_ops, "_audit") as audit, TestClient(app) as client:
        response = client.post("/api/moderation/alerts/ack", json={"alert_id": "alert-1", "reason": "Reviewed"})
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"
    assert audit.call_args.args[2] == "alert.ack"
    database[service.ALERTS].update_one.assert_called_once()


def test_signed_webhook_rejects_missing_secret_and_invalid_signature():
    app = FastAPI()
    app.include_router(chapter14_ops.router)
    with patch.dict("os.environ", {"CH14_ALERT_WEBHOOK_SECRET": "test-only-secret"}, clear=False), TestClient(app) as client:
        response = client.post("/api/moderation/alerts/webhook", json={"metric": "external"}, headers={"X-Chapter14-Signature": "sha256=invalid"})
    assert response.status_code == 401


def test_signed_webhook_accepts_only_valid_hmac_and_records_event():
    app = FastAPI()
    app.include_router(chapter14_ops.router)
    app.state.database = MagicMock()
    body = json.dumps({"source": "test-monitor", "metric": "disk", "current_value": 91}).encode()
    signature = hmac.new(b"test-only-secret", body, hashlib.sha256).hexdigest()
    with patch.dict("os.environ", {"CH14_ALERT_WEBHOOK_SECRET": "test-only-secret"}, clear=False), TestClient(app) as client:
        response = client.post("/api/moderation/alerts/webhook", content=body, headers={"Content-Type": "application/json", "X-Chapter14-Signature": f"sha256={signature}"})
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    app.state.database[service.ALERTS].update_one.assert_called_once()

def test_scheduler_compatibility_name_delegates_to_existing_sampler(monkeypatch):
    database = object()
    expected = {"source": "live_mongodb_server_status"}
    monkeypatch.setattr(service, "collect_sample", lambda supplied: expected if supplied is database else None)

    assert service.sample_monitoring_metrics(database) is expected
