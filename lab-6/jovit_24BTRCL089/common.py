"""Shared helpers for Jovit Chawla's (24BTRCL089) Chapter 6 experiments.

Every number printed by the activity scripts comes from a live MongoDB server:
query-plan figures from ``explain`` with ``executionStats`` verbosity, timings from
``time.perf_counter`` around real round trips. Nothing here fabricates a measurement.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

from pymongo import MongoClient
from pymongo.errors import PyMongoError

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
STUDENT = {"name": "Jovit Chawla", "enrollment": "24BTRCL089"}
DATABASE = "jovit_lab06"


def load_env() -> None:
    """Read KEY=VALUE pairs from this folder's (git-ignored) .env without overriding real env vars."""
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    """Connect to MONGO_URI (default: the local mongod service). Fails loudly; there is no mock fallback."""
    load_env()
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="jovit-24BTRCL089-lab06")
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        sys.exit(f"Cannot reach MongoDB ({type(exc).__name__}). Start mongod or set MONGO_URI in .env.")
    return client, client[os.environ.get("LAB6_DATABASE", DATABASE)]


def fresh(db, name: str, **options):
    """Drop and recreate one of this lab's own collections so every run starts from the same state."""
    db.drop_collection(name)
    return db.create_collection(name, **options)


def _plan_stages(node: dict | None) -> list[str]:
    stages: list[str] = []
    while isinstance(node, dict):
        if "stage" in node:
            stages.append(node["stage"])
        children = node.get("inputStages") or ([node["inputStage"]] if "inputStage" in node else [])
        node = children[0] if children else None
    return stages


def _find_key(obj: Any, key: str):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def summarise_explain(explain: dict) -> dict:
    winning = _find_key(explain, "winningPlan") or {}
    plan = winning.get("queryPlan", winning)
    stats = _find_key(explain, "executionStats") or {}
    pipeline = [next(iter(stage)) for stage in explain.get("stages", []) if isinstance(stage, dict)]
    return {
        "plan": " <- ".join(_plan_stages(plan)) or "n/a",
        "pipeline": pipeline,
        "nReturned": stats.get("nReturned"),
        "keysExamined": stats.get("totalKeysExamined"),
        "docsExamined": stats.get("totalDocsExamined"),
        "executionTimeMillis": stats.get("executionTimeMillis"),
    }


def explain_find(coll, filter_: dict, *, sort=None, limit: int = 0, projection=None, hint=None) -> dict:
    command: dict[str, Any] = {"find": coll.name, "filter": filter_}
    if sort:
        command["sort"] = sort
    if limit:
        command["limit"] = limit
    if projection:
        command["projection"] = projection
    if hint:
        command["hint"] = hint
    return summarise_explain(coll.database.command("explain", command, verbosity="executionStats"))


def explain_aggregate(coll, pipeline: list[dict]) -> dict:
    command = {"aggregate": coll.name, "pipeline": pipeline, "cursor": {}}
    return summarise_explain(coll.database.command("explain", command, verbosity="executionStats"))


def time_ms(fn: Callable[[], Any], repeat: int = 5) -> float:
    """Median wall-clock milliseconds of ``repeat`` real calls."""
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 3)


class Recorder:
    """Prints results to the console and saves the same lines plus structured data under results/."""

    def __init__(self, activity: str, title: str, client):
        self.activity, self.title = activity, title
        self.lines: list[str] = []
        self.data: dict[str, Any] = {}
        info = client.server_info()
        self.environment = {
            "student": STUDENT,
            "run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "machine": platform.node(),
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "python": platform.python_version(),
            "pymongo": __import__("pymongo").version,
            "mongodb_server": info.get("version"),
            "topology": client.admin.command("hello").get("setName", "standalone"),
        }
        self.log(f"{title}")
        self.log(f"Student: {STUDENT['name']} ({STUDENT['enrollment']})")
        self.log(f"Run at {self.environment['run_at_utc']} on {self.environment['machine']} | MongoDB "
                 f"{self.environment['mongodb_server']} ({self.environment['topology']}) | Python "
                 f"{self.environment['python']} | pymongo {self.environment['pymongo']}")

    def log(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def section(self, text: str) -> None:
        self.log()
        self.log(f"== {text} ==")

    def plan(self, label: str, summary: dict) -> dict:
        self.log(f"  {label}: plan={summary['plan']} pipeline={summary['pipeline'] or '-'} "
                 f"returned={summary['nReturned']} keysExamined={summary['keysExamined']} "
                 f"docsExamined={summary['docsExamined']} serverMs={summary['executionTimeMillis']}")
        return summary

    def save(self) -> None:
        RESULTS_DIR.mkdir(exist_ok=True)
        payload = {"activity": self.activity, "title": self.title, "environment": self.environment,
                   "measurements": self.data}
        (RESULTS_DIR / f"{self.activity}.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        (RESULTS_DIR / f"{self.activity}.output.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        print(f"\nSaved results/{self.activity}.json and results/{self.activity}.output.txt")
