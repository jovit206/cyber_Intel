from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from typing_extensions import Annotated

TenantId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")]
TaskTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TaskDescription = Annotated[str, StringConstraints(max_length=5000)]
Role = Literal["admin", "user"]
TaskStatus = Literal["todo", "in_progress", "done"]
TaskPriority = Literal["low", "medium", "high"]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: TenantId
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=12, max_length=256)]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class UserResponse(BaseModel):
    id: str
    username: str
    email: EmailStr
    tenant_id: str
    role: Role
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: TaskTitle
    description: TaskDescription = ""
    priority: TaskPriority = "medium"
    status: TaskStatus = "todo"


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: TaskTitle | None = None
    description: TaskDescription | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None


class TaskResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    title: str
    description: str
    priority: TaskPriority
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
