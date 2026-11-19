"""
Activity 2.1 - Environment Setup Verification
Chapter 02 : Cloud Computing Primer and Environment Setup

Verifies every component needed for the MongoDB labs, in order:
    1. Python interpreter version
    2. PyMongo driver
    3. MongoDB running locally in Docker  (localhost:27017)
    4. MongoDB Atlas  (cloud, from MONGO_URI_ATLAS)
    5. Docker engine + the mongo container
    6. Optional packages used by later chapters

It then writes the whole checklist into MongoDB (chapter02.setup_checks)
and exports that collection to activity_2_1_environment_report.json.

Run:  python activity_2_1_setup_verification.py
"""

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:                                     # .env is optional
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
LOCAL_URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017")
ATLAS_URI = os.getenv("MONGO_URI_ATLAS")


def tls_opts(uri):
    """macOS Python ships without system root CAs, so hand TLS (Atlas) connections
    certifi's bundle. Plain mongodb:// connections need nothing."""
    if uri and uri.startswith("mongodb+srv://"):
        return {"tlsCAFile": certifi.where()}
    return {}

results = {}          # component -> bool
details = {}          # component -> human readable detail


def banner(text):
    print("\n" + "=" * 68)
    print(f" {text}")
    print("=" * 68)


def record(name, ok, detail):
    results[name] = ok
    details[name] = detail
    print(f"  {name:<22}: {'[OK]  ' if ok else '[FAIL]'} {detail}")


# ----------------------------------------------------------------- checks ---
def check_python():
    version = sys.version.split()[0]
    ok = sys.version_info >= (3, 10)
    record("Python", ok, f"{version} on {platform.system()} {platform.machine()}")


def check_pymongo():
    try:
        import pymongo
        record("PyMongo Driver", True, f"version {pymongo.version}")
    except ImportError:
        record("PyMongo Driver", False, "NOT INSTALLED  ->  pip install pymongo")


def check_mongo(label, uri):
    """Ping a deployment and report its server version."""
    if not uri:
        record(label, False, "NOT CONFIGURED -> set MONGO_URI_ATLAS in .env")
        return
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000, **tls_opts(uri))
        client.admin.command("ping")
        info = client.server_info()
        record(label, True, f"MongoDB {info['version']} reachable")
        client.close()
    except PyMongoError as exc:
        hint = ("docker run -d -p 27017:27017 --name mongodb mongo:7"
                if "Local" in label else "check Atlas IP allow-list / credentials")
        record(label, False, f"{type(exc).__name__}  ->  {hint}")


def check_docker():
    try:
        ver = subprocess.run(["docker", "--version"],
                             capture_output=True, text=True, timeout=10)
        if ver.returncode != 0:
            record("Docker Engine", False, "installed but daemon not responding")
            return
        record("Docker Engine", True, ver.stdout.strip())

        ps = subprocess.run(
            ["docker", "ps", "--filter", "ancestor=mongo:7", "--format", "{{.Names}} ({{.Status}})"],
            capture_output=True, text=True, timeout=10)
        running = ps.stdout.strip()
        record("Mongo Container", bool(running),
               running or "no mongo:7 container running")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        record("Docker Engine", False, "NOT INSTALLED / not on PATH")
        record("Mongo Container", False, "skipped - Docker unavailable")


def check_optional_packages():
    print("\nOptional packages (used by later chapters):")
    optional = ["rich", "dotenv", "fastapi", "streamlit", "neo4j", "cassandra", "pandas"]
    found = {}
    for pkg in optional:
        try:
            __import__(pkg)
            found[pkg] = True
            print(f"  {pkg:<22}: installed")
        except ImportError:
            found[pkg] = False
            print(f"  {pkg:<22}: not installed (optional)")
    return found


def mirror_to_atlas(docs, db_name, col_name):
    """Copy the finished collection up to Atlas so the cloud cluster holds the
    same data as the Docker deployment. Purely additive - the activity's own
    work still happens against MONGO_URI_LOCAL first."""
    if not ATLAS_URI:
        print(f"[SKIP] Atlas mirror: MONGO_URI_ATLAS is not set in .env")
        return
    try:
        client = MongoClient(ATLAS_URI, serverSelectionTimeoutMS=10000,
                             tlsCAFile=certifi.where())
        col = client[db_name][col_name]
        col.delete_many({})
        if docs:
            col.insert_many(docs)
        print(f"[OK] Mirrored {len(docs)} document(s) to Atlas -> {db_name}.{col_name}")
        client.close()
    except PyMongoError as exc:
        print(f"[WARN] Atlas mirror failed: {type(exc).__name__} - {exc}")


# ------------------------------------------------------------------ store ---
def save_report(document):
    """Persist the checklist to MongoDB and export the collection to JSON."""
    target = LOCAL_URI if results.get("MongoDB (Local/Docker)") else ATLAS_URI
    exported = False

    if target:
        try:
            client = MongoClient(target, serverSelectionTimeoutMS=5000, **tls_opts(target))
            col = client["chapter02"]["setup_checks"]
            col.delete_many({})
            col.insert_one(dict(document))
            mirror_to_atlas(list(col.find({})), "chapter02", "setup_checks")
            docs = list(col.find({}))
            for d in docs:
                d["_id"] = str(d["_id"])
            out = HERE / "activity_2_1_environment_report.json"
            out.write_text(json.dumps(docs, indent=2, default=str))
            print(f"\n[OK] Stored in chapter02.setup_checks and exported -> {out.name}")
            exported = True
            client.close()
        except PyMongoError as exc:
            print(f"\n[WARN] Could not store report in MongoDB: {exc}")

    if not exported:                       # still produce the JSON artefact
        out = HERE / "activity_2_1_environment_report.json"
        out.write_text(json.dumps([document], indent=2, default=str))
        print(f"\n[WARN] No MongoDB reachable - wrote local JSON only -> {out.name}")


def main():
    banner("Activity 2.1 : Environment Setup Verification")

    print("Core components:")
    check_python()
    check_pymongo()
    check_mongo("MongoDB (Local/Docker)", LOCAL_URI)
    check_mongo("MongoDB Atlas", ATLAS_URI)
    check_docker()
    optional = check_optional_packages()

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    print("\n--- Summary ---")
    print(f"  Core checks passed : {passed}/{total}")
    if results.get("Python") and results.get("PyMongo Driver") and (
            results.get("MongoDB (Local/Docker)") or results.get("MongoDB Atlas")):
        print("  [OK] You are ready to start the labs!")
    else:
        print("  [WARN] Some required components are missing - see the hints above.")

    save_report({
        "activity": "2.1 Environment Setup Verification",
        "checked_at": datetime.now(timezone.utc),
        "host": platform.node(),
        "python_version": sys.version.split()[0],
        "checks": [{"component": k, "passed": v, "detail": details[k]}
                   for k, v in results.items()],
        "optional_packages": optional,
        "core_checks_passed": f"{passed}/{total}",
    })

    banner("Activity 2.1 Complete")


if __name__ == "__main__":
    main()
