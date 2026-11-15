from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId
from pymongo import ReplaceOne

from app.database import create_client, ensure_indexes

TENANTS = tuple(f"tenant_{index}" for index in range(1, 6))
TASKS_PER_TENANT = 100
SEED_MARKER = "chapter15_seed_key"


def _task_id(tenant_id: str, sequence: int) -> ObjectId:
    digest = hashlib.sha256(f"chapter15:{tenant_id}:{sequence}".encode("utf-8")).digest()
    return ObjectId(digest[:12])


def seed(database) -> dict[str, int]:
    ensure_indexes(database)
    tasks = database.tasks
    operations = []
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for tenant_id in TENANTS:
        for sequence in range(1, TASKS_PER_TENANT + 1):
            task_id = _task_id(tenant_id, sequence)
            existing = tasks.find_one({"_id": task_id}, {"tenant_id": 1, SEED_MARKER: 1})
            seed_key = f"{tenant_id}:{sequence}"
            if existing and existing.get(SEED_MARKER) != seed_key:
                raise RuntimeError(f"Refusing to overwrite an unrelated task with ID {task_id}.")
            document = {
                "_id": task_id,
                "tenant_id": tenant_id,
                "title": f"Task {sequence:03d} for {tenant_id}",
                "status": ("todo", "in_progress", "done")[(sequence - 1) % 3],
                "priority": ("low", "medium", "high")[(sequence - 1) % 3],
                "created_at": now - timedelta(minutes=sequence),
                SEED_MARKER: seed_key,
            }
            operations.append(ReplaceOne({"_id": task_id}, document, upsert=True))
    result = tasks.bulk_write(operations, ordered=True)
    counts = {
        tenant_id: tasks.count_documents({"tenant_id": tenant_id, SEED_MARKER: {"$exists": True}})
        for tenant_id in TENANTS
    }
    if any(count != TASKS_PER_TENANT for count in counts.values()):
        raise RuntimeError(f"Seed verification failed: {counts}")
    return {
        "matched": result.matched_count,
        "upserted": result.upserted_count,
        "modified": result.modified_count,
        "seeded_documents": sum(counts.values()),
        **counts,
    }


def main() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        raise SystemExit("Set MONGO_URI before running the seed script.")
    database_name = os.getenv("TASKTRACKER_DB_NAME", "tasktracker")
    client = create_client(uri)
    try:
        client.admin.command("ping")
        result = seed(client[database_name])
        print(json.dumps({"database": database_name, **result}, indent=2))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
