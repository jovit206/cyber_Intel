from __future__ import annotations

import os
import secrets

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "chapter15-test-only-secret-not-for-production-32")
os.environ.setdefault("RATE_LIMIT_REQUESTS", "1000")
os.environ.setdefault("RATE_LIMIT_WINDOW_SECONDS", "60")
TEST_DATABASE_NAME = f"tasktracker_test_{secrets.token_hex(6)}"
os.environ["TASKTRACKER_DB_NAME"] = TEST_DATABASE_NAME


@pytest.fixture(scope="session")
def api_client():
    if not os.getenv("MONGO_URI", "").strip():
        pytest.skip("Set MONGO_URI to run the real MongoDB integration suite.")

    from app.database import create_client
    from app.main import Settings, create_app

    application = create_app(Settings.from_env())
    with TestClient(application) as test_client:
        yield test_client

    if not TEST_DATABASE_NAME.startswith("tasktracker_test_"):
        raise RuntimeError("Refusing to remove a database outside the generated test namespace.")
    client = create_client(os.environ["MONGO_URI"])
    try:
        client.drop_database(TEST_DATABASE_NAME)
    finally:
        client.close()
