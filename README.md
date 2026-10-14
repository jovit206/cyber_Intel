# Threat Intelligence Platform: Labs 7, 8 and 9 (Docker deployment)

A FastAPI / MongoDB threat-intelligence dashboard (Lab 7) running on a three-member MongoDB
replica set `rs0` (Lab 8), with the Lab 9 write-concern latency and network-partition experiments.
Public-source records keep their provenance; the application never invents threat names, indicators,
scores or locations.

| Docker Hub image | Status |
|---|---|
| [`jovit206/threat-intelligence-platform`](https://hub.docker.com/r/jovit206/threat-intelligence-platform) (tag `latest`) | Published; digest `sha256:b6656e1c…`. This tag predates the Lab 9.1/9.2 rewrite and the Python port of the partition script; **republish after verification** to include them. |

> To run the published image set `APP_IMAGE=jovit206/threat-intelligence-platform:latest` in `.env` and use
> `docker compose pull`. `docker compose up -d --build` builds the current source instead (this is what
> contains the newest Lab 9 scripts until the image is republished).

## Contents

- [Prerequisites](#prerequisites)
- [Start the application](#start-the-application)
- [First run and health checks](#first-run-and-health-checks)
- [Lab 7 demonstration](#lab-7-demonstration)
- [Lab 8 demonstration](#lab-8-demonstration)
- [Lab 9.1: write concern latency](#lab-91-write-concern-latency)
- [Lab 9.2: network partition](#lab-92-network-partition)
- [Optional Working Set benchmark data](#optional-working-set-benchmark-data)
- [Troubleshooting](#troubleshooting)
- [Shutdown and data persistence](#shutdown-and-data-persistence)
- [Development without Docker](#development-without-docker)

## Prerequisites

- Docker Desktop (Windows/macOS) or Docker Engine + the Compose plugin (Linux). Compose v2.24 or newer.
- About 4 GB of free RAM for Docker and 3 GB of disk for the images and volumes. The three MongoDB
  members are capped at a 1 GB WiredTiger cache each (`MONGO_CACHE_GB`).
- Internet access on first start: the public MISP / ThreatFox / report feeds are downloaded then.
- For Lab 9.2 only: **Python 3.8+** and the `docker` CLI on the host (it orchestrates `docker network`
  commands, so it cannot run inside the backend container). Lab 9.1 needs nothing beyond Docker.
- A Docker Hub account only if you want to pull a private image or publish your own.

## Start the application

There are two equivalent ways to obtain the image. Both start the same stack.

```bash
cp .env.example .env              # optional values: VIRUSTOTAL_API_KEY, APP_IMAGE, APP_PORT
mkdir -p labs/lab9/results        # experiment reports appear here on the host
```

**A. Pull the published image from Docker Hub** (after publication):

```bash
# in .env:  APP_IMAGE=jovit206/threat-intelligence-platform:latest
docker compose pull
docker compose up -d
```

**B. Build from source:**

```bash
docker compose up -d --build
```

Open <http://localhost:8000>. What `up` does, in order: starts `mongo1`, `mongo2`, `mongo3` and waits
for their healthchecks; runs the one-shot `mongo-init` job, which initialises `rs0` **only if it is not
initialised yet** and then waits for one PRIMARY and three healthy members; starts `lab7-backend`.

| Situation | Command |
|---|---|
| First start on a new machine | `docker compose up -d` |
| Restart an existing deployment (data kept) | `docker compose up -d` (or `docker compose start`) |
| Upgrade the application image only | `docker compose pull lab7-backend && docker compose up -d --no-deps lab7-backend` |
| Adopt an already-running replica set without recreating it | `docker compose up -d --no-recreate --no-deps lab7-backend` |

`mongo-init` never re-initialises or reconfigures a healthy replica set, and no command above deletes
volumes. The VirusTotal key, if you use one, goes only in the local `.env` and is read by the backend at
run time. It is not part of the image and never reaches the browser.

## First run and health checks

```bash
docker compose ps                                   # mongo1-3 healthy, mongo-init exited 0, lab7-backend healthy
curl http://localhost:8000/health                   # {"status":"ok"}
docker compose exec mongo1 mongosh --quiet --eval \
  "rs.status().members.forEach(m => print(m.name, m.stateStr, 'health', m.health))"
docker compose logs mongo-init                      # "replica set rs0 is ready"
```

Expected: one `PRIMARY`, two `SECONDARY`, all `health 1`. The backend logs `[config]` lines at start-up
that describe the MongoDB hosts and whether a VirusTotal key is present (never its value).

## Lab 7 demonstration

Open <http://localhost:8000> and walk the sidebar:

1. **Live Threat Stream**: normalised records from the MISP feeds and ThreatFox, filter by source.
   Records arrive from public feeds on first start (a few minutes; capped by `SYNC_STARTUP_MAX_EVENTS`).
2. **Top Threats** and **Report Corpus**: galaxy clusters and the annual-report corpus.
3. **Cross-Source Analysis**: items found in more than one source, with filters, search and evidence.
4. **Search & Enrich**: VirusTotal lookup (needs `VIRUSTOTAL_API_KEY`); results are cached for 24 h.
5. **Working Set Monitor**: live WiredTiger cache telemetry (one sample every 10 s) and the random
   point-read experiment (see [optional benchmark data](#optional-working-set-benchmark-data)).

No fake data is generated: a fresh machine shows exactly what the public feeds return.
To move an existing dataset, use `mongodump` / `mongorestore` explicitly; nothing does it automatically.

## Lab 8 demonstration

The Lab 8 replica set *is* the database the application uses.

- **Members and primary**: sidebar **Network Reliability**, section "Current cluster status" (live from
  `replSetGetStatus`), or `GET http://localhost:8000/api/lab8/status`.
- **Deployment script (8.1)**, idempotent: `docker compose exec lab7-backend python labs/lab8/lab_8_1_replica_set.py`
- **Failover observer (8.2)**: forces `replSetStepDown`, watches the election and a reader reconnect:
  `docker compose exec lab7-backend python labs/lab8/lab_8_2_failover_monitor.py`
  It briefly changes the primary on purpose, so run it only when you intend to demonstrate failover.
- The standalone Lab 8 stack (`labs/lab8/docker-compose.yml`) uses the same project name, containers and
  volumes, so either compose file manages the same cluster.

## Lab 9.1: write concern latency

Measures the cost of acknowledgement levels. Each configuration inserts **1,000** small documents:

| Write concern | j:false | j:true |
|---|---|---|
| `w:1` | yes | yes |
| `w:majority` | yes | yes |
| `w:all` -> **`w:3`** | yes | yes |

**About `w:all`.** MongoDB does not have `w: "all"`. A string is looked up as a *named tag mode* and the
server answers *"No write concern mode named 'all' found in replica set configuration"* (verified on
MongoDB 7.0.45). Acknowledgement by every member is requested with a number: the count of data-bearing
members, here `w:3`. The script reads that count from the live replica-set configuration, prints the
mapping, and stores it in the report; a value larger than the member count is rejected by the server.

Run it manually (it never starts by itself):

```bash
docker compose exec lab7-backend python labs/lab9/lab_01_write_concern_latency.py            # 1,000 docs/config
docker compose exec lab7-backend python labs/lab9/lab_01_write_concern_latency.py --docs 200 # quicker
docker compose exec lab7-backend python labs/lab9/lab_01_write_concern_latency.py --dry-run  # validate only
```

- Per configuration it records total elapsed time, average latency per write, P50/P99, and
  acknowledged / timed-out / failed counts; each write has a finite `wtimeout`.
- **Output:** a comparison table on screen and `labs/lab9/results/lab_01_<timestamp>.json` + `.csv`
  (plus `lab_01_latest.json`) on the host.
- **Storage:** new collections `cap_latency_test.run_<timestamp>_<config>`. Existing historical data is
  not touched unless you pass `--replace-historical`.
- The dashboard's **Write Concern Latency** page keeps three things separate: *historical* collections,
  *saved experiment runs* (the JSON files above), and the *live benchmark* button (explicit, on demand).
- **Reading the numbers.** Latency depends on disk and journal behaviour, container networking,
  replica-set state and background load. Compare the printed ranking with the explanation the script
  generates from your own run. `w:1` is not guaranteed to be fastest in every run, and `j:true` often
  adds a visible but modest cost. If a member is down, `w:all` writes time out; the report counts them
  and excludes them from the latency figures.

## Lab 9.2: network partition

Isolates **one secondary** from the Docker network, observes the replica set, runs `w:1` and
`w:majority` writes, reconnects, and verifies recovery. It is an explicit manual operation: nothing in
the build, start-up, tests or publication triggers it.

```bash
# run from the project root, in a bash shell with the docker CLI
docker compose cp lab7-backend:/app/labs/lab9/lab_02_simulate_partition.py .   # only if you have no checkout
python labs/lab9/lab_02_simulate_partition.py --dry-run    # prechecks and plan; changes nothing
python labs/lab9/lab_02_simulate_partition.py              # asks you to type the confirmation text
```

1. **Prechecks.** Docker reachable; all three containers running and sharing one network; `rs0` has
   exactly three members, one PRIMARY, two SECONDARY, all healthy. Anything else is refused.
2. **Target.** The first healthy secondary, or `--target <container>`. The primary is never selected.
3. **Confirmation.** You must type `ISOLATE <container>`. The expected effect is printed first.
4. **Observation.** Timestamped `rs.status()` polls (default 30 s), then `w:1` and `w:majority` test
   writes with a 5 s timeout. Acknowledged, timed-out and failed operations are counted separately. The
   isolated member's own view is recorded too.
5. **Recovery.** The member is reconnected (also on error or Ctrl+C) *with its original network
   aliases*; the script waits for three healthy members and compares the document count on the
   returned member with the primary. Recovery is reported as verified only if both checks pass.
6. **Report.** `labs/lab9/results/lab_02_partition_<timestamp>.json` and `.log`
   (plus `lab_02_partition_latest.json`, shown on the Network Reliability page).

What to expect, and what is *not* guaranteed: with three voting members, isolating one secondary leaves
the primary plus one secondary, a majority of two, so `w:majority` is normally still acknowledged and
the primary does not step down. The report states what actually happened. Losing the majority needs two
members isolated; that is a **separate phase** with a different confirmation text:

```bash
python labs/lab9/lab_02_simulate_partition.py --phase majority-loss --dry-run
python labs/lab9/lab_02_simulate_partition.py --phase majority-loss     # type: ISOLATE BOTH SECONDARIES <a> <b>
```

If a run is ever interrupted so badly that cleanup cannot run, reconnect by hand:
`docker network connect --alias mongoN mongo-net <container>` and check `rs.status()`.

## Optional Working Set benchmark data

The Random Point-Read experiment reads from `chapter07.threat_reports_real`, which is **empty by
default** and is never created at start-up. Building it is a separate, explicit step:

```bash
docker compose exec lab7-backend python -m backend.lab_7_2_working_set
```

It inserts **100,000 derived benchmark documents** (about 2 KB each, roughly 200 to 250 MB on disk, and
replicated to three members). They are derived from your stored feed records, are clearly marked
(`derived_from`), and the original threat-intelligence records are not modified. If you press the
button before building it, the page explains this and shows the command.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `pull access denied` / `manifest unknown` | The image is not published or `APP_IMAGE` is wrong. `docker login` (private repo), check the name, or use `docker compose up -d --build`. |
| `port is already allocated` | Set `APP_PORT`, `MONGO1_BIND`, ... in `.env` to free ports. |
| `mongo1` stays `starting` or `unhealthy` | `docker compose logs mongo1`. Usually low memory: raise Docker Desktop memory or lower `MONGO_CACHE_GB`. |
| `mongo-init` exits non-zero | `docker compose logs mongo-init`; fix the member shown, then `docker compose up -d` (init is idempotent). |
| `lab7-backend` restarts | `docker compose logs lab7-backend`: check the `[config]` lines and that `mongo-init` completed. |
| App shows "VirusTotal ... rejected the API key (401)" | The key must be exactly 64 hex characters without quotes or spaces; edit `.env`, then `docker compose up -d --no-deps lab7-backend` (a plain `restart` does not re-read `.env`). |
| Lab 9.2 refuses to run | Read the `REFUSING:` line; it names the failed precheck (unhealthy member, no primary, wrong network ...). |
| A member stays disconnected after Lab 9.2 | See the manual reconnect command above; always include `--alias`. |
| Data seems missing after an upgrade | Volumes are named and persistent; check `docker volume ls` for `mongodb-lab_mongo1-data` and that you did not run `down -v`. |
| Authentication errors | This deployment runs MongoDB without authentication, bound to loopback host ports only and the internal network. Add auth before exposing it. |

## Shutdown and data persistence

```bash
docker compose stop        # stop containers, keep everything
docker compose down        # remove containers and the network, KEEP the named volumes (data persists)
```

`docker compose down -v` (or `docker volume rm`) **deletes the MongoDB data**. It is deliberately not part
of any procedure here. Named volumes: `mongodb-lab_mongo1-data`, `mongodb-lab_mongo2-data`,
`mongodb-lab_mongo3-data`.

## Security notes

- No secret is baked into the image: `.env`, backups and local files are excluded by `.dockerignore`.
- The VirusTotal key is read by the backend only; it is never in HTML/JS, API responses or logs.
- MongoDB host ports are bound to `127.0.0.1`; the replica set talks over the internal `mongo-net`.
- Docker Hub credentials are handled by `docker login`, never by files in this project.

---

# Development without Docker

## Run the app

1. Install Python dependencies: `python -m pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and set `MONGO_URI` and `MONGO_DATABASE`.
3. For VirusTotal reputation lookups, set `VIRUSTOTAL_API_KEY` in the process
   environment or local `.env`. Do not commit a key.
4. Start the API and frontend:
   `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`.
5. Open `http://localhost:8000`. Set `FRONTEND_ORIGIN` when the frontend is
   served from a separate origin. `FRONTEND_API_URL` configures `/config.js`;
   `public/config.js` defaults port 3000 to the API at port 8000.

The public feed, Galaxy, and annual-report sync jobs run on app startup and
again every 24 hours while the API process is running. The feed-sync status,
including the last successful time and failures, is available in the Live
Threat Stream header and `/api/data-source-status`.

## Public threat-intelligence sources

The daily MISP feed sync reads each `manifest.json`, compares the manifest
timestamp and metadata fingerprint to MongoDB, and downloads only new or
changed events. Event downloads use at most eight concurrent workers, retry
timeouts and transient HTTP errors, and log progress. A quick first run can be
capped per feed:

```powershell
python -m backend.collectors.misp_feeds --max-events 25
```

Omit `--max-events` to process every changed event. The same command can be
run manually; repeated runs are idempotent. Feed events are stored by
feed-scoped MISP UUID in `events`, with the original UUID and feed URL retained.
Startup/scheduled syncs are capped by `SYNC_STARTUP_MAX_EVENTS` (default 25)
and report imports by `SYNC_STARTUP_MAX_REPORTS` (default 2); raise them or run
the module commands without caps to pull more history.
Attributes are stored separately in `attributes` and reference the event UUID.
They include the source-provided type, value, category, `to_ids` flag, tags,
and timestamps.

| Feed | Manifest | Events in manifest |
|---|---|---|
| CIRCL OSINT | <https://www.circl.lu/doc/misp/feed-osint/manifest.json> | ~1,681 |
| Botvrij.eu | <https://www.botvrij.eu/data/feed-osint/manifest.json> | ~435 |
| ThreatFox (abuse.ch) | <https://threatfox.abuse.ch/downloads/misp/manifest.json> | ~2,009 events holding several million indicators |

Each feed publishes continuously; the sync runs at least once per day (and
on app startup), so the database converges to the feeds' newest events. A
capped run (`--max-events 25`) is the recommended quick first run; omit the
flag to process every changed event.

Threat names and aliases are synchronized daily from the MISP Galaxy raw JSON
clusters `threat-actor`, `malpedia`, and `tool` in
<https://github.com/MISP/misp-galaxy>. The database stores source-provided
cluster names, aliases, descriptions, and explicit metadata; absent country or
other metadata is not guessed.

Annual security report PDFs are discovered from
<https://github.com/jacobdjwilson/awesome-annual-security-reports>. New or
changed files are downloaded and converted to Markdown with Microsoft
MarkItDown. The `reports` collection keeps repository metadata and the `passages`
collection stores text extracted from those PDFs. Passage and entity counts
are computed from converted source text. Galaxy-name matches use a common-word
blocklist and case-sensitive matching; sync logs the most frequent extracted
names for manual false-positive review. For a quick report import:

```powershell
python -m backend.collectors.reports --max-reports 2
```

VirusTotal API v3 reputation results are cached in `virustotal_cache`, keyed
by indicator type and normalized value. The scheduled worker performs at most
one lookup every 15 minutes (at most 96 per day, below the free-tier 4-per-
minute and 500-per-day limits). A 404 is cached as a not-found result; quota
responses are not cached as reputation results and are surfaced in the UI.
VirusTotal is optional; the public MISP and Galaxy sources do not require
credentials.

## Database safety and source-derived lab data

Application startup does not drop or replace collections. Before removing
legacy records, take a backup and review the exact cleanup scope. This
machine has no `mongodump` binary installed; `python backup_db.py` exports
every collection of the configured database to timestamped JSON files under
`backups/` (BSON types preserved via `bson.json_util`) and writes a
`manifest.json` with per-collection counts.

Lab 7.1 requires synchronized Galaxy clusters and attributes. Temporary
benchmark records copy real source attributes and are removed by their exact
benchmark IDs after the measurement.

Lab 7.2 creates 100,000 documents in `threat_reports_real` only after source
records exist. These benchmark-only documents are deterministic copies of
real events, attributes, report metadata, or extracted report passages. To
reach the target BSON size, the original source text/value is repeated in a
clearly marked payload field; no synthetic threat statement, sensor, severity,
or indicator is added. These rows are benchmark material, not separate live
intelligence. Run:

```powershell
python -m backend.lab_7_1_schema_analysis
python -m backend.lab_7_2_working_set
python -m backend.lab_7_2_activity_monitor --iterations 1
```

The archived Node implementation and its synthetic data seeder have been
retired; the production API and lab scripts are under `backend/`.

## Tests

Run the focused Python tests with:

```powershell
python -m unittest discover -s test -p "test_*.py"
```

With the API server running, `python smoke_test.py` exercises every
endpoint and static page against the live data (feed stream, threat
details, reports, search, WiredTiger metrics, Lab 7.2 analysis and
random-read benchmark) and fails on any empty section.
