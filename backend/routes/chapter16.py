"""Admin-only, read-only health and fraud evidence APIs for Chapter 16."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from backend.routes.admin import _admin_user
from backend.services.chapter16 import fraud_review, status_report

router = APIRouter(prefix="/api/chapter16", tags=["Chapter 16 Cassandra and Neo4j"])


class FraudReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    transaction_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    max_hops: int = Field(default=3, ge=1, le=3)


@router.get("/status")
def get_status(actor: dict = Depends(_admin_user)) -> dict:
    del actor
    return status_report()


@router.post("/fraud-review")
def review_fraud(payload: FraudReviewRequest, actor: dict = Depends(_admin_user)) -> dict:
    del actor
    try:
        return fraud_review(payload.account_id, payload.transaction_id, payload.max_hops)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(503, f"Fraud evidence query unavailable ({type(error).__name__}).") from error
