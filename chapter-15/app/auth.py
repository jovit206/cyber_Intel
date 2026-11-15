from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from bson import ObjectId
from fastapi import HTTPException, status
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "tasktracker"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return password_hash.verify(password, stored_hash)
    except (ValueError, TypeError):
        return False


def create_access_token(user: dict[str, Any], secret: str, lifetime_minutes: int) -> str:
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters.")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["_id"]),
        "tenant_id": user["tenant_id"],
        "iat": now,
        "exp": now + timedelta(minutes=lifetime_minutes),
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def authenticated_user(token: str, secret: str, database) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["sub", "tenant_id", "exp", "iss"]},
        )
        user_id = ObjectId(claims["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    user = database.users.find_one({"_id": user_id, "tenant_id": claims["tenant_id"]})
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
