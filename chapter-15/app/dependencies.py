from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.database import Database

from app.auth import authenticated_user
from app.rate_limiter import request_actor

bearer_scheme = HTTPBearer(auto_error=False)


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    database: Database = Depends(get_database),
) -> dict | None:
    if credentials is None:
        return None
    return authenticated_user(
        credentials.credentials,
        request.app.state.settings.jwt_secret,
        database,
    )


def get_current_user(
    principal: dict | None = Depends(get_optional_user),
) -> dict:
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def enforce_rate_limit(
    request: Request,
    database: Database = Depends(get_database),
    principal: dict | None = Depends(get_optional_user),
) -> None:
    tenant_id, actor = request_actor(request, principal)
    request.app.state.rate_limiter.check(database, tenant_id, actor)
