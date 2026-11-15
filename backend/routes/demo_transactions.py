"""Compatibility endpoints for the retired financial demo view.

The persisted dataset is now consumed only by the Chapter 11.1 evaluation workflow.
These routes remain registered for compatibility but never read or return its records.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/demo-transactions", tags=["Transaction dataset compatibility"])
MOVED = "This dataset is available only in the Chapter 11.1 Shard Key Evaluation."


@router.get("/health")
def health() -> dict:
    return {"available": False, "message": MOVED}


@router.get("")
def retired_history() -> None:
    raise HTTPException(status_code=410, detail=MOVED)
