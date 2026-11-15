from __future__ import annotations

from typing import Any

from pymongo.database import Database

from backend.models.chapter18 import ThreatRecord

FULL_THREATS = "full_threats"
IMPORT_RUNS = "import_runs"
ANALYST_REVIEWS = "analyst_reviews"
BENCHMARK_RUNS = "benchmark_runs"
INVESTIGATIONS = "investigations"

_THREAT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "threat_id",
            "source",
            "from_source",
            "threat_type",
            "timestamp",
            "status",
            "related_threats",
            "transaction_ids",
            "provenance",
        ],
        "properties": {
            "threat_id": {"bsonType": "string"},
            "source": {"bsonType": "string"},
            "from_source": {"bsonType": "string"},
            "threat_type": {"bsonType": "string"},
            "indicator": {"bsonType": ["string", "null"]},
            "target": {"bsonType": ["string", "null"]},
            "timestamp": {"bsonType": "date"},
            "severity": {"bsonType": ["string", "null"]},
            "confidence": {"bsonType": ["double", "int", "long", "null"]},
            "status": {"bsonType": "string"},
            "description": {"bsonType": ["string", "null"]},
            "related_threats": {"bsonType": "array", "items": {"bsonType": "string"}},
            "transaction_ids": {"bsonType": "array", "items": {"bsonType": "string"}},
            "source_reference": {"bsonType": ["string", "null"]},
            "latitude": {"bsonType": ["double", "int", "long", "null"]},
            "longitude": {"bsonType": ["double", "int", "long", "null"]},
            "country_code": {"bsonType": ["string", "null"]},
            "geolocation_source": {"bsonType": ["string", "null"]},
            "provenance": {"enum": ["imported", "test_fixture"]},
        },
    }
}


def ensure_mongo_schema(database: Database) -> dict[str, Any]:
    collections = {
        collection["name"]: collection
        for collection in database.list_collections()
    }
    if FULL_THREATS not in collections:
        database.create_collection(
            FULL_THREATS,
            validator=_THREAT_VALIDATOR,
            validationLevel="strict",
            validationAction="error",
        )
        validator_enabled = True
    else:
        validator_enabled = bool(collections[FULL_THREATS].get("options", {}).get("validator"))

    database[FULL_THREATS].create_index(
        [("threat_id", 1)], name="lab18_threat_id_unique", unique=True
    )
    database[FULL_THREATS].create_index(
        [("from_source", 1), ("provenance", 1), ("timestamp", -1)],
        name="lab18_source_provenance_time",
    )
    database[FULL_THREATS].create_index(
        [("provenance", 1), ("timestamp", -1), ("threat_type", 1)],
        name="lab18_provenance_time_type",
    )
    database[FULL_THREATS].create_index(
        [("target", 1), ("provenance", 1), ("timestamp", -1)],
        name="lab18_target_provenance_time",
    )
    database[FULL_THREATS].create_index(
        [("indicator", 1), ("provenance", 1), ("timestamp", -1)],
        name="lab18_indicator_provenance_time",
    )
    database[IMPORT_RUNS].create_index(
        [("created_at", -1)], name="lab18_import_created_at"
    )
    database[ANALYST_REVIEWS].create_index(
        [("threat_id", 1), ("created_at", -1)], name="lab18_review_threat_time"
    )
    database[BENCHMARK_RUNS].create_index(
        [("run_id", 1)], name="lab18_benchmark_run_id_unique", unique=True
    )
    database[BENCHMARK_RUNS].create_index(
        [("created_at", -1)], name="lab18_benchmark_created_at"
    )
    database[INVESTIGATIONS].create_index(
        [("created_at", -1)], name="lab18_investigation_created_at"
    )
    database[ANALYST_REVIEWS].create_index(
        [("subject_type", 1), ("subject_id", 1), ("created_at", -1)],
        name="lab18_review_subject_time",
    )

    return {
        "database": database.name,
        "collections": {
            FULL_THREATS: True,
            IMPORT_RUNS: True,
            ANALYST_REVIEWS: True,
            BENCHMARK_RUNS: True,
            INVESTIGATIONS: True,
        },
        "validator_enabled": validator_enabled,
        "indexes": {
            FULL_THREATS: [
                "lab18_threat_id_unique",
                "lab18_source_provenance_time",
                "lab18_provenance_time_type",
                "lab18_target_provenance_time",
                "lab18_indicator_provenance_time",
            ],
            IMPORT_RUNS: ["lab18_import_created_at"],
            ANALYST_REVIEWS: [
                "lab18_review_threat_time",
                "lab18_review_subject_time",
            ],
            INVESTIGATIONS: ["lab18_investigation_created_at"],
            BENCHMARK_RUNS: [
                "lab18_benchmark_run_id_unique",
                "lab18_benchmark_created_at",
            ],
        },
    }


def mongo_document(record: ThreatRecord) -> dict[str, Any]:
    return record.model_dump(mode="python")
