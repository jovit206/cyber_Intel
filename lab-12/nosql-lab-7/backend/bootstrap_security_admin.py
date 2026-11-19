"""One-time operator bootstrap for the first platform administrator.

Only promotes an existing account when no administrator exists. It does not
create accounts, change passwords, or run during application startup.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone

from backend.database import close_database, connect_database


def promote_first_admin(db, email: str) -> bool:
    normalized = email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        raise ValueError("Provide a valid email for an existing platform account.")
    if db.subscription_users.count_documents({"role": "admin"}):
        raise RuntimeError("An administrator already exists; first-admin bootstrap is disabled.")
    account = db.subscription_users.find_one({"email": normalized}, {"_id": 1, "user_id": 1})
    if not account:
        raise LookupError("No existing platform account matches that email.")
    result = db.subscription_users.update_one(
        {"_id": account["_id"], "role": {"$ne": "admin"}},
        {"$set": {"role": "admin"}},
    )
    if result.modified_count != 1:
        raise RuntimeError("The account could not be promoted; no change was confirmed.")
    db.security_audit.insert_one({
        "actor_user_id": "operator_bootstrap",
        "action": "first_platform_administrator_bootstrapped",
        "target": account.get("user_id", "existing-account"),
        "details": {"method": "explicit operator command"},
        "created_at": datetime.now(timezone.utc),
    })
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Email of an existing account to promote")
    parser.add_argument("--confirm", action="store_true", help="Confirm this one-time role assignment")
    args = parser.parse_args(argv)
    if not args.confirm:
        parser.error("pass --confirm to perform the one-time promotion")
    try:
        db = connect_database()
        promote_first_admin(db, args.email)
        print("The existing account was assigned the first administrator role and the action was audited.")
        return 0
    except (ValueError, RuntimeError, LookupError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        close_database()


if __name__ == "__main__":
    raise SystemExit(main())
