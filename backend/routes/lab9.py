from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database import get_database
from backend.services import reliability

router = APIRouter(prefix="/api/lab9", tags=["Write Concern & Network Reliability"])


def _client():
    try:
        return get_database().client
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/latency")
def get_write_concern_latency() -> dict[str, Any]:
    """Recorded (historical) write concern latency measurements."""
    return reliability.latency_report(_client())


class RunBenchmarkRequest(BaseModel):
    docs_per_config: int = 50
    confirm: bool = False


@router.post("/run-latency")
def run_live_latency_benchmark(req: RunBenchmarkRequest) -> dict[str, Any]:
    """Explicit, confirmed live benchmark against the replica set."""
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="Explicit confirmation required. Set 'confirm': true to run live benchmark.",
        )
    try:
        return reliability.run_live_benchmark(_client(), req.docs_per_config)
    except reliability.ReliabilityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/partition")
def get_network_partition_results() -> dict[str, Any]:
    """Historical partition-experiment records plus live cluster readiness."""
    return reliability.partition_report(_client())
