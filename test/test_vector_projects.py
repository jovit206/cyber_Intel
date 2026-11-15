import json
import secrets
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import admin as admin_routes
from backend.routes import subscriptions as subscription_routes
from backend.routes import vector_benchmark as benchmark_routes
from backend.routes import vector_rag as rag_routes
from backend.main import require_platform_session
from backend.services.subscriptions import password_hash
from backend.services import vector_benchmark as benchmark
from backend.services.vector_rag import (
    VectorProjectError,
    known_embedding_dimensions,
    record_project_event,
    split_text_into_chunks,
    validate_embeddings,
    vector_index_definition,
)


def test_rag_chunks_use_100_word_windows_with_20_word_overlap():
    words = [f"word{index}" for index in range(201)]

    chunks = split_text_into_chunks(" ".join(words))

    assert [len(chunk.split()) for chunk in chunks] == [100, 100, 41]
    assert chunks[0].split()[-20:] == chunks[1].split()[:20]
    assert chunks[1].split()[-20:] == chunks[2].split()[:20]


def test_embedding_validation_requires_consistent_expected_dimensions():
    assert validate_embeddings([[0.1, 0.2], [0.3, 0.4]], 2, 2) == 2

    with pytest.raises(VectorProjectError) as error:
        validate_embeddings([[0.1, 0.2]], 1, 3)

    assert error.value.code == "embedding_dimension_mismatch"


def test_embedding_dimension_configuration_rejects_non_positive_values():
    with pytest.raises(VectorProjectError) as error:
        known_embedding_dimensions("voyage-4", "0")

    assert error.value.code == "invalid_embedding_dimensions"


def test_embedding_dimension_override_rejects_models_that_do_not_support_it():
    with pytest.raises(VectorProjectError) as error:
        known_embedding_dimensions("voyage-3", "512")

    assert error.value.code == "unsupported_embedding_dimension_override"


def test_rag_vector_index_definition_uses_actual_dimensions_only():
    definition = vector_index_definition(512)

    assert definition["fields"] == [{
        "type": "vector",
        "path": "embedding",
        "numDimensions": 512,
        "similarity": "cosine",
    }]
    serialized = json.dumps(definition).lower()
    assert "ivf" not in serialized
    assert "efconstruction" not in serialized


def test_benchmark_jsonl_parser_requires_exactly_5000_real_records():
    content = b"\n".join(
        json.dumps({"source": f"source-{index}", "text": f"record text {index}"}).encode()
        for index in range(benchmark.DATASET_SIZE)
    )

    rows = benchmark.parse_jsonl_dataset(content)

    assert len(rows) == benchmark.DATASET_SIZE
    assert rows[0] == {"source": "source-0", "text": "record text 0"}
    with pytest.raises(VectorProjectError) as error:
        benchmark.parse_jsonl_dataset(b"\n".join(content.splitlines()[:-1]))
    assert error.value.code == "dataset_record_count_mismatch"


def test_benchmark_index_filters_active_dataset_and_does_not_claim_custom_algorithms():
    definition = benchmark.benchmark_index_definition()
    configurations = {item["id"]: item for item in benchmark.vector_search_configurations()}

    assert definition["fields"][-1] == {"type": "filter", "path": "dataset_id"}
    assert configurations["atlas-ann"]["status"] == "supported"
    assert configurations["atlas-enn"]["status"] == "supported"
    assert configurations["custom-hnsw"]["status"] == "unsupported"
    assert configurations["ivf"]["status"] == "unsupported"


def test_index_build_time_handles_naive_mongodb_utc_timestamps():
    state = {
        "index_requested_at": datetime(2026, 3, 1, tzinfo=timezone.utc).replace(tzinfo=None),
        "index_ready_at": datetime(2026, 3, 1, 0, 0, 2, tzinfo=timezone.utc).replace(tzinfo=None),
    }

    assert benchmark.get_index_build_time(state) == 2000


def test_project_events_keep_correlation_and_safe_stage_metadata_only():
    database = MagicMock()

    record_project_event(
        database,
        project_name="Lab 17.1 RAG Pipeline",
        correlation_id="correlation-1",
        operation="Query embedding generated",
        component="Voyage AI embeddings",
        status="success",
    )

    event = database.__getitem__.return_value.insert_one.call_args.args[0]
    assert event["source"] == "project_execution"
    assert event["project_name"] == "Lab 17.1 RAG Pipeline"
    assert event["correlation_id"] == "correlation-1"
    assert "question" not in event
    assert "document_text" not in event


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/rag/status", None),
        ("get", "/api/rag/documents", None),
        ("post", "/api/rag/documents", {"source": "test", "text": "test"}),
        ("post", "/api/rag/index", {}),
        ("get", "/api/rag/conversations", None),
        ("post", "/api/rag/conversations", {}),
        ("get", "/api/rag/conversations/private-id", None),
        ("post", "/api/rag/query", {"question": "test"}),
        ("get", "/api/vector-benchmark/status", None),
        ("post", "/api/vector-benchmark/dataset", None),
        ("post", "/api/vector-benchmark/index", {}),
        ("post", "/api/vector-benchmark/runs", {}),
        ("get", "/api/vector-benchmark/runs/private-id", None),
    ],
)
def test_every_vector_project_api_requires_admin_authentication(method, path, payload):
    app = FastAPI()
    app.include_router(rag_routes.router)
    app.include_router(benchmark_routes.router)

    with TestClient(app) as client:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)

    assert response.status_code == 401


def test_browser_login_cookie_authenticates_admin_rag_status():
    password = f"T-{secrets.token_urlsafe(18)}a9!"
    salt, digest = password_hash(password)
    user = {
        "user_id": "admin-test",
        "email": "admin@example.test",
        "password_salt": salt,
        "password_hash": digest,
        "role": "admin",
        "status": "active",
    }
    database = MagicMock()
    database.auth_login_attempts.find_one.return_value = None
    database.auth_login_attempts.find_one_and_update.return_value = {"failures": 1}
    database.subscription_users.find_one.return_value = user
    rag_db = MagicMock()
    rag_db.list_collection_names.return_value = []
    app = FastAPI()
    app.middleware("http")(require_platform_session)
    app.include_router(subscription_routes.router)
    app.include_router(rag_routes.router)
    app.state.database = database

    with patch.object(subscription_routes, "get_database", return_value=database), \
         patch.object(admin_routes, "get_database", return_value=database), \
         patch.object(rag_routes, "rag_database", return_value=rag_db), \
         TestClient(app) as client:
        login = client.post("/api/subscriptions/login", json={
            "email": "admin@example.test", "password": password,
        })
        assert login.status_code == 200
        set_cookie = login.headers.get("set-cookie", "").lower()
        assert "aegis_session=" in set_cookie
        assert "httponly" in set_cookie
        assert "samesite=lax" in set_cookie
        stored_session = database.subscription_sessions.insert_one.call_args.args[0]
        database.subscription_sessions.find_one.side_effect = (
            lambda query, *args, **kwargs: {"user_id": "admin-test"}
            if query.get("token_hash") == stored_session["token_hash"]
            else None
        )
        status = client.get("/api/rag/status")

    assert status.status_code == 200
    assert status.json()["project"] == "Lab 17.1"


def test_conversation_lookup_is_scoped_to_the_authenticated_administrator():
    app = FastAPI()
    app.include_router(rag_routes.router)
    rag_database = MagicMock()
    database = MagicMock()
    database.client.__getitem__.return_value = rag_database
    metadata = rag_database.__getitem__.return_value
    metadata.find_one.return_value = None
    app.state.database = database
    app.dependency_overrides[admin_routes._admin_user] = lambda: {"user_id": "admin-a"}

    with TestClient(app) as client:
        response = client.get("/api/rag/conversations/conversation-b")

    assert response.status_code == 404
    metadata.find_one.assert_called_once_with(
        {"conversation_id": "conversation-b", "user_id": "admin-a"},
        {"_id": 0, "conversation_id": 1, "title": 1, "created_at": 1, "updated_at": 1},
    )
