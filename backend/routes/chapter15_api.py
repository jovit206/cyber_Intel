"""Compatibility endpoints for the integrated Chapter 15 API workspace."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from backend.routes.admin import _admin_user
from backend.routes.cases import organization_overview

router = APIRouter(tags=["Chapter 15 Multi-tenant API"])


@router.get("/api/labs/jwks")
def platform_jwks() -> dict[str, list[Any]]:
    """Platform case APIs use revocable session cookies, not asymmetric JWTs."""
    return {"keys": []}


@router.get("/api/moderation/organisations")
def moderation_organisations(request: Request, actor: dict = Depends(_admin_user)) -> dict[str, Any]:
    return organization_overview(request)
