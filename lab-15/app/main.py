from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException

from app.database import create_client, ensure_indexes
from app.rate_limiter import MongoRateLimiter
from app.routes.tasks import router as tasks_router
from app.routes.users import router as users_router


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    database_name: str
    jwt_secret: str
    jwt_lifetime_minutes: int
    rate_limit_requests: int
    rate_limit_window_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
            database_name=os.getenv("TASKTRACKER_DB_NAME", "tasktracker"),
            jwt_secret=os.getenv("JWT_SECRET", ""),
            jwt_lifetime_minutes=int(os.getenv("JWT_LIFETIME_MINUTES", "60")),
            rate_limit_requests=int(os.getenv("RATE_LIMIT_REQUESTS", "100")),
            rate_limit_window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
        )


def create_app(settings: Settings | None = None, client_factory=create_client) -> FastAPI:
    app_settings = settings or Settings.from_env()
    if not app_settings.database_name or not app_settings.database_name.replace("_", "").isalnum():
        raise ValueError("TASKTRACKER_DB_NAME must contain only letters, numbers, and underscores.")
    if len(app_settings.jwt_secret) < 32:
        raise ValueError("JWT_SECRET must be configured with at least 32 characters.")
    if app_settings.jwt_lifetime_minutes < 1:
        raise ValueError("JWT_LIFETIME_MINUTES must be positive.")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = client_factory(app_settings.mongo_uri)
        try:
            client.admin.command("ping")
            database = client[app_settings.database_name]
            ensure_indexes(database)
            app.state.mongo_client = client
            app.state.database = database
            yield
        finally:
            client.close()

    app = FastAPI(
        title="TaskTracker Multi-Tenant SaaS API",
        description=(
            "Tenant-isolated task management API with JWT authentication, "
            "tenant-scoped RBAC, and MongoDB-backed rate limiting."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.rate_limiter = MongoRateLimiter(
        app_settings.rate_limit_requests,
        app_settings.rate_limit_window_seconds,
    )

    @app.get("/health", tags=["Health"], summary="Check API and MongoDB readiness")
    def health() -> dict[str, str]:
        database = getattr(app.state, "database", None)
        if database is None:
            raise HTTPException(status_code=503, detail="Database is not ready.")
        database.command("ping")
        return {"status": "ok"}

    app.include_router(users_router)
    app.include_router(tasks_router)
    return app


app = create_app()
