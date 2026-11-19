"""Security Center diagnostics and least-privilege access management."""
from __future__ import annotations

import logging
import os
import re
import ssl
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, field_validator
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

from backend.config import settings
from backend.database import get_database, mongo_tls_options
from backend.routes.subscriptions import _session_user

logger = logging.getLogger("aegis.security")
router = APIRouter(prefix="/api/security", tags=["Security Center"])
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
ACCESS_ACTIONS = {
    "readOnly": ["find"],
    "restrictedReadWrite": ["find", "insert", "update"],
}


class RoleChange(BaseModel):
    role: Literal["user", "admin"]


class MongoRoleCreate(BaseModel):
    role_name: str = Field(min_length=3, max_length=63)
    database: str = Field(min_length=1, max_length=63)
    collection: str = Field(min_length=1, max_length=63)
    access: Literal["readOnly", "restrictedReadWrite"]

    @field_validator("role_name", "database", "collection")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not NAME_PATTERN.fullmatch(value) or value.startswith("system"):
            raise ValueError("Use a simple MongoDB name without spaces, dots, or system prefixes.")
        return value


class MongoUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=63)
    password: SecretStr = Field(min_length=16, max_length=256)
    database: str = Field(min_length=1, max_length=63)
    role_name: str = Field(min_length=3, max_length=63)

    @field_validator("username", "database", "role_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not NAME_PATTERN.fullmatch(value) or value.startswith("system"):
            raise ValueError("Use a simple MongoDB name without spaces, dots, or system prefixes.")
        return value

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        checks = (any(ch.islower() for ch in raw), any(ch.isupper() for ch in raw),
                  any(ch.isdigit() for ch in raw), any(not ch.isalnum() for ch in raw))
        if len(raw) < 16 or not all(checks):
            raise ValueError("Use at least 16 characters with upper and lowercase letters, a number, and a symbol.")
        return value


class PermissionTest(BaseModel):
    username: str = Field(min_length=3, max_length=63)
    password: SecretStr = Field(min_length=1, max_length=256)
    database: str = Field(min_length=1, max_length=63)
    collection: str = Field(min_length=1, max_length=63)
    operation: Literal["find", "update"]

    @field_validator("username", "database", "collection")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not NAME_PATTERN.fullmatch(value) or value.startswith("system"):
            raise ValueError("Use a simple MongoDB name without spaces, dots, or system prefixes.")
        return value


def _admin_user(request: Request) -> dict:
    user = _session_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required for security administration.")
    return user


def _server_settings(client) -> dict:
    """Return only verified booleans/modes; never return server command-line values."""
    auth_mode = None
    tls_mode = None
    try:
        parsed = client.admin.command("getCmdLineOpts").get("parsed", {})
        auth_mode = (parsed.get("security") or {}).get("authorization") or "disabled"
        tls_mode = ((parsed.get("net") or {}).get("tls") or {}).get("mode") or "disabled"
    except PyMongoError:
        logger.info("MongoDB security settings are not readable by the application connection.")
    try:
        auth_info = client.admin.command("connectionStatus", showPrivileges=True).get("authInfo", {})
        authenticated = bool(auth_info.get("authenticatedUsers"))
        authenticated_roles = auth_info.get("authenticatedUserRoles", [])
        effective_privileges = auth_info.get("authenticatedUserPrivileges", [])
    except PyMongoError:
        authenticated = False
        authenticated_roles = []
        effective_privileges = []
    auth_enabled = auth_mode == "enabled" or authenticated
    auth_verified = auth_mode in {"enabled", "disabled"} or authenticated
    tls_enabled = tls_mode in {"allowTLS", "preferTLS", "requireTLS"}
    tls_verified = tls_mode in {"disabled", "allowTLS", "preferTLS", "requireTLS"}
    return {
        "authentication": {"enabled": auth_enabled, "verified": auth_verified,
                            "server_mode": auth_mode if auth_mode in {"enabled", "disabled"} else "unknown",
                            "application_connection_authenticated": authenticated,
                            "authenticated_user_roles": authenticated_roles,
                            "effective_privileges": effective_privileges},
        "tls": {"enabled": tls_enabled, "verified": tls_verified,
                "server_mode": tls_mode if tls_verified else "unknown",
                "client_uri_tls": _uri_tls_enabled(settings.mongo_uri)},
    }


def _uri_tls_enabled(uri: str) -> bool:
    try:
        query = parse_qs(urlsplit(uri).query)
        value = (query.get("tls") or query.get("ssl") or ["false"])[0].lower()
        return value in {"true", "1", "yes"}
    except Exception:
        return False


def _connection_ok(client) -> bool:
    try:
        return client.admin.command("ping").get("ok") == 1
    except PyMongoError:
        return False


def _certificate_status(path: str) -> dict:
    if not path:
        return {"configured": False, "readable": False, "expires_at": None, "days_remaining": None}
    cert_path = Path(path)
    readable = cert_path.is_file() and os.access(cert_path, os.R_OK)
    result = {"configured": True, "readable": readable, "expires_at": None, "days_remaining": None}
    if not readable:
        return result
    try:
        decoded = ssl._ssl._test_decode_cert(str(cert_path))
        raw = decoded.get("notAfter")
        if raw:
            expiry = datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            result["expires_at"] = expiry.isoformat()
            result["days_remaining"] = (expiry - datetime.now(timezone.utc)).days
    except Exception:
        logger.info("Configured MongoDB certificate metadata could not be parsed.")
    return result


def _allowed_database(name: str) -> None:
    if name not in settings.mongo_security_databases:
        raise HTTPException(status_code=403, detail="That database is not in the Security Center database allowlist.")


@contextmanager
def _security_admin_client():
    uri = settings.mongo_security_admin_uri
    if not uri:
        raise HTTPException(status_code=503, detail="MongoDB role management requires MONGO_SECURITY_ADMIN_URI in the backend secret configuration.")
    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=3000,
                             appname="aegis-security-center", **mongo_tls_options(uri))
        client.admin.command("ping")
        app_hello = get_database().client.admin.command("hello")
        admin_hello = client.admin.command("hello")
        if app_hello.get("setName") != admin_hello.get("setName"):
            raise HTTPException(status_code=409, detail="Security administrator URI points to a different MongoDB replica set.")
        security = _server_settings(client)
        if not security["authentication"]["enabled"]:
            raise HTTPException(status_code=409, detail="MongoDB authorization is disabled. Configure and verify authorization before creating MongoDB users or roles.")
        if not security["authentication"]["application_connection_authenticated"]:
            raise HTTPException(status_code=403, detail="The configured MongoDB administrator URI did not authenticate.")
        yield client
    except HTTPException:
        raise
    except PyMongoError:
        logger.warning("Security Center could not connect with the configured MongoDB administrator credentials.")
        raise HTTPException(status_code=503, detail="MongoDB security administration is unavailable. Check the backend secret configuration and administrator privileges.") from None
    finally:
        if client is not None:
            client.close()


def _audit(db, actor: dict, action: str, target: str, details: dict | None = None) -> None:
    db.security_audit.insert_one({
        "actor_user_id": actor.get("user_id"), "actor_email": actor.get("email"),
        "action": action, "target": target, "details": details or {},
        "created_at": datetime.now(timezone.utc),
    })


@router.get("/overview")
def security_overview(request: Request):
    actor = _admin_user(request)
    db = get_database()
    mongo_client = db.client
    healthy = _connection_ok(mongo_client)
    server = _server_settings(mongo_client) if healthy else {
        "authentication": {"enabled": False, "verified": False, "server_mode": "unknown",
                            "application_connection_authenticated": False},
        "tls": {"enabled": False, "verified": False, "server_mode": "unknown", "client_uri_tls": False},
    }
    auth = server["authentication"]
    tls = server["tls"]
    app_users = db.subscription_users.count_documents({})
    admins = db.subscription_users.count_documents({"role": "admin"})
    tls_mode = tls["server_mode"]
    client_tls = tls["client_uri_tls"]
    tls_ca = _certificate_status(settings.mongo_tls_ca_file)
    tls_server_cert = _certificate_status(settings.mongo_tls_certificate_key_file)
    tls_client_cert = _certificate_status(settings.mongo_tls_client_certificate_file)
    native_configured = bool(settings.mongo_security_admin_uri)
    native_authenticated = False
    native_available = False
    native_message = "Configure MONGO_SECURITY_ADMIN_URI in a backend secret file."
    if native_configured and auth["enabled"]:
        try:
            with _security_admin_client():
                native_authenticated = True
                native_available = True
                native_message = "The dedicated MongoDB security account authenticated. Each requested operation is still checked by MongoDB."
        except HTTPException as error:
            native_message = error.detail if isinstance(error.detail, str) else "The native MongoDB administrator connection was not verified."
    elif native_configured:
        native_message = "MongoDB authorization must be enabled and verified before native role or user management can be used."
    return {
        "database": settings.mongo_database,
        "connection": {"available": healthy, "replica_set": _safe_replica_set(mongo_client) if healthy else None},
        "application_accounts": {"count": app_users, "administrator_count": admins,
                                  "current_user_role": actor.get("role")},
        "authentication": auth,
        "tls": {**tls, "ca_certificate": tls_ca, "server_certificate": tls_server_cert,
                "client_certificate": tls_client_cert,
                "plaintext_rejection_policy": "requireTLS configured" if tls_mode == "requireTLS" else
                "not configured" if tls["verified"] else "unknown",
                "plaintext_rejection_tested": False},
        "native_role_management": {"configured": native_configured,
                                   "authenticated": native_authenticated,
                                   "available": native_available,
                                   "message": native_message,
                                   "allowed_databases": list(settings.mongo_security_databases)},
        "field_protection": {"administrative_connection_verified": native_available,
                             "excluded_field": "salary", "verified_view_count": _redacted_view_count(db)},
        "confidentiality": {"status": "partial" if auth["enabled"] else "needs_configuration",
                            "authentication": auth["verified"], "least_privilege": auth["enabled"],
                            "field_protection": _redacted_view_count(db) > 0, "tls": bool(tls["verified"] and tls["enabled"])},
        "integrity": {"status": "application_validation_and_audit_available",
                       "application_role_changes_audited": True,
                       "mongodb_write_authorization": auth["enabled"]},
        "availability": {"status": "available" if healthy else "unavailable",
                          "database_ping_verified": healthy, "replica_set": _safe_replica_set(mongo_client) if healthy else None},
        "permission_matrix": _permission_matrix(),
    }


def _safe_replica_set(client):
    try:
        hello = client.admin.command("hello")
        return {"name": hello.get("setName"), "primary_available": bool(hello.get("isWritablePrimary") or hello.get("primary"))}
    except PyMongoError:
        return None


def _redacted_view_count(db) -> int:
    try:
        count = 0
        for item in db.list_collections(filter={"name": {"$regex": "^security_redacted_"}}):
            options = item.get("options", {})
            pipeline = options.get("pipeline", [])
            if options.get("viewOn") and any(
                stage.get("$project", {}).get("salary") == 0 for stage in pipeline
            ):
                count += 1
        return count
    except PyMongoError:
        return 0


def _permission_matrix():
    return [
        {"role": "MongoDB read-only", "scope": "One configured database and collection",
         "find": True, "insert": False, "update": False, "delete": False,
         "source": "Fixed Security Center role preset; effective only after MongoDB authorization is enabled."},
        {"role": "MongoDB restricted writer", "scope": "One configured database and collection",
         "find": True, "insert": True, "update": True, "delete": False,
         "source": "Fixed Security Center role preset; effective only after MongoDB authorization is enabled."},
        {"role": "Application user", "scope": "Existing session-protected platform routes",
         "find": True, "insert": "Route-specific", "update": "Own account operations", "delete": False,
         "source": "Existing application RBAC; MongoDB server authorization is reported separately."},
        {"role": "Application admin", "scope": "Application administration and Security Center",
         "find": True, "insert": "Route-specific", "update": "Administrative routes", "delete": False,
         "source": "Existing application administrator checks."},
    ]


@router.get("/application-users")
def list_application_users(request: Request):
    _admin_user(request)
    rows = get_database().subscription_users.find(
        {}, {"_id": 0, "user_id": 1, "email": 1, "role": 1, "plan": 1, "created_at": 1, "renewal_at": 1}
    ).sort("created_at", -1).limit(200)
    return {"users": list(rows), "limit": 200}


@router.patch("/application-users/{user_id}/role")
def update_application_role(user_id: str, payload: RoleChange, request: Request):
    actor = _admin_user(request)
    db = get_database()
    target = db.subscription_users.find_one({"user_id": user_id}, {"_id": 0, "user_id": 1, "email": 1, "role": 1})
    if not target:
        raise HTTPException(status_code=404, detail="Application account was not found.")
    if target.get("role") == "admin" and payload.role == "user" and db.subscription_users.count_documents({"role": "admin"}) <= 1:
        raise HTTPException(status_code=409, detail="The last administrator cannot be demoted.")
    db.subscription_users.update_one({"user_id": user_id}, {"$set": {"role": payload.role}})
    _audit(db, actor, "application_role_changed", user_id,
           {"email": target.get("email"), "from": target.get("role", "user"), "to": payload.role})
    return {"user_id": user_id, "email": target.get("email"), "role": payload.role,
            "message": "Application role updated and recorded in the security audit."}


@router.get("/mongo/catalog")
def mongo_catalog(request: Request):
    _admin_user(request)
    _need_security_admin()
    with _security_admin_client() as client:
        catalog = {}
        for database_name in settings.mongo_security_databases:
            names = [name for name in client[database_name].list_collection_names()
                     if not name.startswith("system.")]
            catalog[database_name] = sorted(names)
        return {"databases": catalog}


def _need_security_admin():
    if not settings.mongo_security_admin_uri:
        raise HTTPException(status_code=503, detail="Configure MONGO_SECURITY_ADMIN_URI in the backend secret store to manage native MongoDB roles and users.")


@router.get("/mongo/users")
def list_mongo_users(request: Request, database: str):
    _admin_user(request)
    _allowed_database(database)
    with _security_admin_client() as client:
        result = client[database].command({"usersInfo": 1, "showCredentials": False, "showPrivileges": True})
        return {"database": database, "users": result.get("users", [])}


@router.get("/mongo/roles")
def list_mongo_roles(request: Request, database: str):
    _admin_user(request)
    _allowed_database(database)
    with _security_admin_client() as client:
        result = client[database].command({"rolesInfo": 1, "showPrivileges": True, "showBuiltinRoles": False})
        return {"database": database, "roles": result.get("roles", [])}


@router.post("/mongo/roles", status_code=201)
def create_mongo_role(payload: MongoRoleCreate, request: Request):
    actor = _admin_user(request)
    _allowed_database(payload.database)
    with _security_admin_client() as client:
        if payload.collection not in client[payload.database].list_collection_names():
            raise HTTPException(status_code=404, detail="Choose an existing collection. Security Center will not create data collections.")
        privilege = {"resource": {"db": payload.database, "collection": payload.collection},
                     "actions": ACCESS_ACTIONS[payload.access]}
        client[payload.database].command({"createRole": payload.role_name,
                                          "privileges": [privilege], "roles": []})
        _audit(get_database(), actor, "mongodb_role_created", f"{payload.database}.{payload.role_name}",
               {"collection": payload.collection, "access": payload.access,
                "actions": ACCESS_ACTIONS[payload.access]})
        return {"database": payload.database, "role_name": payload.role_name,
                "collection": payload.collection, "access": payload.access,
                "actions": ACCESS_ACTIONS[payload.access], "message": "Least-privilege MongoDB role created."}


@router.post("/mongo/users", status_code=201)
def create_mongo_user(payload: MongoUserCreate, request: Request):
    actor = _admin_user(request)
    _allowed_database(payload.database)
    with _security_admin_client() as client:
        roles = client[payload.database].command({"rolesInfo": [{"role": payload.role_name,
                                                                   "db": payload.database}]})
        if not roles.get("roles"):
            raise HTTPException(status_code=404, detail="Create or select a scoped role before adding a MongoDB user.")
        client[payload.database].command({
            "createUser": payload.username, "pwd": payload.password.get_secret_value(),
            "roles": [{"role": payload.role_name, "db": payload.database}],
        })
        _audit(get_database(), actor, "mongodb_user_created", f"{payload.database}.{payload.username}",
               {"role": payload.role_name})
        return {"database": payload.database, "username": payload.username,
                "role": payload.role_name, "message": "MongoDB user created. The password is not stored in the application database or returned."}


@router.post("/permissions/test")
def test_mongo_permission(payload: PermissionTest, request: Request):
    _admin_user(request)
    _allowed_database(payload.database)
    client = None
    try:
        client = MongoClient(settings.mongo_uri, username=payload.username,
                             password=payload.password.get_secret_value(),
                             authSource=payload.database, serverSelectionTimeoutMS=3500,
                             connectTimeoutMS=2500, appname="aegis-security-permission-test",
                             **mongo_tls_options(settings.mongo_uri))
        collection = client[payload.database][payload.collection]
        if payload.operation == "find":
            collection.find_one({}, {"_id": 1})
        else:
            # Explain runs the query planner and checks authorization without executing a write.
            client[payload.database].command({
                "explain": {"update": payload.collection, "updates": [
                    {"q": {"_id": "__security_center_nonexistent_probe__"},
                     "u": {"$set": {"__security_probe": True}}, "multi": False}],
                }, "verbosity": "queryPlanner",
            })
        return {"verified": True, "allowed": True, "operation": payload.operation,
                "message": f"MongoDB allowed the {payload.operation} permission check; no document was returned or changed."}
    except OperationFailure as error:
        if error.code in {13, 18} or "not authorized" in str(error).lower():
            return {"verified": True, "allowed": False, "operation": payload.operation,
                    "message": f"MongoDB denied the {payload.operation} operation for these credentials."}
        logger.info("MongoDB permission test could not be completed (server error code %s).", error.code)
        raise HTTPException(status_code=503, detail="MongoDB could not complete this safe permission check.") from None
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB permission test could not connect. Verify the database and credentials.") from None
    finally:
        if client is not None:
            client.close()


@router.post("/views/redacted")
def create_salary_redacted_view(database: str, collection: str, request: Request):
    actor = _admin_user(request)
    _allowed_database(database)
    if not NAME_PATTERN.fullmatch(collection) or collection.startswith("system"):
        raise HTTPException(status_code=422, detail="Choose a valid existing collection name.")
    view_name = f"security_redacted_{collection}"
    with _security_admin_client() as client:
        db = client[database]
        names = set(db.list_collection_names())
        if collection not in names:
            raise HTTPException(status_code=404, detail="Choose an existing collection. No source collection was changed.")
        if view_name not in names:
            db.create_collection(view_name, viewOn=collection,
                                 pipeline=[{"$project": {"salary": 0}}])
        info = next((item for item in db.list_collections(filter={"name": view_name})), None)
        if not info or info.get("options", {}).get("viewOn") != collection:
            raise HTTPException(status_code=409, detail="An object with the redacted view name already exists and was not changed.")
        sample = next(iter(db[view_name].aggregate([{"$limit": 1}])), None)
        stages = (info or {}).get("options", {}).get("pipeline", [])
        definition_excludes = any(stage.get("$project", {}).get("salary") == 0 for stage in stages)
        excluded = definition_excludes and (sample is None or "salary" not in sample)
        _audit(get_database(), actor, "salary_redacted_view_verified", f"{database}.{view_name}",
               {"source_collection": collection, "salary_excluded": excluded})
        return {"database": database, "source_collection": collection, "view": view_name,
                "verified": excluded, "excluded_field": "salary",
                "message": "View returns no salary field." if excluded else "The view did not pass its field-redaction check."}


@router.post("/tls/test")
def test_tls_connection(request: Request):
    _admin_user(request)
    settings_report = _server_settings(get_database().client)
    tls = settings_report["tls"]
    if not tls["verified"] or not tls["enabled"]:
        return {"verified": False, "status": "not_configured", "encrypted": False,
                "unencrypted_connections": {"tested": False, "rejected": None},
                "message": "MongoDB TLS is not configured or could not be verified. No TLS success is reported."}
    if not tls["client_uri_tls"]:
        return {"verified": False, "status": "client_not_configured", "encrypted": False,
                "unencrypted_connections": {"tested": False, "rejected": None,
                                             "required_by_server_configuration": tls["server_mode"] == "requireTLS"},
                "message": "MongoDB supports TLS, but the application URI does not request TLS. Configure tls=true and the CA certificate before testing."}
    client = None
    try:
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=4000,
                             connectTimeoutMS=3000, appname="aegis-security-tls-check",
                             **mongo_tls_options(settings.mongo_uri))
        client.admin.command("ping")
        plain_test = {"tested": False, "rejected": None}
        if tls["server_mode"] == "requireTLS":
            plain_test = _test_plaintext_rejected(settings.mongo_uri)
        return {"verified": True, "status": "connected", "encrypted": True,
                "server_mode": tls["server_mode"],
                "unencrypted_connections": plain_test,
                "message": "The backend completed a MongoDB ping using its TLS-enabled connection URI."}
    except PyMongoError:
        return {"verified": False, "status": "connection_failed", "encrypted": False,
                "unencrypted_connections": {"tested": False, "rejected": None,
                                             "required_by_server_configuration": tls["server_mode"] == "requireTLS"},
                "message": "TLS connection failed. Check the CA chain, certificate validity, hostname, and backend-mounted certificate paths."}
    finally:
        if client is not None:
            client.close()


def _test_plaintext_rejected(uri: str) -> dict:
    """Probe a configured requireTLS server with an unauthenticated, read-only ping."""
    try:
        from pymongo.uri_parser import parse_uri
        nodes = parse_uri(uri, validate=True).get("nodelist", [])
        if not nodes:
            return {"tested": False, "rejected": None, "message": "No MongoDB seed host is available."}
        host, port = sorted(nodes)[0]
        probe = MongoClient(host=host, port=port, directConnection=True, tls=False,
                            serverSelectionTimeoutMS=2500, connectTimeoutMS=1500,
                            appname="aegis-security-plaintext-probe")
        try:
            probe.admin.command("ping")
            return {"tested": True, "rejected": False,
                    "message": "The server accepted an unencrypted ping; requireTLS rejection was not verified."}
        except PyMongoError as error:
            message = str(error).lower()
            rejected = any(marker in message for marker in ("ssl handshake", "tls", "sslerror", "ssl routines"))
            return {"tested": rejected, "rejected": True if rejected else None,
                    "message": "The plaintext connection was rejected during TLS negotiation." if rejected
                    else "The plaintext probe failed for a reason that does not verify TLS rejection."}
        finally:
            probe.close()
    except Exception:
        logger.info("Unencrypted connection rejection probe could not be completed.")
        return {"tested": False, "rejected": None,
                "message": "The plaintext rejection check could not be completed."}


@router.get("/audit")
def security_audit(request: Request):
    _admin_user(request)
    rows = get_database().security_audit.find({}, {"_id": 0}).sort("created_at", -1).limit(100)
    return {"records": list(rows), "limit": 100}
