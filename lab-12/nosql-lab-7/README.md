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

- [Starting the AEGIS Platform](#starting-the-aegis-platform)
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

## Starting the AEGIS Platform

Use this machine-specific procedure after Docker Desktop or Windows has restarted. The verified
deployment already exists as `fresh-mongo1`, `fresh-mongo2`, `fresh-mongo3`, and `fresh-backend`
(currently running `aegis-cti-financial:2026-10-29-r2`).
The PowerShell script starts only those existing containers, checks `rs0`, verifies backend health
and anonymous-route protection, then opens the sign-in page. It never runs Compose, creates containers, changes networks,
or touches the separate Chapter 10 `bank-primary`, `bank-backup`, and `bank-dr` containers.

### 1. Open the project and start Docker Desktop

Open Docker Desktop from the Windows Start menu and wait until its engine reports that it is running.
Then open PowerShell:

```powershell
cd C:\Users\hp\Downloads\nosql-lab-7
docker version
docker ps -a
```

`docker version` must show both Client and Server sections. `docker ps -a` should list the three
`fresh-mongo*` containers and `fresh-backend`; they may be running already because their restart
policy is `unless-stopped`.

### 2. Start the existing MongoDB replica set and backend

The normal one-command startup is:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-project.ps1
```

The execution-policy option applies to that PowerShell process only. The script verifies that the
existing containers are on `fresh-mongo-net` and that the MongoDB host ports are still 37017, 37018,
and 37019. It starts only members that are stopped, waits for their health checks, verifies one
healthy PRIMARY and two healthy SECONDARY members in `rs0`, and then starts `fresh-backend` if needed.
It checks `/health`, confirms the sign-in page loads, and verifies `/api/meta` returns HTTP 401
without a session. Sign in before using threat or experiment data APIs.

To run the same checks without opening a browser:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-project.ps1 -NoBrowser
```

The script refuses to create or recreate missing containers. If one is missing or on a different
network/port, stop and investigate the existing deployment; do not substitute the root Compose
stack as a repair command. The standard `docker-compose.yml` in this repository describes the
`mongo1`/`mongo2`/`mongo3` deployment and defaults `mongo1` to host port 27017. It is not the
restart procedure for this machine and must not be started alongside the separate Chapter 10 bank
deployment.

### 3. Verify MongoDB, backend, and frontend

The script performs these checks itself. To check them manually:

```powershell
docker ps --filter "name=fresh-"
docker exec fresh-mongo1 mongosh --quiet --eval "const s=rs.status(); print(JSON.stringify({set:s.set,members:s.members.map(m=>({name:m.name,state:m.stateStr,health:m.health}))}))"
Invoke-RestMethod http://localhost:8000/health
(Invoke-WebRequest -UseBasicParsing http://localhost:8000/account).StatusCode
curl.exe -i http://localhost:8000/api/meta
```

Expected: `rs0` has one `PRIMARY`, two `SECONDARY` members, and `health: 1` for all three;
`/health` returns `status: ok`; `/account` loads the sign-in form; and anonymous `/api/meta` access
returns HTTP 401. After creating an account and signing in, the inventory endpoint
currently returns 10 persisted, clearly identified demonstration products from the separate
`lab10_demo.inventory` database. Reservations are stored in `lab10_demo.orders`; it starts empty and
does not contain fabricated history. The live Lab 10 banking replica set is separate and is not
started by this procedure, so Banking Operations continues to show its saved experiment data unless
its dedicated connection is configured separately.

The backend container serves both the FastAPI API and the static frontend in `public/`. There is no
separate frontend framework, Node process, npm install, or second terminal to keep open. The script
runs the containers in the background; Docker Desktop must remain running, but PowerShell can close.
The browser loads the API from the same origin (`/api/...`) at port 8000.
The repository also has a `.venv` for local development/tests, but the verified application runs in
the existing `fresh-backend` Docker container. Do not start a second local Uvicorn process on port
8000 during normal use. Its image starts `uvicorn backend.main:app --host 0.0.0.0 --port 8000`.
Inside `fresh-mongo-net`, its MongoDB URI uses `fresh-mongo1`, `fresh-mongo2`, and `fresh-mongo3` on
port 27017 with `replicaSet=rs0`; `MONGO_DATABASE` is `chapter07`. The backend health endpoint does
not expose database configuration; sign in to access the protected metadata API.

| Service | Address / configuration |
|---|---|
| Frontend | <http://localhost:8000> |
| Backend health | <http://localhost:8000/health> |
| API documentation | <http://localhost:8000/docs> |
| Lab 7 database | `chapter07` on replica set `rs0` |
| MongoDB internal network | `fresh-mongo-net` |
| MongoDB loopback host ports | `fresh-mongo1`: 37017, `fresh-mongo2`: 37018, `fresh-mongo3`: 37019 |

No `.env` edit or API key is required to start the application. VirusTotal enrichment is optional;
its key, if configured, belongs in the local `.env` and must never be pasted into this README.

### Configure optional API credentials

Keep credentials only in the ignored project `.env`. VirusTotal keys are available from your
[VirusTotal account API key page](https://www.virustotal.com/gui/my-apikey). ThreatFox requires a
separate abuse.ch Auth-Key from the [ThreatFox Community API](https://threatfox.abuse.ch/api/).
Add these names locally, without quotes (never put values in source code or Git):

```dotenv
VIRUSTOTAL_API_KEY=your_private_value
THREATFOX_AUTH_KEY=your_private_value
```

The existing `fresh-backend` is a machine-specific container and does not mount the host `.env`.
After editing `.env`, copy it into that container and restart only the backend so the configuration
loader can read it:

```powershell
cd C:\Users\hp\Downloads\nosql-lab-7
docker cp .env fresh-backend:/app/.env
docker restart fresh-backend
Start-Process http://localhost:8000
```

After signing in, the response reports configuration state only; it never returns either secret. The dashboard’s
VirusTotal test performs a real lookup when configured. ThreatFox syncs real IOCs through its
authenticated v1 API; without `THREATFOX_AUTH_KEY`, the feed is explicitly marked as requiring
configuration and its previous successful-sync time is retained.

To restart only the backend without changing credentials, use `docker restart fresh-backend`. To
stop the full AEGIS stack while keeping the containers and database volumes, stop `fresh-backend`,
`fresh-mongo2`, `fresh-mongo3`, and `fresh-mongo1`; do not use the repository’s separate standard
Compose stack for this existing machine-specific deployment.

## Prerequisites

- Docker Desktop (Windows/macOS) or Docker Engine + the Compose plugin (Linux). Compose v2.24 or newer.
- About 4 GB of free RAM for Docker and 3 GB of disk for the images and volumes. The three MongoDB
  members are capped at a 1 GB WiredTiger cache each (`MONGO_CACHE_GB`).
- Internet access on first start: the public MISP / ThreatFox / report feeds are downloaded then.
- For Lab 9.2 only: **Python 3.8+** and the `docker` CLI on the host (it orchestrates `docker network`
  commands, so it cannot run inside the backend container). Lab 9.1 needs nothing beyond Docker.
- A Docker Hub account only if you want to pull a private image or publish your own.

## Start the application

> This section describes the repository's standard Compose deployment. For this computer's already
> created `fresh-*` containers and safe post-restart procedure, use [Starting the AEGIS Platform](#starting-the-aegis-platform).
> Do not run the Compose commands below as a shortcut for that procedure: the checked-in Compose
> file uses the separate `mongo1` naming and defaults to host port 27017.

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

### Restarting this machine's verified deployment

| Symptom | Fix |
|---|---|
| `docker info` cannot reach the server | Open Docker Desktop, wait for the engine to finish starting, then retry `docker version` and `docker ps -a`. |
| `start-project.ps1` says a required container is missing | The script intentionally refuses to create containers. Do not run `docker compose up` as a shortcut: this checkout's Compose file uses `mongo1` and defaults to host port 27017, separate from the verified `fresh-*` deployment. Restore the existing deployment only from its original configuration and volumes. |
| A port is already in use | The verified MongoDB containers use loopback ports 37017-37019, not 27017. Check listeners with `Get-NetTCPConnection -State Listen -LocalPort 37017,37018,37019,8000,18000 -ErrorAction SilentlyContinue`. Do not stop or rebind the Chapter 10 `bank-primary` container to free port 27017. |
| A MongoDB member is unhealthy or `rs0` has no primary | Run `docker logs --tail 100 fresh-mongo1` (or the affected member), then rerun `start-project.ps1`. Do not run `rs.initiate()`, `rs.reconfig()`, or a data reset against this existing set. |
| The backend container will not start or the health check fails | Run `docker logs --tail 150 fresh-backend`, then check `http://localhost:8000/health` and `/account`. Anonymous requests to `/api/meta` should return HTTP 401. Do not also launch local Uvicorn on port 8000. |
| The page is blank or its API calls fail | Open <http://localhost:8000>, hard-refresh the page, and check `http://localhost:8000/health`. The frontend is served by `fresh-backend`; there is no separate Node process. Check `docker logs --tail 150 fresh-backend` for API errors. |
| Python packages are missing | This does not affect the Docker startup path; dependencies are installed in the image. For optional local development/tests, activate `.\.venv\Scripts\Activate.ps1` and install `python -m pip install -r requirements.txt`. |
| Frontend packages are missing | No frontend package install is required. The project serves plain HTML/CSS/JavaScript from `public/`; there is no `package.json` startup command. |
| VirusTotal reports missing credentials | `VIRUSTOTAL_API_KEY` is optional and only enables enrichment. Keep the key in the ignored local `.env`; never put its value in the README, script, or browser code. |
| Inventory or banking data is unavailable | Sign in before checking `/api/lab10/inventory/products` or other protected APIs, then review backend logs. Inventory uses `lab10_demo.inventory` in clearly labeled demonstration mode when the separate live Lab 10 URI is not configured. Banking stays on saved experiment data until its dedicated `LAB10_MONGO_URI` is available; this startup procedure does not start or modify the Chapter 10 bank containers. |

| Symptom | Fix |
|---|---|
| `pull access denied` / `manifest unknown` | The image is not published or `APP_IMAGE` is wrong. `docker login` (private repo), check the name, or use `docker compose up -d --build`. |
| `port is already allocated` | For the verified `fresh-*` stack, inspect ports 37017-37019 and 8000/18000; do not change the Chapter 10 bank port mapping. The `.env`/`MONGO1_BIND` advice below applies only to a separately reviewed standard Compose deployment. |
| `mongo1` stays `starting` or `unhealthy` | `docker compose logs mongo1`. Usually low memory: raise Docker Desktop memory or lower `MONGO_CACHE_GB`. |
| `mongo-init` exits non-zero | `docker compose logs mongo-init`; fix the member shown, then `docker compose up -d` (init is idempotent). |
| `lab7-backend` restarts | `docker compose logs lab7-backend`: check the `[config]` lines and that `mongo-init` completed. |
| App shows "VirusTotal ... rejected the API key (401)" | The key must be exactly 64 hex characters without quotes or spaces; edit `.env`, then `docker compose up -d --no-deps lab7-backend` (a plain `restart` does not re-read `.env`). |
| Lab 9.2 refuses to run | Read the `REFUSING:` line; it names the failed precheck (unhealthy member, no primary, wrong network ...). |
| A member stays disconnected after Lab 9.2 | See the manual reconnect command above; always include `--alias`. |
| Data seems missing after an upgrade | Volumes are named and persistent; check `docker volume ls` for `mongodb-lab_mongo1-data` and that you did not run `down -v`. |
| Authentication errors | This deployment runs MongoDB without authentication, bound to loopback host ports only and the internal network. Add auth before exposing it. |

## Shutdown and data persistence

For the verified `fresh-*` deployment, stop only its existing application and MongoDB containers.
These commands keep the containers and MongoDB volumes; they do not touch Chapter 10 bank containers:

```powershell
docker stop fresh-backend
docker stop fresh-mongo2
docker stop fresh-mongo3
docker stop fresh-mongo1
```

Start them again with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-project.ps1`.

The Compose commands below apply only to a stack created with this repository's root Compose file;
they are not the verified machine restart procedure above.

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
| ThreatFox (abuse.ch) | <https://threatfox-api.abuse.ch/api/v1/> (`POST`, `get_iocs`, up to 7 days) | Requires `THREATFOX_AUTH_KEY`; Auth-Key header required |

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

## Account, wallet and subscriptions

The separate `/account` page provides sign-in, account creation, account details,
and logout. Wallet balances, simulated top-ups, subscription plans, purchases,
payments, receipts, and the single personal financial history are all managed in
**Banking Operations**. The Account page contains no financial controls or
transaction table. Existing
Lab 10 transfer/retry/inventory pages remain academic experiment views; their
saved or separate-bank records are not presented as purchases by the signed-in
wallet user. Plan
defaults are Free ₹0/100 credits, Plus ₹299/1,000 credits, and Pro ₹799/5,000
credits. Wallet top-ups and subscription payments are simulated INR amounts;
no payment gateway is connected. Each top-up and subscription purchase uses a
MongoDB transaction with snapshot reads, majority writes, and primary reads.
Changing plans charges the full monthly price without proration and replaces
the current monthly credit allocation. Rebuying an active plan is rejected.

Registration requires at least 12 characters with upper and lowercase letters,
a number, and a symbol. Passwords use PBKDF2-HMAC-SHA256 with unique salts and
are never printed by the application. The main dashboard and all threat,
VirusTotal, cross-source, and experiment data APIs require a MongoDB-backed
session. Anonymous visitors are redirected to `/account`, while protected API
requests return HTTP 401. The login page presents one form at a time. For HTTPS,
set `SESSION_COOKIE_SECURE=true`; local HTTP uses an HttpOnly SameSite=Lax
cookie without the Secure flag.

To create a normal account, open <http://localhost:8000/account>, choose
**Create account**, and complete registration. New accounts start on Free with
100 credits and a clearly labeled ₹1,000 simulated opening wallet. After signing
in, use Banking Operations to add simulated money or purchase a plan. Demo-user
initialization below is optional and is intended for local evaluation.

Configure demo passwords in the current PowerShell session before initializing
accounts. This creates `demo-free@aegis.local`, `demo-plus@aegis.local`,
`demo-pro@aegis.local`, and `demo-admin@aegis.local` once; existing accounts
are left unchanged. The platform wallet is created with a zero balance.

```powershell
Set-Location C:\Users\hp\Downloads\nosql-lab-7
function Set-DemoPassword([string]$Name) {
  $secret = Read-Host "Password for $Name (12+ chars; upper/lowercase, number, symbol)" -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
  try { [Environment]::SetEnvironmentVariable($Name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process') }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
Set-DemoPassword DEMO_FREE_PASSWORD
Set-DemoPassword DEMO_PLUS_PASSWORD
Set-DemoPassword DEMO_PRO_PASSWORD
Set-DemoPassword DEMO_ADMIN_PASSWORD
& .\.venv\Scripts\python.exe -m backend.init_subscription_demo
```

The module exposes `GET /api/subscriptions/plans`, register/login/logout,
session-backed `GET /api/subscriptions/me`, purchase quote, plan purchase,
simulated wallet top-up (`POST /api/subscriptions/wallet/top-up`), wallet
transfer, and the authenticated `GET /api/subscriptions/financial-activity`
list/detail API. The API derives the owner from the server session; user IDs in
query strings are not used to select private records. Admin-only summary and
audit routes also report the platform wallet balance. Passwords are PBKDF2-SHA256 hashed
with unique salts. Session tokens are random, stored only as SHA-256 hashes,
and expire through a MongoDB TTL index after seven days. The browser session
cookie is HttpOnly and SameSite=Lax; set `SESSION_COOKIE_SECURE=true` when
serving HTTPS. Never put demo passwords in `.env.example`, source control, or
Docker build arguments.

Financial activity reuses these MongoDB collections; it does not copy legacy
Lab 10 experiment snapshots into personal history:

| Collection | Purpose in financial activity |
|---|---|
| `wallet_transactions` | Canonical per-user committed entries, including type, status, amount/currency, balances, plan/credit changes, related IDs, metadata, and idempotency key. |
| `wallets`, `wallet_ledger` | Current balances and atomic money deltas. |
| `subscription_payments`, `subscription_history` | Payment and plan-change records joined by transaction ID for new operations. |
| `credit_ledger` | Plan allocations and feature-credit deductions. |
| `wallet_operations`, `subscription_receipts` | Idempotency results, printable receipt details, and separately surfaced rolled-back failures. |

The history API supports type/status/date/search filters and page-based results.
Indexes cover per-user date/type/status lookup, unique per-user transaction IDs,
idempotency keys, and one failure receipt per operation key. Add Money, plan
changes, and wallet transfers insert their canonical activity rows in the same
snapshot/majority/primary MongoDB transaction as balances, ledgers, payments,
credits, and receipts. A reusable `record_financial_transaction()` writer and
`charge_feature_credits()` transaction service are available to future paid
features. Current threat features are not credit-metered, so the UI does not
invent feature charges. There is no wallet refund endpoint today; the common
writer accepts refunded operations when a real refund service is added.

To demonstrate the workflow, sign in at `/account`, open Banking Operations,
add simulated funds, and choose Plus or Pro. The result appears in Banking
Operations → Transaction history, where filters, pagination, and Details show its receipt and
ledger links. An insufficient-balance purchase is rejected and shown as a
rolled-back failure without a committed money-history row. The separate
`POST /api/subscriptions/transfer` route records one outgoing and one incoming
private row for the respective wallet owners. The Lab 10 bank transfer
demonstration remains separate because the configured app does not associate
its experimental account numbers with subscription users.

Focused tests:

```powershell
& .\.venv\Scripts\python.exe -m unittest test.test_subscriptions -v
& .\.venv\Scripts\python.exe test\test_integration.py http://localhost:8000
& .\.venv\Scripts\python.exe -m py_compile backend\services\subscriptions.py backend\routes\subscriptions.py backend\init_subscription_demo.py backend\main.py
node --check public\account.js
node --check public\banking-finance.js
./scripts/health_check.ps1
```

The source checkout's backend service is `lab7-backend` in
`docker-compose.yml`; Docker must already be running with its configured
replica set. To build and replace only this backend service with a new local
tag, preserving MongoDB containers and volumes:

```powershell
Set-Location C:\Users\hp\Downloads\nosql-lab-7
docker build --file Dockerfile.upgrade --tag aegis-cti-financial:2026-10-29-r4 .

# Keep the old backend container for rollback; only the backend is stopped.
docker stop fresh-backend
docker rename fresh-backend fresh-backend-rollback-20261029-r12
$results = (Resolve-Path .\labs\lab9\results).Path
docker run --detach --name fresh-backend --network fresh-mongo-net `
  --restart unless-stopped --env-file .env `
  --env MONGO_URI='mongodb://fresh-mongo1:27017,fresh-mongo2:27017,fresh-mongo3:27017/?replicaSet=rs0' `
  --env MONGO_DATABASE=chapter07 --env FRONTEND_ORIGIN=http://localhost:8000 `
  --env FRONTEND_API_URL= `
  --env LAB9_RESULTS_DIR=/app/labs/lab9/results `
  --mount "type=bind,source=$results,target=/app/labs/lab9/results" `
  --publish 8000:8000 --publish 18000:8000 aegis-cti-financial:2026-10-29-r4
Invoke-RestMethod http://localhost:8000/health
Start-Process http://localhost:8000/account
```

If the replacement fails health checks, preserve it and roll back without
deleting either container:

```powershell
docker stop fresh-backend
docker rename fresh-backend fresh-backend-failed-20261029
docker rename fresh-backend-rollback-20261029-r12 fresh-backend
docker start fresh-backend
```

The repository's existing `start-project.ps1` is a separate safe starter for
the already-created `fresh-*` containers; it starts the current backend but
does not rebuild or recreate it. The build above reuses the locally installed
dependencies in `lab7-threat-intel:latency-fix`, writes a new tag, and keeps
the old image tag available for rollback.

For a machine that does not already have `lab7-threat-intel:latency-fix`, build
from the normal Python base instead (this downloads the packages in
`requirements.txt`):

```powershell
docker build --tag aegis-cti-auth:2026-10-29-r5 .
```

Do not push until the Docker Hub username and repository are confirmed. After
confirmation, replace the placeholder with the intended namespace:

```powershell
docker login
docker tag aegis-cti-auth:2026-10-29-r5 <DOCKERHUB_USER>/<REPOSITORY>:2026-10-29-r5
docker push <DOCKERHUB_USER>/<REPOSITORY>:2026-10-29-r5
```

### Current feature map

| Page / operation | Existing or added behavior |
| --- | --- |
| Live Threat Stream, Top Threats, Report Corpus, Search & Enrich | Existing public threat data, filters, search, detail and VirusTotal UI remain available. |
| Cross-Source Analysis | Existing cross-source routes and analysis view remain available. |
| Working Set Monitor, Write Concern Latency, Network Reliability | Existing Experiment 7–9 monitoring and experiment views remain available. |
| Banking Operations | Single place for personal wallet summaries, simulated deposits, plan selection and purchases, receipts, and private transaction history. Lab 10 saved transfers remain separately labeled experiment data. |
| Reliability & Inventory | Existing Lab 10 retry, reservation, and saved benchmark views remain available. |
| Account & Subscription (`/account`) | Account creation/sign-in, account identity, and logout; all wallet and subscription management is in Banking Operations. |
| Admin Dashboard | Session- and role-protected user, subscription, wallet, transaction, dashboard, and audit views backed by existing records. See [ADMIN_GUIDE.md](ADMIN_GUIDE.md) for one-time local administrator setup. |
| Security administration APIs | Existing backend security mechanisms and authenticated routes are retained; the separate Security Center page and navigation are removed. |

Administrator metrics use the existing account and financial collections.
“Active users” is based on successful logins in the preceding 30 days; accounts
created before login timestamps were introduced have no inferred activity.
Financial activity remains read-only in the Admin Dashboard; it does not repair
or rewrite historical data.
The Experiment 10 original submission remains in
`C:\Users\hp\Downloads\Chapter_10_Submission\Chapter_10_Submission`; it is not
part of this app's build context and has not been modified.

See [SECURITY_CENTER.md](SECURITY_CENTER.md) for verified deployment state, safe MongoDB role/TLS prerequisites, and Security Center endpoints.

See [BILLING_AND_ENTITLEMENTS.md](BILLING_AND_ENTITLEMENTS.md) for the Free wallet cap, monthly plan access, configurable Plus limits, and transaction-history behavior.
See [ADMIN_GUIDE.md](ADMIN_GUIDE.md) for administrator credential setup and reporting definitions.
