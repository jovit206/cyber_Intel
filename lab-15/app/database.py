from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database


def create_client(uri: str) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=5000, appname="chapter15-tasktracker")


def ensure_indexes(database: Database) -> None:
    database.users.create_index([("email", ASCENDING)], unique=True, name="uq_users_email")
    database.users.create_index(
        [("tenant_id", ASCENDING), ("username", ASCENDING)],
        unique=True,
        name="uq_users_tenant_username",
    )
    database.tasks.create_index(
        [("tenant_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_tasks_tenant_created",
    )
    database.tasks.create_index(
        [("tenant_id", ASCENDING), ("user_id", ASCENDING)],
        name="ix_tasks_tenant_user",
    )
    database.rate_limits.create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_rate_limits_expires",
    )
    database.tenants.create_index([("created_at", ASCENDING)], name="ix_tenants_created")
