"""Add tenant_id to legacy organization-scoped cases; dry-run unless --apply is set."""
from __future__ import annotations

import argparse
import os

from pymongo import MongoClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write tenant_id values; default only reports the candidate count")
    args = parser.parse_args()
    uri = os.getenv("MONGO_URI", "").strip()
    database_name = os.getenv("MONGO_DATABASE", "chapter07").strip()
    if not uri:
        parser.error("Set MONGO_URI in the environment.")
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
        cases = client[database_name].cases
        filter_ = {"tenant_id": {"$exists": False}, "organization_id": {"$type": "string"}}
        count = cases.count_documents(filter_)
        print(f"Legacy cases eligible for tenant_id backfill: {count}")
        if not args.apply:
            print("Dry run only. Review the count and take a backup before rerunning with --apply.")
            return 0
        result = cases.update_many(filter_, [{"$set": {"tenant_id": "$organization_id"}}])
        print(f"Backfilled {result.modified_count} case documents; no records were deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
