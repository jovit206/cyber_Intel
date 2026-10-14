from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId, Timestamp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import ReadPreference
from pymongo.errors import AutoReconnect, PyMongoError

from backend.config import settings
from backend.database import get_database

logger = logging.getLogger("aegis.lab8")

router = APIRouter(prefix="/api/lab8", tags=["Lab 8 - MongoDB Replica Set & Failover"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAB8_DIR_INTERNAL = PROJECT_ROOT / "labs" / "lab8"
LAB8_DIR_EXTERNAL = Path(r"C:\Users\hp\Downloads\Jovit_24BTRCL089-LAB_8\Chapter_08_Submission\Chapter_08_Submission")


def _sanitize(value: Any) -> Any:
    """Recursively converts BSON / non-JSON types into JSON serializable structures."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Timestamp):
        return {"time": value.time, "inc": value.inc}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    return value


def _read_file_safe(path: Path) -> str | None:
    if path.exists() and path.is_file():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Could not read %s: %s", path, e)
    return None


def _load_json_file(path: Path) -> dict[str, Any] | None:
    content = _read_file_safe(path)
    if content:
        try:
            return json.loads(content)
        except Exception as e:
            logger.warning("Failed to parse JSON from %s: %s", path, e)
    return None


@router.get("/status")
def get_replica_set_status() -> dict[str, Any]:
    """Retrieve current, live replica set topology and member health."""
    try:
        db = get_database()
        client = db.client
        raw_status = client.admin.command(
            "replSetGetStatus",
            read_preference=ReadPreference.PRIMARY_PREFERRED
        )
        status = _sanitize(raw_status)
    except Exception as exc:
        logger.error("Failed to query replSetGetStatus: %s", exc)
        return {
            "source": "LIVE",
            "connection_status": "DEGRADED",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "members": [],
            "primary": None,
            "secondaries": [],
            "healthy": False,
        }

    members = status.get("members", [])
    primary_member = next((m for m in members if m.get("stateStr") == "PRIMARY"), None)
    secondary_members = [m for m in members if m.get("stateStr") == "SECONDARY"]
    arbiters = [m for m in members if m.get("stateStr") == "ARBITER"]

    all_healthy = len(members) > 0 and all(m.get("health", 0) == 1 for m in members)
    has_primary = primary_member is not None

    clean_members = []
    for m in members:
        clean_members.append({
            "id": m.get("_id"),
            "name": m.get("name"),
            "state": m.get("state"),
            "state_str": m.get("stateStr"),
            "health": m.get("health"),
            "uptime": m.get("uptime"),
            "ping_ms": m.get("pingMs", 0),
            "sync_source_host": m.get("syncSourceHost"),
            "last_heartbeat": m.get("lastHeartbeat"),
            "optime_date": m.get("optimeDate"),
            "is_self": m.get("self", False),
        })

    return {
        "source": "LIVE",
        "connection_status": "CONNECTED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "set_name": status.get("set", "rs0"),
        "term": status.get("term"),
        "primary": {
            "name": primary_member.get("name"),
            "uptime": primary_member.get("uptime"),
            "optime_date": primary_member.get("optimeDate"),
        } if primary_member else None,
        "secondaries": [
            {
                "name": s.get("name"),
                "uptime": s.get("uptime"),
                "ping_ms": s.get("pingMs", 0),
                "sync_source": s.get("syncSourceHost"),
            } for s in secondary_members
        ],
        "arbiters": [a.get("name") for a in arbiters],
        "member_count": len(members),
        "voting_members": status.get("votingMembersCount"),
        "healthy": all_healthy and has_primary,
        "members": clean_members,
        "raw_status": status,
    }


@router.get("/historical")
def get_historical_results() -> dict[str, Any]:
    """Load historical Lab 8 experiment outputs, write concern proofs, and failover logs."""
    # Find files from internal or external paths
    r81_json = _load_json_file(LAB8_DIR_INTERNAL / "lab_8_1_replica_set.json") or \
               _load_json_file(LAB8_DIR_EXTERNAL / "Activity_8.1" / "lab_8_1_replica_set.json")

    r81_txt = _read_file_safe(LAB8_DIR_INTERNAL / "lab_8_1_replica_set_output.txt") or \
              _read_file_safe(LAB8_DIR_EXTERNAL / "Activity_8.1" / "lab_8_1_replica_set_output.txt")

    r82_json = _load_json_file(LAB8_DIR_INTERNAL / "lab_8_2_failover_monitor.json") or \
               _load_json_file(LAB8_DIR_EXTERNAL / "Activity_8.2" / "lab_8_2_failover_monitor.json")

    r82_txt = _read_file_safe(LAB8_DIR_INTERNAL / "lab_8_2_failover_monitor_output.txt") or \
              _read_file_safe(LAB8_DIR_EXTERNAL / "Activity_8.2" / "lab_8_2_failover_monitor_output.txt")

    try81_json = _load_json_file(LAB8_DIR_INTERNAL / "try_8_1_add_arbiter.json") or \
                 _load_json_file(LAB8_DIR_EXTERNAL / "Activity_8.1_Try_It_Yourself" / "try_8_1_add_arbiter.json")
    try81_txt = _read_file_safe(LAB8_DIR_INTERNAL / "try_8_1_add_arbiter_output.txt") or \
                _read_file_safe(LAB8_DIR_EXTERNAL / "Activity_8.1_Try_It_Yourself" / "try_8_1_add_arbiter_output.txt")

    try82_json = _load_json_file(LAB8_DIR_INTERNAL / "try_8_2_priority_election.json") or \
                 _load_json_file(LAB8_DIR_EXTERNAL / "Activity_8.2_Try_It_Yourself" / "try_8_2_priority_election.json")
    try82_txt = _read_file_safe(LAB8_DIR_INTERNAL / "try_8_2_priority_election_output.txt") or \
                _read_file_safe(LAB8_DIR_EXTERNAL / "Activity_8.2_Try_It_Yourself" / "try_8_2_priority_election_output.txt")

    # Extract structured summaries from 8.1
    act81_summary = {
        "title": "Lab 8.1 - Replica Set Deployment & Replication Verification",
        "has_data": r81_json is not None,
        "raw_text": r81_txt,
        "steps": r81_json.get("steps", []) if r81_json else [],
    }

    # Extract structured summaries from 8.2 (failover)
    act82_summary = {
        "title": "Lab 8.2 - Observe Failover, Election & Continuous Reads",
        "has_data": r82_json is not None,
        "raw_text": r82_txt,
        "steps": r82_json.get("steps", []) if r82_json else [],
    }

    # Try-It-Yourself activities
    try_activities = []
    if try81_json or try81_txt:
        try_activities.append({
            "title": "Activity 8.1 Try It Yourself: Arbiter Configuration",
            "has_data": True,
            "raw_text": try81_txt,
            "steps": try81_json.get("steps", []) if try81_json else [],
        })
    if try82_json or try82_txt:
        try_activities.append({
            "title": "Activity 8.2 Try It Yourself: Priority-Based Election",
            "has_data": True,
            "raw_text": try82_txt,
            "steps": try82_json.get("steps", []) if try82_json else [],
        })

    return {
        "source": "HISTORICAL",
        "label": "HISTORICAL LAB 8 EXPERIMENT ARCHIVE",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "activity_8_1": act81_summary,
        "activity_8_2": act82_summary,
        "try_it_yourself": try_activities,
    }


class StepDownRequest(BaseModel):
    confirm: bool = False
    seconds: int = 60


@router.post("/stepdown")
def trigger_stepdown(req: StepDownRequest) -> dict[str, Any]:
    """Explicit, guarded failover trigger requiring confirmation."""
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Set 'confirm': true to execute replSetStepDown."
        )

    try:
        db = get_database()
        client = db.client
        # Query current primary first
        status = client.admin.command("replSetGetStatus", read_preference=ReadPreference.PRIMARY_PREFERRED)
        primary_member = next((m["name"] for m in status.get("members", []) if m.get("stateStr") == "PRIMARY"), None)

        if not primary_member:
            raise HTTPException(status_code=400, detail="No PRIMARY currently detected to step down.")

        try:
            res = client.admin.command("replSetStepDown", req.seconds)
        except AutoReconnect:
            res = {"note": "Primary closed connection as expected during stepDown"}

        return {
            "source": "ACTION_EXECUTED",
            "status": "success",
            "message": f"Sent replSetStepDown({req.seconds}) to primary {primary_member}",
            "previous_primary": primary_member,
            "stepdown_seconds": req.seconds,
            "result": _sanitize(res),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("Failed to execute stepDown: %s", exc)
        raise HTTPException(status_code=500, detail=f"Stepdown execution failed: {exc}")
