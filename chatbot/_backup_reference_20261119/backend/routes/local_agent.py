"""Signed-in access to the local Windows agent: status, job progress, cancellation and confirmations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict

from backend.routes.subscriptions import _session_user
from backend.services import local_agent

router = APIRouter(prefix="/api/agent", tags=["Local laptop agent"])


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


def _allowed_user(actor: dict = Depends(_session_user)) -> dict:
    if not local_agent.allowed(actor):
        raise HTTPException(403, "Local laptop inspection is limited to administrators and approved accounts.")
    return actor


def _call(func, *args):
    try:
        return func(*args)
    except local_agent.LocalAgentError as error:
        raise HTTPException(error.status, {"message": error.message, "code": error.code}) from error


@router.get("/status")
def agent_status(actor: dict = Depends(_session_user)) -> dict:
    """Whether the local agent is connected and whether this account may use it."""
    result = local_agent.status(actor) if local_agent.allowed(actor) else {
        "connected": False, "configured": local_agent.configured(), "allowed": False,
        "message": "Local laptop inspection is limited to administrators and approved accounts."}
    result.pop("tools", None) if not result.get("allowed") else None
    return result


@router.get("/jobs/{job_id}")
def agent_job(job_id: str = Path(pattern="^[0-9a-f]{32}$"), actor: dict = Depends(_allowed_user)) -> dict:
    """Progress and result of a local-agent job started by this account."""
    return _call(local_agent.job, actor, job_id)


@router.post("/jobs/{job_id}/cancel")
def agent_cancel(job_id: str = Path(pattern="^[0-9a-f]{32}$"), actor: dict = Depends(_allowed_user)) -> dict:
    """Ask the local agent to stop a running scan."""
    return _call(local_agent.cancel, actor, job_id)


@router.post("/confirmations/{confirmation_id}")
def agent_confirm(payload: Confirmation, confirmation_id: str = Path(pattern="^[A-Za-z0-9_-]{16,40}$"),
                  actor: dict = Depends(_allowed_user)) -> dict:
    """The signed-in user approves or declines one pending action. Only this endpoint can approve an action."""
    return _call(local_agent.confirm, actor, confirmation_id, payload.approve)
