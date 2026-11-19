from uuid import uuid4


def test_mongodb_rate_limit_returns_429(api_client):
    suffix = uuid4().hex[:12]
    registered = api_client.post("/auth/register", json={
        "tenant_id": f"rate_{suffix}",
        "username": f"rate-user-{suffix}",
        "email": f"rate-{suffix}@example.com",
        "password": "Chapter15!TestPassword123",
    })
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    limiter = api_client.app.state.rate_limiter
    original_limit = limiter.limit
    try:
        limiter.limit = 1
        first = api_client.get("/tasks/", headers=headers)
        second = api_client.get("/tasks/", headers=headers)
        assert first.status_code == 200
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) > 0
    finally:
        limiter.limit = original_limit


def test_invalid_bearer_token_is_rejected(api_client):
    response = api_client.get("/tasks/", headers={"Authorization": "Bearer invalid.token.value"})
    assert response.status_code == 401


def test_tenant_id_is_not_accepted_as_task_body_authority(api_client):
    suffix = uuid4().hex[:12]
    registered = api_client.post("/auth/register", json={
        "tenant_id": f"body_{suffix}",
        "username": f"body-user-{suffix}",
        "email": f"body-{suffix}@example.com",
        "password": "Chapter15!TestPassword123",
    })
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    response = api_client.post("/tasks/", headers=headers, json={
        "tenant_id": "victim_tenant",
        "title": "Tenant override attempt",
    })
    assert response.status_code == 422
