from __future__ import annotations

import importlib.util
import json
import logging
import importlib
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.operations import SearchIndexModel

from backend.services.vector_rag import (
    RAG_DATABASE,
    VectorProjectError,
    inspect_vector_index,
    record_project_event,
    vector_index_definition,
)

logger = logging.getLogger("aegis.vector_projects.benchmark")

BENCHMARK_PROJECT = "Lab 17.2 Vector Search Benchmark"
BENCHMARK_DATABASE = RAG_DATABASE
BENCHMARK_COLLECTION = "vector_benchmark_documents"
BENCHMARK_JOBS = "vector_benchmark_jobs"
BENCHMARK_RUNS = BENCHMARK_JOBS
BENCHMARK_STATE = "vector_benchmark_state"
BENCHMARK_INDEX_NAME = "vector_benchmark_index"
BENCHMARK_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BENCHMARK_DIMENSIONS = 384
DATASET_SIZE = 5000
DATASET_BATCH_SIZE = 500
QUERY_SAMPLE_SIZE = 100
TOP_K = 10
MAX_JSONL_BYTES = 25 * 1024 * 1024
MAX_TEXT_LENGTH = 10_000
_ACTIVE_JOB_KEY = "lab17-vector-benchmark"


def parse_jsonl_dataset(content: bytes) -> list[dict[str, str]]:
    if len(content) > MAX_JSONL_BYTES:
        raise VectorProjectError(
            "dataset_too_large",
            f"The JSONL dataset exceeds the {MAX_JSONL_BYTES // (1024 * 1024)} MB upload limit.",
            "dataset_validation",
            413,
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise VectorProjectError(
            "dataset_invalid_encoding",
            "Upload a UTF-8 encoded JSONL file.",
            "dataset_validation",
            422,
        ) from error
    lines = text.splitlines()
    if len(lines) != DATASET_SIZE:
        raise VectorProjectError(
            "dataset_record_count_mismatch",
            f"The benchmark requires exactly {DATASET_SIZE:,} non-empty JSONL records; received {len(lines):,}.",
            "dataset_validation",
            422,
        )
    rows = []
    for index, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise VectorProjectError(
                "dataset_invalid_json",
                f"JSONL record {index} is not valid JSON.",
                "dataset_validation",
                422,
            ) from error
        if not isinstance(row, dict):
            raise VectorProjectError(
                "dataset_invalid_record",
                f"JSONL record {index} must be a JSON object.",
                "dataset_validation",
                422,
            )
        source = row.get("source")
        value = row.get("text")
        if not isinstance(source, str) or not source.strip() or len(source.strip()) > 255:
            raise VectorProjectError(
                "dataset_invalid_source",
                f"JSONL record {index} must include a source string of 1 to 255 characters.",
                "dataset_validation",
                422,
            )
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_TEXT_LENGTH:
            raise VectorProjectError(
                "dataset_invalid_text",
                f"JSONL record {index} must include a text string of 1 to {MAX_TEXT_LENGTH:,} characters.",
                "dataset_validation",
                422,
            )
        rows.append({"source": source.strip(), "text": value.strip()})
    return rows


def benchmark_capabilities() -> dict[str, Any]:
    try:
        sentence_transformers_installed = importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        sentence_transformers_installed = False
    try:
        numpy_installed = importlib.util.find_spec("numpy") is not None
    except (ImportError, ValueError):
        numpy_installed = False
    return {
        "embedding_model": BENCHMARK_MODEL,
        "expected_dimensions": BENCHMARK_DIMENSIONS,
        "sentence_transformers_installed": sentence_transformers_installed,
        "numpy_installed": numpy_installed,
        "ready_to_embed": sentence_transformers_installed and numpy_installed,
        "install_note": (
            None if sentence_transformers_installed and numpy_installed
            else "Install lab-17/requirements-vector-lab.txt in the backend image to enable dataset embedding."
        ),
    }


def benchmark_index_definition() -> dict:
    definition = vector_index_definition(BENCHMARK_DIMENSIONS)
    definition["fields"].append({"type": "filter", "path": "dataset_id"})
    return definition


def _numpy_module():
    try:
        return importlib.import_module("numpy")
    except ImportError as error:
        raise VectorProjectError(
            "benchmark_numpy_not_installed",
            "NumPy is required for the exact-search benchmark. Install the optional Lab 17.2 requirements.",
            "benchmark_setup",
            409,
        ) from error


def vector_search_configurations() -> list[dict[str, str]]:
    return [
        {
            "id": "atlas-ann",
            "label": "MongoDB Atlas ANN (managed HNSW)",
            "status": "supported",
            "note": "The index uses MongoDB-managed defaults; Atlas does not expose custom HNSW M or efConstruction settings here.",
        },
        {
            "id": "atlas-enn",
            "label": "MongoDB Atlas ENN (exact search)",
            "status": "supported",
            "note": "Exact nearest-neighbor search is the database comparison baseline, not a separate index.",
        },
        {
            "id": "custom-hnsw",
            "label": "Custom HNSW M / efConstruction variants",
            "status": "unsupported",
            "note": "MongoDB Atlas Vector Search does not expose these index parameters in its supported vector-index definition.",
        },
        {
            "id": "ivf",
            "label": "IVF index",
            "status": "unsupported",
            "note": "MongoDB Atlas Vector Search does not provide an IVF index configuration. No HNSW measurements are relabeled as IVF.",
        },
    ]


def exact_top_k_ids(
    matrix: Any,
    document_ids: list[str],
    query_vector: Any,
    limit: int,
) -> tuple[list[str], float]:
    np = _numpy_module()
    if matrix.ndim != 2 or matrix.shape[1] != BENCHMARK_DIMENSIONS:
        raise VectorProjectError(
            "dataset_dimension_mismatch",
            f"The benchmark expects stored vectors with exactly {BENCHMARK_DIMENSIONS} dimensions.",
            "exact_search",
            409,
        )
    if len(document_ids) != matrix.shape[0] or not 1 <= limit <= matrix.shape[0]:
        raise VectorProjectError(
            "dataset_shape_mismatch",
            "The exact-search dataset is incomplete or invalid.",
            "exact_search",
            409,
        )
    started = time.perf_counter()
    query_norm = np.linalg.norm(query_vector)
    if not np.isfinite(query_norm) or query_norm == 0:
        raise VectorProjectError(
            "invalid_query_vector",
            "The benchmark query vector is empty or non-finite.",
            "exact_search",
            422,
        )
    norms = np.linalg.norm(matrix, axis=1)
    valid = np.isfinite(norms) & (norms > 0) & np.isfinite(matrix).all(axis=1)
    if not valid.all():
        raise VectorProjectError(
            "invalid_dataset_vector",
            "The dataset contains a zero-length or non-finite vector.",
            "exact_search",
            409,
        )
    scores = matrix @ query_vector / (norms * query_norm)
    best = np.argpartition(scores, -limit)[-limit:]
    ordered = best[np.argsort(scores[best])[::-1]]
    duration_ms = (time.perf_counter() - started) * 1000
    return [document_ids[index] for index in ordered], duration_ms


def summarize_latency(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"query_count": 0, "average_ms": None, "p50_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None}
    ordered = sorted(values)
    def percentile(value: float) -> float:
        position = (len(ordered) - 1) * value
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight
    return {
        "query_count": len(values),
        "average_ms": round(sum(values) / len(values), 3),
        "p50_ms": round(percentile(0.5), 3),
        "p95_ms": round(percentile(0.95), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }


def ensure_benchmark_indexes(database: Database) -> None:
    db = database.client[BENCHMARK_DATABASE]
    db[BENCHMARK_COLLECTION].create_index(
        [("dataset_id", ASCENDING), ("document_index", ASCENDING)],
        unique=True,
        name="vector_benchmark_dataset_document_unique",
    )
    db[BENCHMARK_COLLECTION].create_index(
        [("dataset_id", ASCENDING), ("document_id", ASCENDING)],
        name="vector_benchmark_dataset_document",
    )
    db[BENCHMARK_JOBS].create_index(
        [("active_key", ASCENDING)],
        unique=True,
        sparse=True,
        name="vector_benchmark_active_job_unique",
    )
    db[BENCHMARK_JOBS].create_index(
        [("created_at", DESCENDING)],
        name="vector_benchmark_jobs_created",
    )


def _set_job_failure(database: Database, job_id: str, code: str, message: str) -> None:
    database.client[BENCHMARK_DATABASE][BENCHMARK_JOBS].update_one(
        {"job_id": job_id},
        {"$set": {
            "status": "failed",
            "stage": "failed",
            "error_code": code,
            "error_message": message,
            "completed_at": datetime.now(timezone.utc),
        }, "$unset": {"active_key": ""}},
    )


def _update_job(database: Database, job_id: str, fields: dict) -> None:
    database.client[BENCHMARK_DATABASE][BENCHMARK_JOBS].update_one(
        {"job_id": job_id},
        {"$set": fields},
    )


def _embed_dataset(database: Database, job_id: str, dataset_id: str, rows: list[dict[str, str]], source_size: int) -> None:
    db = database.client[BENCHMARK_DATABASE]
    collection = db[BENCHMARK_COLLECTION]
    started = datetime.now(timezone.utc)
    correlation_id = job_id
    try:
        np = _numpy_module()
        sentence_transformers = importlib.import_module("sentence_transformers")
        model = sentence_transformers.SentenceTransformer(BENCHMARK_MODEL)
        vectors_count = 0
        failures = 0
        for start in range(0, len(rows), DATASET_BATCH_SIZE):
            batch = rows[start:start + DATASET_BATCH_SIZE]
            embeddings_started = time.perf_counter()
            embeddings = model.encode(
                [item["text"] for item in batch],
                batch_size=32,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            embedding_duration_ms = (time.perf_counter() - embeddings_started) * 1000
            if embeddings.ndim != 2 or embeddings.shape != (len(batch), BENCHMARK_DIMENSIONS):
                raise VectorProjectError(
                    "embedding_dimension_mismatch",
                    f"{BENCHMARK_MODEL} returned vectors that do not have {BENCHMARK_DIMENSIONS} dimensions.",
                    "dataset_embedding",
                    502,
                )
            if not np.isfinite(embeddings).all():
                raise VectorProjectError(
                    "invalid_dataset_embedding",
                    "The embedding model produced non-finite vector values.",
                    "dataset_embedding",
                    502,
                )
            documents = [{
                "dataset_id": dataset_id,
                "document_id": f"{dataset_id}:{start + offset}",
                "document_index": start + offset,
                "source": item["source"],
                "text": item["text"],
                "embedding_model": BENCHMARK_MODEL,
                "embedding_dimensions": BENCHMARK_DIMENSIONS,
                "embedding": [float(value) for value in embeddings[offset]],
                "created_at": started,
            } for offset, item in enumerate(batch)]
            collection.insert_many(documents, ordered=True)
            vectors_count += len(documents)
            completed = min(start + len(batch), DATASET_SIZE)
            _update_job(database, job_id, {
                "status": "running",
                "stage": "dataset_embedding_and_storage",
                "completed_units": completed,
                "total_units": DATASET_SIZE,
                "progress_percent": round(completed / DATASET_SIZE * 100, 1),
                "successful_vectors": vectors_count,
                "failed_vectors": failures,
                "updated_at": datetime.now(timezone.utc),
            })
            record_project_event(
                database,
                project_name=BENCHMARK_PROJECT,
                correlation_id=correlation_id,
                operation="Dataset embeddings generated and batch inserted",
                component="Sentence Transformers / MongoDB",
                status="success",
                duration_ms=embedding_duration_ms,
                final_outcome=f"{vectors_count} of {DATASET_SIZE} vectors stored",
            )
        if vectors_count != DATASET_SIZE:
            raise VectorProjectError(
                "dataset_insert_count_mismatch",
                f"Expected {DATASET_SIZE} stored vectors; database reported {vectors_count}.",
                "dataset_storage",
                503,
            )
        db[BENCHMARK_STATE].update_one(
            {"_id": "active_dataset"},
            {"$set": {
                "dataset_id": dataset_id,
                "record_count": vectors_count,
                "dimensions": BENCHMARK_DIMENSIONS,
                "model": BENCHMARK_MODEL,
                "source_size_bytes": source_size,
                "created_at": started,
                "ready_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        _update_job(database, job_id, {
            "status": "completed",
            "stage": "completed",
            "completed_units": DATASET_SIZE,
            "total_units": DATASET_SIZE,
            "progress_percent": 100,
            "successful_vectors": vectors_count,
            "failed_vectors": failures,
            "completed_at": datetime.now(timezone.utc),
            "dataset_id": dataset_id,
        })
        database.client[BENCHMARK_DATABASE][BENCHMARK_JOBS].update_one(
            {"job_id": job_id},
            {"$unset": {"active_key": ""}},
        )
        record_project_event(
            database,
            project_name=BENCHMARK_PROJECT,
            correlation_id=correlation_id,
            operation="Dataset generation completed",
            component="Sentence Transformers / MongoDB",
            status="success",
            duration_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
            final_outcome=f"{vectors_count} vectors stored at {BENCHMARK_DIMENSIONS} dimensions",
        )
    except VectorProjectError as error:
        _set_job_failure(database, job_id, error.code, error.message)
        record_project_event(
            database,
            project_name=BENCHMARK_PROJECT,
            correlation_id=correlation_id,
            operation="Dataset generation failed",
            component=error.stage,
            status="failed",
            error_type=error.code,
            error_message=error.message,
            final_outcome="failed",
        )
    except (ImportError, OSError, RuntimeError, ValueError, httpx.HTTPError, PyMongoError) as error:
        logger.exception("Lab 17.2 dataset job %s failed", job_id)
        _set_job_failure(
            database,
            job_id,
            type(error).__name__,
            "Dataset embedding or storage failed. Check backend logs for the stage and retry after correcting the configuration.",
        )
        record_project_event(
            database,
            project_name=BENCHMARK_PROJECT,
            correlation_id=correlation_id,
            operation="Dataset generation failed",
            component="Sentence Transformers / MongoDB",
            status="failed",
            error_type=type(error).__name__,
            error_message="Dataset embedding or storage failed. Check backend logs for the stage and retry after correcting the configuration.",
            final_outcome="failed",
        )


def start_dataset_job(database: Database, rows: list[dict[str, str]], source_size: int) -> dict:
    ensure_benchmark_indexes(database)
    capabilities = benchmark_capabilities()
    if not capabilities["ready_to_embed"]:
        raise VectorProjectError(
            "benchmark_model_not_installed",
            capabilities["install_note"],
            "dataset_embedding",
            409,
        )
    job_id = str(uuid4())
    dataset_id = str(uuid4())
    now = datetime.now(timezone.utc)
    job = {
        "job_id": job_id,
        "job_type": "dataset",
        "status": "queued",
        "stage": "queued",
        "active_key": _ACTIVE_JOB_KEY,
        "completed_units": 0,
        "total_units": DATASET_SIZE,
        "progress_percent": 0,
        "successful_vectors": 0,
        "failed_vectors": 0,
        "dataset_id": dataset_id,
        "created_at": now,
        "updated_at": now,
    }
    try:
        database.client[BENCHMARK_DATABASE][BENCHMARK_JOBS].insert_one(job)
    except DuplicateKeyError as error:
        raise VectorProjectError(
            "benchmark_job_already_running",
            "Another Lab 17.2 dataset or benchmark job is already running.",
            "job_start",
            409,
        ) from error
    record_project_event(
        database,
        project_name=BENCHMARK_PROJECT,
        correlation_id=job_id,
        operation="Dataset embedding job queued",
        component="Lab 17.2 benchmark job",
        status="running",
        final_outcome="queued",
    )
    thread = threading.Thread(
        target=_embed_dataset,
        args=(database, job_id, dataset_id, rows, source_size),
        name=f"lab17-dataset-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "dataset_id": dataset_id, "status": "queued", "progress_percent": 0}


def create_benchmark_index(collection: Collection) -> str:
    state = inspect_vector_index(collection, BENCHMARK_INDEX_NAME, BENCHMARK_DIMENSIONS)
    if not state["supported"]:
        raise VectorProjectError("vector_search_not_supported", state["detail"], "index", 409)
    if state["ready"]:
        return BENCHMARK_INDEX_NAME
    if state.get("exists"):
        if state.get("dimensions") != BENCHMARK_DIMENSIONS:
            raise VectorProjectError(
                "vector_index_definition_mismatch",
                state.get("detail", "An index with this name already exists with a different definition."),
                "index",
                409,
            )
        return BENCHMARK_INDEX_NAME
    if state.get("dimensions") and state["dimensions"] != BENCHMARK_DIMENSIONS:
        raise VectorProjectError(
            "vector_index_dimension_mismatch",
            state["detail"],
            "index",
            409,
        )
    index = SearchIndexModel(
        name=BENCHMARK_INDEX_NAME,
        type="vectorSearch",
        definition=benchmark_index_definition(),
    )
    return collection.create_search_index(index)


def _query_vector(
    collection: Collection,
    vector: list[float],
    dataset_id: str,
    exact: bool,
) -> tuple[list[str], float]:
    options: dict[str, Any] = {
        "index": BENCHMARK_INDEX_NAME,
        "path": "embedding",
        "queryVector": vector,
        "limit": TOP_K,
        "exact": exact,
        "filter": {"dataset_id": {"$eq": dataset_id}},
    }
    if not exact:
        options["numCandidates"] = 200
    started = time.perf_counter()
    rows = list(collection.aggregate([
        {"$vectorSearch": options},
        {"$project": {"_id": 0, "document_id": 1}},
    ], maxTimeMS=10_000))
    return [str(item["document_id"]) for item in rows], (time.perf_counter() - started) * 1000


def _run_benchmark(database: Database, run_id: str, dataset_id: str, index_build_time_ms: float | None) -> None:
    db = database.client[BENCHMARK_DATABASE]
    collection = db[BENCHMARK_COLLECTION]
    started = datetime.now(timezone.utc)
    try:
        np = _numpy_module()
        rows = list(collection.find(
            {"dataset_id": dataset_id},
            {"_id": 0, "document_id": 1, "embedding": 1},
        ).sort("document_index", ASCENDING))
        if len(rows) != DATASET_SIZE:
            raise VectorProjectError(
                "dataset_incomplete",
                f"The active dataset contains {len(rows)} vectors; {DATASET_SIZE} are required.",
                "dataset_validation",
                409,
            )
        ids = [row["document_id"] for row in rows]
        matrix = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
        if matrix.shape != (DATASET_SIZE, BENCHMARK_DIMENSIONS) or not np.isfinite(matrix).all():
            raise VectorProjectError(
                "dataset_dimension_mismatch",
                f"The active dataset must contain {DATASET_SIZE} valid {BENCHMARK_DIMENSIONS}-dimensional vectors.",
                "dataset_validation",
                409,
            )
        index_state = inspect_vector_index(collection, BENCHMARK_INDEX_NAME, BENCHMARK_DIMENSIONS)
        if not index_state.get("supported") or not index_state.get("ready"):
            raise VectorProjectError(
                "vector_index_not_ready",
                index_state.get("detail", "The Atlas Vector Search index is not ready."),
                "benchmark_search",
                409,
            )
        seed = int(dataset_id.replace("-", "")[:8], 16)
        query_indexes = np.random.default_rng(seed).choice(DATASET_SIZE, size=QUERY_SAMPLE_SIZE, replace=False)
        ann_latencies: list[float] = []
        enn_latencies: list[float] = []
        brute_latencies: list[float] = []
        recall_values: list[float] = []
        enn_recall_values: list[float] = []
        failed_queries = 0
        for position, query_index in enumerate(query_indexes, start=1):
            query_row = rows[int(query_index)]
            query_vector = matrix[int(query_index)]
            exact_ids, brute_ms = exact_top_k_ids(matrix, ids, query_vector, TOP_K)
            try:
                ann_ids, ann_ms = _query_vector(collection, query_row["embedding"], dataset_id, exact=False)
                enn_ids, enn_ms = _query_vector(collection, query_row["embedding"], dataset_id, exact=True)
            except PyMongoError as error:
                failed_queries += 1
                record_project_event(
                    database,
                    project_name=BENCHMARK_PROJECT,
                    correlation_id=run_id,
                    operation="Vector query failed",
                    component="MongoDB Vector Search",
                    status="failed",
                    error_type=type(error).__name__,
                    error_message="Atlas rejected or failed to execute a benchmark vector query.",
                    final_outcome=f"query {position} failed",
                )
                raise VectorProjectError(
                    "vector_query_failed",
                    "MongoDB Atlas could not execute a benchmark vector query. See the run details and backend logs.",
                    "benchmark_search",
                    503,
                ) from error
            ann_latencies.append(ann_ms)
            enn_latencies.append(enn_ms)
            brute_latencies.append(brute_ms)
            if not exact_ids:
                raise VectorProjectError(
                    "empty_exact_result",
                    "Exact local search did not return a reference result set.",
                    "exact_search",
                    503,
                )
            recall_values.append(len(set(ann_ids).intersection(exact_ids)) / len(exact_ids) * 100)
            enn_recall_values.append(len(set(enn_ids).intersection(exact_ids)) / len(exact_ids) * 100)
            db[BENCHMARK_RUNS].update_one(
                {"run_id": run_id},
                {"$set": {
                    "status": "running",
                    "stage": "measuring",
                    "completed_units": position,
                    "total_units": QUERY_SAMPLE_SIZE,
                    "progress_percent": round(position / QUERY_SAMPLE_SIZE * 100, 1),
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            record_project_event(
                database,
                project_name=BENCHMARK_PROJECT,
                correlation_id=run_id,
                operation="ANN, ENN and local exact measurements completed",
                component="MongoDB Vector Search benchmark",
                status="success",
                duration_ms=ann_ms + enn_ms + brute_ms,
                final_outcome=f"query {position} of {QUERY_SAMPLE_SIZE}; recall@{TOP_K} {recall_values[-1]:.2f}%",
            )

        result = {
            "dataset_id": dataset_id,
            "dataset_size": DATASET_SIZE,
            "dimensions": BENCHMARK_DIMENSIONS,
            "embedding_model": BENCHMARK_MODEL,
            "query_count": len(query_indexes),
            "top_k": TOP_K,
            "failed_queries": failed_queries,
            "index_build_time_ms": round(index_build_time_ms, 3) if index_build_time_ms is not None else None,
            "measurements": {
                "atlas_ann": {
                    "label": "MongoDB Atlas ANN (managed HNSW)",
                    **summarize_latency(ann_latencies),
                    "recall_at_k_percent": round(sum(recall_values) / len(recall_values), 3) if recall_values else None,
                    "recall_samples_percent": [round(value, 3) for value in recall_values],
                    "latency_samples_ms": [round(value, 3) for value in ann_latencies],
                },
                "atlas_enn": {
                    "label": "MongoDB Atlas ENN (exact search)",
                    **summarize_latency(enn_latencies),
                    "recall_at_k_percent": round(sum(enn_recall_values) / len(enn_recall_values), 3)
                    if enn_recall_values else None,
                    "latency_samples_ms": [round(value, 3) for value in enn_latencies],
                },
                "local_exact": {
                    "label": "Local NumPy exact cosine baseline",
                    **summarize_latency(brute_latencies),
                    "recall_at_k_percent": 100.0,
                    "latency_samples_ms": [round(value, 3) for value in brute_latencies],
                },
            },
            "unsupported_configurations": [
                config for config in vector_search_configurations() if config["status"] == "unsupported"
            ],
            "recommendation": _recommendation(ann_latencies, enn_latencies, recall_values),
        }
        db[BENCHMARK_RUNS].update_one(
            {"run_id": run_id},
            {"$set": {
                "status": "completed",
                "stage": "completed",
                "completed_units": QUERY_SAMPLE_SIZE,
                "total_units": QUERY_SAMPLE_SIZE,
                "progress_percent": 100,
                "result": result,
                "completed_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }, "$unset": {"active_key": ""}},
        )
        record_project_event(
            database,
            project_name=BENCHMARK_PROJECT,
            correlation_id=run_id,
            operation="Vector Search benchmark completed",
            component="MongoDB Vector Search benchmark",
            status="success",
            duration_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
            final_outcome=f"{len(query_indexes)} measured queries",
        )
    except VectorProjectError as error:
        db[BENCHMARK_RUNS].update_one(
            {"run_id": run_id},
            {"$set": {
                "status": "failed",
                "stage": error.stage,
                "error_code": error.code,
                "error_message": error.message,
                "completed_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }, "$unset": {"active_key": ""}},
        )
        record_project_event(
            database,
            project_name=BENCHMARK_PROJECT,
            correlation_id=run_id,
            operation="Vector Search benchmark failed",
            component=error.stage,
            status="failed",
            error_type=error.code,
            error_message=error.message,
            final_outcome="failed",
        )
    except (ImportError, OSError, RuntimeError, ValueError, PyMongoError) as error:
        logger.exception("Lab 17.2 benchmark run %s failed", run_id)
        db[BENCHMARK_RUNS].update_one(
            {"run_id": run_id},
            {"$set": {
                "status": "failed",
                "stage": "benchmark_execution",
                "error_code": type(error).__name__,
                "error_message": "Benchmark execution failed. Check backend logs for the stage and database error.",
                "completed_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }, "$unset": {"active_key": ""}},
        )
        record_project_event(
            database,
            project_name=BENCHMARK_PROJECT,
            correlation_id=run_id,
            operation="Vector Search benchmark failed",
            component="MongoDB Vector Search benchmark",
            status="failed",
            error_type=type(error).__name__,
            error_message="Benchmark execution failed. Check backend logs for the stage and database error.",
            final_outcome="failed",
        )


def _recommendation(ann: list[float], enn: list[float], recall: list[float]) -> str:
    if not ann or not enn or not recall:
        return "No recommendation is available because the benchmark did not complete enough measurements."
    ann_average = sum(ann) / len(ann)
    enn_average = sum(enn) / len(enn)
    recall_average = sum(recall) / len(recall)
    if ann_average < enn_average:
        speedup = (enn_average - ann_average) / enn_average * 100 if enn_average else 0
        return (
            f"On this measured 5,000-vector dataset, managed ANN averaged {ann_average:.3f} ms "
            f"versus ENN at {enn_average:.3f} ms ({speedup:.1f}% lower mean latency) with "
            f"{recall_average:.2f}% recall@{TOP_K}. Validate that recall meets the application’s own acceptance target."
        )
    return (
        f"On this measured 5,000-vector dataset, managed ANN averaged {ann_average:.3f} ms "
        f"versus ENN at {enn_average:.3f} ms and achieved {recall_average:.2f}% recall@{TOP_K}. "
        "Review the measured recall and query workload before choosing ANN; do not infer performance for other datasets."
    )


def start_benchmark_job(database: Database, dataset_id: str, index_build_time_ms: float | None) -> dict:
    ensure_benchmark_indexes(database)
    run_id = str(uuid4())
    now = datetime.now(timezone.utc)
    run = {
        "run_id": run_id,
        "job_type": "benchmark",
        "status": "queued",
        "stage": "queued",
        "active_key": _ACTIVE_JOB_KEY,
        "dataset_id": dataset_id,
        "created_at": now,
        "updated_at": now,
        "completed_units": 0,
        "total_units": QUERY_SAMPLE_SIZE,
        "progress_percent": 0,
    }
    try:
        database.client[BENCHMARK_DATABASE][BENCHMARK_RUNS].insert_one(run)
    except DuplicateKeyError as error:
        raise VectorProjectError(
            "benchmark_job_already_running",
            "Another Lab 17.2 dataset or benchmark job is already running.",
            "job_start",
            409,
        ) from error
    record_project_event(
        database,
        project_name=BENCHMARK_PROJECT,
        correlation_id=run_id,
        operation="Vector Search benchmark queued",
        component="Lab 17.2 benchmark job",
        status="running",
        final_outcome="queued",
    )
    thread = threading.Thread(
        target=_run_benchmark,
        args=(database, run_id, dataset_id, index_build_time_ms),
        name=f"lab17-benchmark-{run_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"run_id": run_id, "status": "queued", "progress_percent": 0}


def get_index_build_time(state: dict | None) -> float | None:
    if not state or not isinstance(state.get("index_requested_at"), datetime) or not isinstance(state.get("index_ready_at"), datetime):
        return None
    requested_at = state["index_requested_at"]
    ready_at = state["index_ready_at"]
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    if ready_at.tzinfo is None:
        ready_at = ready_at.replace(tzinfo=timezone.utc)
    return max(0.0, (ready_at - requested_at).total_seconds() * 1000)
