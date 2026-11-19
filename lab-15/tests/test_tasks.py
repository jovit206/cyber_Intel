from uuid import uuid4


def register(client, tenant_id: str, suffix: str) -> dict:
    response = client.post("/auth/register", json={
        "tenant_id": tenant_id,
        "username": f"user-{suffix}",
        "email": f"{suffix}@example.com",
        "password": "Chapter15!TestPassword123",
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_task_crud(api_client):
    suffix = uuid4().hex[:12]
    admin = register(api_client, f"crud_{suffix}", suffix)
    headers = {"Authorization": f"Bearer {admin['access_token']}"}

    created = api_client.post("/tasks/", headers=headers, json={
        "title": "Design tenant-safe API",
        "description": "Exercise create and read",
        "priority": "high",
    })
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["tenant_id"] == admin["user"]["tenant_id"]
    assert task["status"] == "todo"

    listed = api_client.get("/tasks/", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [task["id"]]

    fetched = api_client.get(f"/tasks/{task['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Design tenant-safe API"

    updated = api_client.put(f"/tasks/{task['id']}", headers=headers, json={
        "title": "Updated task",
        "status": "in_progress",
    })
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated task"
    assert updated.json()["status"] == "in_progress"

    deleted = api_client.delete(f"/tasks/{task['id']}", headers=headers)
    assert deleted.status_code == 204
    assert api_client.get(f"/tasks/{task['id']}", headers=headers).status_code == 404


def test_login_returns_same_identity_and_rejects_bad_password(api_client):
    suffix = uuid4().hex[:12]
    account = register(api_client, f"login_{suffix}", suffix)
    response = api_client.post("/auth/login", json={
        "email": account["user"]["email"],
        "password": "Chapter15!TestPassword123",
    })
    assert response.status_code == 200
    assert response.json()["user"]["id"] == account["user"]["id"]
    assert response.json()["user"]["role"] == "admin"

    bad_password = api_client.post("/auth/login", json={
        "email": account["user"]["email"],
        "password": "incorrect",
    })
    assert bad_password.status_code == 401
