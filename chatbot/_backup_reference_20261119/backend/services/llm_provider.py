"""Server-side chat provider selection for the existing RAG workspace.

Ollama is the default for new installations. Existing Chapter 17 deployments that
already configure the legacy RAG endpoint or API key retain the OpenAI-compatible
provider unless LLM_PROVIDER is explicitly set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import time
from typing import Iterator
from urllib.parse import urlsplit

import httpx


class LLMProviderError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class LLMConfiguration:
    provider: str
    provider_label: str
    endpoint: str
    model: str
    api_key: str = field(repr=False)
    endpoint_secure: bool
    configured: bool
    timeout_seconds: int


def _secure_endpoint(endpoint: str, *, local_http_hosts: set[str]) -> bool:
    try:
        parsed = urlsplit(endpoint)
        if parsed.scheme == "https":
            return bool(parsed.hostname and not parsed.username and not parsed.password)
        return (
            parsed.scheme == "http"
            and parsed.hostname in local_http_hosts
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def _timeout_seconds() -> int:
    raw = os.getenv("LLM_TIMEOUT_SECONDS", "90").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise LLMProviderError(
            "invalid_llm_timeout", "LLM_TIMEOUT_SECONDS must be an integer from 1 to 600.", 503
        ) from error
    if not 1 <= value <= 600:
        raise LLMProviderError(
            "invalid_llm_timeout", "LLM_TIMEOUT_SECONDS must be an integer from 1 to 600.", 503
        )
    return value


def _ollama_max_tokens() -> int:
    raw = os.getenv("OLLAMA_MAX_TOKENS", "256").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise LLMProviderError(
            "invalid_ollama_max_tokens", "OLLAMA_MAX_TOKENS must be an integer from 32 to 1024.", 503
        ) from error
    if not 32 <= value <= 1024:
        raise LLMProviderError(
            "invalid_ollama_max_tokens", "OLLAMA_MAX_TOKENS must be an integer from 32 to 1024.", 503
        )
    return value


def get_llm_configuration() -> LLMConfiguration:
    explicit_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    legacy_endpoint = os.getenv("RAG_LLM_API_URL", "").strip()
    legacy_model = os.getenv("RAG_LLM_MODEL", "").strip()
    api_key = os.getenv("RAG_LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()

    # Preserve existing Chapter 17 deployments that already set the previous
    # provider's endpoint/key. With no legacy configuration, new installs use Ollama.
    provider = explicit_provider or ("openai-compatible" if legacy_endpoint or legacy_model or api_key else "ollama")
    if provider in {"openai", "openai_compatible", "openai-compatible-api"}:
        provider = "openai-compatible"
    nvidia_nim = provider in {"nvidia-nim", "nim"}
    if nvidia_nim:
        provider = "openai-compatible"
        api_key = os.getenv("NVIDIA_NIM_API_KEY", "").strip() or api_key

    timeout_seconds = _timeout_seconds()
    if provider == "ollama":
        endpoint = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
        # Matches .env.example and docker-compose.yml; the platform is provisioned with Llama 3.2 (3B).
        model = os.getenv("OLLAMA_CHAT_MODEL", "").strip() or "llama3.2:3b"
        ollama_key = os.getenv("OLLAMA_API_KEY", "").strip()
        # host.docker.internal is Docker Desktop's name for the local host machine, used when the backend
        # container reaches an Ollama server running on the host.
        local_hosts = {"localhost", "127.0.0.1", "::1", "ollama", "host.docker.internal"}
        secure = _secure_endpoint(endpoint, local_http_hosts=local_hosts)
        is_local = urlsplit(endpoint).hostname in local_hosts
        return LLMConfiguration(
            provider=provider,
            provider_label="Ollama" if is_local else "Ollama Cloud",
            endpoint=endpoint,
            model=model,
            api_key=ollama_key,
            endpoint_secure=secure,
            configured=bool(model and secure and (is_local or ollama_key)),
            timeout_seconds=timeout_seconds,
        )

    if provider != "openai-compatible":
        raise LLMProviderError(
            "unsupported_llm_provider",
            "LLM_PROVIDER must be 'ollama' or 'openai-compatible'.",
            503,
        )

    endpoint = (legacy_endpoint or os.getenv("NVIDIA_NIM_BASE_URL", "").strip() or
                ("https://integrate.api.nvidia.com/v1/chat/completions" if nvidia_nim else "https://api.openai.com/v1/chat/completions"))
    model = os.getenv("RAG_LLM_MODEL", "gpt-4o-mini").strip()
    if nvidia_nim:
        model = os.getenv("NVIDIA_NIM_MODEL", model).strip()
    secure = _secure_endpoint(endpoint, local_http_hosts={"localhost", "127.0.0.1", "::1"})
    return LLMConfiguration(
        provider=provider,
        provider_label="NVIDIA NIM" if nvidia_nim else "OpenAI-compatible API",
        endpoint=endpoint,
        model=model,
        api_key=api_key,
        endpoint_secure=secure,
        configured=bool(api_key and model and secure),
        timeout_seconds=timeout_seconds,
    )


def _keep_alive() -> str:
    # Keep the model loaded between questions so a reply never pays the model-load cost.
    value = os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip()
    return value if re.fullmatch(r"-?\d+[smh]?", value) else "30m"


def stream_chat_answer(system_prompt: str, user_prompt: str, *, max_tokens: int | None = None,
                       history: list[dict] | None = None) -> Iterator[str]:
    """Yield the answer as it is generated (Ollama streaming); other providers yield one chunk.

    ``history`` is a list of earlier {"role", "content"} messages sent as real chat turns.
    Raises LLMProviderError before the first chunk if the provider is unavailable or rejects the request.
    """
    config = get_llm_configuration()
    if config.provider != "ollama":
        answer, _ = generate_chat_answer(system_prompt, user_prompt)
        yield answer
        return
    if not config.endpoint_secure:
        raise LLMProviderError("llm_endpoint_insecure",
                               "The LLM endpoint must use HTTPS. HTTP is allowed only for the local Ollama service.", 503)
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    limit = max(32, min(int(max_tokens or _ollama_max_tokens()), 1024))
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m["role"], "content": m["content"]} for m in (history or [])
                 if m.get("role") in {"user", "assistant"} and m.get("content")]
    messages.append({"role": "user", "content": user_prompt})
    payload = {
        "model": config.model,
        "stream": True,
        "keep_alive": _keep_alive(),
        "options": {"temperature": 0.2, "num_predict": limit, "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "4096") or 4096)},
        "messages": messages,
    }
    timeout = httpx.Timeout(config.timeout_seconds, connect=min(10, config.timeout_seconds))
    produced = False
    try:
        with httpx.stream("POST", f"{config.endpoint}/api/chat", headers=headers, json=payload, timeout=timeout) as response:
            if response.status_code == 429:
                raise LLMProviderError("llm_provider_rate_limited", "The LLM provider rate-limited the request. Wait and retry.", 429)
            if response.status_code >= 400:
                raise LLMProviderError(
                    "llm_provider_rejected",
                    f"The LLM provider rejected the request with HTTP {response.status_code}. Check model configuration and provider availability.",
                    502)
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError as error:
                    raise LLMProviderError("invalid_llm_response", "The LLM provider returned an invalid stream.", 502) from error
                if event.get("error"):
                    raise LLMProviderError("llm_provider_rejected", "The LLM provider reported an error while generating.", 502)
                text = (event.get("message") or {}).get("content") or ""
                if text:
                    produced = True
                    yield text
                if event.get("done"):
                    break
    except httpx.TimeoutException as error:
        raise LLMProviderError("llm_provider_timeout", "The LLM provider did not respond before the request timed out.", 504) from error
    except httpx.RequestError as error:
        raise LLMProviderError("llm_provider_unreachable", "The configured LLM provider could not be reached.", 502) from error
    if not produced:
        raise LLMProviderError("empty_llm_response", "The LLM provider returned an empty answer.", 502)


def generate_chat_answer(system_prompt: str, user_prompt: str) -> tuple[str, float]:
    config = get_llm_configuration()
    if not config.endpoint_secure:
        raise LLMProviderError(
            "llm_endpoint_insecure",
            "The LLM endpoint must use HTTPS. HTTP is allowed only for the local Ollama service.",
            503,
        )
    if not config.model:
        raise LLMProviderError("llm_model_not_configured", "An LLM model must be configured.", 503)
    if config.provider == "openai-compatible" and not config.api_key:
        raise LLMProviderError(
            "llm_provider_not_configured",
            "No LLM API key is configured. Set RAG_LLM_API_KEY or OPENAI_API_KEY on the backend.",
            503,
        )

    if config.provider == "ollama":
        endpoint = f"{config.endpoint}/api/chat"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        payload = {
            "model": config.model,
            "stream": False,
            "keep_alive": _keep_alive(),
            "options": {"temperature": 0.1, "num_predict": _ollama_max_tokens()},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
    else:
        endpoint = config.endpoint
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

    started = time.perf_counter()
    try:
        response = httpx.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(config.timeout_seconds, connect=min(10, config.timeout_seconds)),
        )
    except httpx.TimeoutException as error:
        raise LLMProviderError(
            "llm_provider_timeout", "The LLM provider did not respond before the request timed out.", 504
        ) from error
    except httpx.RequestError as error:
        raise LLMProviderError(
            "llm_provider_unreachable", "The configured LLM provider could not be reached.", 502
        ) from error

    duration_ms = (time.perf_counter() - started) * 1000
    if response.status_code == 429:
        raise LLMProviderError(
            "llm_provider_rate_limited", "The LLM provider rate-limited the request. Wait and retry.", 429
        )
    if response.status_code >= 400:
        raise LLMProviderError(
            "llm_provider_rejected",
            f"The LLM provider rejected the request with HTTP {response.status_code}. Check model configuration and provider availability.",
            502,
        )

    try:
        result = response.json()
        if config.provider == "ollama":
            answer = result["message"]["content"]
        else:
            answer = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise LLMProviderError(
            "invalid_llm_response", "The LLM provider returned an invalid chat response.", 502
        ) from error
    if not isinstance(answer, str) or not answer.strip():
        raise LLMProviderError("empty_llm_response", "The LLM provider returned an empty answer.", 502)
    return answer.strip(), duration_ms
