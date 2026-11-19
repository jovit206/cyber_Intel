"""Shared helpers for Jovit Chawla's (24BTRCL089) Chapter 2 activities.

Connection strings come only from environment variables or this folder's git-ignored .env file
(see .env.example). Nothing is hard-coded and secrets are never printed: URIs are shown with the
password masked.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
STUDENT = {"name": "Jovit Chawla", "enrollment": "24BTRCL089"}
DATABASE = "jovit_lab02"
DEFAULTS = {
    "MONGO_URI_DOCKER": "mongodb://localhost:27028",   # jovit-lab02-mongo container (mongo:7.0)
    "MONGO_URI_NATIVE": "mongodb://localhost:27017",   # MongoDB Windows service
}


def load_env() -> None:
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    for key, value in DEFAULTS.items():
        os.environ.setdefault(key, value)


def mask(uri: str) -> str:
    """mongodb+srv://user:secret@host -> mongodb+srv://user:****@host"""
    return re.sub(r"(//[^:/@]+):[^@]+@", r"\1:****@", uri)


def deployments() -> dict[str, str]:
    """Configured deployments in a fixed order; Atlas only when MONGO_URI_ATLAS is set."""
    load_env()
    found = {"docker": os.environ["MONGO_URI_DOCKER"], "native": os.environ["MONGO_URI_NATIVE"]}
    if os.environ.get("MONGO_URI_ATLAS", "").strip():
        found["atlas"] = os.environ["MONGO_URI_ATLAS"].strip()
    return found


def client_for(uri: str) -> MongoClient:
    options: dict[str, Any] = {"serverSelectionTimeoutMS": 5000, "appname": "jovit-24BTRCL089-lab02"}
    if uri.startswith("mongodb+srv://"):
        try:
            import certifi
            options["tlsCAFile"] = certifi.where()
        except ImportError:
            pass
    return MongoClient(uri, **options)


def connect(name: str = "docker"):
    """Connect to one deployment, ping it, and fail loudly if it is unreachable (no mock fallback)."""
    uri = deployments().get(name)
    if not uri:
        sys.exit(f"Deployment '{name}' is not configured (see .env.example).")
    client = client_for(uri)
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - report any connection failure the same way
        sys.exit(f"Cannot reach the {name} MongoDB at {mask(uri)} ({type(exc).__name__}).")
    return client, client[DATABASE]


def to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


class Recorder:
    def __init__(self, activity: str, title: str):
        self.activity, self.title = activity, title
        self.lines: list[str] = []
        self.data: dict[str, Any] = {}
        self.environment = {"student": STUDENT, "run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                            "machine": platform.node(), "os": f"{platform.system()} {platform.release()} ({platform.version()})",
                            "python": platform.python_version(), "pymongo": __import__("pymongo").version}
        self.log(title)
        self.log(f"Student: {STUDENT['name']} ({STUDENT['enrollment']})")
        self.log(f"Run at {self.environment['run_at_utc']} on {self.environment['machine']} | Python "
                 f"{self.environment['python']} | pymongo {self.environment['pymongo']}")

    def log(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def section(self, text: str) -> None:
        self.log()
        self.log(f"== {text} ==")

    def save(self) -> None:
        RESULTS_DIR.mkdir(exist_ok=True)
        payload = {"activity": self.activity, "title": self.title, "environment": self.environment,
                   "measurements": to_jsonable(self.data)}
        (RESULTS_DIR / f"{self.activity}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (RESULTS_DIR / f"{self.activity}.output.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        print(f"\nSaved results/{self.activity}.json and results/{self.activity}.output.txt")
