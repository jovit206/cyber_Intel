from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import cases


def make_client():
    app = FastAPI()
    app.include_router(cases.router)
    return app, TestClient(app)


def test_case_routes_require_an_authenticated_platform_session():
    _, client = make_client()
    try:
        for path in ("/api/cases/me", "/api/cases", "/api/cases/overview", "/api/cases/organizations"):
            response = client.get(path)
            assert response.status_code == 401
    finally:
        client.close()


def test_case_detail_is_scoped_to_organization_membership():
    app, client = make_client()
    database = MagicMock()
    database.cases.find_one.return_value = {"_id": ObjectId(), "organization_id": str(ObjectId()), "title": "Private", "description": "private case text"}
    database.organization_memberships.find_one.return_value = None
    with patch.object(cases, "_session_user", return_value={"user_id": "outsider", "role": "user"}), \
         patch.object(cases, "get_database", return_value=database):
        response = client.get(f"/api/cases/{database.cases.find_one.return_value['_id']}")
    client.close()
    assert response.status_code == 403
    assert "private case text" not in response.text


def test_rate_limit_returns_retry_after_and_records_refused_attempt():
    database = MagicMock()
    now = datetime.now(timezone.utc)
    database.case_rate_limits.find_one_and_update.return_value = {
        "counter": cases.RATE_LIMIT + 1,
        "window_start": now - timedelta(seconds=20),
    }
    from fastapi import HTTPException
    try:
        cases._rate_limit(database, "org-1", {"user_id": "user-1"})
    except HTTPException as error:
        assert error.status_code == 429
        assert int(error.headers["Retry-After"]) > 0
    else:
        raise AssertionError("Expected the request limit to reject this request")
    database.case_rate_limit_daily.update_one.assert_called_once()
