"""Idempotently create a read-only manager account from process environment."""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from backend.config import settings
from backend.services.subscriptions import ensure_indexes, password_hash


def main() -> int:
    manager_id = os.getenv("AEGIS_MANAGER_ID", "manager").strip()
    password = os.getenv("AEGIS_MANAGER_PASSWORD", "")
    email = os.getenv("AEGIS_MANAGER_EMAIL", "manager@aegis.local").strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", manager_id) or not password or len(password) > 256:
        print("Set valid AEGIS_MANAGER_ID and AEGIS_MANAGER_PASSWORD process variables.", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        print("AEGIS_MANAGER_EMAIL must be a valid email address.", file=sys.stderr)
        return 2
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        db = client[settings.mongo_database]
        ensure_indexes(db)
        existing = db.subscription_users.find_one({"user_id": manager_id}, {"role": 1})
        if existing:
            if existing.get("role") == "manager":
                print(f"Manager '{manager_id}' already exists; no password or account data changed.")
                return 0
            print("Refusing to change the role of an existing account.", file=sys.stderr)
            return 3
        if db.subscription_users.find_one({"email": email}, {"_id": 1}):
            print("The configured manager email is already assigned to another account.", file=sys.stderr)
            return 3
        salt, digest = password_hash(password)
        now = datetime.now(timezone.utc)
        try:
            with client.start_session() as session:
                with session.start_transaction():
                    db.subscription_users.insert_one({"user_id": manager_id, "email": email,
                        "password_salt": salt, "password_hash": digest, "role": "manager", "plan": "free",
                        "created_at": now, "renewal_at": None, "credits_allocated": 0, "credits_used": 0}, session=session)
                    db.wallets.insert_one({"user_id": manager_id, "balance": 0, "currency": "INR", "updated_at": now}, session=session)
                    db.admin_audit_log.insert_one({"administrator_id": "setup-script", "actor_role": "manager",
                        "action": "manager_bootstrap", "target_record": manager_id,
                        "created_at": now, "outcome": "created"}, session=session)
        except DuplicateKeyError:
            raced = db.subscription_users.find_one({"user_id": manager_id}, {"role": 1})
            if raced and raced.get("role") == "manager":
                print(f"Manager '{manager_id}' already exists; no password or account data changed.")
                return 0
            raise
        print(f"Manager '{manager_id}' created with a salted PBKDF2 password hash.")
        return 0
    finally:
        password = ""
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
