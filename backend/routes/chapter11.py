"""Authenticated Chapter 11 status, report, run-control and advisor endpoints."""
from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from pymongo import MongoClient
from backend.routes.subscriptions import _session_user
from backend.services.shard_advisor import STRATEGIES, recommend

logger = logging.getLogger("aegis.chapter11")
router = APIRouter(prefix="/api/chapter11", tags=["Chapter 11 - Sharding"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = PROJECT_ROOT / "chapter11"
REPORTS_DIR = LAB_ROOT / "reports"
MONGO_URI_ENV = "CHAPTER11_MONGO_URI"
DEFAULT_MONGO_URI = "mongodb://localhost:37317"
DEFAULT_CONFIG_URI = "mongodb://localhost:37117/?directConnection=true"
COLLECTIONS = ("orders_by_timestamp", "orders_by_hashed_user")
REPORT_FILES = {
    "decision-matrix": ("lab11_1_decision_matrix.json", "application/json"),
    "decision-matrix-markdown": ("lab11_1_decision_matrix.md", "text/markdown"),
    "measurements": ("lab11_2_measurements.json", "application/json"),
    "measurements-markdown": ("lab11_2_measurements.md", "text/markdown"),
}
RUN_TIMEOUT_SECONDS = 180
_job_lock = threading.Lock()
_job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chapter11-run")
_jobs: dict[str, dict[str, Any]] = {}
_active_job_id: str | None = None
_latest_test: dict[str, Any] = {"status": "not_run_by_dashboard", "finished_at": None}


def _admin_user(request: Request) -> dict[str, Any]:
    user = _session_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required to run Chapter 11 experiments.")
    return user


def _safe_report(name: str) -> Any:
    filename, _ = REPORT_FILES[name]
    path = REPORTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This report has not been generated yet. Run the corresponding lab first.")
    if filename.endswith(".json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Chapter 11 report could not be read: %s", filename)
            raise HTTPException(status_code=503, detail="The report is temporarily unavailable.") from None
    return path


@router.get("/capabilities")
def capabilities(request: Request) -> dict[str, bool]:
    user = _session_user(request)
    return {"can_run_experiments": user.get("role") == "admin"}


@router.get("/status")
def cluster_status() -> dict[str, Any]:
    uri = os.getenv(MONGO_URI_ENV, DEFAULT_MONGO_URI).strip()
    config_uri = os.getenv("CHAPTER11_CONFIG_URI", DEFAULT_CONFIG_URI).strip()
    result: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "available": False,
        "mongos_connected": False,
        "shard_count": 0,
        "shards": [],
        "config_replica_set": {"available": False, "name": None, "healthy": False, "members": []},
        "sharding_enabled": False,
        "sharded_collections": {},
        "collections": {},
        "latest_test": dict(_latest_test),
    }
    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2500, connectTimeoutMS=1800, appname="aegis-chapter11-status")
        hello = client.admin.command("hello")
        if hello.get("msg") != "isdbgrid":
            raise RuntimeError("Configured Chapter 11 endpoint is not a mongos router.")
        shards = client.admin.command("listShards").get("shards", [])
        db_metadata = client.config.databases.find_one({"_id": "chapter11"}) or {}
        sharded_collections = {
            name: client.config.collections.find_one({"_id": f"chapter11.{name}"}) is not None
            for name in COLLECTIONS
        }
        result.update({
            "available": True,
            "mongos_connected": True,
            "mongodb_version": client.server_info().get("version"),
            "shard_count": len(shards),
            "shards": [{"name": row.get("_id"), "host": row.get("host"), "registered": row.get("state") == 1}
                       for row in shards],
            # MongoDB 7 can keep the database metadata's legacy partitioned flag false
            # while its collection metadata is sharded; report the effective state.
            "sharding_enabled": bool(db_metadata.get("partitioned")) or any(sharded_collections.values()),
            "sharded_collections": sharded_collections,
        })
        db = client["chapter11"]
        existing = set(db.list_collection_names())
        result["collections"] = {name: {"available": name in existing,
                                        "documents": db[name].count_documents({}) if name in existing else None}
                                 for name in COLLECTIONS}
    except Exception as exc:
        # Keep implementation details in server logs; the dashboard gets a safe state.
        logger.warning("Chapter 11 mongos status check failed: %s", type(exc).__name__)
        result["message"] = "The Chapter 11 sharded cluster is temporarily unavailable."
    finally:
        if client is not None:
            client.close()

    config_client = None
    try:
        config_client = MongoClient(config_uri, serverSelectionTimeoutMS=1800, connectTimeoutMS=1500,
                                    appname="aegis-chapter11-config-status")
        raw = config_client.admin.command("replSetGetStatus")
        members = [{"name": row.get("name"), "state": row.get("stateStr"), "health": row.get("health")}
                   for row in raw.get("members", [])]
        healthy = bool(members) and all(member["health"] == 1 for member in members) and any(
            member["state"] == "PRIMARY" for member in members
        )
        result["config_replica_set"] = {"available": True, "name": raw.get("set"),
                                        "healthy": healthy, "members": members}
    except Exception as exc:
        logger.warning("Chapter 11 config replica-set status check failed: %s", type(exc).__name__)
    finally:
        if config_client is not None:
            config_client.close()

    with _job_lock:
        result["latest_test"] = dict(_latest_test)
        result["active_job"] = dict(_jobs[_active_job_id]) if _active_job_id in _jobs else None
    return result


@router.get("/reports/{report_name}")
def get_report(report_name: str, download: bool = Query(False)):
    if report_name not in REPORT_FILES:
        raise HTTPException(status_code=404, detail="Unknown Chapter 11 report.")
    report = _safe_report(report_name)
    if isinstance(report, Path):
        filename, media_type = REPORT_FILES[report_name]
        return FileResponse(report, media_type=media_type, filename=filename if download else None)
    return JSONResponse(content=report, headers={"Content-Disposition": "attachment; filename=chapter11-" + report_name + ".json"}
                        if download else None)


class AdvisorRequest(BaseModel):
    profile: Literal["ecommerce_orders", "customer_transactions", "time_series", "threat_intelligence", "custom"]
    expected_documents: int = Field(ge=1, le=100_000_000_000)
    write_frequency: Literal["low", "moderate", "high"]
    query_patterns: list[Literal["customer_lookup", "entity_lookup", "entity_history", "order_lookup",
                                 "date_range", "time_range", "indicator_lookup"]] = Field(max_length=7)
    range_queries_important: bool
    recent_write_concentration: bool
    entity_scoped_queries: bool
    compare_a: Literal["range_customer_id", "hashed_customer_id", "range_order_id", "range_event_time", "hashed_event_time", "hashed_indicator"]
    compare_b: Literal["range_customer_id", "hashed_customer_id", "range_order_id", "range_event_time", "hashed_event_time", "hashed_indicator"]

    @model_validator(mode="after")
    def strategies_must_differ(self):
        if self.compare_a == self.compare_b:
            raise ValueError("Choose two different shard-key strategies to compare.")
        return self


@router.post("/advisor")
def advise(payload: AdvisorRequest) -> dict[str, Any]:
    return recommend(payload.model_dump())


class RunRequest(BaseModel):
    lab: Literal["lab11_1", "lab11_2"]


def _set_job(job_id: str, **values: Any) -> None:
    with _job_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(values)


def _execute_lab(job_id: str, lab: str) -> None:
    global _active_job_id, _latest_test
    script = "lab11_1_shard_key.py" if lab == "lab11_1" else "lab11_2_hotspot.py"
    env = os.environ.copy()
    env[MONGO_URI_ENV] = os.getenv(MONGO_URI_ENV, DEFAULT_MONGO_URI)
    _set_job(job_id, status="running", phase="Running " + ("Lab 11.1 analysis" if lab == "lab11_1" else "Lab 11.2 measurements"),
             started_at=datetime.now(timezone.utc).isoformat())
    try:
        completed = subprocess.run([sys.executable, str(LAB_ROOT / script)], cwd=PROJECT_ROOT, env=env,
                                   capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS, check=False)
        if completed.returncode != 0:
            logger.error("Chapter 11 %s failed (exit %s): %s", lab, completed.returncode, completed.stderr[-3000:])
            _set_job(job_id, status="failed", phase="Failed", error="The experiment failed. Check backend logs for details.",
                     output=completed.stdout[-3000:])
            if lab == "lab11_2":
                with _job_lock:
                    _latest_test = {"status": "failed", "finished_at": datetime.now(timezone.utc).isoformat()}
            return
        output = completed.stdout[-5000:]
        if lab == "lab11_2":
            _set_job(job_id, phase="Running Chapter 11 automated checks", output=output)
            test = subprocess.run([sys.executable, str(LAB_ROOT / "test_lab11.py")], cwd=LAB_ROOT, env=env,
                                  capture_output=True, text=True, timeout=60, check=False)
            test_ok = test.returncode == 0
            finished = datetime.now(timezone.utc).isoformat()
            with _job_lock:
                _latest_test = {"status": "passed" if test_ok else "failed", "finished_at": finished}
            if not test_ok:
                logger.error("Chapter 11 automated checks failed: %s", test.stdout[-3000:] + test.stderr[-3000:])
                _set_job(job_id, status="failed", phase="Automated checks failed",
                         error="Measurements were captured, but one or more Chapter 11 checks failed.",
                         output=(output + "\n" + test.stdout[-3000:])[-8000:])
                return
            output = (output + "\n" + test.stdout[-5000:])[-8000:]
        _set_job(job_id, status="completed", phase="Completed", finished_at=datetime.now(timezone.utc).isoformat(),
                 output=output, error=None)
    except subprocess.TimeoutExpired:
        logger.error("Chapter 11 %s exceeded the %ss execution timeout", lab, RUN_TIMEOUT_SECONDS)
        _set_job(job_id, status="failed", phase="Timed out", error="The experiment exceeded its time limit.")
        if lab == "lab11_2":
            with _job_lock:
                _latest_test = {"status": "not_completed", "finished_at": datetime.now(timezone.utc).isoformat()}
    except Exception:
        logger.exception("Unexpected Chapter 11 %s runner failure", lab)
        _set_job(job_id, status="failed", phase="Failed", error="The experiment runner encountered an internal error.")
    finally:
        with _job_lock:
            if _active_job_id == job_id:
                _active_job_id = None


@router.post("/runs", status_code=202)
def start_run(payload: RunRequest, request: Request) -> dict[str, Any]:
    global _active_job_id
    _admin_user(request)
    with _job_lock:
        if _active_job_id is not None:
            current = _jobs.get(_active_job_id, {})
            if current.get("status") in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="A Chapter 11 experiment is already running.")
        job_id = secrets.token_urlsafe(18)
        _jobs[job_id] = {"job_id": job_id, "lab": payload.lab, "status": "queued", "phase": "Queued",
                         "created_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
                         "output": "", "error": None}
        _active_job_id = job_id
        try:
            _job_executor.submit(_execute_lab, job_id, payload.lab)
        except Exception:
            _active_job_id = None
            _jobs.pop(job_id, None)
            logger.exception("Could not queue Chapter 11 job")
            raise HTTPException(status_code=503, detail="The experiment runner is unavailable.") from None
    return dict(_jobs[job_id])


@router.get("/runs/{job_id}")
def get_run(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Chapter 11 run was not found on this backend instance.")
        return dict(job)


@router.get("/definitions")
def definitions() -> dict[str, Any]:
    return {"strategies": [{"id": key, **{name: value for name, value in detail.items() if name != "patterns" and name != "ranges" and name != "monotonic"}}
                            for key, detail in STRATEGIES.items()]}
