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
