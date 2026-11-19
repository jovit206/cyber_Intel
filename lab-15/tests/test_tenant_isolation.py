from uuid import uuid4


def _register(client, tenant_id: str, username: str, email: str) -> dict:
    response = client.post("/auth/register", json={
        "tenant_id": tenant_id,
        "username": username,
        "email": email,
        "password": "Chapter15!TestPassword123",
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_tenant_cannot_read_update_or_delete_another_tenants_task(api_client):
    suffix = uuid4().hex[:12]
    tenant_a = f"tenant_a_{suffix}"
    tenant_b = f"tenant_b_{suffix}"
    admin_a = _register(api_client, tenant_a, f"admin-a-{suffix}", f"a-{suffix}@example.com")
    admin_b = _register(api_client, tenant_b, f"admin-b-{suffix}", f"b-{suffix}@example.com")
    headers_a = {"Authorization": f"Bearer {admin_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {admin_b['access_token']}"}
    created = api_client.post("/tasks/", headers=headers_a, json={"title": "Tenant A private"})
    assert created.status_code == 201
    task_id = created.json()["id"]

    assert api_client.get(f"/tasks/{task_id}", headers=headers_b).status_code == 404
    assert api_client.put(
        f"/tasks/{task_id}", headers=headers_b, json={"title": "Changed by tenant B"}
    ).status_code == 404
    assert api_client.delete(f"/tasks/{task_id}", headers=headers_b).status_code == 404
    assert api_client.get("/tasks/", headers=headers_b).json() == []
    assert api_client.get(f"/tasks/{task_id}", headers=headers_a).status_code == 200
