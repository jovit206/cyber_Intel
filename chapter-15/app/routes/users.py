from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.errors import DuplicateKeyError
from pymongo.database import Database

from app.auth import create_access_token, hash_password, verify_password
from app.dependencies import enforce_rate_limit, get_current_user, get_database
from app.models import AuthResponse, LoginRequest, RegisterRequest, UserResponse

router = APIRouter(tags=["Authentication and users"], dependencies=[Depends(enforce_rate_limit)])


def _public_user(user: dict) -> UserResponse:
    return UserResponse(
        id=str(user["_id"]),
        username=user["username"],
        email=user["email"],
        tenant_id=user["tenant_id"],
        role=user["role"],
        created_at=user["created_at"],
    )


def _auth_response(request: Request, user: dict) -> AuthResponse:
    token = create_access_token(
        user,
        request.app.state.settings.jwt_secret,
        request.app.state.settings.jwt_lifetime_minutes,
    )
    return AuthResponse(access_token=token, user=_public_user(user))


@router.post(
    "/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a tenant user",
    description="The first successfully registered user for a tenant becomes its admin; later users receive the regular user role.",
    responses={201: {"description": "User registered"}, 409: {"description": "Email or username already registered"}},
)
def register(
    payload: RegisterRequest,
    request: Request,
    database: Database = Depends(get_database),
) -> AuthResponse:
    now = datetime.now(timezone.utc)
    user_id = ObjectId()
    user = {
        "_id": user_id,
        "username": payload.username,
        "email": str(payload.email).lower(),
        "password_hash": hash_password(payload.password),
        "tenant_id": payload.tenant_id,
        "role": "user",
        "created_at": now,
    }
    try:
        database.users.insert_one(user)
    except DuplicateKeyError as error:
        index_name = error.details.get("keyPattern", {}) if error.details else {}
        detail = "Email is already registered." if "email" in index_name else "Username is already used in this tenant."
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from None

    try:
        tenant = database.tenants.find_one_and_update(
            {"_id": payload.tenant_id},
            {"$setOnInsert": {"created_at": now, "admin_user_id": user_id}},
            upsert=True,
            return_document=False,
        )
        if tenant is None:
            tenant = database.tenants.find_one({"_id": payload.tenant_id})
        if tenant and tenant.get("admin_user_id") == user_id:
            database.users.update_one(
                {"_id": user_id, "tenant_id": payload.tenant_id},
                {"$set": {"role": "admin"}},
            )
            user["role"] = "admin"
    except Exception:
        database.users.delete_one({"_id": user_id, "tenant_id": payload.tenant_id})
        raise
    return _auth_response(request, user)


@router.post(
    "/auth/login",
    response_model=AuthResponse,
    summary="Log in",
    description="Authenticate with email and password and receive a signed JWT bearer token.",
    responses={401: {"description": "Invalid credentials"}},
)
def login(
    payload: LoginRequest,
    request: Request,
    database: Database = Depends(get_database),
) -> AuthResponse:
    user = database.users.find_one({"email": str(payload.email).lower()})
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _auth_response(request, user)


@router.get(
    "/users/me",
    response_model=UserResponse,
    summary="Get current user",
    responses={401: {"description": "Authentication required"}},
)
def current_user(user: dict = Depends(get_current_user)) -> UserResponse:
    return _public_user(user)
