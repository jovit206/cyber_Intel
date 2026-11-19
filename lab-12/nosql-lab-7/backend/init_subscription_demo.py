"""Create one demo login per plan plus an admin; passwords must come from environment variables."""
from __future__ import annotations

import os

from pymongo import MongoClient

from backend.config import settings
from backend.services.subscriptions import BillingError, PLANS, create_account, ensure_indexes

ACCOUNTS = (
    ("DEMO_FREE", "demo-free@aegis.local", "free", 1000, "user"),
    ("DEMO_PLUS", "demo-plus@aegis.local", "plus", 2000, "user"),
    ("DEMO_PRO", "demo-pro@aegis.local", "pro", 5000, "user"),
    ("DEMO_ADMIN", "demo-admin@aegis.local", "free", 1000, "admin"),
)


def main() -> int:
    passwords = {key: os.getenv(f"{key}_PASSWORD", "") for key, *_ in ACCOUNTS}
    missing = [key for key, password in passwords.items() if len(password) < 12]
    if missing:
        print("Set strong 12+ character passwords (upper/lowercase, number, symbol) for: " + ", ".join(missing))
        return 2
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        hello = client.admin.command("hello")
        if not hello.get("setName"):
            print("A MongoDB replica set is required for this transaction demonstration.")
            return 2
        db = client[settings.mongo_database]
        ensure_indexes(db)
        db.wallets.update_one({"user_id": "platform"}, {"$setOnInsert": {"user_id": "platform",
            "balance": 0, "currency": "INR"}}, upsert=True)
        for key, email, plan, opening_balance, role in ACCOUNTS:
            if db.subscription_users.find_one({"email": email}, {"_id": 1}):
                print(f"SKIP {email}: account already exists; its password and balances were left unchanged")
                continue
            try:
                create_account(db, email=email, password=passwords[key], plan=plan,
                               role=role, opening_balance=opening_balance)
                print(f"PASS created {email} ({plan}, opening wallet {opening_balance} INR)")
            except BillingError as error:
                print(f"FAIL {email}: {error}")
                return 1
        print("PASS platform wallet is initialized at zero INR")
        print("Account passwords are not printed. Use the password values supplied in the environment.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
