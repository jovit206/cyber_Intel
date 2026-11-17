"""Session-authenticated, membership-scoped multi-tenant case operations."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pymongo import ReturnDocument
from typing_extensions import Annotated

from backend.database import get_database
from backend.routes.subscriptions import _session_user

def _enforce_case_rate_limit(request: Request) -> None:
    _rate_limit(_db(), "platform", _actor(request))


router = APIRouter(prefix="/api/cases", tags=["Multi-tenant cases"], dependencies=[Depends(_enforce_case_rate_limit)])
CaseTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
CaseDescription = Annotated[str, StringConstraints(max_length=8000)]
OrgRole = Literal["org_admin", "analyst", "viewer"]
CaseStatus = Literal["open", "in_progress", "closed"]
RATE_LIMIT = 30
RATE_WINDOW = timedelta(minutes=1)
_INDEXED_DATABASE_IDS: set[int] = set()


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    description: Annotated[str, StringConstraints(max_length=500)] = ""


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    role: OrgRole = "analyst"


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    title: CaseTitle
    description: CaseDescription = ""
    linked_threat_ids: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]] = Field(default_factory=list, max_length=50)


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CaseStatus | None = None
    assigned_to: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None
    title: CaseTitle | None = None
    description: CaseDescription | None = None


def _db():
    db = get_database()
    if id(db) not in _INDEXED_DATABASE_IDS:
        db.organizations.create_index("name_key", unique=True, name="uq_case_organization_name")
        db.organization_memberships.create_index([("organization_id", 1), ("user_id", 1)], unique=True, name="uq_organization_member")
        db.cases.create_index([("tenant_id", 1), ("status", 1), ("updated_at", -1)], name="ix_cases_tenant_status_updated")
        db.case_audit_log.create_index([("tenant_id", 1), ("created_at", -1)], name="ix_case_audit_tenant_created")
        db.case_rate_limits.create_index("expires_at", expireAfterSeconds=0, name="ttl_case_rate_limits")
        _INDEXED_DATABASE_IDS.add(id(db))
    return db


def _actor(request: Request) -> dict:
    return _session_user(request)


def _is_admin(actor: dict) -> bool:
    return actor.get("role") == "admin"


def _object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=404, detail="Record not found.")
    return ObjectId(value)


def _org_role(db, organization_id: str, actor: dict) -> str | None:
    if _is_admin(actor):
        return "platform_admin"
    record = db.organization_memberships.find_one({
        "organization_id": organization_id,
        "user_id": actor["user_id"],
    })
    return record.get("role") if record else None


def _require_org_role(db, organization_id: str, actor: dict, allowed: set[str]):
    role = _org_role(db, organization_id, actor)
    if role is None:
        raise HTTPException(status_code=403, detail="Ask an administrator to add you to an organisation.")
    if role not in allowed:
        raise HTTPException(status_code=403, detail="Your organisation role does not allow this action.")
    return role


def _rate_limit(db, organization_id: str, actor: dict) -> None:
    now = datetime.now(timezone.utc)
    key = hashlib.sha256(f"case-user:{actor['user_id']}".encode()).hexdigest()
    reset_before = now - RATE_WINDOW
    expired = {"$or": [
        {"$eq": [{"$type": "$window_start"}, "missing"]},
        {"$lte": ["$window_start", {"$literal": reset_before}]},
    ]}
    record = db.case_rate_limits.find_one_and_update(
        {"_id": key},
        [{"$set": {
            "organization_id": {"$literal": organization_id},
            "window_start": {"$cond": [expired, {"$literal": now}, "$window_start"]},
            "counter": {"$cond": [expired, 1, {"$add": [{"$ifNull": ["$counter", 0]}, 1]}]},
            "expires_at": {"$dateAdd": {"startDate": {"$cond": [expired, {"$literal": now}, "$window_start"]}, "unit": "second", "amount": 60}},
        }}],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if record and record["counter"] > RATE_LIMIT:
        db.case_rate_limits.update_one({"_id": key}, {"$inc": {"refused_total": 1}})
        db.case_rate_limit_daily.update_one(
            {"_id": f"{organization_id}:{now.date().isoformat()}"},
            {"$setOnInsert": {"organization_id": organization_id, "date": now.date().isoformat()}, "$inc": {"refused": 1}},
            upsert=True,
        )
        start = record["window_start"]
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        retry = max(1, int((start + RATE_WINDOW - now).total_seconds()))
        raise HTTPException(status_code=429, detail="Organisation case request limit reached. Retry after the supplied interval.", headers={"Retry-After": str(retry)})


def _case_summary(row: dict) -> dict:
    return {
        "case_id": str(row["_id"]),
        "organization_id": row.get("organization_id", row.get("tenant_id")),
        "tenant_id": row.get("tenant_id", row.get("organization_id")),
        "title": row["title"],
        "status": row["status"],
        "assigned_to": row.get("assigned_to"),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "linked_threat_count": len(row.get("linked_threat_ids", [])),
    }


def _tenant_case_query(actor: dict, database, case_id: ObjectId) -> dict:
    query: dict = {"_id": case_id}
    if _is_admin(actor):
        return query
    memberships = list(database.organization_memberships.find(
        {"user_id": actor["user_id"]}, {"organization_id": 1, "_id": 0},
    ))
    allowed = [row["organization_id"] for row in memberships]
    if not allowed:
        return {"_id": case_id, "tenant_id": "__no_membership__"}
    query["$or"] = [
        {"tenant_id": {"$in": allowed}},
        {"tenant_id": {"$exists": False}, "organization_id": {"$in": allowed}},
    ]
    return query


def _audit_case(database, actor: dict, action: str, tenant_id: str, case_id: str, details: dict | None = None) -> None:
    database.case_audit_log.insert_one({
        "action": action,
        "actor_user_id": actor["user_id"],
        "actor_role": actor.get("role", "user"),
        "tenant_id": tenant_id,
        "case_id": case_id,
        "created_at": datetime.now(timezone.utc),
        "details": details or {},
    })


def _tenant_filter(tenant_id: str) -> dict:
    """Support the additive tenant_id migration while keeping pre-migration rows isolated."""
    return {"$or": [
        {"tenant_id": tenant_id},
        {"tenant_id": {"$exists": False}, "organization_id": tenant_id},
    ]}


@router.get("/me")
def my_organizations(request: Request) -> dict:
    actor = _actor(request)
    db = _db()
    memberships = list(db.organization_memberships.find({"user_id": actor["user_id"]}, {"_id": 0}))
    organizations = []
    for membership in memberships:
        org = db.organizations.find_one({"_id": membership["organization_id"]}, {"_id": 0, "name": 1, "description": 1})
        if org:
            organizations.append({"organization_id": membership["organization_id"], "role": membership["role"], **org})
    return {"organizations": organizations}


@router.get("/overview")
def organization_overview(request: Request):
    actor = _actor(request)
    if not _is_admin(actor):
        raise HTTPException(status_code=403, detail="Administrator access is required for organisation analytics.")
    db = _db()
    organizations = list(db.organizations.find({}, {"_id": 1, "name": 1, "created_at": 1}).sort("name", 1))
    summary = []
    for org in organizations:
        org_id = str(org["_id"])
        counts = {state: db.cases.count_documents({"$and": [_tenant_filter(org_id), {"status": state}]}) for state in ("open", "in_progress", "closed")}
        summary.append({"organization_id": org_id, "name": org["name"], "created_at": org.get("created_at"),
                        "members": db.organization_memberships.count_documents({"organization_id": org_id}),
                        "cases": sum(counts.values()), "case_status_counts": counts})
    refused = list(db.case_rate_limit_daily.aggregate([{"$group": {"_id": None, "count": {"$sum": "$refused"}}}]))
    return {"organization_count": len(organizations), "member_count": db.organization_memberships.count_documents({}),
            "case_count": db.cases.count_documents({}), "rate_limit_refusals": refused[0]["count"] if refused else 0,
            "organizations": summary, "privacy_note": "Overview contains organization metadata and counts only; case descriptions and contents are excluded."}


@router.get("/organizations")
def list_organizations(request: Request):
    actor = _actor(request)
    db = _db()
    if not _is_admin(actor):
        memberships = list(db.organization_memberships.find({"user_id": actor["user_id"]}, {"organization_id": 1, "role": 1}))
        allowed = {row["organization_id"] for row in memberships}
        rows = db.organizations.find({"_id": {"$in": list(map(_object_id, allowed))}})
    else:
        rows = db.organizations.find({})
    return {"organizations": [{"organization_id": str(row["_id"]), "name": row["name"], "description": row.get("description", ""), "created_at": row.get("created_at")} for row in rows]}


@router.post("/organizations", status_code=201)
def create_organization(payload: OrganizationCreate, request: Request):
    actor = _actor(request)
    if not _is_admin(actor):
        raise HTTPException(status_code=403, detail="Administrator access is required to create organisations.")
    db = _db()
    if db.organizations.find_one({"name_key": payload.name.casefold()}):
        raise HTTPException(status_code=409, detail="An organisation with this name already exists.")
    organization_id = str(ObjectId())
    db.organizations.insert_one({"_id": ObjectId(organization_id), "name": payload.name, "name_key": payload.name.casefold(),
                                 "description": payload.description, "created_at": datetime.now(timezone.utc), "created_by": actor["user_id"]})
    db.organization_memberships.insert_one({"organization_id": organization_id, "user_id": actor["user_id"], "role": "org_admin", "created_at": datetime.now(timezone.utc)})
    return {"organization_id": organization_id, "name": payload.name, "role": "org_admin"}


@router.get("/org/{organization_id}/members")
def organization_members(organization_id: str, request: Request):
    actor = _actor(request)
    db = _db()
    _require_org_role(db, organization_id, actor, {"platform_admin", "org_admin", "analyst", "viewer"})
    members = list(db.organization_memberships.find({"organization_id": organization_id}, {"_id": 0, "user_id": 1, "role": 1, "created_at": 1}))
    user_ids = [row["user_id"] for row in members]
    users = {row["user_id"]: row for row in db.subscription_users.find({"user_id": {"$in": user_ids}}, {"user_id": 1, "name": 1, "email": 1})}
    return {"members": [{**row, "name": users.get(row["user_id"], {}).get("name"), "email": users.get(row["user_id"], {}).get("email")} for row in members]}


@router.get("/org/members")
def my_organization_members(request: Request, organization_id: str | None = None):
    actor = _actor(request)
    db = _db()
    if organization_id:
        _require_org_role(db, organization_id, actor, {"platform_admin", "org_admin", "analyst", "viewer"})
        organization_ids = [organization_id]
    else:
        organization_ids = [row["organization_id"] for row in db.organization_memberships.find(
            {"user_id": actor["user_id"]}, {"organization_id": 1, "_id": 0},
        )]
    rows = list(db.organization_memberships.find({"organization_id": {"$in": organization_ids}},
        {"_id": 0, "organization_id": 1, "user_id": 1, "role": 1, "created_at": 1}))
    user_ids = [row["user_id"] for row in rows]
    users = {row["user_id"]: row for row in db.subscription_users.find(
        {"user_id": {"$in": user_ids}}, {"user_id": 1, "name": 1, "email": 1},
    )}
    return {"members": [{**row, "name": users.get(row["user_id"], {}).get("name"),
                          "email": users.get(row["user_id"], {}).get("email")} for row in rows]}


@router.post("/org/{organization_id}/members", status_code=201)
def add_organization_member(organization_id: str, payload: MemberAdd, request: Request):
    actor = _actor(request)
    db = _db()
    _require_org_role(db, organization_id, actor, {"platform_admin", "org_admin"})
    if not db.organizations.find_one({"_id": _object_id(organization_id)}):
        raise HTTPException(status_code=404, detail="Organisation not found.")
    if not db.subscription_users.find_one({"user_id": payload.user_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Platform user not found.")
    db.organization_memberships.update_one({"organization_id": organization_id, "user_id": payload.user_id},
        {"$set": {"role": payload.role, "updated_at": datetime.now(timezone.utc)}, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}}, upsert=True)
    _audit_case(db, actor, "user.update", organization_id, payload.user_id, {"membership_role": payload.role})
    return {"organization_id": organization_id, "user_id": payload.user_id, "role": payload.role}


@router.get("")
def list_cases(request: Request, organization_id: str | None = None, status: CaseStatus | None = None, limit: int = Query(50, ge=1, le=100)):
    actor = _actor(request)
    db = _db()
    query: dict = {}
    if not _is_admin(actor):
        memberships = list(db.organization_memberships.find({"user_id": actor["user_id"]}, {"organization_id": 1}))
        allowed = [row["organization_id"] for row in memberships]
        if not allowed:
            return {"cases": [], "message": "Ask an administrator to add you to an organisation."}
        query["$or"] = [
            {"tenant_id": {"$in": allowed}},
            {"tenant_id": {"$exists": False}, "organization_id": {"$in": allowed}},
        ]
    if organization_id:
        _require_org_role(db, organization_id, actor, {"platform_admin", "org_admin", "analyst", "viewer"})
        tenant_match = _tenant_filter(organization_id)
        query = {"$and": [query, tenant_match]} if query else tenant_match
    if status:
        query["status"] = status
    rows = db.cases.find(query).sort("updated_at", -1).limit(limit)
    return {"cases": [_case_summary(row) for row in rows]}


@router.post("", status_code=201)
def create_case(payload: CaseCreate, request: Request):
    actor = _actor(request)
    db = _db()
    org_id = payload.organization_id
    _require_org_role(db, org_id, actor, {"platform_admin", "org_admin", "analyst"})
    if not db.organizations.find_one({"_id": _object_id(org_id)}):
        raise HTTPException(status_code=404, detail="Organisation not found.")
    now = datetime.now(timezone.utc)
    row = {"_id": ObjectId(), "tenant_id": org_id, "organization_id": org_id, "title": payload.title, "description": payload.description,
           "linked_threat_ids": payload.linked_threat_ids, "status": "open", "assigned_to": None,
           "created_by": actor["user_id"], "created_at": now, "updated_at": now}
    db.cases.insert_one(row)
    _audit_case(db, actor, "case.create", org_id, str(row["_id"]), {"title": payload.title})
    return _case_summary(row)


@router.get("/{case_id}")
def get_case(case_id: str, request: Request):
    actor = _actor(request)
    db = _db()
    row = db.cases.find_one(_tenant_case_query(actor, db, _object_id(case_id)))
    if not row:
        raise HTTPException(status_code=404, detail="Case not found.")
    _require_org_role(db, row["organization_id"], actor, {"platform_admin", "org_admin", "analyst", "viewer"})
    return {**_case_summary(row), "description": row.get("description", ""), "linked_threat_ids": row.get("linked_threat_ids", [])}


@router.patch("/{case_id}")
def update_case(case_id: str, payload: CaseUpdate, request: Request):
    actor = _actor(request)
    db = _db()
    scoped_query = _tenant_case_query(actor, db, _object_id(case_id))
    row = db.cases.find_one(scoped_query)
    if not row:
        raise HTTPException(status_code=404, detail="Case not found.")
    role = _require_org_role(db, row["organization_id"], actor, {"platform_admin", "org_admin", "analyst"})
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide at least one case field to update.")
    if "assigned_to" in changes:
        if role not in {"platform_admin", "org_admin"}:
            raise HTTPException(status_code=403, detail="Only organisation administrators can assign cases.")
        if not db.organization_memberships.find_one({"organization_id": row["organization_id"], "user_id": changes["assigned_to"]}):
            raise HTTPException(status_code=422, detail="Assignee must be an active member of this organisation.")
    changes["updated_at"] = datetime.now(timezone.utc)
    updated = db.cases.find_one_and_update(scoped_query, {"$set": changes}, return_document=ReturnDocument.AFTER)
    _audit_case(db, actor, "case.update", row.get("tenant_id", row["organization_id"]), case_id, {"fields": sorted(changes)})
    return _case_summary(updated)


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: str, request: Request):
    actor = _actor(request)
    db = _db()
    scoped_query = _tenant_case_query(actor, db, _object_id(case_id))
    row = db.cases.find_one(scoped_query)
    if not row:
        raise HTTPException(status_code=404, detail="Case not found.")
    _require_org_role(db, row["organization_id"], actor, {"platform_admin", "org_admin"})
    db.cases.delete_one(scoped_query)
    _audit_case(db, actor, "case.delete", row.get("tenant_id", row["organization_id"]), case_id)
    return Response(status_code=204)
