from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import lab8
from backend.routes import chapter16
from backend.main import require_platform_session


def test_replica_set_controls_require_admin_authentication():
    app = FastAPI()
    app.include_router(lab8.router)
    with TestClient(app) as client:
        for method, path, body in (
            ("get", "/api/lab8/status", None),
            ("get", "/api/lab8/historical", None),
            ("post", "/api/lab8/stepdown", {"confirm": True, "seconds": 10}),
        ):
            response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
            assert response.status_code == 401


def test_replica_set_controls_reject_an_authenticated_non_admin():
    app = FastAPI()
    app.include_router(lab8.router)
    database = MagicMock()
    database.subscription_sessions.find_one.return_value = {"user_id": "member"}
    database.subscription_users.find_one.return_value = {"user_id": "member", "role": "user", "status": "active"}
    with patch("backend.routes.admin.get_database", return_value=database), TestClient(app) as client:
        response = client.get("/api/lab8/status", cookies={"aegis_session": "opaque-test-cookie"})
    assert response.status_code == 403


def test_admin_workspace_deep_link_survives_sign_in_redirect():
    app = FastAPI()
    app.middleware("http")(require_platform_session)
    app.get("/")(lambda: {"page": "admin"})
    with TestClient(app) as client:
        response = client.get("/?view=admin&tab=labs", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/account?next=%2F%3Fview%3Dadmin%26tab%3Dlabs"


def test_chapter16_database_controls_require_admin_authentication():
    app = FastAPI()
    app.include_router(chapter16.router)
    with TestClient(app) as client:
        assert client.get("/api/chapter16/status").status_code == 401
        assert client.post("/api/chapter16/fraud-review", json={"account_id": "a", "transaction_id": "t"}).status_code == 401


def test_chapter16_status_reports_unconfigured_without_secret_values():
    app = FastAPI()
    app.include_router(chapter16.router)
    app.dependency_overrides[chapter16._admin_user] = lambda: {"user_id": "admin", "role": "admin"}
    with patch.dict("os.environ", {"CHAPTER16_MONGO_URI": "", "CHAPTER16_CASSANDRA_HOSTS": "", "CHAPTER16_NEO4J_URI": "", "CHAPTER16_NEO4J_USERNAME": "", "CHAPTER16_NEO4J_PASSWORD": ""}, clear=False), TestClient(app) as client:
        response = client.get("/api/chapter16/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_configured"
    assert all(item["status"] == "not_configured" for item in payload["databases"].values())
    assert "password" not in response.text.lower()
    assert "uri" not in response.text.lower()


def test_chapter16_fraud_review_rejects_unbounded_graph_depth():
    app = FastAPI()
    app.include_router(chapter16.router)
    app.dependency_overrides[chapter16._admin_user] = lambda: {"user_id": "admin", "role": "admin"}
    with TestClient(app) as client:
        response = client.post("/api/chapter16/fraud-review", json={"account_id": "a", "transaction_id": "t", "max_hops": 4})
    assert response.status_code == 422
