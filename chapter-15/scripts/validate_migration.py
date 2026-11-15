from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.database import create_client
from scripts.migrate_tenants import EXPECTED_TENANTS, TENANT_COLLECTION_PREFIX

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "migration_validation.md"


def validate(database) -> dict:
    source = database.tasks
    discovered = set(source.distinct("tenant_id", {"tenant_id": {"$type": "string"}}))
    actual_collections = {
        name for name in database.list_collection_names()
        if name.startswith(TENANT_COLLECTION_PREFIX)
    }
    expected_collections = {
        f"{TENANT_COLLECTION_PREFIX}{tenant_id}" for tenant_id in EXPECTED_TENANTS
    }
    checks = {
        "exactly_five_source_tenants": discovered == EXPECTED_TENANTS,
        "exactly_five_tenant_collections": actual_collections == expected_collections,
    }
    per_tenant: dict[str, dict] = {}
    total = 0

    for tenant_id in sorted(EXPECTED_TENANTS):
        source_documents = list(source.find({"tenant_id": tenant_id}).sort("_id", 1))
        target = database[f"{TENANT_COLLECTION_PREFIX}{tenant_id}"]
        target_documents = list(target.find({}).sort("_id", 1)) if target.name in database.list_collection_names() else []
        source_by_id = {document["_id"]: document for document in source_documents}
        target_by_id = {document["_id"]: document for document in target_documents}
        missing_ids = set(source_by_id) - set(target_by_id)
        extra_ids = set(target_by_id) - set(source_by_id)
        field_mismatches = [
            str(document_id)
            for document_id in source_by_id.keys() & target_by_id.keys()
            if source_by_id[document_id] != target_by_id[document_id]
        ]
        wrong_tenant = target.count_documents({"tenant_id": {"$ne": tenant_id}})
        count_ok = len(source_documents) == 100 and len(target_documents) == 100
        ids_ok = not missing_ids and not extra_ids
        isolation_ok = wrong_tenant == 0
        fields_ok = not field_mismatches
        checks[f"{tenant_id}_count_is_100"] = count_ok
        checks[f"{tenant_id}_ids_preserved"] = ids_ok
        checks[f"{tenant_id}_fields_preserved"] = fields_ok
        checks[f"{tenant_id}_has_no_cross_tenant_records"] = isolation_ok
        total += len(target_documents)
        per_tenant[tenant_id] = {
            "source_count": len(source_documents),
            "migrated_count": len(target_documents),
            "missing_ids": len(missing_ids),
            "extra_ids": len(extra_ids),
            "field_mismatches": len(field_mismatches),
            "wrong_tenant_documents": wrong_tenant,
        }

    checks["total_migrated_documents_is_500"] = total == 500
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_tenants": sorted(discovered),
        "tenant_collections": sorted(actual_collections),
        "total_migrated_documents": total,
        "checks": checks,
        "per_tenant": per_tenant,
    }


def write_report(result: dict, report_path: Path = REPORT_PATH) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Chapter 15 Migration Validation",
        "",
        f"- **Status:** {result['status']}",
        f"- **Validated at:** {result['checked_at']}",
        f"- **Migrated document count:** {result['total_migrated_documents']}",
        f"- **Source tenants:** {', '.join(result['source_tenants']) or '(none)'}",
        f"- **Tenant collections:** {', '.join(result['tenant_collections']) or '(none)'}",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    lines.extend(
        f"| `{name}` | {'PASS' if passed else 'FAIL'} |"
        for name, passed in result["checks"].items()
    )
    lines.extend([
        "",
        "| Tenant | Source | Migrated | Missing IDs | Extra IDs | Field mismatches | Cross-tenant records |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for tenant_id, data in result["per_tenant"].items():
        lines.append(
            f"| {tenant_id} | {data['source_count']} | {data['migrated_count']} | "
            f"{data['missing_ids']} | {data['extra_ids']} | {data['field_mismatches']} | "
            f"{data['wrong_tenant_documents']} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        raise SystemExit("Set MONGO_URI before running migration validation.")
    database_name = os.getenv("TASKTRACKER_DB_NAME", "tasktracker")
    client = create_client(uri)
    try:
        client.admin.command("ping")
        result = validate(client[database_name])
        write_report(result)
        print(json.dumps(result, indent=2, default=str))
    finally:
        client.close()
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
