from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.collectors.cti import start_collectors
from backend.collectors.galaxies import sync_galaxies
from backend.collectors.misp_feeds import FEEDS, sync_feed
from backend.collectors.reports import sync_reports
from backend.config import settings
from backend.database import close_database, connect_database
from backend.routes.api import router as api_router
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
        sync_feed(database, feed_name, feed_url, max_events=settings.sync_startup_max_events)
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
    database = connect_database()
    _ensure_indexes(database)
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
        next_run_time=None,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
    _run_galaxy_sync,
    "interval",
    days=1,
    args=[database],
    id="misp-galaxy-sync",
    replace_existing=True,
    next_run_time=datetime.now(timezone.utc),
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
    next_run_time=datetime.now(timezone.utc),
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
            next_run_time=datetime.now(timezone.utc),
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


@app.exception_handler(404)
async def handle_not_found(request: Request, error) -> JSONResponse:
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=404,
            content={"error": f"API route not found: {request.method} {request.url.path}"},
        )
    return JSONResponse(status_code=404, content={"detail": "Not found"})


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="frontend")
