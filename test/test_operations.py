from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from bson import ObjectId

from backend.routes import api as api_routes
from backend.routes import operations as operations_routes
from backend.services import operations


def _request(path: str, app=None) -> Request:
    return Request({
        "app": app or FastAPI(),
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"private=value",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    })


def test_operation_record_distinguishes_failure_warning_and_success_without_sensitive_fields():
    base = {
        "event_id": "event-1",
        "correlation_id": "correlation-1",
        "method": "GET",
        "route_path": "/api/users/{user_id}",
        "timestamp": datetime.now(timezone.utc),
        "completed_at": datetime.now(timezone.utc),
        "duration_ms": 4.1,
    }

    success = operations.build_operation_record(**base, http_status_code=200)
    warning = operations.build_operation_record(**base, http_status_code=404)
    failed = operations.build_operation_record(**base, http_status_code=503)

    assert success["status"] == "success"
    assert warning["status"] == "warning"
    assert failed["status"] == "failed"
    assert failed["error_type"] == "HTTP_503"
    assert failed["recovery_status"] == "not_recorded"
    assert failed["recovery_attempts"] is None
    assert failed["operation"] == "GET /api/users/{user_id}"
    assert failed["error_message"] is None
    assert "private" not in repr(failed)
    assert "user_id_value" not in failed


def test_operation_tracking_skips_dashboard_and_auth_endpoints():
    assert operations.should_track_request(_request("/api/admin/dashboard"))
    assert not operations.should_track_request(_request("/api/operations/overview"))
    assert not operations.should_track_request(_request("/api/rag/query"))
    assert not operations.should_track_request(_request("/api/vector-benchmark/runs"))
    assert not operations.should_track_request(_request("/api/subscriptions/login"))
    assert not operations.should_track_request(_request("/health"))


def test_tracking_middleware_persists_route_template_not_query_values():
    records = []
    app = FastAPI()
    app.state.database = {"system_operations": SimpleNamespace(insert_one=records.append)}

    @app.middleware("http")
    async def operation_tracker(request, call_next):
        return await operations.track_api_request(request, call_next)

    @app.get("/api/lab9/items/{item_id}")
    def get_item(item_id: str):
        return {"item_id": item_id}

    with TestClient(app) as client:
        response = client.get("/api/lab9/items/secret-value?token=private")

    assert len(records) == 1
    assert records[0]["operation"] == "GET /api/lab9/items/{item_id}"
    assert records[0]["component"] == "Reliability experiments"
    assert records[0]["status"] == "success"
    assert "private" not in repr(records[0])
    assert response.headers["x-correlation-id"] == records[0]["correlation_id"]
    assert operations.active_request_count() == 0


def test_incompatible_database_handle_does_not_break_api_request(caplog):
    app = FastAPI()
    app.state.database = object()

    @app.middleware("http")
    async def operation_tracker(request, call_next):
        return await operations.track_api_request(request, call_next)

    @app.get("/api/status")
    def get_status():
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    assert app.state.operations_tracking_enabled is False
    assert "incompatible database handle" in caplog.text


def test_tracking_index_has_30_day_ttl_and_timestamp_sorting():
    database = MagicMock()

    operations.ensure_indexes(database)

    database.__getitem__.return_value.create_index.assert_called_once_with(
        [("timestamp", 1)],
        name="system_operations_timestamp_ttl",
        expireAfterSeconds=30 * 24 * 60 * 60,
    )


def test_cache_hit_ratio_waits_for_a_real_counter_interval(monkeypatch):
    database = MagicMock()
    status = {
        "wiredTiger": {
            "cache": {
                "maximum bytes configured": 1000,
                "bytes currently in the cache": 500,
                "tracked dirty bytes in the cache": 10,
                "pages read into cache": 5,
                "pages evicted by application threads": 0,
                "pages requested from the cache": 10,
            }
        }
    }
    database.client.admin.command.return_value = status
    monkeypatch.setattr(api_routes, "previous_cache_sample", None)
    monkeypatch.setattr(api_routes, "metrics_history", [])

    first = api_routes.sample_cache_metrics(database)

    assert first["hit_ratio_interval"] is None

    status["wiredTiger"]["cache"]["pages read into cache"] = 7
    status["wiredTiger"]["cache"]["pages requested from the cache"] = 20
    second = api_routes.sample_cache_metrics(database)

    assert second["hit_ratio_interval"] == 0.8


class OperationsRouteTests:
    def setup_method(self):
        self.app = FastAPI()
        self.app.include_router(operations_routes.router)
        self.client = TestClient(self.app)

    def teardown_method(self):
        self.client.close()

    @patch("backend.routes.admin.get_database")
    def test_operations_api_requires_admin(self, get_database):
        get_database.return_value = MagicMock()

        response = self.client.get("/api/operations/overview")

        assert response.status_code == 401

    @patch("backend.routes.admin.get_database")
    def test_overview_reports_recovery_as_unavailable_without_recorded_events(self, get_database):
        database = MagicMock()
        database.__getitem__.side_effect = lambda name: getattr(database, name)
        database.subscription_sessions.find_one.return_value = {"user_id": "admin"}
        database.subscription_users.find_one.return_value = {"user_id": "admin", "role": "admin"}
        database.system_operations.aggregate.side_effect = [[], [], [], []]
        database.system_operations.find_one.return_value = None
        database.system_operations.find.return_value.sort.return_value.limit.return_value = []
        database.admin_audit_log.count_documents.return_value = 0
        database.admin_audit_log.find_one.return_value = None
        database.client.admin.command.side_effect = [
            {"ok": 1},
            {"setName": "rs0", "hosts": ["mongo-1:27017"], "primary": "mongo-1:27017"},
        ]
        get_database.return_value = database
        self.app.state.database = database
        self.app.state.scheduler = SimpleNamespace(running=True, get_jobs=lambda: [])
        self.app.state.operations_tracking_started_at = datetime.now(timezone.utc)
        self.app.state.operations_tracking_enabled = True

        response = self.client.get(
            "/api/operations/overview",
            cookies={"aegis_session": "test-token"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["total_operations"] == 0
        assert payload["summary"]["recovered_operations"] is None
        assert payload["summary"]["total_recovery_attempts"] is None
        assert payload["tracking"]["tracking_enabled"] is True
        assert payload["tracking"]["historical_data_available"] is False

    @patch("backend.routes.admin.get_database")
    def test_activity_includes_safe_historical_admin_audit_fields(self, get_database):
        database = MagicMock()
        database.__getitem__.side_effect = lambda name: getattr(database, name)
        database.subscription_sessions.find_one.return_value = {"user_id": "admin"}
        database.subscription_users.find_one.return_value = {"user_id": "admin", "role": "admin"}
        database.system_operations.count_documents.return_value = 0
        database.system_operations.find.return_value.sort.return_value.limit.return_value = []
        audit_record = {
            "_id": ObjectId(),
            "action": "login",
            "outcome": "failure",
            "actor_role": "admin",
            "created_at": datetime.now(timezone.utc),
            "administrator_id": "private-user-id",
            "target_record": "private-target",
            "details": {"token": "private-token"},
        }
        database.admin_audit_log.count_documents.return_value = 1
        database.admin_audit_log.find.return_value.sort.return_value.limit.return_value = [audit_record]
        get_database.return_value = database
        self.app.state.database = database

        response = self.client.get(
            "/api/operations/events",
            cookies={"aegis_session": "test-token"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["events"][0]["operation"] == "ADMIN login"
        assert payload["events"][0]["status"] == "failed"
        assert payload["events"][0]["correlation_id"] is None
        assert "private-user-id" not in response.text
        assert "private-target" not in response.text
        assert "private-token" not in response.text
