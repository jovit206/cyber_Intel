"""
Activity 2.1 - Try It Yourself
"Try running both Atlas and Docker MongoDB simultaneously. Write a script that
 connects to both and compares their server version information using the
 server_info() command."

Opens TWO MongoClient connections at the same time - one to the self-managed
local MongoDB and one to managed MongoDB Atlas - calls server_info() on each,
and prints a side-by-side comparison of the two deployments.

The local side is auto-detected: if a Docker container is publishing the port
it is labelled DOCKER, otherwise NATIVE (a mongod running directly on the host).

The full comparison is written to both deployments (chapter02.server_comparison)
and exported to activity_2_1_server_comparison.json.

Run:  python activity_2_1_try_it_yourself.py
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
DOCKER_URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017")
ATLAS_URI = os.getenv("MONGO_URI_ATLAS")


def tls_opts(uri):
    """macOS Python ships without system root CAs, so hand TLS (Atlas) connections
    certifi's bundle. Plain mongodb:// connections need nothing."""
    if uri and uri.startswith("mongodb+srv://"):
        return {"tlsCAFile": certifi.where()}
    return {}

# server_info() keys we want to line up against each other
FIELDS = [
    ("version",            "Server version"),
    ("gitVersion",         "Git version"),
    ("versionArray",       "Version array"),
    ("openssl",            "OpenSSL"),
    ("javascriptEngine",   "JS engine"),
    ("bits",               "Architecture (bits)"),
    ("debug",              "Debug build"),
    ("maxBsonObjectSize",  "Max BSON object size"),
    ("storageEngines",     "Storage engines"),
    ("modules",            "Modules"),
    ("allocator",          "Allocator"),
]


def banner(text):
    print("\n" + "=" * 78)
    print(f" {text}")
    print("=" * 78)


def detect_local_flavour(uri):
    """Is the local MongoDB a Docker container, or a mongod on the host?"""
    port = uri.rsplit(":", 1)[-1].split("/")[0]
    try:
        ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Ports}}"],
                            capture_output=True, text=True, timeout=8)
        for line in ps.stdout.strip().splitlines():
            name, image, ports = (line.split("\t") + ["", ""])[:3]
            if f":{port}->" in ports:
                return f"DOCKER ({image})", {"deployment_type": "Docker container",
                                             "container": name, "image": image}
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return "LOCAL (native mongod)", {"deployment_type": "Self-managed mongod on host"}


def connect(label, uri):
    """Open a connection and pull server_info(). Returns (client, info) or (None, None)."""
    if not uri:
        print(f"[SKIP] {label}: no URI configured (set MONGO_URI_ATLAS in .env)")
        return None, None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=8000, **tls_opts(uri))
        client.admin.command("ping")
        info = client.server_info()
        print(f"[OK]   {label}: connected - MongoDB {info['version']}")
        return client, info
    except PyMongoError as exc:
        print(f"[FAIL] {label}: {type(exc).__name__} - {exc}")
        return None, None


def deployment_facts(client):
    """Extra context that server_info() does not return."""
    facts = {}
    try:
        status = client.admin.command("serverStatus")
        facts["host"] = status.get("host")
        facts["process"] = status.get("process")
        facts["uptime_seconds"] = int(status.get("uptime", 0))
        facts["storage_engine"] = status.get("storageEngine", {}).get("name")
        facts["current_connections"] = status.get("connections", {}).get("current")
    except PyMongoError:
        facts["note"] = "serverStatus not permitted on this deployment"
    try:
        hello = client.admin.command("hello")
        facts["topology"] = "replica set" if hello.get("setName") else "standalone"
        facts["replica_set"] = hello.get("setName")
        facts["is_writable_primary"] = hello.get("isWritablePrimary")
    except PyMongoError:
        pass
    facts["databases"] = sorted(client.list_database_names())
    return facts


def cell(value, width=26):
    """Format one table cell, truncating anything too wide to keep columns aligned."""
    text = str(value)
    return text if len(text) <= width else text[: width - 3] + "..."


def row(label, left, right):
    print(f"  {label:<24} | {cell(left):<26} | {cell(right):<26}")


def compare(docker_info, atlas_info, docker_facts, atlas_facts, local_label="LOCAL"):
    banner("SIDE-BY-SIDE : server_info() comparison")
    row("Property", local_label, "ATLAS (managed cloud)")
    print("  " + "-" * 24 + "-+-" + "-" * 26 + "-+-" + "-" * 26)

    differences = []
    for key, label in FIELDS:
        d = (docker_info or {}).get(key, "n/a")
        a = (atlas_info or {}).get(key, "n/a")
        if isinstance(d, list):
            d = ", ".join(map(str, d)) or "-"
        if isinstance(a, list):
            a = ", ".join(map(str, a)) or "-"
        row(label, d, a)
        if docker_info and atlas_info and d != a:
            differences.append({"property": label, "local": d, "atlas": a})

    print("\n  " + "-" * 24 + "-+-" + "-" * 26 + "-+-" + "-" * 26)
    for key in ["host", "topology", "replica_set", "storage_engine",
                "uptime_seconds", "current_connections"]:
        row(key.replace("_", " ").title(),
            (docker_facts or {}).get(key, "n/a"),
            (atlas_facts or {}).get(key, "n/a"))

    banner("DIFFERENCES FOUND")
    if not (docker_info and atlas_info):
        print("  Only one deployment was reachable - cannot diff.")
    elif not differences:
        print("  Both deployments report identical server_info() values.")
    else:
        for diff in differences:
            print(f"  * {diff['property']}")
            print(f"      Local  : {diff['local']}")
            print(f"      Atlas  : {diff['atlas']}")
    return differences


def persist(document, clients):
    """Write the comparison into every reachable deployment, then export to JSON."""
    for label, client in clients:
        if client is None:
            continue
        try:
            col = client["chapter02"]["server_comparison"]
            col.delete_many({})
            col.insert_one(dict(document))
            print(f"  [OK] stored in {label} -> chapter02.server_comparison")
        except PyMongoError as exc:
            print(f"  [WARN] could not store in {label}: {exc}")

    out = HERE / "activity_2_1_server_comparison.json"
    out.write_text(json.dumps(document, indent=2, default=str))
    print(f"  [OK] exported -> {out.name}")


def main():
    LOCAL_LABEL, local_flavour = detect_local_flavour(DOCKER_URI)
    kind = "Docker" if LOCAL_LABEL.startswith("DOCKER") else "Local"
    banner(f"Activity 2.1 Try It Yourself : Atlas vs {kind} MongoDB - server_info()")

    print("Opening BOTH connections simultaneously...\n")
    docker_client, docker_info = connect(f"{LOCAL_LABEL:<22}", DOCKER_URI)
    atlas_client, atlas_info = connect(f"{'ATLAS (managed cloud)':<22}", ATLAS_URI)

    if not docker_client and not atlas_client:
        print("\n[ERROR] Neither deployment is reachable. Start Docker with:")
        print("  docker run -d -p 27017:27017 --name mongodb mongo:7")
        print("and set MONGO_URI_ATLAS in ../.env for the Atlas cluster.")
        return

    docker_facts = deployment_facts(docker_client) if docker_client else {}
    docker_facts.update(local_flavour)
    atlas_facts = deployment_facts(atlas_client) if atlas_client else {}

    differences = compare(docker_info, atlas_info, docker_facts, atlas_facts, LOCAL_LABEL)

    banner("PERSISTING RESULT")
    persist({
        "activity": "2.1 Try It Yourself - Atlas vs Docker server_info() comparison",
        "compared_at": datetime.now(timezone.utc),
        "local": {"uri": DOCKER_URI, "label": LOCAL_LABEL,
                  "server_info": docker_info, "facts": docker_facts},
        "atlas": {"uri": "<hidden - credentials redacted>", "label": "ATLAS (managed cloud)",
                  "server_info": atlas_info, "facts": atlas_facts},
        "differences": differences,
        "both_connected": bool(docker_client and atlas_client),
    }, [(LOCAL_LABEL, docker_client), ("ATLAS", atlas_client)])

    for client in (docker_client, atlas_client):
        if client:
            client.close()

    banner("Activity 2.1 Try It Yourself Complete")


if __name__ == "__main__":
    main()
