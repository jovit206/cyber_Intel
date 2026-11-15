from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from backend.routes.admin import _admin_user
from backend.services import vector_benchmark as benchmark
from backend.services.vector_rag import VectorProjectError, inspect_vector_index, record_project_event

router = APIRouter(prefix="/api/vector-benchmark", tags=["Lab 17.2 Vector Search Benchmark"])


def _database(request: Request):
    return request.app.state.database


def _benchmark_database(request: Request):
    return _database(request).client[benchmark.BENCHMARK_DATABASE]


def _safe_job(job: dict | None) -> dict | None:
    if not job:
        return None
    fields = (
        "job_id", "run_id", "job_type", "status", "stage", "completed_units", "total_units",
        "progress_percent", "successful_vectors", "failed_vectors", "dataset_id", "created_at",
        "updated_at", "completed_at", "error_code", "error_message",
    )
    result = {key: job[key] for key in fields if key in job}
    for field in ("created_at", "updated_at", "completed_at"):
        value = result.get(field)
        if isinstance(value, datetime):
            result[field] = value.astimezone(timezone.utc).isoformat()
    return result


def _record_failure(database, correlation_id: str, operation: str, error: VectorProjectError) -> None:
    record_project_event(
        database,
        project_name=benchmark.BENCHMARK_PROJECT,
        correlation_id=correlation_id,
        operation=operation,
        component=error.stage,
        status="failed",
        error_type=error.code,
        error_message=error.message,
        final_outcome="failed",
    )


@router.get("/status")
def get_benchmark_status(
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    del actor
    db = _benchmark_database(request)
    state = db[benchmark.BENCHMARK_STATE].find_one({"_id": "active_dataset"})
    collections = set(db.list_collection_names())
    collection_exists = benchmark.BENCHMARK_COLLECTION in collections
    dataset_id = state.get("dataset_id") if state else None
    dataset_count = (
        db[benchmark.BENCHMARK_COLLECTION].count_documents({"dataset_id": dataset_id})
        if collection_exists and dataset_id else 0
    )
    index = (
        inspect_vector_index(
            db[benchmark.BENCHMARK_COLLECTION],
            benchmark.BENCHMARK_INDEX_NAME,
            benchmark.BENCHMARK_DIMENSIONS,
        )
        if collection_exists
        else {
            "supported": None,
            "ready": False,
            "name": benchmark.BENCHMARK_INDEX_NAME,
            "dimensions": None,
            "detail": "Index support is checked after the dataset is prepared.",
        }
    )
    now = datetime.now(timezone.utc)
    if index.get("ready") and state and not state.get("index_ready_at"):
        db[benchmark.BENCHMARK_STATE].update_one(
            {"_id": "active_dataset"},
            {"$set": {"index_ready_at": now}},
        )
        state["index_ready_at"] = now
    active_job = db[benchmark.BENCHMARK_JOBS].find_one({"active_key": "lab17-vector-benchmark"})
    latest_completed = db[benchmark.BENCHMARK_RUNS].find_one(
        {"job_type": "benchmark", "status": "completed"},
        {"_id": 0, "run_id": 1, "created_at": 1, "completed_at": 1, "result": 1},
        sort=[("completed_at", DESCENDING)],
    )
    latest_run = db[benchmark.BENCHMARK_RUNS].find_one(
        {"job_type": "benchmark"},
        {"_id": 0, "run_id": 1, "status": 1, "stage": 1, "progress_percent": 1, "error_code": 1, "error_message": 1, "created_at": 1},
        sort=[("created_at", DESCENDING)],
    )
    for row in (latest_completed, latest_run):
        if row:
            for field in ("created_at", "completed_at"):
                if isinstance(row.get(field), datetime):
                    row[field] = row[field].astimezone(timezone.utc).isoformat()

    return {
        "project": "Lab 17.2",
        "capabilities": benchmark.benchmark_capabilities(),
        "dataset": {
            "dataset_id": dataset_id,
            "record_count": dataset_count,
            "required_records": benchmark.DATASET_SIZE,
            "dimensions": state.get("dimensions") if state else None,
            "model": state.get("model") if state else benchmark.BENCHMARK_MODEL,
            "source_size_bytes": state.get("source_size_bytes") if state else None,
            "created_at": state.get("created_at").astimezone(timezone.utc).isoformat()
            if state and isinstance(state.get("created_at"), datetime) else None,
            "ready": dataset_count == benchmark.DATASET_SIZE
            and state.get("dimensions") == benchmark.BENCHMARK_DIMENSIONS if state else False,
        },
        "index": {
            **index,
            "build_time_ms": benchmark.get_index_build_time(state),
            "build_time_note": (
                "Observed from the first status poll that found the index queryable; actual readiness occurred between polls."
                if benchmark.get_index_build_time(state) is not None
                else "Not measured by this application yet."
            ),
        },
        "active_job": _safe_job(active_job),
        "latest_run": latest_run,
        "latest_completed_run": latest_completed,
        "configurations": benchmark.vector_search_configurations(),
        "limits": {
            "dataset_records": benchmark.DATASET_SIZE,
            "insert_batch_size": benchmark.DATASET_BATCH_SIZE,
            "query_count": benchmark.QUERY_SAMPLE_SIZE,
            "top_k": benchmark.TOP_K,
            "jsonl_upload_limit_bytes": benchmark.MAX_JSONL_BYTES,
        },
    }


@router.post("/dataset", status_code=202)
async def upload_benchmark_dataset(
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    del actor
    database = _database(request)
    correlation_id = str(uuid4())
    capabilities = benchmark.benchmark_capabilities()
    if not capabilities["ready_to_embed"]:
        error = VectorProjectError(
            "benchmark_model_not_installed",
            capabilities["install_note"],
            "dataset_embedding",
            409,
        )
        _record_failure(database, correlation_id, "Dataset upload rejected", error)
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > benchmark.MAX_JSONL_BYTES:
                error = VectorProjectError(
                    "dataset_too_large",
                    f"The JSONL dataset exceeds the {benchmark.MAX_JSONL_BYTES // (1024 * 1024)} MB upload limit.",
                    "dataset_validation",
                    413,
                )
                _record_failure(database, correlation_id, "Dataset upload rejected", error)
                raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from error
    raw = await request.body()
    try:
        rows = benchmark.parse_jsonl_dataset(raw)
        result = benchmark.start_dataset_job(database, rows, len(raw))
    except VectorProjectError as error:
        _record_failure(database, correlation_id, "Dataset upload rejected", error)
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    return result


@router.post("/index", status_code=202)
def create_benchmark_search_index(
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    del actor
    database = _database(request)
    db = _benchmark_database(request)
    collection = db[benchmark.BENCHMARK_COLLECTION]
    state = db[benchmark.BENCHMARK_STATE].find_one({"_id": "active_dataset"})
    if not state or state.get("record_count") != benchmark.DATASET_SIZE:
        raise HTTPException(status_code=409, detail="Prepare the complete 5,000-record dataset before creating the index.")
    if collection.count_documents({"dataset_id": state.get("dataset_id")}) != benchmark.DATASET_SIZE:
        raise HTTPException(status_code=409, detail="The active dataset is incomplete; the index cannot be created.")

    correlation_id = str(uuid4())
    prior = inspect_vector_index(collection, benchmark.BENCHMARK_INDEX_NAME, benchmark.BENCHMARK_DIMENSIONS)
    if not prior.get("supported"):
        error = VectorProjectError("vector_search_not_supported", prior["detail"], "index", 409)
        _record_failure(database, correlation_id, "Benchmark index creation failed", error)
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})

    requested_at = state.get("index_requested_at") or datetime.now(timezone.utc)
    if not prior.get("ready"):
        db[benchmark.BENCHMARK_STATE].update_one(
            {"_id": "active_dataset"},
            {"$set": {"index_requested_at": requested_at}},
        )
    try:
        index_name = benchmark.create_benchmark_index(collection)
    except VectorProjectError as error:
        _record_failure(database, correlation_id, "Benchmark index creation failed", error)
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    except PyMongoError as error:
        failure = VectorProjectError(
            "vector_index_create_failed",
            "MongoDB could not create the Vector Search index. Verify deployment support and database-user index permissions.",
            "index",
            503,
        )
        _record_failure(database, correlation_id, "Benchmark index creation failed", failure)
        raise HTTPException(status_code=failure.status_code, detail={"code": failure.code, "message": failure.message}) from error

    record_project_event(
        database,
        project_name=benchmark.BENCHMARK_PROJECT,
        correlation_id=correlation_id,
        operation="Benchmark Vector Search index creation requested",
        component="MongoDB Vector Search",
        status="success",
        final_outcome="index creation requested",
    )
    return {
        "index_name": index_name,
        "dimensions": benchmark.BENCHMARK_DIMENSIONS,
        "status": "requested",
        "message": "MongoDB accepted the index request. Refresh status to confirm when it becomes queryable.",
    }


@router.post("/runs", status_code=202)
def start_benchmark(
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    del actor
    database = _database(request)
    db = _benchmark_database(request)
    state = db[benchmark.BENCHMARK_STATE].find_one({"_id": "active_dataset"})
    if not state or state.get("record_count") != benchmark.DATASET_SIZE:
        raise HTTPException(status_code=409, detail="Prepare a complete 5,000-record dataset before running the benchmark.")
    if db[benchmark.BENCHMARK_COLLECTION].count_documents({"dataset_id": state.get("dataset_id")}) != benchmark.DATASET_SIZE:
        raise HTTPException(status_code=409, detail="The active dataset is incomplete; the benchmark cannot start.")
    if not benchmark.benchmark_capabilities()["numpy_installed"]:
        raise HTTPException(status_code=409, detail="NumPy is required. Install the optional Lab 17.2 requirements.")
    index_state = inspect_vector_index(
        db[benchmark.BENCHMARK_COLLECTION],
        benchmark.BENCHMARK_INDEX_NAME,
        benchmark.BENCHMARK_DIMENSIONS,
    )
    if not index_state.get("supported") or not index_state.get("ready"):
        raise HTTPException(status_code=409, detail=index_state.get("detail", "A ready Atlas Vector Search index is required."))
    index_build_time_ms = benchmark.get_index_build_time(state)
    try:
        return benchmark.start_benchmark_job(database, state["dataset_id"], index_build_time_ms)
    except VectorProjectError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error


@router.get("/runs/{run_id}")
def get_benchmark_run(
    run_id: str,
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    del actor
    row = _benchmark_database(request)[benchmark.BENCHMARK_RUNS].find_one(
        {"run_id": run_id, "job_type": "benchmark"},
        {"_id": 0, "active_key": 0},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Benchmark run not found.")
    for field in ("created_at", "updated_at", "completed_at"):
        if isinstance(row.get(field), datetime):
            row[field] = row[field].astimezone(timezone.utc).isoformat()
    return row
