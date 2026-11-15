from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pymongo import ReplaceOne

from app.database import create_client

EXPECTED_TENANTS = {f"tenant_{index}" for index in range(1, 6)}
TENANT_COLLECTION_PREFIX = "tasks_tenant_"


def migrate(database) -> dict[str, int]:
    tenant_ids = database.tasks.distinct("tenant_id", {"tenant_id": {"$type": "string"}})
    if set(tenant_ids) != EXPECTED_TENANTS:
        raise RuntimeError(
            "Migration requires exactly the five seeded tenant IDs; "
            f"found {sorted(tenant_ids)}."
        )

    counts: dict[str, int] = {}
    for tenant_id in sorted(tenant_ids):
        if not re.fullmatch(r"tenant_[1-5]", tenant_id):
            raise RuntimeError(f"Unsupported tenant ID: {tenant_id}")
        source_documents = list(database.tasks.find({"tenant_id": tenant_id}).sort("_id", 1))
        if len(source_documents) != 100:
            raise RuntimeError(
                f"{tenant_id} has {len(source_documents)} source tasks; expected exactly 100."
            )
        destination = database[f"{TENANT_COLLECTION_PREFIX}{tenant_id}"]
        destination.create_index([("tenant_id", 1), ("created_at", -1)], name="ix_tenant_created")
        operations = [
            ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            for document in source_documents
        ]
        if operations:
            destination.bulk_write(operations, ordered=True)
        counts[tenant_id] = destination.count_documents({})
        if counts[tenant_id] != 100:
            raise RuntimeError(
                f"{destination.name} contains {counts[tenant_id]} documents after migration."
            )
    return counts


def main() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        raise SystemExit("Set MONGO_URI before running the migration script.")
    database_name = os.getenv("TASKTRACKER_DB_NAME", "tasktracker")
    client = create_client(uri)
    try:
        client.admin.command("ping")
        counts = migrate(client[database_name])
        print(json.dumps({
            "database": database_name,
            "tenant_collections": len(counts),
            "migrated_documents": sum(counts.values()),
            "per_tenant": counts,
        }, indent=2))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
