from uuid import uuid4


def test_regular_user_sees_and_changes_only_their_own_tasks(api_client):
    suffix = uuid4().hex[:12]
    tenant_id = f"rbac_{suffix}"
    admin_result = api_client.post("/auth/register", json={
        "tenant_id": tenant_id,
        "username": f"admin-{suffix}",
        "email": f"admin-{suffix}@example.com",
        "password": "Chapter15!TestPassword123",
    })
    assert admin_result.status_code == 201
    admin = admin_result.json()
    user_result = api_client.post("/auth/register", json={
        "tenant_id": tenant_id,
        "username": f"user-{suffix}",
        "email": f"user-{suffix}@example.com",
        "password": "Chapter15!TestPassword123",
    })
    assert user_result.status_code == 201
    regular_user = user_result.json()
    assert admin["user"]["role"] == "admin"
    assert regular_user["user"]["role"] == "user"

    admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
    user_headers = {"Authorization": f"Bearer {regular_user['access_token']}"}
    admin_task = api_client.post("/tasks/", headers=admin_headers, json={"title": "Admin task"}).json()
    user_task = api_client.post("/tasks/", headers=user_headers, json={"title": "User task"}).json()

    assert {task["id"] for task in api_client.get("/tasks/", headers=admin_headers).json()} == {
        admin_task["id"], user_task["id"]
    }
    assert [task["id"] for task in api_client.get("/tasks/", headers=user_headers).json()] == [
        user_task["id"]
    ]
    assert api_client.get(f"/tasks/{admin_task['id']}", headers=user_headers).status_code == 404
    assert api_client.put(
        f"/tasks/{admin_task['id']}", headers=user_headers, json={"title": "Unauthorized"}
    ).status_code == 404
    assert api_client.delete(f"/tasks/{admin_task['id']}", headers=user_headers).status_code == 404

    assert api_client.put(
        f"/tasks/{user_task['id']}", headers=admin_headers, json={"status": "done"}
    ).status_code == 200
    assert api_client.delete(f"/tasks/{user_task['id']}", headers=admin_headers).status_code == 204
