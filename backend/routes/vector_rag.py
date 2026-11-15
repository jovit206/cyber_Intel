from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from uuid import uuid4

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
    create_voyage_embeddings,
    create_vector_index,
    ensure_rag_indexes,
    inspect_vector_index,
    known_embedding_dimensions,
    provider_configuration,
    rag_database,
    record_project_event,
    split_text_into_chunks,
    generate_llm_answer,
    validate_embeddings,
)

router = APIRouter(prefix="/api/rag", tags=["Lab 17.1 RAG Pipeline"])


class DocumentUpload(BaseModel):
    source: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=200_000)


class RagQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)
    top_k: int = Field(default=5, ge=1, le=10)


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
        model = provider_configuration()["embedding_model"]
        expected_dimensions = known_embedding_dimensions(
            model,
            os.getenv("VOYAGE_OUTPUT_DIMENSION", "").strip() or None,
        )
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
            vectors, dimensions, duration_ms = create_voyage_embeddings(batch_chunks, "document")
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

    try:
        vectors, dimensions, duration_ms = _stage(
            database,
            correlation_id,
            stages,
            name="Query embedding generated",
            component="Voyage AI embeddings",
            operation="Query embedding generated",
            action=lambda: create_voyage_embeddings([payload.question.strip()], "query"),
        )
        if dimensions != len(vectors[0]):
            raise VectorProjectError(
                "embedding_dimension_mismatch",
                "The query embedding does not match the provider response dimensions.",
                "embedding",
                502,
            )

        collection = rag[DOCUMENTS_COLLECTION]
        index_state = inspect_vector_index(collection, RAG_INDEX_NAME, dimensions)
        if not index_state.get("supported"):
            raise VectorProjectError(
                "vector_search_not_supported",
                index_state["detail"],
                "vector_search",
                503,
            )
        if not index_state.get("ready"):
            raise VectorProjectError(
                "vector_index_not_ready",
                index_state["detail"],
                "vector_search",
                409,
            )
        if index_state.get("dimensions") != dimensions:
            raise VectorProjectError(
                "vector_index_dimension_mismatch",
                "The Vector Search index dimensions do not match the query embedding.",
                "vector_search",
                409,
            )
        retrieved = _stage(
            database,
            correlation_id,
            stages,
            name="Vector search executed",
            component="MongoDB Vector Search",
            operation="Vector search executed",
            action=lambda: list(collection.aggregate([
                {"$vectorSearch": {
                    "index": RAG_INDEX_NAME,
                    "path": "embedding",
                    "queryVector": vectors[0],
                    "numCandidates": max(50, payload.top_k * 10),
                    "limit": payload.top_k,
                }},
                {"$project": {
                    "_id": 0,
                    "text": 1,
                    "source": 1,
                    "chunk_id": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }},
            ], maxTimeMS=5000)),
        )
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
            component="MongoDB Vector Search",
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
            ).sort("timestamp", DESCENDING).limit(5)),
        )
        memory.reverse()
        system_prompt, user_prompt = build_rag_prompt(payload.question.strip(), retrieved, memory)
        answer, generation_ms = _stage(
            database,
            correlation_id,
            stages,
            name="LLM response generated",
            component="OpenAI-compatible LLM",
            operation="LLM response generated",
            action=lambda: generate_llm_answer(system_prompt, user_prompt),
        )
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
    } for item in retrieved]
    return {
        "answer": answer,
        "conversation_id": conversation_id,
        "sources": sources,
        "stages": stages,
        "correlation_id": correlation_id,
        "metrics": {
            "query_embedding_ms": round(duration_ms, 2),
            "llm_generation_ms": round(generation_ms, 2),
            "retrieved_chunks": len(sources),
        },
    }
