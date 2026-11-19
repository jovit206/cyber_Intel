# Lab 2 - Cloud Computing Primer and MongoDB Environment Setup

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 2 activities) |
| **Executed on** | 2026-11-19T14:22:44+00:00 on `LAPTOP-DSDFIQOI`, Windows 11 (10.0.26300) |
| **Software** | Python 3.12.10, pymongo 4.20.0, docker: MongoDB 7.0.45 (linux); native: MongoDB 8.3.7 (windows); Atlas: not configured |

> **Own work.** All code and results in `lab-2/jovit_24BTRCL089/` are my own and were produced by running the
> scripts below on my machine against my own MongoDB deployments. The `lab-2/Chapter02_Submission/` folder is a
> reference submission by another student, read only to understand the activities; none of its code, results or
> credentials are used. In particular its `.env` file contains another person's MongoDB Atlas login - it was not
> used, and this lab reads connection strings only from my own git-ignored `.env`.

## 1. Experiment title

Setting up and verifying a MongoDB development environment (Docker, native and cloud deployments) and performing
CRUD operations with the PyMongo driver.

## 2. Aim

To install and verify the tools needed for the MongoDB labs, compare different MongoDB deployment models, and use
Python to create, read, update and delete documents.

## 3. Objectives

1. Verify Python, PyMongo, Docker, a Docker-hosted MongoDB, a native MongoDB and (optionally) MongoDB Atlas, and
   store the verification report in MongoDB (Activity 2.1).
2. Connect to several MongoDB deployments at the same time and compare their `server_info()` (Activity 2.1, Try it
   yourself).
3. Implement bookstore CRUD operations on embedded documents and arrays with PyMongo (Activity 2.2).
4. Increase the price of every book published before 2010 by 10% and verify it (Activity 2.2, Try it yourself).

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | Windows 11 (10.0.26300) |
| Machine | `LAPTOP-DSDFIQOI` (laptop) |
| Python / driver | Python 3.12.10, pymongo 4.20.0 (`pip install -r requirements.txt`) |
| Container runtime | Docker Desktop, engine 29.8.2 (linux/amd64) |
| MongoDB deployments | docker: MongoDB 7.0.45 (linux); native: MongoDB 8.3.7 (windows); Atlas: not configured |
| GUI (optional) | MongoDB Compass |
| Database used | `jovit_lab02` (collections `environment_checks`, `server_comparison`, `books`, `books_price_update`) |

## 5. Theory

**Cloud service models.** *IaaS* provides virtual machines, storage and networks (e.g. AWS EC2) that you manage
yourself; *PaaS* provides a managed platform where you deploy applications or databases without managing servers;
*SaaS* delivers finished software over the internet. **MongoDB Atlas** is a fully managed Database-as-a-Service:
the provider runs replica sets, backups, monitoring, patching and scaling, and the user connects with a
`mongodb+srv://` connection string over TLS.

**Deployment options for MongoDB.**
- *Native installation* - `mongod` runs directly on the host OS (here, a Windows service on port 27017). Simple
  and fast, but tied to the machine's configuration.
- *Docker container* - `mongod` runs from the official `mongo` image in an isolated container with its data in a
  named volume. Versions can be switched by changing the image tag, and the same setup reproduces on any machine.
- *Atlas (cloud)* - managed, replicated and reachable from anywhere; higher network latency than localhost.

**PyMongo.** The official Python driver. A `MongoClient` holds a connection pool; databases and collections are
created lazily on first write. `server_info()` returns the server's build information, and the `hello` command
reports topology (standalone, replica set, mongos) and wire-protocol version.

**CRUD in MongoDB.** *Create*: `insert_one`, `insert_many`. *Read*: `find`, `find_one` with filters (`$gte`,
`$lt`, `$in` ...), projections and sorting; dot notation (`publisher.name`) queries embedded documents, and a
filter on an array field matches any element. *Update*: `update_one` / `update_many` with operators such as
`$set`, `$inc`, `$mul`, `$addToSet`, or an aggregation-pipeline update for computed values; `upsert=True` inserts
when nothing matches. *Delete*: `delete_one`, `delete_many`. A unique index (here on `isbn`) makes the server
reject duplicates with `DuplicateKeyError`.

## 6. Procedure

1. Install Python packages: `python -m pip install -r requirements.txt`.
2. Start the Docker MongoDB:
   `docker run -d --name jovit-lab02-mongo -p 127.0.0.1:27028:27017 -v jovit-lab02-data:/data/db mongo:7.0 --wiredTigerCacheSizeGB 0.25`
   (port 27028, because 27017 is already used by the native MongoDB service).
3. Optionally copy `.env.example` to `.env` and add your own Atlas connection string (`MONGO_URI_ATLAS`).
4. Run the activities (or `run_all.ps1`, which also starts the container if needed):
   `python activity_2_1_environment_check.py`, `python activity_2_1_try_it_yourself.py`,
   `python activity_2_2_bookstore_crud.py`, `python activity_2_2_try_it_yourself.py`.
5. Each script prints its results and saves them in `results/` (JSON + console output); the CRUD activities also
   export their final collections as JSON.
6. Open MongoDB Compass at `mongodb://localhost:27028`, database `jovit_lab02`, to inspect the collections - e.g.
   filter `books_price_update` with `{ "year": { "$lt": 2010 } }`.
7. Run `python build_report.py` to regenerate this report.

## 7. Source code

The complete source of every file used in this lab.

### `common.py`

```python
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
```

### `books.py`

```python
"""Seed data for the Chapter 2 bookstore activities (Jovit Chawla, 24BTRCL089).

Titles, authors, first-publication years and publishers are those of real, well-known books. The `price_inr`
and `stock` values are store values chosen for this lab exercise, not market data.
"""

BOOKS = [
    {"isbn": "978-0132350884", "title": "Clean Code", "authors": ["Robert C. Martin"], "year": 2008,
     "publisher": {"name": "Prentice Hall", "country": "USA"}, "tags": ["software engineering", "best practices"],
     "price_inr": 650.0, "stock": 12},
    {"isbn": "978-0201616224", "title": "The Pragmatic Programmer", "authors": ["Andrew Hunt", "David Thomas"],
     "year": 1999, "publisher": {"name": "Addison-Wesley", "country": "USA"},
     "tags": ["software engineering", "career"], "price_inr": 720.0, "stock": 7},
    {"isbn": "978-1449373320", "title": "Designing Data-Intensive Applications", "authors": ["Martin Kleppmann"],
     "year": 2017, "publisher": {"name": "O'Reilly Media", "country": "USA"},
     "tags": ["databases", "distributed systems"], "price_inr": 1450.0, "stock": 5},
    {"isbn": "978-0262033848", "title": "Introduction to Algorithms", "authors": ["Thomas H. Cormen", "Charles E. Leiserson",
     "Ronald L. Rivest", "Clifford Stein"], "year": 1990, "publisher": {"name": "MIT Press", "country": "USA"},
     "tags": ["algorithms", "textbook"], "price_inr": 1250.0, "stock": 9},
    {"isbn": "978-0201835953", "title": "The Mythical Man-Month", "authors": ["Frederick P. Brooks Jr."], "year": 1975,
     "publisher": {"name": "Addison-Wesley", "country": "USA"}, "tags": ["software engineering", "management"],
     "price_inr": 540.0, "stock": 4},
    {"isbn": "978-0262035613", "title": "Deep Learning", "authors": ["Ian Goodfellow", "Yoshua Bengio", "Aaron Courville"],
     "year": 2016, "publisher": {"name": "MIT Press", "country": "USA"}, "tags": ["machine learning", "textbook"],
     "price_inr": 1600.0, "stock": 6},
    {"isbn": "978-0201485677", "title": "Refactoring", "authors": ["Martin Fowler"], "year": 1999,
     "publisher": {"name": "Addison-Wesley", "country": "USA"}, "tags": ["software engineering", "best practices"],
     "price_inr": 820.0, "stock": 3},
    {"isbn": "978-1934356920", "title": "Seven Databases in Seven Weeks", "authors": ["Eric Redmond", "Jim R. Wilson"],
     "year": 2012, "publisher": {"name": "Pragmatic Bookshelf", "country": "USA"}, "tags": ["databases", "nosql"],
     "price_inr": 780.0, "stock": 8},
    {"isbn": "978-1593276034", "title": "Python Crash Course", "authors": ["Eric Matthes"], "year": 2015,
     "publisher": {"name": "No Starch Press", "country": "USA"}, "tags": ["python", "beginner"],
     "price_inr": 560.0, "stock": 15},
    {"isbn": "978-8173711466", "title": "Wings of Fire", "authors": ["A. P. J. Abdul Kalam", "Arun Tiwari"], "year": 1999,
     "publisher": {"name": "Universities Press", "country": "India"}, "tags": ["autobiography", "india"],
     "price_inr": 299.0, "stock": 20},
]
```

### `activity_2_1_environment_check.py`

```python
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
```

### `activity_2_1_try_it_yourself.py`

```python
"""Activity 2.1 (Try it yourself) - Connect to several MongoDB deployments at the same time and compare them.

Author: Jovit Chawla (24BTRCL089)

Task: run more than one MongoDB deployment simultaneously, connect to all of them from one script and compare
their server_info(). Deployments used: the jovit-lab02-mongo Docker container, the native MongoDB Windows
service and - when MONGO_URI_ATLAS is configured - MongoDB Atlas.

All clients are opened concurrently (one thread each). For every deployment the script records server_info()
and hello fields plus a median round-trip latency, prints a side-by-side table, stores the comparison in each
reachable deployment (jovit_lab02.server_comparison) and exports it to results/.
"""
from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from common import DATABASE, Recorder, client_for, deployments, mask

FIELDS = [
    ("version", lambda i, h: i.get("version")),
    ("gitVersion", lambda i, h: (i.get("gitVersion") or "")[:12]),
    ("target OS", lambda i, h: i.get("buildEnvironment", {}).get("target_os")),
    ("distmod", lambda i, h: i.get("buildEnvironment", {}).get("distmod") or "-"),
    ("bits", lambda i, h: i.get("bits")),
    ("allocator", lambda i, h: i.get("allocator")),
    ("OpenSSL", lambda i, h: (i.get("openssl", {}) or {}).get("running", "-")),
    ("storage engines", lambda i, h: ", ".join(i.get("storageEngines", []))),
    ("max wire version", lambda i, h: h.get("maxWireVersion")),
    ("topology", lambda i, h: h.get("setName") or ("mongos" if h.get("msg") == "isdbgrid" else "standalone")),
    ("writable primary", lambda i, h: h.get("isWritablePrimary")),
    ("max BSON size (MB)", lambda i, h: round(h.get("maxBsonObjectSize", 0) / 2**20)),
]


def inspect(name: str, uri: str) -> dict:
    client = client_for(uri)
    try:
        client.admin.command("ping")
        samples = []
        for _ in range(20):
            started = time.perf_counter()
            client.admin.command("ping")
            samples.append((time.perf_counter() - started) * 1000)
        info, hello = client.server_info(), client.admin.command("hello")
        row = {label: fn(info, hello) for label, fn in FIELDS}
        row["ping ms (median of 20)"] = round(statistics.median(samples), 3)
        return {"name": name, "uri": mask(uri), "reachable": True, "fields": row, "client": client}
    except Exception as exc:  # noqa: BLE001
        client.close()
        return {"name": name, "uri": mask(uri), "reachable": False, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}


def main() -> None:
    rec = Recorder("activity_2_1_try_it_yourself", "Activity 2.1 (Try it yourself) - Compare MongoDB deployments")
    targets = deployments()
    if "atlas" not in targets:
        rec.log("  note: MONGO_URI_ATLAS is not configured, so Atlas is not part of this run")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda item: inspect(*item), targets.items()))
    rec.log(f"  opened {len(results)} connections concurrently in {(time.perf_counter() - started) * 1000:.0f} ms")
    up = [r for r in results if r["reachable"]]
    for r in results:
        if not r["reachable"]:
            rec.log(f"  {r['name']}: UNREACHABLE ({r['uri']}) {r['error']}")

    rec.section("Side-by-side comparison (live server_info() / hello)")
    labels = [label for label, _ in FIELDS] + ["ping ms (median of 20)"]
    rec.log(f"  {'field':<24}" + "".join(f"{r['name'] + ' ' + r['uri']:<36}" for r in up))
    differences = []
    for label in labels:
        values = [r["fields"][label] for r in up]
        if len({str(v) for v in values}) > 1 and label != "ping ms (median of 20)":
            differences.append(label)
        rec.log(f"  {label:<24}" + "".join(f"{str(v):<36}" for v in values))
    rec.section("What differs")
    rec.log(f"  fields that differ between deployments: {', '.join(differences) or 'none'}")

    comparison = {"run_at": rec.environment["run_at_utc"], "student": rec.environment["student"],
                  "deployments": [{k: v for k, v in r.items() if k != "client"} for r in results],
                  "differences": differences}
    for r in up:
        coll = r["client"][DATABASE]["server_comparison"]
        inserted = coll.insert_one(dict(comparison)).inserted_id
        rec.log(f"  stored comparison in {r['name']}:{DATABASE}.server_comparison (_id {inserted})")
        r["client"].close()
    rec.data = comparison
    rec.save()


if __name__ == "__main__":
    main()
```

### `activity_2_2_bookstore_crud.py`

```python
"""Activity 2.2 - Bookstore CRUD with PyMongo.

Author: Jovit Chawla (24BTRCL089)

Runs Create, Read, Update and Delete operations on jovit_lab02.books (Docker deployment by default; pass
--deployment native to use the Windows service). Book documents embed the publisher as a sub-document and keep
authors/tags as arrays. Every step checks its own result against the database, so the run fails if an
operation did not do what it claims. The final collection is exported to results/activity_2_2_books.json.
"""
from __future__ import annotations

import argparse
import copy
import json

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from books import BOOKS
from common import RESULTS_DIR, Recorder, connect

PROJECTION = {"_id": 0, "title": 1, "year": 1, "price_inr": 1}


def show(rec: Recorder, label: str, docs: list[dict]) -> None:
    rec.log(f"  {label} ({len(docs)}):")
    for doc in docs:
        rec.log(f"    - {json.dumps(doc, ensure_ascii=False, default=str)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", default="docker", choices=["docker", "native", "atlas"])
    args = parser.parse_args()
    client, db = connect(args.deployment)
    rec = Recorder("activity_2_2", "Activity 2.2 - Bookstore CRUD Operations")
    rec.log(f"Deployment: {args.deployment} (MongoDB {client.server_info()['version']}), collection {db.name}.books")
    db.drop_collection("books")
    books = db["books"]
    books.create_index("isbn", unique=True)
    books.create_index([("tags", ASCENDING)])
    steps: dict[str, dict] = {}

    rec.section("CREATE")
    first, rest = copy.deepcopy(BOOKS[0]), copy.deepcopy(BOOKS[1:])
    one = books.insert_one(first)
    many = books.insert_many(rest)
    rec.log(f"  insert_one -> _id {one.inserted_id}; insert_many -> {len(many.inserted_ids)} ids")
    try:
        books.insert_one({"isbn": BOOKS[0]["isbn"], "title": "duplicate"})
        duplicate = "accepted (unexpected)"
    except DuplicateKeyError:
        duplicate = "rejected with DuplicateKeyError (unique index on isbn)"
    rec.log(f"  inserting a second book with ISBN {BOOKS[0]['isbn']}: {duplicate}")
    total = books.count_documents({})
    assert total == len(BOOKS), total
    steps["create"] = {"inserted": total, "duplicate_isbn": duplicate}

    rec.section("READ")
    by_isbn = books.find_one({"isbn": "978-1449373320"}, {"_id": 0, "title": 1, "publisher.name": 1, "authors": 1})
    rec.log(f"  find_one by ISBN: {by_isbn}")
    db_books = list(books.find({"tags": "databases"}, PROJECTION).sort("year", ASCENDING))
    show(rec, "books tagged 'databases', oldest first", db_books)
    mit = list(books.find({"publisher.name": "MIT Press"}, PROJECTION))
    show(rec, "query on the embedded publisher: MIT Press", mit)
    range_ = list(books.find({"year": {"$gte": 1995, "$lte": 2010}, "price_inr": {"$lt": 800}}, PROJECTION)
                  .sort([("price_inr", DESCENDING)]))
    show(rec, "published 1995-2010 and under INR 800, most expensive first", range_)
    multi_author = books.count_documents({"authors.2": {"$exists": True}})
    rec.log(f"  books with three or more authors: {multi_author}")
    per_publisher = list(books.aggregate([{"$group": {"_id": "$publisher.name", "titles": {"$sum": 1},
                                                      "stock": {"$sum": "$stock"}}}, {"$sort": {"titles": -1, "_id": 1}}]))
    rec.log("  titles per publisher: " + ", ".join(f"{p['_id']}={p['titles']}" for p in per_publisher))
    assert len(db_books) == 2 and len(mit) == 2
    steps["read"] = {"by_isbn": by_isbn, "databases": db_books, "mit_press": mit, "range": range_,
                     "three_plus_authors": multi_author, "per_publisher": per_publisher}

    rec.section("UPDATE")
    sold = books.update_one({"isbn": "978-1593276034", "stock": {"$gte": 2}}, {"$inc": {"stock": -2}})
    tagged = books.update_one({"isbn": "978-1449373320"}, {"$addToSet": {"tags": "nosql"}})
    tagged_again = books.update_one({"isbn": "978-1449373320"}, {"$addToSet": {"tags": "nosql"}})
    moved = books.update_many({"publisher.name": "Addison-Wesley"}, {"$set": {"publisher.imprint": "Pearson"}})
    upsert = books.update_one({"isbn": "978-0596517748"},
                              {"$setOnInsert": {"title": "JavaScript: The Good Parts", "authors": ["Douglas Crockford"],
                                                "year": 2008, "publisher": {"name": "O'Reilly Media", "country": "USA"},
                                                "tags": ["javascript"], "price_inr": 480.0, "stock": 0}},
                              upsert=True)
    rec.log(f"  sell 2 copies of Python Crash Course ($inc): matched {sold.matched_count}, modified {sold.modified_count}")
    rec.log(f"  add tag 'nosql' ($addToSet): modified {tagged.modified_count}; repeating it modified "
            f"{tagged_again.modified_count} (no duplicate tag)")
    rec.log(f"  set publisher.imprint on all Addison-Wesley books (update_many): modified {moved.modified_count}")
    rec.log(f"  upsert a new title by ISBN: upserted_id {upsert.upserted_id}")
    after = books.find_one({"isbn": "978-1593276034"}, {"_id": 0, "stock": 1})
    tags = books.find_one({"isbn": "978-1449373320"}, {"_id": 0, "tags": 1})["tags"]
    assert after["stock"] == 13 and tags.count("nosql") == 1 and moved.modified_count == 3
    rec.log(f"  verified: stock now {after['stock']}, tags {tags}")
    steps["update"] = {"stock_after_sale": after["stock"], "tags": tags, "imprint_modified": moved.modified_count,
                       "upserted": upsert.upserted_id is not None}

    rec.section("DELETE")
    out_of_stock = books.delete_many({"stock": 0})
    one_gone = books.delete_one({"isbn": "978-0201835953"})
    remaining = books.count_documents({})
    rec.log(f"  delete_many out-of-stock books: deleted {out_of_stock.deleted_count}")
    rec.log(f"  delete_one The Mythical Man-Month: deleted {one_gone.deleted_count}")
    assert remaining == len(BOOKS) - 1 and books.count_documents({"isbn": "978-0201835953"}) == 0
    rec.log(f"  verified: {remaining} books remain, deleted ISBN no longer found")
    steps["delete"] = {"deleted_out_of_stock": out_of_stock.deleted_count, "deleted_one": one_gone.deleted_count,
                       "remaining": remaining}

    final = list(books.find({}, {"_id": 0}).sort("isbn", ASCENDING))
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "activity_2_2_books.json").write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    rec.log(f"\n  exported {len(final)} books to results/activity_2_2_books.json")
    rec.log("  all CRUD checks passed")
    rec.data = {"deployment": args.deployment, "steps": steps, "final_count": len(final)}
    rec.save()


if __name__ == "__main__":
    main()
```

### `activity_2_2_try_it_yourself.py`

```python
"""Activity 2.2 (Try it yourself) - Raise the price of every book published before 2010 by 10 percent.

Author: Jovit Chawla (24BTRCL089)

Uses its own collection (jovit_lab02.books_price_update) so it never disturbs Activity 2.2's data. Steps:
  1. seed the books and snapshot every price
  2. one update_many with an aggregation-pipeline update, so the new price is rounded to 2 decimals on the
     server and the change is stamped:  price = round(price * 1.10, 2)  for  {year: {$lt: 2010}}
  3. guard against applying the increase twice ({price_revised_2010: {$ne: true}} in the filter)
  4. verify book by book: every pre-2010 price equals round(old * 1.10, 2), every later book is unchanged
  5. print the Compass filter to inspect the result in the GUI
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json

from books import BOOKS
from common import RESULTS_DIR, Recorder, connect

FILTER = {"year": {"$lt": 2010}, "price_revised_2010": {"$ne": True}}
PIPELINE = [{"$set": {"old_price_inr": "$price_inr",
                      "price_inr": {"$round": [{"$multiply": ["$price_inr", 1.10]}, 2]},
                      "price_revised_2010": True, "price_revised_at": "$$NOW"}}]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", default="docker", choices=["docker", "native", "atlas"])
    args = parser.parse_args()
    client, db = connect(args.deployment)
    rec = Recorder("activity_2_2_try_it_yourself", "Activity 2.2 (Try it yourself) - 10% price rise for pre-2010 books")
    rec.log(f"Deployment: {args.deployment} (MongoDB {client.server_info()['version']}), collection "
            f"{db.name}.books_price_update")
    db.drop_collection("books_price_update")
    coll = db["books_price_update"]
    coll.create_index("isbn", unique=True)
    coll.insert_many(copy.deepcopy(BOOKS))
    before = {b["isbn"]: (b["title"], b["year"], b["price_inr"]) for b in coll.find({}, {"_id": 0})}
    eligible = sum(1 for _, year, _ in before.values() if year < 2010)
    rec.log(f"  seeded {len(before)} books; {eligible} were published before 2010")

    rec.section("Update")
    rec.log(f"  filter:   {json.dumps(FILTER)}")
    rec.log("  update:   [{$set: {old_price_inr: '$price_inr', price_inr: {$round: [{$multiply: ['$price_inr', 1.10]}, 2]}, "
            "price_revised_2010: true, price_revised_at: '$$NOW'}}]")
    result = coll.update_many(FILTER, PIPELINE)
    rec.log(f"  matched {result.matched_count}, modified {result.modified_count}")
    again = coll.update_many(FILTER, PIPELINE)
    rec.log(f"  running the same update again: matched {again.matched_count}, modified {again.modified_count} "
            "(the guard stops a second 10% increase)")

    rec.section("Verification")
    rows, problems = [], []
    for doc in coll.find({}, {"_id": 0}).sort([("year", 1), ("title", 1)]):
        title, year, old = before[doc["isbn"]]
        expected = round(old * 1.10, 2) if year < 2010 else old
        ok = abs(doc["price_inr"] - expected) < 1e-9
        rows.append({"title": title, "year": year, "before": old, "after": doc["price_inr"], "expected": expected,
                     "changed": year < 2010, "ok": ok})
        if not ok:
            problems.append(title)
        rec.log(f"  {'OK  ' if ok else 'FAIL'} {year}  {title:<40} {old:>8.2f} -> {doc['price_inr']:>8.2f}"
                f"{'  (+10%)' if year < 2010 else '  (unchanged)'}")
    rec.log(f"  {len(rows) - len(problems)}/{len(rows)} prices correct; modified count matches eligible books: "
            f"{result.modified_count == eligible}")
    total_before = round(sum(p for _, _, p in before.values()), 2)
    total_after = round(sum(r["after"] for r in rows), 2)
    rec.log(f"  catalogue value: INR {total_before:,.2f} -> INR {total_after:,.2f}")

    rec.section("Check in MongoDB Compass")
    rec.log(f"  connect to {('mongodb://localhost:27028' if args.deployment == 'docker' else 'your deployment')}, open "
            f"{db.name} > books_price_update and use:")
    rec.log('    Filter:  { "year": { "$lt": 2010 } }      -> every result has price_revised_2010: true and old_price_inr')
    rec.log('    Filter:  { "year": { "$gte": 2010 } }     -> no result has price_revised_2010')

    final = list(coll.find({}, {"_id": 0}).sort("isbn", 1))
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "activity_2_2_try_it_yourself_books.json").write_text(
        json.dumps(final, indent=2, ensure_ascii=False, default=lambda v: v.isoformat() if isinstance(v, dt.datetime) else str(v)),
        encoding="utf-8")
    rec.log(f"\n  exported {len(final)} books to results/activity_2_2_try_it_yourself_books.json")
    rec.data = {"deployment": args.deployment, "eligible": eligible, "matched": result.matched_count,
                "modified": result.modified_count, "second_run_modified": again.modified_count, "rows": rows,
                "problems": problems, "catalogue_before": total_before, "catalogue_after": total_after}
    rec.save()
    if problems or result.modified_count != eligible or again.modified_count != 0:
        raise SystemExit("verification failed")


if __name__ == "__main__":
    main()
```

### `build_report.py`

```python
"""Build REPORT.md for Lab 2 from the saved results/ files and the source code in this folder.

Author: Jovit Chawla (24BTRCL089). Every value is read from results/*.json written by the activity scripts;
nothing is invented. Run after the activities (run_all.ps1 does both).
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ACTIVITIES = [
    ("activity_2_1", "activity_2_1_environment_check.py", "Activity 2.1 - Environment Setup Verification"),
    ("activity_2_1_try_it_yourself", "activity_2_1_try_it_yourself.py", "Activity 2.1 (Try it yourself) - Compare MongoDB Deployments"),
    ("activity_2_2", "activity_2_2_bookstore_crud.py", "Activity 2.2 - Bookstore CRUD Operations"),
    ("activity_2_2_try_it_yourself", "activity_2_2_try_it_yourself.py", "Activity 2.2 (Try it yourself) - 10% Price Rise Before 2010"),
]


def load(name: str) -> dict:
    path = RESULTS / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path.name} is missing - run the activity scripts first (run_all.ps1).")
    return json.loads(path.read_text(encoding="utf-8"))


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(out)


def results_2_1(m: dict) -> str:
    rows = [[c["check"], "yes" if c["required"] else "no", c["status"], c["detail"]] for c in m["checks"]]
    return (table(["Check", "Required", "Status", "Evidence"], rows) +
            f"\n\nRequired checks failed: **{', '.join(m['required_failed']) or 'none'}**. "
            f"The run was stored in `jovit_lab02.environment_checks` (`_id` {m.get('stored_id', '-')}).")


def results_2_1_tiy(m: dict) -> str:
    up = [d for d in m["deployments"] if d["reachable"]]
    labels = list(up[0]["fields"]) if up else []
    rows = [[label] + [d["fields"][label] for d in up] for label in labels]
    down = [f"{d['name']} ({d['error']})" for d in m["deployments"] if not d["reachable"]]
    note = "" if any(d["name"] == "atlas" for d in m["deployments"]) else (
        "\n\nAtlas was **not part of this run**: no `MONGO_URI_ATLAS` is configured. Adding your own Atlas "
        "connection string to `.env` and re-running adds an Atlas column automatically.")
    return (table(["Field"] + [f"{d['name']} ({d['uri']})" for d in up], rows) +
            f"\n\nFields that differ: **{', '.join(m['differences']) or 'none'}**." +
            (f" Unreachable: {', '.join(down)}." if down else "") + note)


def results_2_2(m: dict) -> str:
    s = m["steps"]
    rows = [["Create", f"{s['create']['inserted']} books inserted (insert_one + insert_many); duplicate ISBN "
                       f"{s['create']['duplicate_isbn']}"],
            ["Read", f"{len(s['read']['databases'])} 'databases' books, {len(s['read']['mit_press'])} MIT Press books "
                     f"(embedded field query), {len(s['read']['range'])} in the year/price range, "
                     f"{s['read']['three_plus_authors']} with 3+ authors"],
            ["Update", f"stock after selling 2 copies = {s['update']['stock_after_sale']}; tags {s['update']['tags']}; "
                       f"imprint set on {s['update']['imprint_modified']} books; upsert created a book: {s['update']['upserted']}"],
            ["Delete", f"{s['delete']['deleted_out_of_stock']} out-of-stock deleted, {s['delete']['deleted_one']} deleted by "
                       f"ISBN, {s['delete']['remaining']} remain"]]
    return table(["Operation", "Verified result"], rows) + f"\n\nDeployment: `{m['deployment']}`. Final collection: " \
        f"{m['final_count']} books, exported to `results/activity_2_2_books.json`."


def results_2_2_tiy(m: dict) -> str:
    rows = [[r["year"], r["title"], f"{r['before']:.2f}", f"{r['after']:.2f}", "+10%" if r["changed"] else "unchanged",
             "OK" if r["ok"] else "FAIL"] for r in m["rows"]]
    return (table(["Year", "Title", "Price before (INR)", "Price after (INR)", "Change", "Check"], rows) +
            f"\n\n`update_many` matched {m['matched']} and modified {m['modified']} books ({m['eligible']} were published "
            f"before 2010); repeating it modified {m['second_run_modified']}. Catalogue value INR "
            f"{m['catalogue_before']:,.2f} -> INR {m['catalogue_after']:,.2f}. Problems: {m['problems'] or 'none'}.")


BUILDERS = {"activity_2_1": results_2_1, "activity_2_1_try_it_yourself": results_2_1_tiy,
            "activity_2_2": results_2_2, "activity_2_2_try_it_yourself": results_2_2_tiy}

HEADER = """# Lab 2 - Cloud Computing Primer and MongoDB Environment Setup

| | |
|---|---|
| **Name** | Jovit Chawla |
| **Enrollment No.** | 24BTRCL089 |
| **Course** | NoSQL Databases using MongoDB (Chapter 2 activities) |
| **Executed on** | {run_at} on `{machine}`, {os} |
| **Software** | Python {python}, pymongo {pymongo}, {servers} |

> **Own work.** All code and results in `lab-2/jovit_24BTRCL089/` are my own and were produced by running the
> scripts below on my machine against my own MongoDB deployments. The `lab-2/Chapter02_Submission/` folder is a
> reference submission by another student, read only to understand the activities; none of its code, results or
> credentials are used. In particular its `.env` file contains another person's MongoDB Atlas login - it was not
> used, and this lab reads connection strings only from my own git-ignored `.env`.

## 1. Experiment title

Setting up and verifying a MongoDB development environment (Docker, native and cloud deployments) and performing
CRUD operations with the PyMongo driver.

## 2. Aim

To install and verify the tools needed for the MongoDB labs, compare different MongoDB deployment models, and use
Python to create, read, update and delete documents.

## 3. Objectives

1. Verify Python, PyMongo, Docker, a Docker-hosted MongoDB, a native MongoDB and (optionally) MongoDB Atlas, and
   store the verification report in MongoDB (Activity 2.1).
2. Connect to several MongoDB deployments at the same time and compare their `server_info()` (Activity 2.1, Try it
   yourself).
3. Implement bookstore CRUD operations on embedded documents and arrays with PyMongo (Activity 2.2).
4. Increase the price of every book published before 2010 by 10% and verify it (Activity 2.2, Try it yourself).

## 4. Software and hardware requirements

| Item | Used for this run |
|---|---|
| Operating system | {os} |
| Machine | `{machine}` (laptop) |
| Python / driver | Python {python}, pymongo {pymongo} (`pip install -r requirements.txt`) |
| Container runtime | Docker Desktop, engine {docker} |
| MongoDB deployments | {servers} |
| GUI (optional) | MongoDB Compass |
| Database used | `jovit_lab02` (collections `environment_checks`, `server_comparison`, `books`, `books_price_update`) |

## 5. Theory

**Cloud service models.** *IaaS* provides virtual machines, storage and networks (e.g. AWS EC2) that you manage
yourself; *PaaS* provides a managed platform where you deploy applications or databases without managing servers;
*SaaS* delivers finished software over the internet. **MongoDB Atlas** is a fully managed Database-as-a-Service:
the provider runs replica sets, backups, monitoring, patching and scaling, and the user connects with a
`mongodb+srv://` connection string over TLS.

**Deployment options for MongoDB.**
- *Native installation* - `mongod` runs directly on the host OS (here, a Windows service on port 27017). Simple
  and fast, but tied to the machine's configuration.
- *Docker container* - `mongod` runs from the official `mongo` image in an isolated container with its data in a
  named volume. Versions can be switched by changing the image tag, and the same setup reproduces on any machine.
- *Atlas (cloud)* - managed, replicated and reachable from anywhere; higher network latency than localhost.

**PyMongo.** The official Python driver. A `MongoClient` holds a connection pool; databases and collections are
created lazily on first write. `server_info()` returns the server's build information, and the `hello` command
reports topology (standalone, replica set, mongos) and wire-protocol version.

**CRUD in MongoDB.** *Create*: `insert_one`, `insert_many`. *Read*: `find`, `find_one` with filters (`$gte`,
`$lt`, `$in` ...), projections and sorting; dot notation (`publisher.name`) queries embedded documents, and a
filter on an array field matches any element. *Update*: `update_one` / `update_many` with operators such as
`$set`, `$inc`, `$mul`, `$addToSet`, or an aggregation-pipeline update for computed values; `upsert=True` inserts
when nothing matches. *Delete*: `delete_one`, `delete_many`. A unique index (here on `isbn`) makes the server
reject duplicates with `DuplicateKeyError`.

## 6. Procedure

1. Install Python packages: `python -m pip install -r requirements.txt`.
2. Start the Docker MongoDB:
   `docker run -d --name jovit-lab02-mongo -p 127.0.0.1:27028:27017 -v jovit-lab02-data:/data/db mongo:7.0 --wiredTigerCacheSizeGB 0.25`
   (port 27028, because 27017 is already used by the native MongoDB service).
3. Optionally copy `.env.example` to `.env` and add your own Atlas connection string (`MONGO_URI_ATLAS`).
4. Run the activities (or `run_all.ps1`, which also starts the container if needed):
   `python activity_2_1_environment_check.py`, `python activity_2_1_try_it_yourself.py`,
   `python activity_2_2_bookstore_crud.py`, `python activity_2_2_try_it_yourself.py`.
5. Each script prints its results and saves them in `results/` (JSON + console output); the CRUD activities also
   export their final collections as JSON.
6. Open MongoDB Compass at `mongodb://localhost:27028`, database `jovit_lab02`, to inspect the collections - e.g.
   filter `books_price_update` with `{{ "year": {{ "$lt": 2010 }} }}`.
7. Run `python build_report.py` to regenerate this report.
"""


def main() -> None:
    data = {name: load(name) for name, _, _ in ACTIVITIES}
    env = data["activity_2_1"]["environment"]
    checks = {c["check"]: c for c in data["activity_2_1"]["measurements"]["checks"]}
    servers = "; ".join(f"{d['name']}: MongoDB {d['fields']['version']} ({d['fields']['target OS']})"
                        for d in data["activity_2_1_try_it_yourself"]["measurements"]["deployments"] if d["reachable"])
    if not any(d["name"] == "atlas" for d in data["activity_2_1_try_it_yourself"]["measurements"]["deployments"]):
        servers += "; Atlas: not configured"
    docker = checks.get("Docker engine", {}).get("detail", "-").replace("server ", "")
    parts = [HEADER.format(run_at=env["run_at_utc"], machine=env["machine"], os=env["os"], python=env["python"],
                           pymongo=env["pymongo"], servers=servers, docker=docker)]
    parts.append("\n## 7. Source code\n\nThe complete source of every file used in this lab.\n")
    for file in ["common.py", "books.py"] + [s for _, s, _ in ACTIVITIES] + ["build_report.py", "run_all.ps1"]:
        lang = "powershell" if file.endswith(".ps1") else "python"
        parts.append(f"\n### `{file}`\n\n```{lang}\n{(HERE / file).read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 8. Execution output\n\nConsole output captured from the actual runs (`results/*.output.txt`).\n")
    for name, _, title in ACTIVITIES:
        parts.append(f"\n### {title}\n\n```text\n{(RESULTS / f'{name}.output.txt').read_text(encoding='utf-8').rstrip()}\n```\n")
    parts.append("\n## 9. Results\n\n" + "\n\n".join(f"### {title}\n\n{BUILDERS[name](data[name]['measurements'])}"
                                                    for name, _, title in ACTIVITIES))
    m21, tiy, crud, price = (data[n]["measurements"] for n, _, _ in ACTIVITIES)
    up = [d for d in tiy["deployments"] if d["reachable"]]
    conclusion = [
        f"- The environment check passed {m21['counts'].get('PASS', 0)} of {len(m21['checks'])} checks; required checks "
        f"failed: {', '.join(m21['required_failed']) or 'none'}. Atlas is "
        f"{checks['MongoDB Atlas']['status'].lower()}, and missing optional packages are only needed in later labs.",
        f"- {len(up)} deployments were used simultaneously. They differ in {', '.join(tiy['differences'])}, which shows "
        "that the same driver code talks to different server versions and builds (Linux container vs Windows service) "
        "through one wire protocol; localhost ping latency: " +
        ", ".join(f"{d['name']} {d['fields']['ping ms (median of 20)']} ms" for d in up) + ".",
        f"- All four CRUD operations behaved as expected on embedded documents and arrays, and the unique index "
        f"rejected a duplicate ISBN; {crud['final_count']} books remained.",
        f"- The targeted `update_many` raised exactly the {price['modified']} pre-2010 prices by 10% "
        f"({len(price['rows']) - len(price['problems'])}/{len(price['rows'])} prices verified), and the guard field "
        f"made a repeated run change {price['second_run_modified']} documents.",
    ]
    parts.append("\n\n## 10. Conclusion\n\n" + "\n".join(conclusion) + "\n")
    (HERE / "REPORT.md").write_text("".join(parts), encoding="utf-8")
    print(f"Wrote REPORT.md ({sum(len(p) for p in parts):,} characters)")


if __name__ == "__main__":
    main()
```

### `run_all.ps1`

```powershell
# Runs every Lab 2 activity, then rebuilds REPORT.md from the saved outputs.
# Usage (from this folder):  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_all.ps1 [-Python <path>]
param([string]$Python = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $Python) {
    $venv = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}
$running = docker inspect jovit-lab02-mongo --format "{{.State.Running}}" 2>$null
if ($running -ne "true") {
    Write-Host "Starting the jovit-lab02-mongo container..." -ForegroundColor Yellow
    docker start jovit-lab02-mongo | Out-Null
    Start-Sleep -Seconds 5
}
$scripts = "activity_2_1_environment_check.py", "activity_2_1_try_it_yourself.py",
           "activity_2_2_bookstore_crud.py", "activity_2_2_try_it_yourself.py"
foreach ($script in $scripts) {
    Write-Host "`n>>> $script" -ForegroundColor Cyan
    & $Python $script
    if ($LASTEXITCODE -ne 0) { throw "$script failed with exit code $LASTEXITCODE" }
}
& $Python build_report.py
if ($LASTEXITCODE -ne 0) { throw "build_report.py failed" }
```

## 8. Execution output

Console output captured from the actual runs (`results/*.output.txt`).

### Activity 2.1 - Environment Setup Verification

```text
Activity 2.1 - Environment Setup Verification
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:22:44+00:00 on LAPTOP-DSDFIQOI | Python 3.12.10 | pymongo 4.20.0

== Checklist ==
  [PASS          ] Python interpreter: CPython 3.12.10 at G:\Projects\nosql-lab-7\.venv\Scripts\python.exe
  [PASS          ] PyMongo driver: pymongo 4.20.0
  [PASS          ] MongoDB in Docker: mongodb://localhost:27028 -> MongoDB 7.0.45, standalone, first ping 31.4 ms
  [PASS          ] MongoDB native service: mongodb://localhost:27017 -> MongoDB 8.3.7, standalone, first ping 23.8 ms
  [PASS          ] Docker engine: server 29.8.2 (linux/amd64)
  [PASS          ] Lab MongoDB container: jovit-lab02-mongo: image mongo:7.0, running, ports 27017/tcp->127.0.0.1:27028
  [NOT CONFIGURED] MongoDB Atlas: MONGO_URI_ATLAS is not set in .env; add your own Atlas connection string to run this check
  [PASS          ] optional: python-dotenv: 1.2.4
  [PASS          ] optional: certifi: 2026.10.23
  [PASS          ] optional: dnspython: 2.9.2
  [MISSING       ] optional: pandas: not installed (only needed by later labs)
  [MISSING       ] optional: matplotlib: not installed (only needed by later labs)
  [MISSING       ] optional: faker: not installed (only needed by later labs)

== Summary ==
  13 checks: {'MISSING': 3, 'NOT CONFIGURED': 1, 'PASS': 9}
  required checks failed: none
  stored this run in jovit_lab02.environment_checks (_id 6aff06345e61c4eadc9a8964); 13 checks read back; runs stored so far: 2
```

### Activity 2.1 (Try it yourself) - Compare MongoDB Deployments

```text
Activity 2.1 (Try it yourself) - Compare MongoDB deployments
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:22:46+00:00 on LAPTOP-DSDFIQOI | Python 3.12.10 | pymongo 4.20.0
  note: MONGO_URI_ATLAS is not configured, so Atlas is not part of this run
  opened 2 connections concurrently in 95 ms

== Side-by-side comparison (live server_info() / hello) ==
  field                   docker mongodb://localhost:27028    native mongodb://localhost:27017    
  version                 7.0.45                              8.3.7                               
  gitVersion              a52313e13ab5                        34eee04f3498                        
  target OS               linux                               windows                             
  distmod                 ubuntu2204                          windows                             
  bits                    64                                  64                                  
  allocator               tcmalloc                            tcmalloc-gperf                      
  OpenSSL                 OpenSSL 3.0.2 15 Mar 2022           Windows SChannel                    
  storage engines         devnull, wiredTiger                 devnull, wiredTiger                 
  max wire version        21                                  28                                  
  topology                standalone                          standalone                          
  writable primary        True                                True                                
  max BSON size (MB)      16                                  16                                  
  ping ms (median of 20)  2.208                               0.579                               

== What differs ==
  fields that differ between deployments: version, gitVersion, target OS, distmod, allocator, OpenSSL, max wire version
  stored comparison in docker:jovit_lab02.server_comparison (_id 6aff0636ca686ed6be159dfe)
  stored comparison in native:jovit_lab02.server_comparison (_id 6aff0636ca686ed6be159dff)
```

### Activity 2.2 - Bookstore CRUD Operations

```text
Activity 2.2 - Bookstore CRUD Operations
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:22:47+00:00 on LAPTOP-DSDFIQOI | Python 3.12.10 | pymongo 4.20.0
Deployment: docker (MongoDB 7.0.45), collection jovit_lab02.books

== CREATE ==
  insert_one -> _id 6aff0637e5396035a71e8459; insert_many -> 9 ids
  inserting a second book with ISBN 978-0132350884: rejected with DuplicateKeyError (unique index on isbn)

== READ ==
  find_one by ISBN: {'title': 'Designing Data-Intensive Applications', 'authors': ['Martin Kleppmann'], 'publisher': {'name': "O'Reilly Media"}}
  books tagged 'databases', oldest first (2):
    - {"title": "Seven Databases in Seven Weeks", "year": 2012, "price_inr": 780.0}
    - {"title": "Designing Data-Intensive Applications", "year": 2017, "price_inr": 1450.0}
  query on the embedded publisher: MIT Press (2):
    - {"title": "Introduction to Algorithms", "year": 1990, "price_inr": 1250.0}
    - {"title": "Deep Learning", "year": 2016, "price_inr": 1600.0}
  published 1995-2010 and under INR 800, most expensive first (3):
    - {"title": "The Pragmatic Programmer", "year": 1999, "price_inr": 720.0}
    - {"title": "Clean Code", "year": 2008, "price_inr": 650.0}
    - {"title": "Wings of Fire", "year": 1999, "price_inr": 299.0}
  books with three or more authors: 2
  titles per publisher: Addison-Wesley=3, MIT Press=2, No Starch Press=1, O'Reilly Media=1, Pragmatic Bookshelf=1, Prentice Hall=1, Universities Press=1

== UPDATE ==
  sell 2 copies of Python Crash Course ($inc): matched 1, modified 1
  add tag 'nosql' ($addToSet): modified 1; repeating it modified 0 (no duplicate tag)
  set publisher.imprint on all Addison-Wesley books (update_many): modified 3
  upsert a new title by ISBN: upserted_id 6aff06372e05c52578569fa3
  verified: stock now 13, tags ['databases', 'distributed systems', 'nosql']

== DELETE ==
  delete_many out-of-stock books: deleted 1
  delete_one The Mythical Man-Month: deleted 1
  verified: 9 books remain, deleted ISBN no longer found

  exported 9 books to results/activity_2_2_books.json
  all CRUD checks passed
```

### Activity 2.2 (Try it yourself) - 10% Price Rise Before 2010

```text
Activity 2.2 (Try it yourself) - 10% price rise for pre-2010 books
Student: Jovit Chawla (24BTRCL089)
Run at 2026-11-19T14:22:48+00:00 on LAPTOP-DSDFIQOI | Python 3.12.10 | pymongo 4.20.0
Deployment: docker (MongoDB 7.0.45), collection jovit_lab02.books_price_update
  seeded 10 books; 6 were published before 2010

== Update ==
  filter:   {"year": {"$lt": 2010}, "price_revised_2010": {"$ne": true}}
  update:   [{$set: {old_price_inr: '$price_inr', price_inr: {$round: [{$multiply: ['$price_inr', 1.10]}, 2]}, price_revised_2010: true, price_revised_at: '$$NOW'}}]
  matched 6, modified 6
  running the same update again: matched 0, modified 0 (the guard stops a second 10% increase)

== Verification ==
  OK   1975  The Mythical Man-Month                     540.00 ->   594.00  (+10%)
  OK   1990  Introduction to Algorithms                1250.00 ->  1375.00  (+10%)
  OK   1999  Refactoring                                820.00 ->   902.00  (+10%)
  OK   1999  The Pragmatic Programmer                   720.00 ->   792.00  (+10%)
  OK   1999  Wings of Fire                              299.00 ->   328.90  (+10%)
  OK   2008  Clean Code                                 650.00 ->   715.00  (+10%)
  OK   2012  Seven Databases in Seven Weeks             780.00 ->   780.00  (unchanged)
  OK   2015  Python Crash Course                        560.00 ->   560.00  (unchanged)
  OK   2016  Deep Learning                             1600.00 ->  1600.00  (unchanged)
  OK   2017  Designing Data-Intensive Applications     1450.00 ->  1450.00  (unchanged)
  10/10 prices correct; modified count matches eligible books: True
  catalogue value: INR 8,669.00 -> INR 9,096.90

== Check in MongoDB Compass ==
  connect to mongodb://localhost:27028, open jovit_lab02 > books_price_update and use:
    Filter:  { "year": { "$lt": 2010 } }      -> every result has price_revised_2010: true and old_price_inr
    Filter:  { "year": { "$gte": 2010 } }     -> no result has price_revised_2010

  exported 10 books to results/activity_2_2_try_it_yourself_books.json
```

## 9. Results

### Activity 2.1 - Environment Setup Verification

| Check | Required | Status | Evidence |
|---|---|---|---|
| Python interpreter | yes | PASS | CPython 3.12.10 at G:\Projects\nosql-lab-7\.venv\Scripts\python.exe |
| PyMongo driver | yes | PASS | pymongo 4.20.0 |
| MongoDB in Docker | yes | PASS | mongodb://localhost:27028 -> MongoDB 7.0.45, standalone, first ping 31.4 ms |
| MongoDB native service | no | PASS | mongodb://localhost:27017 -> MongoDB 8.3.7, standalone, first ping 23.8 ms |
| Docker engine | yes | PASS | server 29.8.2 (linux/amd64) |
| Lab MongoDB container | yes | PASS | jovit-lab02-mongo: image mongo:7.0, running, ports 27017/tcp->127.0.0.1:27028 |
| MongoDB Atlas | no | NOT CONFIGURED | MONGO_URI_ATLAS is not set in .env; add your own Atlas connection string to run this check |
| optional: python-dotenv | no | PASS | 1.2.4 |
| optional: certifi | no | PASS | 2026.10.23 |
| optional: dnspython | no | PASS | 2.9.2 |
| optional: pandas | no | MISSING | not installed (only needed by later labs) |
| optional: matplotlib | no | MISSING | not installed (only needed by later labs) |
| optional: faker | no | MISSING | not installed (only needed by later labs) |

Required checks failed: **none**. The run was stored in `jovit_lab02.environment_checks` (`_id` 6aff06345e61c4eadc9a8964).

### Activity 2.1 (Try it yourself) - Compare MongoDB Deployments

| Field | docker (mongodb://localhost:27028) | native (mongodb://localhost:27017) |
|---|---|---|
| version | 7.0.45 | 8.3.7 |
| gitVersion | a52313e13ab5 | 34eee04f3498 |
| target OS | linux | windows |
| distmod | ubuntu2204 | windows |
| bits | 64 | 64 |
| allocator | tcmalloc | tcmalloc-gperf |
| OpenSSL | OpenSSL 3.0.2 15 Mar 2022 | Windows SChannel |
| storage engines | devnull, wiredTiger | devnull, wiredTiger |
| max wire version | 21 | 28 |
| topology | standalone | standalone |
| writable primary | True | True |
| max BSON size (MB) | 16 | 16 |
| ping ms (median of 20) | 2.208 | 0.579 |

Fields that differ: **version, gitVersion, target OS, distmod, allocator, OpenSSL, max wire version**.

Atlas was **not part of this run**: no `MONGO_URI_ATLAS` is configured. Adding your own Atlas connection string to `.env` and re-running adds an Atlas column automatically.

### Activity 2.2 - Bookstore CRUD Operations

| Operation | Verified result |
|---|---|
| Create | 10 books inserted (insert_one + insert_many); duplicate ISBN rejected with DuplicateKeyError (unique index on isbn) |
| Read | 2 'databases' books, 2 MIT Press books (embedded field query), 3 in the year/price range, 2 with 3+ authors |
| Update | stock after selling 2 copies = 13; tags ['databases', 'distributed systems', 'nosql']; imprint set on 3 books; upsert created a book: True |
| Delete | 1 out-of-stock deleted, 1 deleted by ISBN, 9 remain |

Deployment: `docker`. Final collection: 9 books, exported to `results/activity_2_2_books.json`.

### Activity 2.2 (Try it yourself) - 10% Price Rise Before 2010

| Year | Title | Price before (INR) | Price after (INR) | Change | Check |
|---|---|---|---|---|---|
| 1975 | The Mythical Man-Month | 540.00 | 594.00 | +10% | OK |
| 1990 | Introduction to Algorithms | 1250.00 | 1375.00 | +10% | OK |
| 1999 | Refactoring | 820.00 | 902.00 | +10% | OK |
| 1999 | The Pragmatic Programmer | 720.00 | 792.00 | +10% | OK |
| 1999 | Wings of Fire | 299.00 | 328.90 | +10% | OK |
| 2008 | Clean Code | 650.00 | 715.00 | +10% | OK |
| 2012 | Seven Databases in Seven Weeks | 780.00 | 780.00 | unchanged | OK |
| 2015 | Python Crash Course | 560.00 | 560.00 | unchanged | OK |
| 2016 | Deep Learning | 1600.00 | 1600.00 | unchanged | OK |
| 2017 | Designing Data-Intensive Applications | 1450.00 | 1450.00 | unchanged | OK |

`update_many` matched 6 and modified 6 books (6 were published before 2010); repeating it modified 0. Catalogue value INR 8,669.00 -> INR 9,096.90. Problems: none.

## 10. Conclusion

- The environment check passed 9 of 13 checks; required checks failed: none. Atlas is not configured, and missing optional packages are only needed in later labs.
- 2 deployments were used simultaneously. They differ in version, gitVersion, target OS, distmod, allocator, OpenSSL, max wire version, which shows that the same driver code talks to different server versions and builds (Linux container vs Windows service) through one wire protocol; localhost ping latency: docker 2.208 ms, native 0.579 ms.
- All four CRUD operations behaved as expected on embedded documents and arrays, and the unique index rejected a duplicate ISBN; 9 books remained.
- The targeted `update_many` raised exactly the 6 pre-2010 prices by 10% (10/10 prices verified), and the guard field made a repeated run change 0 documents.
