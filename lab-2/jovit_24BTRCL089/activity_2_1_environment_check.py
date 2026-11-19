"""Activity 2.1 - Verify the MongoDB lab environment.

Author: Jovit Chawla (24BTRCL089)

Checks, in order, everything later labs depend on and records a PASS / FAIL / NOT CONFIGURED status with the
evidence for each one:
  1. Python interpreter (>= 3.9)            5. Docker engine and the lab MongoDB container
  2. PyMongo driver                          6. MongoDB Atlas (only if MONGO_URI_ATLAS is set)
  3. MongoDB in Docker  (MONGO_URI_DOCKER)   7. Optional packages used by later chapters
  4. MongoDB native Windows service (MONGO_URI_NATIVE)

The checklist is stored in MongoDB (jovit_lab02.environment_checks on the Docker deployment) and exported to
results/. The exit code is non-zero if a required check fails.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import platform
import subprocess
import sys
import time

from common import Recorder, client_for, connect, deployments, mask

CONTAINER = "jovit-lab02-mongo"
OPTIONAL = ["python-dotenv", "certifi", "dnspython", "pandas", "matplotlib", "faker"]


def check_server(name: str, uri: str) -> dict:
    started = time.perf_counter()
    try:
        client = client_for(uri)
        client.admin.command("ping")
        ping_ms = round((time.perf_counter() - started) * 1000, 1)
        info, hello = client.server_info(), client.admin.command("hello")
        client.close()
        return {"status": "PASS", "detail": f"{mask(uri)} -> MongoDB {info['version']}, "
                f"{hello.get('setName', 'standalone')}, first ping {ping_ms} ms", "version": info["version"]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "FAIL", "detail": f"{mask(uri)} unreachable: {type(exc).__name__}: {str(exc)[:120]}"}


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return done.returncode, (done.stdout or done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def main() -> None:
    rec = Recorder("activity_2_1", "Activity 2.1 - Environment Setup Verification")
    targets = deployments()
    checks: list[dict] = []

    def add(name: str, required: bool, result: dict) -> None:
        checks.append({"check": name, "required": required, **result})
        rec.log(f"  [{result['status']:<14}] {name}: {result['detail']}")

    rec.section("Checklist")
    version = sys.version_info
    add("Python interpreter", True, {"status": "PASS" if version >= (3, 9) else "FAIL",
        "detail": f"{platform.python_implementation()} {platform.python_version()} at {sys.executable}"})
    try:
        add("PyMongo driver", True, {"status": "PASS", "detail": f"pymongo {md.version('pymongo')}"})
    except md.PackageNotFoundError:
        add("PyMongo driver", True, {"status": "FAIL", "detail": "pymongo is not installed"})
    add("MongoDB in Docker", True, check_server("docker", targets["docker"]))
    add("MongoDB native service", False, check_server("native", targets["native"]))

    code, out = run(["docker", "version", "--format", "{{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})"])
    add("Docker engine", True, {"status": "PASS" if code == 0 else "FAIL", "detail": f"server {out}" if code == 0 else out[:160]})
    code, out = run(["docker", "inspect", CONTAINER, "--format",
                     "{{.Config.Image}}|{{.State.Status}}|{{range $p, $b := .NetworkSettings.Ports}}{{$p}}->"
                     "{{range $b}}{{.HostIp}}:{{.HostPort}}{{end}} {{end}}"])
    if code == 0:
        image, state, ports = out.split("|")
        add("Lab MongoDB container", True, {"status": "PASS" if state == "running" else "FAIL",
            "detail": f"{CONTAINER}: image {image}, {state}, ports {ports.strip()}"})
    else:
        add("Lab MongoDB container", True, {"status": "FAIL", "detail": f"{CONTAINER} not found"})

    if "atlas" in targets:
        add("MongoDB Atlas", False, check_server("atlas", targets["atlas"]))
    else:
        add("MongoDB Atlas", False, {"status": "NOT CONFIGURED", "detail": "MONGO_URI_ATLAS is not set in .env; "
                                     "add your own Atlas connection string to run this check"})
    for package in OPTIONAL:
        try:
            add(f"optional: {package}", False, {"status": "PASS", "detail": md.version(package)})
        except md.PackageNotFoundError:
            add(f"optional: {package}", False, {"status": "MISSING", "detail": "not installed (only needed by later labs)"})

    required_failed = [c["check"] for c in checks if c["required"] and c["status"] != "PASS"]
    rec.section("Summary")
    counts = {s: sum(c["status"] == s for c in checks) for s in sorted({c["status"] for c in checks})}
    rec.log(f"  {len(checks)} checks: {counts}")
    rec.log(f"  required checks failed: {required_failed or 'none'}")

    if "MongoDB in Docker" not in required_failed:
        client, db = connect("docker")
        coll = db["environment_checks"]
        run_doc = {"run_at": rec.environment["run_at_utc"], "machine": rec.environment["machine"],
                   "student": rec.environment["student"], "checks": checks}
        inserted = coll.insert_one(run_doc).inserted_id
        stored = coll.find_one({"_id": inserted}, {"_id": 0, "checks.check": 1})
        rec.log(f"  stored this run in jovit_lab02.environment_checks (_id {inserted}); "
                f"{len(stored['checks'])} checks read back; runs stored so far: {coll.count_documents({})}")
        rec.data["stored_id"] = str(inserted)
    rec.data.update({"checks": checks, "counts": counts, "required_failed": required_failed})
    rec.save()
    sys.exit(1 if required_failed else 0)


if __name__ == "__main__":
    main()
