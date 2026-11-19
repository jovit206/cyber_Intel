from __future__ import annotations

import hashlib
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.collectors.cti import start_collectors
from backend.collectors.galaxies import sync_galaxies
from backend.collectors.misp_feeds import FEEDS, sync_feed
from backend.collectors.reports import sync_reports
from backend.config import settings, startup_report
from backend.services.entitlements import (FEATURE_LABELS, billing_period, describe_entitlements,
    feature_for_path, release_feature_use, reserve_feature_use)
from backend.database import close_database, connect_database
from backend.routes.api import router as api_router
from backend.routes.cross_source import router as cross_source_router
from backend.routes.lab8 import router as lab8_router
from backend.routes.lab9 import router as lab9_router
from backend.routes.lab10 import router as lab10_router
from backend.routes.subscriptions import router as subscriptions_router
from backend.routes.chapter11 import router as chapter11_router
from backend.routes.demo_transactions import router as demo_transactions_router
from backend.routes.shard_distribution import router as shard_distribution_router
from backend.routes.security import router as security_router
from backend.routes.admin import router as admin_router
from backend.routes.manager import router as manager_router
from backend.services.subscriptions import ensure_indexes as ensure_subscription_indexes
from backend.services.cross_source import ensure_built as ensure_cross_source
from backend.services.cross_source import rebuild as rebuild_cross_source
from backend.routes.api import sample_cache_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("aegis")
PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


def _ensure_indexes(database) -> None:
    indexes = list(database["events"].list_indexes())
    source_id_index = next((
        index for index in indexes
        if len(index["key"]) == 2
        and index["key"].get("source") == 1
        and index["key"].get("source_id") == 1
    ), None)
    if source_id_index and not source_id_index.get("unique"):
        raise RuntimeError(
            "Existing events index on {source, source_id} is not unique; "
            "resolve it before enabling CTI deduplication."
        )
    if not source_id_index:
        database["events"].create_index(
            [("source", 1), ("source_id", 1)],
            name="cti_source_source_id_unique",
            unique=True,
            partialFilterExpression={"source_id": {"$type": "string"}},
        )
    if not any(
        len(index["key"]) == 2
        and index["key"].get("source") == 1
        and index["key"].get("timestamp") == -1
        for index in indexes
    ):
        database["events"].create_index([("source", 1), ("timestamp", -1)])
    if not any(
        len(index["key"]) == 3
        and index["key"].get("source") == 1
        and index["key"].get("indicator_type") == 1
        and index["key"].get("normalized_indicator") == 1
        for index in indexes
    ):
        database["events"].create_index([
            ("source", 1),
            ("indicator_type", 1),
            ("normalized_indicator", 1),
        ])
    database["events"].create_index(
        [("source_feed", 1), ("misp_uuid", 1)],
        name="misp_feed_uuid_unique",
        unique=True,
        partialFilterExpression={
            "source_feed": {"$type": "string"},
            "misp_uuid": {"$type": "string"},
        },
    )
    database["events"].create_index([("source_feed_name", 1), ("timestamp", -1)])
    database["attributes"].create_index(
        [("source_feed", 1), ("event_uuid", 1), ("attribute_uuid", 1)],
        name="misp_attribute_uuid_unique",
        unique=True,
        partialFilterExpression={
            "source_feed": {"$type": "string"},
            "event_uuid": {"$type": "string"},
            "attribute_uuid": {"$type": "string"},
        },
    )
    database["attributes"].create_index([("event_id", 1), ("is_current", 1)])
    database["attributes"].create_index([("normalized_indicator", 1), ("is_current", 1)])
    database["threats"].create_index(
        [("galaxy_type", 1), ("cluster_uuid", 1)],
        name="misp_galaxy_cluster_uuid_unique",
        unique=True,
        partialFilterExpression={
            "source": "misp-galaxy",
            "cluster_uuid": {"$type": "string"},
        },
    )
    database["passages"].create_index([("report_id", 1), ("is_current", 1)])


def _run_misp_feed(database, feed_name: str, feed_url: str) -> None:
    try:
        result = sync_feed(database, feed_name, feed_url, max_events=settings.sync_startup_max_events)
        if result and result.get("fetched_events"):  # new/changed events only
            try:
                rebuild_cross_source(database)
            except Exception:
                logger.exception("[%s] Cross-source refresh failed", feed_name)
    except Exception as error:
        database["cti_sync_state"].update_one(
            {"_id": feed_name},
            {"$set": {
                "source_feed": feed_url,
                "last_attempt": datetime.now(timezone.utc),
                "last_error": str(error),
            }},
            upsert=True,
        )
        logger.exception("[%s] Public MISP feed sync failed", feed_name)
        raise


def _run_galaxy_sync(database) -> None:
    try:
        sync_galaxies(database)
    except Exception:
        logger.exception("[Galaxy] Public cluster sync failed")
        raise


def _run_report_sync(database) -> None:
    try:
        sync_reports(database, max_reports=settings.sync_startup_max_reports)
    except Exception:
        logger.exception("[Reports] Public annual-report sync failed")
        raise


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    for level, message in startup_report():
        getattr(logger, level)("[config] %s", message)
    database = connect_database()
    _ensure_indexes(database)
    ensure_subscription_indexes(database)
    database["wallets"].update_one({"user_id": "platform"}, {"$setOnInsert": {
        "user_id": "platform", "balance": 0, "currency": "INR",
        "updated_at": datetime.now(timezone.utc),
    }}, upsert=True)
    application.state.database = database
    logger.info("[MongoDB] Connected to %s", settings.mongo_database)

    scheduler = BackgroundScheduler(timezone="UTC")
    application.state.scheduler = scheduler
    scheduler.add_job(
        sample_cache_metrics,
        "interval",
        seconds=10,
        args=[database],
        id="wiredtiger-cache-sampler",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    if settings.run_initial_feed_sync:
        scheduler.add_job(
            ensure_cross_source,
            args=[database],
            id="cross-source-initial-build",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
            max_instances=1,
        )
    else:
        logger.info("[startup] Initial feed and derived-index sync disabled for this launch; recurring jobs remain scheduled.")
    scheduler.add_job(
    _run_galaxy_sync,
    "interval",
    days=1,
    args=[database],
    id="misp-galaxy-sync",
    replace_existing=True,
    next_run_time=datetime.now(timezone.utc) if settings.run_initial_feed_sync else None,
    max_instances=1,
    coalesce=True,
    )
    scheduler.add_job(
    _run_report_sync,
    "interval",
    days=1,
    args=[database],
    id="annual-report-sync",
    replace_existing=True,
    next_run_time=datetime.now(timezone.utc) if settings.run_initial_feed_sync else None,
    max_instances=1,
    coalesce=True,
    )
    start_collectors(scheduler, database)
    for name, feed_url in FEEDS.items():
        scheduler.add_job(
            _run_misp_feed,
            "interval",
            days=1,
            args=[database, name, feed_url],
            id=f"misp-feed-{name}",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc) if settings.run_initial_feed_sync else None,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    try:
        sample_cache_metrics(database)
    except Exception as error:
        logger.error("[METRICS] Initial WiredTiger cache sample failed: %s", error)
    try:
        yield
    finally:
        scheduler.shutdown(wait=True)
        close_database()
        logger.info("[MongoDB] Disconnected")


app = FastAPI(
    title="AEGIS-CTI API",
    version="1.0.0",
    lifespan=lifespan,
)

origins = list(dict.fromkeys([
    *(origin.strip() for origin in settings.frontend_origin.split(",") if origin.strip()),
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]))
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(api_router)
app.include_router(cross_source_router)
app.include_router(lab8_router)
app.include_router(lab9_router)
app.include_router(lab10_router)
app.include_router(subscriptions_router)
app.include_router(chapter11_router)
app.include_router(demo_transactions_router)
app.include_router(shard_distribution_router)
app.include_router(security_router)
app.include_router(admin_router)
app.include_router(manager_router)


@app.middleware("http")
async def require_platform_session(request: Request, call_next):
    """Guard every platform API and the dashboard HTML before returning protected content."""
    path = request.url.path
    public_api = {
        "/api/subscriptions/plans", "/api/subscriptions/register",
        "/api/subscriptions/login", "/api/subscriptions/logout",
        "/api/demo-transactions/health",
    }
    guarded_api = path.startswith("/api/") and path not in public_api
    guarded_page = path in {"/", "/index.html"}
    manager_write_check = (path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}
                           and path != "/api/subscriptions/logout")
    if not guarded_api and not guarded_page and not manager_write_check:
        return await call_next(request)

    token = request.cookies.get("aegis_session", "")
    authenticated = False
    session_user = None
    if token:
        try:
            database = request.app.state.database
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            session = database.subscription_sessions.find_one({
                "token_hash": token_hash,
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            })
            if session:
                session_user = database.subscription_users.find_one({"user_id": session["user_id"]})
                authenticated = bool(session_user) and session_user.get(
                    "status", session_user.get("account_status", "active")
                ) not in {"disabled", "suspended", "inactive", "banned"}
        except Exception:
            if guarded_api:
                return JSONResponse(status_code=503, content={"detail": "Authentication service is temporarily unavailable."})
            return HTMLResponse("Authentication service is temporarily unavailable.", status_code=503)
    if not authenticated:
        if guarded_api:
            return JSONResponse(status_code=401, content={"detail": "Please sign in to continue."})
        if manager_write_check:
            return await call_next(request)
        return Response(status_code=303, headers={"Location": "/account?next=%2F"})
    if manager_write_check and session_user.get("role") == "manager":
        return JSONResponse(status_code=403, content={"detail": "Manager accounts have read-only access."})
    feature = feature_for_path(path, request.method)
    reserved_period = None
    if guarded_api and feature and session_user.get("role") != "admin":
        period = billing_period()
        try:
            allowed, limit = reserve_feature_use(request.app.state.database, session_user, feature, period)
        except Exception:
            logger.exception("Feature entitlement check failed for %s", feature)
            return JSONResponse(status_code=503, content={"detail": "Feature access could not be verified. Please retry."})
        if not allowed:
            entitlements = describe_entitlements(session_user, request.app.state.database)
            remaining = entitlements["features"][feature]["remaining"]
            reason = "This feature is included with Plus and Pro plans." if limit == 0 else "Your monthly feature allowance has been used."
            return JSONResponse(status_code=402, content={"detail": {
                "message": reason, "feature": feature, "label": FEATURE_LABELS[feature],
                "current_plan": entitlements["plan"], "required_plan": "pro" if entitlements["plan"] == "plus" else "plus",
                "remaining": remaining, "used": entitlements["features"][feature]["used"],
                "period": entitlements["period"], "limit": limit, "reset_period": "monthly",
                "upgrade_prices": {"plus": 299, "pro": 799},
            }})
        if limit is not None:
            reserved_period = period
    try:
        response = await call_next(request)
    except Exception:
        if feature and reserved_period:
            try:
                release_feature_use(request.app.state.database, session_user["user_id"], feature, reserved_period)
            except Exception:
                logger.exception("Feature usage rollback failed after an exception for %s", feature)
        raise
    if feature and reserved_period and response.status_code >= 400:
        try:
            release_feature_use(request.app.state.database, session_user["user_id"], feature, reserved_period)
        except Exception:
            logger.exception("Feature usage reservation rollback failed for %s", feature)
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config.js")
def frontend_config() -> Response:
    base_url = settings.frontend_api_url.rstrip("/")
    encoded = base_url.replace("\\", "\\\\").replace("'", "\\'")
    return Response(
        content=f"window.AEGIS_API_BASE_URL = '{encoded}';",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/account", include_in_schema=False)
def account_page() -> HTMLResponse:
    html = (PUBLIC_DIR / "account.html").read_text(encoding="utf-8")
    version = _asset_version()
    html = _ASSET_REFERENCE.sub(lambda m: f'{m["attr"]}="{m["name"]}?v={version}"', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.exception_handler(404)
async def handle_not_found(request: Request, error) -> JSONResponse:
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=404,
            content={"error": f"API route not found: {request.method} {request.url.path}"},
        )
    return JSONResponse(status_code=404, content={"detail": "Not found"})


# Local assets referenced by index.html. Their URLs get a content hash appended so that a browser can
# never combine a new page with an older cached script or stylesheet.
_VERSIONED_ASSETS = ("style.css", "app.js", "cross-source.js", "reliability.js", "lab10.js", "lab11.js", "admin.js", "manager.js", "account.js", "banking-finance.js", "demo-transactions.js", "shard-distribution.js")
_ASSET_REFERENCE = re.compile(r'(?P<attr>href|src)="(?P<name>' + "|".join(re.escape(n) for n in _VERSIONED_ASSETS) + r')"')


def _asset_version() -> str:
    digest = hashlib.sha1()
    for name in _VERSIONED_ASSETS:
        try:
            digest.update((PUBLIC_DIR / name).read_bytes())
        except OSError:
            digest.update(name.encode())
    return digest.hexdigest()[:10]


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index_page() -> HTMLResponse:
    html = (PUBLIC_DIR / "index.html").read_text(encoding="utf-8")
    version = _asset_version()
    html = _ASSET_REFERENCE.sub(lambda m: f'{m["attr"]}="{m["name"]}?v={version}"', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


class RevalidatingStaticFiles(StaticFiles):
    """Serve the frontend with Cache-Control: no-cache so browsers revalidate (ETag) on every
    load instead of heuristically reusing an older script after a deploy."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", RevalidatingStaticFiles(directory=PUBLIC_DIR, html=True), name="frontend")
