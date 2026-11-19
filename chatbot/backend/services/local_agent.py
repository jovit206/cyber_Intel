"""Client for the AEGIS local Windows agent (local_agent/aegis_local_agent.py).

The backend never inspects the host itself; it sends HMAC-signed requests to the agent running on the Windows host
(reached from Docker at host.docker.internal). Only accounts with the admin role, or listed in
LOCAL_AGENT_ALLOWED_EMAILS, may use the agent, because it inspects the machine of the person running the platform.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

import httpx

DEFAULT_URL = "http://host.docker.internal:8765"


class LocalAgentError(Exception):
    def __init__(self, message: str, status: int = 503, code: str = "local_agent_unavailable"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def _config() -> tuple[str, str]:
    return os.getenv("LOCAL_AGENT_URL", DEFAULT_URL).strip().rstrip("/"), os.getenv("LOCAL_AGENT_TOKEN", "").strip()


def configured() -> bool:
    return bool(_config()[1])


def allowed(actor: dict) -> bool:
    emails = {e.strip().lower() for e in os.getenv("LOCAL_AGENT_ALLOWED_EMAILS", "").split(",") if e.strip()}
    return actor.get("role") == "admin" or str(actor.get("email", "")).lower() in emails


def _identity(actor: dict) -> str:
    return str(actor.get("user_id") or actor.get("email") or "unknown")


def _call(method: str, path: str, actor: dict, payload: dict | None = None, timeout: float = 15) -> dict:
    url, token = _config()
    if not token:
        raise LocalAgentError("The local agent is not configured on the server (LOCAL_AGENT_TOKEN is not set).",
                              503, "local_agent_not_configured")
    body = json.dumps({**(payload or {}), "requested_by": _identity(actor)}).encode() if method == "POST" else b""
    timestamp, nonce = str(time.time()), secrets.token_urlsafe(18)
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode() + hashlib.sha256(body).hexdigest().encode()
    headers = {"Content-Type": "application/json", "X-Aegis-Timestamp": timestamp, "X-Aegis-Nonce": nonce,
               "X-Aegis-Signature": hmac.new(token.encode(), message, hashlib.sha256).hexdigest(),
               "X-Aegis-User": _identity(actor)}
    try:
        response = httpx.request(method, url + path, content=body or None, headers=headers, timeout=timeout)
    except httpx.TimeoutException as error:
        raise LocalAgentError("The local agent did not respond in time.", 504, "local_agent_timeout") from error
    except httpx.RequestError as error:
        raise LocalAgentError("The local agent is not running or cannot be reached. Start it on the laptop with: "
                              "python local_agent/aegis_local_agent.py", 503, "local_agent_unreachable") from error
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code == 401:
        raise LocalAgentError("The local agent rejected the server's signature; the shared token does not match.",
                              502, "local_agent_auth_failed")
    if response.status_code >= 400:
        status_code = response.status_code if response.status_code in {400, 403, 404} else 502
        raise LocalAgentError(str(data.get("error") or f"Local agent error (HTTP {response.status_code})."),
                              status_code, "local_agent_rejected")
    return data


def status(actor: dict) -> dict[str, Any]:
    if not configured():
        return {"connected": False, "configured": False, "allowed": allowed(actor),
                "message": "The local agent is not configured on the server."}
    health = None
    for attempt in range(2):  # one retry: a briefly busy host (for example under memory pressure) is not "offline"
        try:
            health = _call("GET", "/health", actor, timeout=6)
            break
        except LocalAgentError as error:
            if attempt == 1 or error.code in {"local_agent_auth_failed", "local_agent_not_configured"}:
                return {"connected": False, "configured": True, "allowed": allowed(actor), "message": error.message}
    return {"connected": True, "configured": True, "allowed": allowed(actor), "version": health.get("version"),
            "scope": health.get("scope"), "content_roots": health.get("content_roots", []),
            "tools": health.get("tools", {})}


# Server-side enforcement of the agent's scope: the backend only ever requests these cybersecurity checks, whatever
# the chat model or a client asks for. (The agent's own registry contains only the same tools.)
SECURITY_TOOLS = frozenset({"threat_scan", "defender_status", "defender_quick_scan", "suspicious_files", "suspicious_processes",
                            "persistence_entries", "network_connections", "unwanted_applications", "security_posture",
                            "powershell_activity", "secret_scan"})


def start(actor: dict, tool: str, args: dict) -> dict:
    if tool not in SECURITY_TOOLS:
        raise LocalAgentError("The local agent is restricted to cybersecurity inspection.", 403, "local_agent_out_of_scope")
    return _call("POST", "/jobs", actor, {"tool": tool, "args": args})


def job(actor: dict, job_id: str) -> dict:
    return _call("GET", f"/jobs/{job_id}", actor)


def cancel(actor: dict, job_id: str) -> dict:
    return _call("POST", f"/jobs/{job_id}/cancel", actor, {})


def confirm(actor: dict, confirmation_id: str, approve: bool) -> dict:
    return _call("POST", f"/confirmations/{confirmation_id}", actor, {"approve": approve})
