from __future__ import annotations

import os
import json
import re
import time
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from backend.routes.admin import _admin_user
from backend.services.vector_rag import (
    CONVERSATION_META_COLLECTION,
    CONVERSATIONS_COLLECTION,
    DOCUMENTS_COLLECTION,
    MAX_VECTOR_DIMENSIONS,
    RAG_INDEX_NAME,
    RAG_PROJECT,
    VectorProjectError,
    build_rag_prompt,
    create_embeddings,
    create_vector_index,
    ensure_rag_indexes,
    inspect_vector_index,
    known_embedding_dimensions,
    provider_configuration,
    rag_database,
    rag_search_collections,
    record_project_event,
    split_text_into_chunks,
    generate_llm_answer,
    validate_embeddings,
)
from backend.services.threat_retrieval import retrieve_structured_threat_evidence

router = APIRouter(prefix="/api/rag", tags=["Lab 17.1 RAG Pipeline"])


class DocumentUpload(BaseModel):
    source: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=200_000)


class RagQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)
    top_k: int = Field(default=5, ge=1, le=10)
    platform: str | None = Field(default=None, min_length=1, max_length=100)
    threat_status: str | None = Field(default=None, min_length=1, max_length=64)


def _actor_id(actor: dict) -> str:
    user_id = actor.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="The administrator session has no valid user identity.")
    return user_id


def _stage(
    database,
    correlation_id: str,
    stages: list[dict],
    *,
    name: str,
    component: str,
    operation: str,
    action,
):
    started = time.perf_counter()
    try:
        result = action()
    except PyMongoError as mongo_error:
        duration_ms = (time.perf_counter() - started) * 1000
        error = VectorProjectError(
            "mongodb_operation_failed",
            f"{component} could not complete its MongoDB operation.",
            name.lower().replace(" ", "_"),
            503,
        )
        stages.append({
            "name": name,
            "status": "failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 2),
            "error_code": error.code,
            "message": error.message,
        })
        record_project_event(
            database,
            project_name=RAG_PROJECT,
            correlation_id=correlation_id,
            operation=operation,
            component=component,
            status="failed",
            duration_ms=duration_ms,
            error_type=error.code,
            error_message=error.message,
            final_outcome="failed",
        )
        raise error from mongo_error
    except VectorProjectError as error:
        duration_ms = (time.perf_counter() - started) * 1000
        stages.append({
            "name": name,
            "status": "failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 2),
            "error_code": error.code,
            "message": error.message,
        })
        record_project_event(
            database,
            project_name=RAG_PROJECT,
            correlation_id=correlation_id,
            operation=operation,
            component=component,
            status="failed",
            duration_ms=duration_ms,
            error_type=error.code,
            error_message=error.message,
            final_outcome="failed",
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    stages.append({
        "name": name,
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round(duration_ms, 2),
    })
    record_project_event(
        database,
        project_name=RAG_PROJECT,
        correlation_id=correlation_id,
        operation=operation,
        component=component,
        status="success",
        duration_ms=duration_ms,
        final_outcome="succeeded",
    )
    return result


def _error_response(error: VectorProjectError, correlation_id: str, conversation_id: str | None, stages: list[dict]):
    raise HTTPException(
        status_code=error.status_code,
        detail={
            "code": error.code,
            "message": error.message,
            "stage": error.stage,
            "correlation_id": correlation_id,
            "conversation_id": conversation_id,
            "stages": stages,
        },
    ) from error


@router.get("/status")
def get_rag_status(request: Request, actor: dict = Depends(_admin_user)) -> dict:
    del actor
    database = request.app.state.database
    rag = rag_database(database)
    collection_names = set(rag.list_collection_names())
    docs = rag[DOCUMENTS_COLLECTION]
    documents_exist = DOCUMENTS_COLLECTION in collection_names
    config_error = None
    try:
        configuration = provider_configuration()
    except VectorProjectError as error:
        configuration = {
            "embedding_provider": "Voyage AI",
            "embedding_configured": False,
            "embedding_model": None,
            "embedding_dimensions": None,
            "llm_provider": "OpenAI-compatible API",
            "llm_configured": False,
            "llm_model": None,
            "llm_endpoint_secure": False,
        }
        config_error = {"code": error.code, "message": error.message}

    index = (
        inspect_vector_index(docs, RAG_INDEX_NAME)
        if documents_exist
        else {
            "supported": None,
            "ready": False,
            "name": RAG_INDEX_NAME,
            "dimensions": None,
            "detail": "Index support is checked after the first document is stored.",
        }
    )
    dimensions = []
    document_count = chunk_count = failed_chunk_count = pending_chunk_count = 0
    average_embedding_ms = None
    if documents_exist:
        dimensions = sorted(docs.distinct("embedding_dimensions", {"status": "embedded"}))
        document_count = len(docs.distinct("document_id"))
        chunk_count = docs.count_documents({"status": "embedded"})
        failed_chunk_count = docs.count_documents({"status": "failed"})
        pending_chunk_count = docs.count_documents({"status": "pending"})
        timing = list(docs.aggregate([
            {"$match": {"status": "embedded", "embedding_duration_ms": {"$type": "number"}}},
            {"$group": {"_id": None, "average": {"$avg": "$embedding_duration_ms"}}},
        ], maxTimeMS=5000))
        if timing:
            average_embedding_ms = round(timing[0]["average"], 2)

    active_dimensions = dimensions[0] if len(dimensions) == 1 else None
    if len(dimensions) > 1:
        index["detail"] = "Stored documents use multiple embedding dimensions; a single Vector Search index cannot serve them."
        index["ready"] = False
    elif active_dimensions and index.get("dimensions") and index["dimensions"] != active_dimensions:
        index["ready"] = False
        index["detail"] = (
            f"The index uses {index['dimensions']} dimensions but stored embeddings use {active_dimensions}."
        )
    return {
        "project": "Lab 17.1",
        "configuration": configuration,
        "configuration_error": config_error,
        "vector_search": index,
        "documents": {
            "document_count": document_count,
            "embedded_chunks": chunk_count,
            "failed_chunks": failed_chunk_count,
            "pending_chunks": pending_chunk_count,
            "dimensions": active_dimensions,
            "multiple_dimensions": len(dimensions) > 1,
            "average_embedding_ms": average_embedding_ms,
        },
        "source_collections": [
            {"collection": collection.name, "database": collection.database.name,
             "record_count": collection.count_documents({}),
             "embedded_count": collection.count_documents({"embedding": {"$type": "array"}})}
            for collection in rag_search_collections(database) if collection.name == "report_chunks"
        ],
        "limitations": [
            "The application does not store provider keys in MongoDB or return them to the browser.",
            "Vector Search uses MongoDB's managed ANN configuration; custom HNSW parameters and IVF are not represented as supported configurations.",
            "No answer or performance result is reported until the configured providers and database operations actually complete.",
        ],
    }


@router.get("/documents")
def list_rag_documents(
    request: Request,
    actor: dict = Depends(_admin_user),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    del actor
    rag = rag_database(request.app.state.database)
    rows = list(rag[DOCUMENTS_COLLECTION].aggregate([
        {"$sort": {"created_at": DESCENDING}},
        {"$group": {
            "_id": "$document_id",
            "source": {"$first": "$source"},
            "statuses": {"$addToSet": "$status"},
            "chunk_count": {"$sum": 1},
            "created_at": {"$first": "$created_at"},
        }},
        {"$sort": {"created_at": DESCENDING}},
        {"$limit": limit},
        {"$project": {"_id": 0, "document_id": "$_id", "source": 1, "statuses": 1, "chunk_count": 1, "created_at": 1}},
    ], maxTimeMS=5000))
    for row in rows:
        statuses = set(row.pop("statuses", []))
        row["status"] = "partial" if len(statuses) > 1 else next(iter(statuses), "unknown")
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].astimezone(timezone.utc).isoformat()
    return {"documents": rows, "limit": limit}


@router.post("/documents", status_code=201)
def upload_rag_document(
    payload: DocumentUpload,
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    database = request.app.state.database
    user_id = _actor_id(actor)
    if not payload.source.strip():
        raise HTTPException(status_code=422, detail="A non-empty document source is required.")
    correlation_id = str(uuid4())
    rag = rag_database(database)
    try:
        expected_dimensions = provider_configuration()["embedding_dimensions"]
    except VectorProjectError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    ensure_rag_indexes(database)
    document_id = str(uuid4())
    created_at = datetime.now(timezone.utc)
    chunks = split_text_into_chunks(payload.text)
    if not chunks:
        raise HTTPException(status_code=422, detail="The document contains no text to embed.")

    stages: list[dict] = []
    _stage(
        database,
        correlation_id,
        stages,
        name="Document chunked",
        component="RAG document ingestion",
        operation="Document chunking completed",
        action=lambda: chunks,
    )
    chunk_documents = [
        {
            "chunk_id": f"{document_id}:{index}",
            "document_id": document_id,
            "source": payload.source.strip(),
            "text": chunk,
            "chunk_index": index,
            "user_id": user_id,
            "status": "pending",
            "embedding_model": None,
            "embedding_dimensions": None,
            "embedding": None,
            "created_at": created_at,
        }
        for index, chunk in enumerate(chunks)
    ]
    try:
        rag[DOCUMENTS_COLLECTION].insert_many(chunk_documents, ordered=True)
    except PyMongoError as error:
        record_project_event(
            database,
            project_name=RAG_PROJECT,
            correlation_id=correlation_id,
            operation="Document chunks storage failed",
            component="MongoDB document ingestion",
            status="failed",
            error_type=type(error).__name__,
            error_message="MongoDB could not store the submitted document chunks.",
            final_outcome="failed",
        )
        raise HTTPException(status_code=503, detail="The document could not be stored in MongoDB.") from error
    stages.append({"name": "Chunks stored", "status": "completed", "chunk_count": len(chunks)})
    record_project_event(
        database,
        project_name=RAG_PROJECT,
        correlation_id=correlation_id,
        operation="Document chunks stored",
        component="MongoDB document ingestion",
        status="success",
        final_outcome="succeeded",
    )

    embedded_count = 0
    failed_chunks = 0
    actual_dimensions = None
    batch_size = 64
    for start in range(0, len(chunks), batch_size):
        duration_ms = None
        batch_chunks = chunks[start:start + batch_size]
        batch_docs = chunk_documents[start:start + batch_size]
        try:
            vectors, dimensions, duration_ms = create_embeddings(batch_chunks, "document")
            if actual_dimensions is not None and dimensions != actual_dimensions:
                raise VectorProjectError(
                    "embedding_dimension_changed",
                    "The embedding provider changed vector dimensions during this document upload.",
                    "embedding",
                    502,
                )
            actual_dimensions = dimensions
        except VectorProjectError as error:
            failed_chunks += len(batch_chunks)
            rag[DOCUMENTS_COLLECTION].update_many(
                {"chunk_id": {"$in": [item["chunk_id"] for item in batch_docs]}},
                {"$set": {"status": "failed", "error_code": error.code, "error_message": error.message}},
            )
            record_project_event(
                database,
                project_name=RAG_PROJECT,
                correlation_id=correlation_id,
                operation="Document embedding failed",
                component="Voyage AI embeddings",
                status="failed",
                duration_ms=duration_ms,
                error_type=error.code,
                error_message=error.message,
                final_outcome="failed",
            )
            remaining = chunk_documents[start + batch_size:]
            if remaining:
                rag[DOCUMENTS_COLLECTION].update_many(
                    {"chunk_id": {"$in": [item["chunk_id"] for item in remaining]}},
                    {"$set": {"status": "failed", "error_code": error.code, "error_message": error.message}},
                )
                failed_chunks += len(remaining)
            _error_response(error, correlation_id, None, stages)
        validate_embeddings(vectors, len(batch_chunks), expected_dimensions)
        update_operations = []
        for item, vector in zip(batch_docs, vectors):
            update_operations.append((item["chunk_id"], vector))
        for chunk_id, vector in update_operations:
            rag[DOCUMENTS_COLLECTION].update_one(
                {"chunk_id": chunk_id},
                {"$set": {
                    "embedding": vector,
                    "embedding_model": model,
                    "embedding_dimensions": dimensions,
                    "embedding_duration_ms": duration_ms / max(len(batch_chunks), 1),
                    "status": "embedded",
                    "embedded_at": datetime.now(timezone.utc),
                    "error_code": None,
                    "error_message": None,
                }},
            )
        embedded_count += len(batch_chunks)
        stages.append({
            "name": "Embedding batch generated",
            "status": "completed",
            "chunks": len(batch_chunks),
            "dimensions": dimensions,
            "duration_ms": round(duration_ms, 2),
        })
        record_project_event(
            database,
            project_name=RAG_PROJECT,
            correlation_id=correlation_id,
            operation="Document embeddings generated",
            component="Voyage AI embeddings",
            status="success",
            duration_ms=duration_ms,
            final_outcome="succeeded",
        )

    return {
        "document_id": document_id,
        "status": "embedded" if failed_chunks == 0 else "partial",
        "chunk_count": len(chunks),
        "embedded_chunks": embedded_count,
        "failed_chunks": failed_chunks,
        "dimensions": actual_dimensions,
        "correlation_id": correlation_id,
        "stages": stages,
    }


@router.post("/index")
def create_rag_search_index(
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    del actor
    database = request.app.state.database
    rag = rag_database(database)
    collection = rag[DOCUMENTS_COLLECTION]
    dimensions = rag[DOCUMENTS_COLLECTION].distinct("embedding_dimensions", {"status": "embedded"})
    if len(dimensions) != 1:
        raise HTTPException(status_code=409, detail="A single embedding dimension is required before creating the index.")
    if dimensions[0] is None or not 1 <= dimensions[0] <= MAX_VECTOR_DIMENSIONS:
        raise HTTPException(status_code=409, detail="No valid embedded document vectors are available for indexing.")
    correlation_id = str(uuid4())
    try:
        index_name = create_vector_index(collection, RAG_INDEX_NAME, dimensions[0])
    except VectorProjectError as error:
        record_project_event(
            database,
            project_name=RAG_PROJECT,
            correlation_id=correlation_id,
            operation="RAG Vector Search index creation failed",
            component="MongoDB Vector Search",
            status="failed",
            error_type=error.code,
            error_message=error.message,
            final_outcome="failed",
        )
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    record_project_event(
        database,
        project_name=RAG_PROJECT,
        correlation_id=correlation_id,
        operation="RAG Vector Search index creation requested",
        component="MongoDB Vector Search",
        status="success",
        final_outcome="index creation requested",
    )
    return {
        "index_name": index_name,
        "dimensions": dimensions[0],
        "status": "requested",
        "message": "MongoDB accepted the index request. Refresh status to confirm when it becomes queryable.",
    }


@router.post("/conversations", status_code=201)
def create_conversation(
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    database = request.app.state.database
    user_id = _actor_id(actor)
    ensure_rag_indexes(database)
    conversation_id = str(uuid4())
    now = datetime.now(timezone.utc)
    rag_database(database)[CONVERSATION_META_COLLECTION].insert_one({
        "conversation_id": conversation_id,
        "user_id": user_id,
        "title": "New conversation",
        "created_at": now,
        "updated_at": now,
    })
    return {"conversation_id": conversation_id, "title": "New conversation", "created_at": now.isoformat()}


@router.get("/conversations")
def list_conversations(
    request: Request,
    actor: dict = Depends(_admin_user),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    user_id = _actor_id(actor)
    rows = list(rag_database(request.app.state.database)[CONVERSATION_META_COLLECTION].find(
        {"user_id": user_id},
        {"_id": 0, "conversation_id": 1, "title": 1, "created_at": 1, "updated_at": 1},
    ).sort("updated_at", DESCENDING).limit(limit))
    for row in rows:
        for field in ("created_at", "updated_at"):
            if isinstance(row.get(field), datetime):
                row[field] = row[field].astimezone(timezone.utc).isoformat()
    return {"conversations": rows}


def forget_conversation_history(request: Request, actor: dict = Depends(_admin_user)) -> dict:
    """Delete only the signed-in user's conversation messages and metadata."""
    user_id = _actor_id(actor)
    rag = rag_database(request.app.state.database)
    messages = rag[CONVERSATIONS_COLLECTION].delete_many({"user_id": user_id})
    conversations = rag[CONVERSATION_META_COLLECTION].delete_many({"user_id": user_id})
    return {"deleted_messages": messages.deleted_count, "deleted_conversations": conversations.deleted_count}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    request: Request,
    actor: dict = Depends(_admin_user),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    user_id = _actor_id(actor)
    rag = rag_database(request.app.state.database)
    metadata = rag[CONVERSATION_META_COLLECTION].find_one(
        {"conversation_id": conversation_id, "user_id": user_id},
        {"_id": 0, "conversation_id": 1, "title": 1, "created_at": 1, "updated_at": 1},
    )
    if not metadata:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = list(rag[CONVERSATIONS_COLLECTION].find(
        {"conversation_id": conversation_id, "user_id": user_id},
        {"_id": 0, "conversation_id": 0, "user_id": 0, "correlation_id": 0},
    ).sort("timestamp", 1).limit(limit))
    for message in messages:
        if isinstance(message.get("timestamp"), datetime):
            message["timestamp"] = message["timestamp"].astimezone(timezone.utc).isoformat()
    for field in ("created_at", "updated_at"):
        if isinstance(metadata.get(field), datetime):
            metadata[field] = metadata[field].astimezone(timezone.utc).isoformat()
    return {"conversation": metadata, "messages": messages}


@router.post("/query")
def query_rag(
    payload: RagQuestion,
    request: Request,
    actor: dict = Depends(_admin_user),
) -> dict:
    database = request.app.state.database
    user_id = _actor_id(actor)
    if not payload.question.strip():
        raise HTTPException(status_code=422, detail="A non-empty question is required.")
    rag = rag_database(database)
    ensure_rag_indexes(database)
    correlation_id = str(uuid4())
    conversation_id = payload.conversation_id or str(uuid4())
    metadata = rag[CONVERSATION_META_COLLECTION].find_one({
        "conversation_id": conversation_id,
        "user_id": user_id,
    })
    if payload.conversation_id and not metadata:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    now = datetime.now(timezone.utc)
    if not metadata:
        rag[CONVERSATION_META_COLLECTION].insert_one({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "title": payload.question.strip()[:80],
            "created_at": now,
            "updated_at": now,
        })
    user_message_id = str(uuid4())
    rag[CONVERSATIONS_COLLECTION].insert_one({
        "message_id": user_message_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "role": "user",
        "content": payload.question.strip(),
        "timestamp": now,
        "correlation_id": correlation_id,
    })
    stages: list[dict] = [{
        "name": "User query received",
        "status": "completed",
        "timestamp": now.isoformat(),
    }]
    record_project_event(
        database,
        project_name=RAG_PROJECT,
        correlation_id=correlation_id,
        operation="User query received",
        component="RAG Playground",
        status="success",
        final_outcome="query stored",
    )

    structured_result: dict[str, Any] = {"items": [], "sources": [], "unavailable_sources": []}
    try:
        collections = rag_search_collections(database)
        filtered_query = bool(payload.platform or payload.threat_status)
        retrieval_mode = "structured_filtered" if filtered_query else "vector_search+structured"
        vector_retrieved: list[dict] = []
        duration_ms: float | None = None
        try:
            if filtered_query:
                raise VectorProjectError(
                    "vector_filter_not_supported",
                    "The existing report vector index does not contain validated platform or threat-status filter fields.",
                    "vector_search",
                    409,
                )
            vectors, dimensions, duration_ms = _stage(
                database, correlation_id, stages, name="Query embedding generated",
                component="Configured embedding provider", operation="Query embedding generated",
                action=lambda: create_embeddings([payload.question.strip()], "query"),
            )
            vector_collection = None
            unavailable_detail = "No matching Vector Search index was found for the configured embedding dimensions."
            for candidate in collections:
                index_state = inspect_vector_index(candidate, RAG_INDEX_NAME, dimensions)
                if index_state.get("supported") and index_state.get("ready") and index_state.get("dimensions") == dimensions:
                    vector_collection = candidate
                    break
                unavailable_detail = index_state.get("detail", unavailable_detail)
            if vector_collection is None:
                raise VectorProjectError("vector_search_unavailable", unavailable_detail, "vector_search", 409)
            vector_retrieved = _stage(
                database, correlation_id, stages, name="Vector search executed",
                component="MongoDB Vector Search", operation="Vector search executed",
                action=lambda: list(vector_collection.aggregate([
                    {"$vectorSearch": {"index": RAG_INDEX_NAME, "path": "embedding", "queryVector": vectors[0],
                     "numCandidates": max(50, payload.top_k * 10), "limit": payload.top_k}},
                    {"$project": {"_id": 0, "text": 1, "content": 1, "chunk_text": 1,
                                  "source": 1, "source_url": 1, "source_reference": 1, "document_id": 1, "chunk_id": 1,
                                  "page": 1, "page_number": 1,
                                  "score": {"$meta": "vectorSearchScore"}}},
                ], maxTimeMS=5000)),
            )
        except VectorProjectError as vector_error:
            if not filtered_query:
                retrieval_mode = "structured_fallback"
            stages.append({"name": "Report vector retrieval unavailable", "status": "completed", "reason": vector_error.code})
            report_retrieved = []
            if not filtered_query:
                terms = list(dict.fromkeys(re.findall(r"[\w.-]{2,}", payload.question.strip(), flags=re.UNICODE)))[:12]
                query = {"status": "embedded", "$or": [{"text": {"$regex": re.escape(term), "$options": "i"}} for term in terms]} if terms else {"status": "embedded", "_id": {"$exists": False}}
                if terms:
                    query["$or"] = [{field: {"$regex": re.escape(term), "$options": "i"}} for term in terms for field in ("text", "content", "chunk_text")]
                for candidate in collections:
                    candidate_query = query if candidate.name != "report_chunks" else {"$or": query["$or"]} if terms else {"_id": {"$exists": False}}
                    rows = list(candidate.find(candidate_query, {"_id": 0, "text": 1, "content": 1, "chunk_text": 1,
                        "source": 1, "source_url": 1, "source_reference": 1, "chunk_id": 1, "document_id": 1, "page": 1, "page_number": 1}).limit(payload.top_k - len(report_retrieved)))
                    for item in rows:
                        item["text"] = item.get("text") or item.get("content") or item.get("chunk_text") or ""
                        item["source"] = item.get("source") or item.get("source_url") or item.get("source_reference") or item.get("document_id") or candidate.name
                        item["chunk_id"] = item.get("chunk_id") or item.get("document_id")
                        item["score"] = None
                        report_retrieved.append(item)
                    if len(report_retrieved) >= payload.top_k:
                        break
            vector_retrieved = report_retrieved
            if not filtered_query:
                stages.append({"name": "Structured report retrieval fallback", "status": "completed", "source_count": len(report_retrieved)})
        structured_result = _stage(
            database, correlation_id, stages,
            name="Threat intelligence records searched",
            component="Existing structured CTI collections",
            operation="Structured threat intelligence retrieval completed",
            action=lambda: retrieve_structured_threat_evidence(
                database,
                payload.question.strip(),
                limit=payload.top_k,
                platform=payload.platform,
                threat_status=payload.threat_status,
            ),
        )
        # Interleave semantic report chunks and structured CTI records so neither
        # evidence class can starve the other when the top-k limit is small.
        retrieved = []
        vector_items = iter(vector_retrieved)
        structured_items = iter(structured_result["items"])
        while len(retrieved) < payload.top_k:
            added = False
            for iterator in (vector_items, structured_items):
                try:
                    item = next(iterator)
                except StopIteration:
                    continue
                if not any(existing.get("chunk_id") == item.get("chunk_id") for existing in retrieved):
                    retrieved.append(item)
                    added = True
                    if len(retrieved) >= payload.top_k:
                        break
            if not added:
                break
        for item in retrieved:
            item["text"] = item.get("text") or item.get("content") or item.get("chunk_text") or ""
            item["source"] = item.get("source") or item.get("source_url") or item.get("source_reference") or item.get("document_id") or "Unknown source"
            item["chunk_id"] = item.get("chunk_id") or item.get("document_id")
        stages.append({
            "name": "Relevant chunks retrieved",
            "status": "completed",
            "source_count": len(retrieved),
        })
        record_project_event(
            database,
            project_name=RAG_PROJECT,
            correlation_id=correlation_id,
            operation="Relevant chunks retrieved",
            component="MongoDB retrieval",
            status="success",
            final_outcome=f"{len(retrieved)} chunks retrieved",
        )

        memory = _stage(
            database,
            correlation_id,
            stages,
            name="Conversation memory retrieved",
            component="MongoDB conversation memory",
            operation="Conversation memory retrieved",
            action=lambda: list(rag[CONVERSATIONS_COLLECTION].find(
                {"conversation_id": conversation_id, "user_id": user_id, "message_id": {"$ne": user_message_id}},
                {"_id": 0, "role": 1, "content": 1},
            ).sort("timestamp", DESCENDING).limit(10)),
        )
        memory.reverse()
        question_with_filters = payload.question.strip()
        active_filters = {key: value for key, value in {
            "platform": payload.platform.strip() if payload.platform else None,
            "threat_status": payload.threat_status.strip() if payload.threat_status else None,
        }.items() if value}
        if active_filters:
            question_with_filters += "\n\nApply these exact retrieval filters: " + json.dumps(active_filters, ensure_ascii=False)
        system_prompt, user_prompt = build_rag_prompt(question_with_filters, retrieved, memory)
        if not retrieved:
            answer, generation_ms, response_mode = "The available indexed documents do not contain evidence for this question.", 0.0, "insufficient_evidence"
        else:
            try:
                answer, generation_ms = _stage(
                    database, correlation_id, stages, name="LLM response generated",
                    component="Configured LLM provider", operation="LLM response generated",
                    action=lambda: generate_llm_answer(system_prompt, user_prompt),
                )
                response_mode = "grounded_llm"
            except VectorProjectError as generation_error:
                excerpts = [f"[S{index}] {item.get('text', '')[:1200]}" for index, item in enumerate(retrieved, start=1)]
                answer = "Extractive fallback from retrieved documents (the configured LLM was unavailable):\n\n" + "\n\n".join(excerpts)
                generation_ms, response_mode = 0.0, "extractive_fallback"
                stages.append({"name": "Extractive answer fallback", "status": "completed", "reason": generation_error.code})
        def save_answer() -> None:
            answer_id = str(uuid4())
            rag[CONVERSATIONS_COLLECTION].insert_one({
                "message_id": answer_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "assistant",
                "content": answer,
                "timestamp": datetime.now(timezone.utc),
                "correlation_id": correlation_id,
                "source_chunk_ids": [item.get("chunk_id") for item in retrieved],
            })
            rag[CONVERSATION_META_COLLECTION].update_one(
                {"conversation_id": conversation_id, "user_id": user_id},
                {"$set": {"updated_at": datetime.now(timezone.utc)}},
            )

        _stage(
            database,
            correlation_id,
            stages,
            name="Conversation saved",
            component="MongoDB conversation memory",
            operation="Conversation saved",
            action=save_answer,
        )
        stages.append({"name": "Final answer generated", "status": "completed"})
        record_project_event(
            database,
            project_name=RAG_PROJECT,
            correlation_id=correlation_id,
            operation="Final answer generated",
            component="RAG Playground",
            status="success",
            final_outcome="answer returned",
        )
    except VectorProjectError as error:
        if not stages or stages[-1].get("status") != "failed":
            record_project_event(
                database,
                project_name=RAG_PROJECT,
                correlation_id=correlation_id,
                operation=f"{error.stage.replace('_', ' ').title()} failed",
                component="RAG Playground",
                status="failed",
                error_type=error.code,
                error_message=error.message,
                final_outcome="failed",
            )
            stages.append({
                "name": error.stage.replace("_", " ").title(),
                "status": "failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_code": error.code,
                "message": error.message,
            })
        _error_response(error, correlation_id, conversation_id, stages)

    sources = [{
        "source": item.get("source"),
        "chunk_id": item.get("chunk_id"),
        "score": item.get("score"),
        "text": item.get("text"),
        "page": item.get("page") or item.get("page_number"),
        "source_reference": item.get("source_reference") or item.get("source_url"),
    } for item in retrieved]
    return {
        "answer": answer,
        "response_mode": response_mode,
        "retrieval_mode": retrieval_mode,
        "filters": active_filters,
        "structured_sources": structured_result["sources"],
        "unavailable_sources": structured_result["unavailable_sources"],
        "conversation_id": conversation_id,
        "sources": sources,
        "stages": stages,
        "correlation_id": correlation_id,
        "metrics": {
            "query_embedding_ms": round(duration_ms, 2) if duration_ms is not None else None,
            "llm_generation_ms": round(generation_ms, 2),
            "retrieved_chunks": len(sources),
        },
    }
