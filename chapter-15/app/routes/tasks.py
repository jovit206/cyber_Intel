from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo import DESCENDING
from pymongo.database import Database

from app.dependencies import enforce_rate_limit, get_current_user, get_database
from app.models import TaskCreate, TaskResponse, TaskStatus, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["Tasks"], dependencies=[Depends(enforce_rate_limit)])


def _task_response(task: dict) -> TaskResponse:
    return TaskResponse(
        id=str(task["_id"]),
        tenant_id=task["tenant_id"],
        user_id=str(task["user_id"]),
        title=task["title"],
        description=task["description"],
        priority=task["priority"],
        status=task["status"],
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


def _task_filter(task_id: str, user: dict) -> dict:
    try:
        object_id = ObjectId(task_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Task not found.") from None
    query = {"_id": object_id, "tenant_id": user["tenant_id"]}
    if user["role"] != "admin":
        query["user_id"] = user["_id"]
    return query


@router.post(
    "/",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    responses={201: {"description": "Task created"}, 401: {"description": "Authentication required"},
               429: {"description": "Rate limit exceeded"}},
)
def create_task(
    payload: TaskCreate,
    database: Database = Depends(get_database),
    user: dict = Depends(get_current_user),
) -> TaskResponse:
    now = datetime.now(timezone.utc)
    task = {
        "tenant_id": user["tenant_id"],
        "user_id": user["_id"],
        **payload.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    result = database.tasks.insert_one(task)
    task["_id"] = result.inserted_id
    return _task_response(task)


@router.get(
    "/",
    response_model=list[TaskResponse],
    summary="List tasks for the current tenant",
    responses={200: {"description": "Tasks visible to the caller"}, 429: {"description": "Rate limit exceeded"}},
)
def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
    database: Database = Depends(get_database),
    user: dict = Depends(get_current_user),
) -> list[TaskResponse]:
    query = {"tenant_id": user["tenant_id"]}
    if user["role"] != "admin":
        query["user_id"] = user["_id"]
    if status_filter:
        query["status"] = status_filter
    cursor = database.tasks.find(query).sort("created_at", DESCENDING).skip(skip).limit(limit)
    return [_task_response(task) for task in cursor]


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Get a task",
    responses={404: {"description": "Task not found in the caller's authorized scope"}},
)
def get_task(
    task_id: str,
    database: Database = Depends(get_database),
    user: dict = Depends(get_current_user),
) -> TaskResponse:
    task = database.tasks.find_one(_task_filter(task_id, user))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return _task_response(task)


@router.put(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Update a task",
    responses={404: {"description": "Task not found in the caller's authorized scope"}},
)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    database: Database = Depends(get_database),
    user: dict = Depends(get_current_user),
) -> TaskResponse:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one task field must be provided.")
    changes["updated_at"] = datetime.now(timezone.utc)
    task = database.tasks.find_one_and_update(
        _task_filter(task_id, user),
        {"$set": changes},
        return_document=True,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return _task_response(task)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
    responses={204: {"description": "Task deleted"}, 404: {"description": "Task not found in authorized scope"}},
)
def delete_task(
    task_id: str,
    database: Database = Depends(get_database),
    user: dict = Depends(get_current_user),
) -> Response:
    result = database.tasks.delete_one(_task_filter(task_id, user))
    if result.deleted_count != 1:
        raise HTTPException(status_code=404, detail="Task not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
