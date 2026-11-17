"""Faculty URL aliases and report-chunk indexing for the Chapter 17 RAG pipeline."""
from datetime import datetime, timezone
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pymongo import UpdateOne
from typing_extensions import Annotated

from backend.database import get_database
from backend.routes.admin import _admin_user
from backend.routes.subscriptions import _session_user
from backend.services.vector_rag import (
    DOCUMENTS_COLLECTION, RAG_INDEX_NAME, VectorProjectError, create_embeddings,
    create_vector_index, inspect_vector_index, known_embedding_dimensions,
    provider_configuration, rag_database, rag_search_collections,
)
from backend.routes.vector_rag import (
    RagQuestion, forget_conversation_history, get_rag_status, list_conversations,
    get_conversation, query_rag,
)

router = APIRouter(tags=["Chapter 17 RAG aliases"])
REPORT_INDEX_COLLECTION = "report_chunks_index"


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    conversation_id: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    platform: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    threat_status: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None


def _source_chunk(row: dict) -> tuple[str, str, int | None] | None:
    text = row.get("text") or row.get("content") or row.get("chunk_text")
    if not isinstance(text, str) or not text.strip():
        return None
    source = row.get("source") or row.get("source_url") or row.get("source_reference") or row.get("report_id") or row.get("document_id") or "Unknown source"
    page = row.get("page") or row.get("page_number")
    try:
        page = int(page) if page is not None else None
    except (ValueError, TypeError):
        page = None
    return text.strip(), str(source), page


@router.post("/api/ask")
def ask(payload: AskRequest, request: Request, actor: dict = Depends(_session_user)):
    if not payload.question.strip():
        raise HTTPException(422, "Question must contain 3 to 500 characters.")
    legacy = RagQuestion(question=payload.question, conversation_id=payload.conversation_id, top_k=5,
                         platform=payload.platform, threat_status=payload.threat_status)
    result = query_rag(legacy, request, actor)
    try:
        active_model = provider_configuration().get("llm_model")
    except VectorProjectError:
        active_model = None
    return {**result, "model": active_model,
            "mode": "extractive" if result.get("response_mode") == "extractive_fallback" else result.get("response_mode")}


@router.get("/api/ask/history")
def history(request: Request, actor: dict = Depends(_session_user), limit: int = Query(20, ge=1, le=50)):
    return list_conversations(request, actor, limit)


@router.get("/api/ask/history/{conversation_id}")
def conversation_history(conversation_id: str, request: Request, actor: dict = Depends(_session_user)):
    return get_conversation(conversation_id, request, actor)


@router.delete("/api/ask/history")
def forget_history(request: Request, actor: dict = Depends(_session_user)):
    return forget_conversation_history(request, actor)


@router.post("/api/ops/ask-index")
def create_index(request: Request, actor: dict = Depends(_admin_user)):
    del actor
    database = request.app.state.database if hasattr(request.app.state, "database") else get_database()
    source_collection = None
    for candidate in rag_search_collections(database):
        if candidate.name == "report_chunks":
            source_collection = candidate
            break
    if source_collection is None:
        raise HTTPException(409, "No existing report_chunks collection is available to index.")
    config = provider_configuration()
    index = rag_database(database)[REPORT_INDEX_COLLECTION]
    source_rows = source_collection.find({}, {"_id": 1, "text": 1, "content": 1, "chunk_text": 1,
        "source": 1, "source_url": 1, "source_reference": 1, "report_id": 1, "document_id": 1,
        "page": 1, "page_number": 1}).limit(10000)
    indexed = 0
    vectors_by_dimension: set[int] = set()
    batch = []

    def flush(rows):
        nonlocal indexed
        if not rows:
            return
        try:
            vectors, dimensions, _ = create_embeddings([item["text"] for item in rows], "document")
        except VectorProjectError as error:
            raise HTTPException(error.status_code, error.message) from error
        vectors_by_dimension.add(dimensions)
        operations = []
        for row, vector in zip(rows, vectors):
            operations.append(UpdateOne({"chunk_id": row["chunk_id"]}, {"$set": {**row, "embedding": vector,
                "embedding_dimensions": dimensions, "embedding_model": config["embedding_model"],
                "status": "embedded", "indexed_at": datetime.now(timezone.utc)}}, upsert=True))
        index.bulk_write(operations, ordered=False)
        indexed += len(rows)

    for raw in source_rows:
        normalized = _source_chunk(raw)
        if not normalized:
            continue
        text, source, page = normalized
        digest = hashlib.sha256(f"{source}\0{page}\0{text}".encode("utf-8")).hexdigest()
        batch.append({"chunk_id": digest, "document_id": str(raw.get("report_id") or raw.get("document_id") or source),
            "source": source, "source_reference": raw.get("source_url") or raw.get("source_reference") or source,
            "page": page, "text": text, "created_at": datetime.now(timezone.utc)})
        if len(batch) >= 64:
            flush(batch); batch = []
    flush(batch)
    if indexed == 0:
        raise HTTPException(409, "The report_chunks collection contains no usable report text.")
    if len(vectors_by_dimension) != 1:
        raise HTTPException(409, "The embedding provider returned inconsistent dimensions.")
    dimensions = next(iter(vectors_by_dimension))
    try:
        index_name = create_vector_index(index, RAG_INDEX_NAME, dimensions)
        state = inspect_vector_index(index, RAG_INDEX_NAME, dimensions)
    except VectorProjectError as error:
        raise HTTPException(error.status_code, error.message) from error
    return {"source_collection": source_collection.name, "indexed_chunks": indexed, "dimensions": dimensions,
            "embedding_model": config["embedding_model"], "index_name": index_name,
            "index_status": state.get("status", "requested"), "ready": state.get("ready", False)}


@router.get("/api/ops/ask-index")
def index_status(request: Request, actor: dict = Depends(_admin_user)):
    del actor
    database = request.app.state.database if hasattr(request.app.state, "database") else get_database()
    status = get_rag_status(request, {"user_id": "admin"})
    collection = rag_database(database)[REPORT_INDEX_COLLECTION]
    dimensions = collection.distinct("embedding_dimensions", {"status": "embedded"})
    state = inspect_vector_index(collection, RAG_INDEX_NAME, dimensions[0] if len(dimensions) == 1 else None)
    status["report_index"] = {"collection": REPORT_INDEX_COLLECTION,
        "indexed_chunks": collection.count_documents({"status": "embedded"}),
        "dimensions": dimensions[0] if len(dimensions) == 1 else None,
        "multiple_dimensions": len(dimensions) > 1, "vector_search": state}
    return status
