from unittest.mock import MagicMock, patch

import pytest

from backend.services.llm_provider import (
    LLMProviderError,
    generate_chat_answer,
    get_llm_configuration,
)
from backend.services.vector_rag import provider_configuration


def _clear_provider_environment(monkeypatch):
    for name in (
        "LLM_PROVIDER",
        "LLM_TIMEOUT_SECONDS",
        "OLLAMA_BASE_URL",
        "OLLAMA_CHAT_MODEL",
        "OLLAMA_MAX_TOKENS",
        "RAG_LLM_API_URL",
        "RAG_LLM_API_KEY",
        "RAG_LLM_MODEL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_new_install_defaults_to_ollama_without_returning_credentials(monkeypatch):
    _clear_provider_environment(monkeypatch)

    config = get_llm_configuration()
    status = provider_configuration()

    assert config.provider == "ollama"
    assert config.model == "llama3.2:3b"
    assert status["llm_provider"] == "Ollama"
    assert status["llm_configured"] is True
    assert "api_key" not in status
    assert "endpoint" not in status


def test_existing_rag_credentials_select_compatible_provider(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("RAG_LLM_API_KEY", "test-secret-value")
    monkeypatch.setenv("RAG_LLM_API_URL", "https://llm.example.test/v1/chat/completions")
    monkeypatch.setenv("RAG_LLM_MODEL", "legacy-model")

    config = get_llm_configuration()

    assert config.provider == "openai-compatible"
    assert config.provider_label == "OpenAI-compatible API"
    assert config.model == "legacy-model"
    assert config.configured is True


def test_legacy_model_setting_also_preserves_compatible_provider_selection(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("RAG_LLM_MODEL", "legacy-model")

    assert get_llm_configuration().provider == "openai-compatible"


def test_explicit_provider_overrides_legacy_environment(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("RAG_LLM_API_KEY", "test-secret-value")

    assert get_llm_configuration().provider == "ollama"


def test_ollama_chat_uses_native_api_without_auth_header(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_CHAT_MODEL", "local-model:latest")
    response = MagicMock(status_code=200)
    response.json.return_value = {"message": {"content": "Grounded answer"}}

    with patch("backend.services.llm_provider.httpx.post", return_value=response) as post:
        answer, duration = generate_chat_answer("system", "question")

    assert answer == "Grounded answer"
    assert duration >= 0
    args, kwargs = post.call_args
    assert args[0] == "http://ollama:11434/api/chat"
    assert kwargs["json"]["stream"] is False
    assert kwargs["json"]["model"] == "local-model:latest"
    assert "Authorization" not in kwargs["headers"]


def test_ollama_generation_uses_bounded_configurable_token_limit(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MAX_TOKENS", "128")
    response = MagicMock(status_code=200)
    response.json.return_value = {"message": {"content": "Grounded answer"}}

    with patch("backend.services.llm_provider.httpx.post", return_value=response) as post:
        generate_chat_answer("system", "question")

    assert post.call_args.kwargs["json"]["options"]["num_predict"] == 128


def test_invalid_ollama_token_limit_is_rejected_before_provider_request(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MAX_TOKENS", "2048")

    with patch("backend.services.llm_provider.httpx.post") as post:
        with pytest.raises(LLMProviderError) as error:
            generate_chat_answer("system", "question")

    assert error.value.code == "invalid_ollama_max_tokens"
    post.assert_not_called()


def test_openai_compatible_chat_keeps_legacy_endpoint_and_bearer_auth(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("RAG_LLM_API_KEY", "test-secret-value")
    monkeypatch.setenv("RAG_LLM_API_URL", "https://llm.example.test/v1/chat/completions")
    monkeypatch.setenv("RAG_LLM_MODEL", "legacy-model")
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": "Legacy answer"}}]}

    with patch("backend.services.llm_provider.httpx.post", return_value=response) as post:
        answer, _ = generate_chat_answer("system", "question")

    assert answer == "Legacy answer"
    assert post.call_args.args[0] == "https://llm.example.test/v1/chat/completions"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-secret-value"


def test_configuration_repr_never_contains_external_provider_key(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("RAG_LLM_API_KEY", "test-secret-value")

    assert "test-secret-value" not in repr(get_llm_configuration())


def test_remote_plain_http_ollama_endpoint_is_rejected(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://model-host.example.test:11434")

    with pytest.raises(LLMProviderError) as error:
        generate_chat_answer("system", "question")

    assert error.value.code == "llm_endpoint_insecure"


def test_invalid_timeout_is_reported_without_provider_request(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "not-a-number")

    with pytest.raises(LLMProviderError) as error:
        get_llm_configuration()

    assert error.value.code == "invalid_llm_timeout"
