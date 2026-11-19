"""Standalone web application for the AI Threat Assistant (entry point of the Docker image).

NEW FILE for the standalone build. The original platform starts from backend/main.py (kept for reference in
docs/reference/original_main.py), which also loads ~25 unrelated dashboards/labs. This app serves only the
assistant, using the original modules in backend/ unchanged:

  backend.routes.chapter17_compat  /api/ask, /api/ask/stream, /api/ask/history ...  chat API
  backend.routes.local_agent       /api/agent/...                                   optional laptop security agent
  backend.routes.subscriptions     /api/subscriptions/... (register, login, me ...) accounts + session cookie

From backend/main.py it reproduces only what these routers need: the MongoDB connection on app.state.database,
the account indexes, the background warm-up of the platform-knowledge index, /config.js and /account.
Added for container operation: /health (liveness, used by the Docker HEALTHCHECK) and /health/details
(MongoDB, Ollama and model checks).

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from backend import database as db_module
from backend.config import settings, startup_report
from backend.database import close_database, connect_database
from backend.routes.chapter17_compat import router as chat_router
from backend.routes.local_agent import router as local_agent_router
from backend.routes.subscriptions import router as subscriptions_router
from backend.services.llm_provider import get_llm_configuration
from backend.services.platform_knowledge import warm_platform_knowledge
from backend.services.subscriptions import ensure_indexes as ensure_subscription_indexes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("aegis.chatbot")
PUBLIC_DIR = Path(__file__).resolve().parent / "public"


@asynccontextmanager
async def lifespan(application: FastAPI):
    for level, message in startup_report():
        getattr(logger, level)("[config] %s", message)
    database = connect_database()                 # raises if MongoDB is unreachable - the container then exits
    ensure_subscription_indexes(database)
    application.state.database = database
    logger.info("[MongoDB] Connected to %s", settings.mongo_database)
    llm = get_llm_configuration()
    logger.info("[LLM] provider=%s model=%s endpoint=%s configured=%s", llm.provider, llm.model, llm.endpoint, llm.configured)
    warm_platform_knowledge(application, database)
    try:
        yield
    finally:
        close_database()
        logger.info("[MongoDB] Disconnected")


app = FastAPI(title="AI Threat Assistant", lifespan=lifespan)
app.include_router(subscriptions_router)
app.include_router(chat_router)
app.include_router(local_agent_router)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/details", include_in_schema=False)
def health_details() -> JSONResponse:
    """Dependency check: MongoDB ping, Ollama reachability and whether the configured model is installed."""
    checks: dict[str, dict] = {}
    started = time.perf_counter()
    try:
        db_module.client.admin.command("ping")
        hello = db_module.client.admin.command("hello")
        checks["mongodb"] = {"ok": True, "database": settings.mongo_database,
                             "replica_set": hello.get("setName"), "ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - report, never raise from a health check
        checks["mongodb"] = {"ok": False, "error": type(exc).__name__}
    llm = get_llm_configuration()
    checks["llm"] = {"provider": llm.provider, "model": llm.model, "endpoint": llm.endpoint, "configured": llm.configured}
    if llm.provider == "ollama":
        try:
            started = time.perf_counter()
            tags = httpx.get(f"{llm.endpoint}/api/tags", timeout=5).json()
            names = [m.get("name") for m in tags.get("models", [])]
            checks["ollama"] = {"ok": True, "model_installed": llm.model in names, "models": names,
                                "ms": round((time.perf_counter() - started) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            checks["ollama"] = {"ok": False, "error": type(exc).__name__}
    healthy = checks["mongodb"]["ok"] and checks.get("ollama", {"ok": True, "model_installed": True}).get("ok") \
        and checks.get("ollama", {"model_installed": True}).get("model_installed")
    return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503)


@app.get("/config.js", include_in_schema=False)
def frontend_config() -> Response:
    # Same output as the original backend/main.py /config.js.
    base_url = settings.frontend_api_url.rstrip("/")
    encoded = base_url.replace("\\", "\\\\").replace("'", "\\'")
    return Response(content=f"window.AEGIS_API_BASE_URL = '{encoded}';", media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})


@app.get("/account", include_in_schema=False)
def account_page() -> HTMLResponse:
    return HTMLResponse((PUBLIC_DIR / "account.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
def assistant_page() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "assistant.html", headers={"Cache-Control": "no-store"})


@app.get("/index.html", include_in_schema=False)
def legacy_index() -> RedirectResponse:
    # public/index.html is the original full dashboard page, kept because the platform-knowledge index reads its
    # navigation; its other views belong to the full platform, so visitors are sent to the assistant page.
    return RedirectResponse("/")


app.mount("/", StaticFiles(directory=PUBLIC_DIR), name="public")
