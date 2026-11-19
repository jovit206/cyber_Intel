# Chapter 11 — Sharding & Horizontal Scalability

This experiment runs as an isolated MongoDB 7.0 sharded cluster. It does not reuse the Lab 7/8/9 replica set, the Chapter 10 banking deployment, their networks, ports, data directories, or volumes.

## Isolated topology and host ports

| Role | Containers | Replica set | Loopback host port(s) |
|---|---|---|---:|
| Config server | `c11-config1`, `c11-config2`, `c11-config3` | `c11-config-rs` | 37117–37119 |
| Shard 1 | `c11-shard1` | `c11-shard1-rs` | 37217 |
| Shard 2 | `c11-shard2` | `c11-shard2-rs` | 37218 |
| Router | `c11-mongos` | — | 37317 |

Network: `chapter11-sharded-net`. Named volumes: `chapter11-config1-data`, `chapter11-config2-data`, `chapter11-config3-data`, `chapter11-shard1-data`, and `chapter11-shard2-data`. Each MongoDB data service has a 256 MiB WiredTiger cache setting for this local lab. Host-published ports bind to loopback only.

`scripts/start.ps1` checks the dedicated host ports before creating the cluster, waits for health, initializes the config-server and shard replica sets idempotently, then registers the shards with `mongos`. It sets majority as the new cluster's default write concern before adding shards. Python lab scripts and tests refuse a direct `mongod` URI; application reads/writes go through `mongos` at `mongodb://localhost:37317`.

## PowerShell workflow

Run these commands from the project root. `-ExecutionPolicy Bypass` applies only to this PowerShell process; it avoids changing the machine-wide execution policy.

```powershell
Set-Location 'C:\Users\hp\Downloads\nosql-lab-7'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\chapter11\scripts\start.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\chapter11\scripts\status.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\chapter11\scripts\run-labs.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\chapter11\scripts\test.ps1
```

`run-labs.ps1` generates the Lab 11.1 matrix, upserts exactly 10,000 deterministic experiment documents into each Lab 11.2 collection, records live per-shard and query-plan measurements, and runs the automated checks. Re-running it updates the same experiment document IDs instead of creating duplicate rows. It does not drop collections or delete documents.

Outputs are stored under `reports/`:

- `lab11_1_decision_matrix.md` and `.json`
- `lab11_2_measurements.md` and `.json` (actual output captured from the running cluster; raw `explain()` plans included in JSON)

To stop the isolated services while retaining containers and data:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\chapter11\scripts\stop.ps1
```

To remove only Chapter 11 containers and its network while retaining all five data volumes, use the explicit cleanup command. PowerShell asks for confirmation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\chapter11\scripts\cleanup.ps1
```

Never add `--volumes` or `-v`. The cleanup script does not target `docker-compose.yml`, `lab8/docker-compose.yml`, `fresh-*`, `mongo*`, `bank-primary`, `bank-backup`, or `bank-dr` containers.

## Lab 11.1 — Evaluate shard key choices

Run `python lab11_1_shard_key.py` to generate the decision matrix. The stated example workload is a customer-centric order service with frequent customer equality lookups and sustained writes, common order-ID lookups, and secondary date-window reporting.

The matrix is calculated from the existing, persisted 1,400-row `chapter07.demo_financial_activity` Transaction Dataset. It compares `order_id`, `customer_id`, `order_date`, and hashed `customer_id` on measured cardinality and skew, live shard placement, query plans, range behavior, and monotonic-write risk. It refreshes only four dedicated `chapter11.transactions_by_*` collections and the Lab 11.1 reports. The recommendation is computed from the observed plans and displayed workload weights; a different dataset or cluster placement may produce a different winner. Hashed keys can spread writes across key ranges and target equality reads, but discard range ordering and can scatter range queries.

## Lab 11.2 — Hotspot and query routing experiment

The isolated database `chapter11` contains the independent Lab 11.2 benchmark collections:

- `orders_by_timestamp`, range-sharded on increasing `event_at`. Its time key has two ranges on separate shards so the report can compare historical distribution with the active latest-write range.
- `orders_by_hashed_user`, hashed-sharded on `user_id`, with one full-range zone shared by both shards so the empty-collection chunks can be initially distributed on both.

Both collections receive the same 10,000 reproducible order-shaped benchmark documents. They do not read or derive from the 1,400-row Transaction Dataset or the application database. The script measures actual owned document counts with `$shardedDataDistribution`, per-shard storage/count statistics with `$collStats`, and execution plans for an exact user lookup and a recent-date range through `mongos`. The measured distribution is never asserted to a fixed ratio; balancing, chunk ownership, server version, and placement can affect observed results. The report also lists the actual shard names used by each plan and labels scatter/gather only when the explain output identifies the targets.

The timestamp collection illustrates a range-key write edge: a future increasing timestamp writes to the newest chunk, even if older chunks and stored documents span multiple shards. A hashed user equality query can target one shard, while a date-range query on that collection scatters because the date is not the shard key. Conversely, the timestamp shard key targets date windows but scatters user lookups.

## MongoDB 5.0+ resharding notes

MongoDB 5.0 introduced the `reshardCollection` operation for changing an existing collection's shard key. The process is coordinated by the config-server primary: MongoDB determines a new distribution, clones records into recipient-side temporary collections, builds compatible indexes, applies writes that arrived during the copy, reaches a consistency point, then commits the new placement. The final phase can briefly block writes; resharding consumes additional storage, network, and shard capacity while old and new copies coexist. A resharding operation cannot be aborted after it reaches commit.

Before resharding, confirm the new key and supporting index, unique-index compatibility, available temporary disk space on donor/recipient shards, healthy replica sets, adequate network capacity, and no in-progress index build. Hashed shard keys cannot be unique. MongoDB Search indexes may need rebuilding after resharding; Queryable Encryption collections are not supported by resharding. MongoDB 8.0 added same-key redistribution with `forceRedistribution`; that option is not required for the MongoDB 7.0 experiment here. Resharding is a deliberate data movement operation and is not run by these labs.

Official references:

- [MongoDB `reshardCollection` command](https://www.mongodb.com/docs/manual/reference/command/reshardcollection/)
- [MongoDB sharding overview](https://www.mongodb.com/docs/manual/sharding/)
- [MongoDB hashed sharding](https://www.mongodb.com/docs/manual/core/hashed-sharding/)
- [MongoDB shard collection command and considerations](https://www.mongodb.com/docs/manual/reference/command/shardcollection/)
- [MongoDB `$collStats`](https://www.mongodb.com/docs/manual/reference/operator/aggregation/collstats/)
- [MongoDB 7.0 `$shardedDataDistribution`](https://www.mongodb.com/docs/v7.0/reference/operator/aggregation/shardeddatadistribution/)

## Application integration

The signed-in Threat Intelligence dashboard exposes this experiment at:

```text
http://localhost:8000/
```

Open **Lab Notebook → Chapter 11 → Sharding & Horizontal Scalability**. The page has separate **Shard Key Evaluation** and **Hotspot Analysis** tabs. Lab 11.1 charts and its decision matrix use the latest measured Transaction Dataset report. Lab 11.2 reads only its independent 10,000-document workload report. Missing reports or unavailable clusters show an unavailable state instead of invented metrics. Raw `explain()` plans and report downloads are available with the relevant experiment.

The frontend is served by the existing `fresh-backend` container; there is no separate frontend development server. To restart the current backend and reopen the application:

```powershell
Set-Location 'C:\Users\hp\Downloads\nosql-lab-7'
docker restart fresh-backend
Invoke-RestMethod http://localhost:8000/health
Start-Process http://localhost:8000/account
```

The current Chapter 11 UI build is `aegis-cti-financial:2026-10-30-ch11-ui`. Rebuild only when changing source files:

```powershell
docker build --file Dockerfile.upgrade --tag aegis-cti-financial:2026-10-30-ch11-ui .
```

Building an image does not replace the running container. Follow the backend replacement procedure below to deploy a rebuilt tag, keeping the existing container as rollback.

The bundled FastAPI service connects to the router at `mongodb://host.docker.internal:37317` and reads config-server health through the separate loopback-published status port `37117`. The Python lab scripts themselves still connect to mongos, never directly to shard members. The dashboard and all `/api/chapter11/*` endpoints require a platform session. Status and reports are read-only; run controls are shown to administrators only.

The application endpoints are:

| Endpoint | Purpose |
|---|---|
| `GET /api/chapter11/status` | mongos, shard registry, config replica-set health, collection counts, latest dashboard-run test status |
| `GET /api/chapter11/capabilities` | reports whether the signed-in user has the admin run capability |
| `GET /api/chapter11/reports/decision-matrix` | generated Lab 11.1 JSON |
| `GET /api/chapter11/reports/measurements` | measured Lab 11.2 data and raw explain plans |
| `GET /api/chapter11/reports/{name}?download=true` | downloads an allow-listed Markdown or JSON report |
| `POST /api/chapter11/advisor` | validates a workload and returns transparent rule-based advice and a two-strategy comparison |
| `POST /api/chapter11/runs` | queues `lab11_1` or `lab11_2`; admin-only, one active job, 180-second script timeout |
| `GET /api/chapter11/runs/{job_id}` | reads the in-memory job status and completion output |

The advisor supports e-commerce orders, customer transaction history, time-series events, threat intelligence, and custom profiles. It accepts a bounded expected-document count, write frequency, query patterns, range/recent-write/entity-scope preferences, and two distinct comparison strategies. Its returned score is a transparent rule-based fit signal, not an AI result or measured performance prediction. The UI explains range locality, targeted versus scatter/gather reads, write hotspots, and when to evaluate compound keys. Production use still requires workload testing.

### Restart or rebuild the existing backend

The frontend is served by `fresh-backend`; no separate frontend process is needed. The tested image is already deployed. Restart it and reopen the sign-in page with:

```powershell
Set-Location 'C:\Users\hp\Downloads\nosql-lab-7'
docker restart fresh-backend
Invoke-RestMethod http://localhost:8000/health
Start-Process http://localhost:8000/account
```

If source files change later, build a new versioned image tag:

```powershell
docker build --file Dockerfile.upgrade --tag aegis-cti-financial:2026-10-30-ch11-ui-next .
```

Building does not replace the running container. When deploying a rebuilt image, preserve the current `fresh-backend` environment, `fresh-mongo-net` network, ports 8000/18000, and Lab 9 results mount. Keep the stopped rollback container `fresh-backend-rollback-20261030-ch11-ui`; do not recreate or alter any MongoDB, Chapter 10 banking, or Chapter 11 containers.

To stop only the Chapter 11 cluster while retaining all data, use `scripts/stop.ps1`. The `cleanup.ps1` script removes only Chapter 11 containers and its network, with no volume option; verify its plan safely with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\chapter11\scripts\cleanup.ps1 -WhatIf`. Do not stop `bank-primary`, `bank-backup`, or `bank-dr` for this lab.

Troubleshooting:

- If status says mongos is unavailable, run `scripts/status.ps1`; confirm the router is listening on loopback port 37317 and that the app has the `CHAPTER11_MONGO_URI` runtime variable.
- If the config replica set shows unavailable, check `docker exec c11-config1 mongosh --quiet --port 27019 --eval "rs.status().members.map(m => ({name:m.name,state:m.stateStr,health:m.health}))"` and the Chapter 11 config logs.
- If a report is absent, start the cluster and run the matching lab; the UI marks missing reports as unavailable and does not synthesize results.
- If run controls are hidden, sign in with an account whose stored role is `admin`; regular users can inspect status/reports and use Shard Advisor but cannot launch database experiments.
- If builds fail, retain the running rollback container and image tag. Do not remove old images or data volumes as part of troubleshooting.

See [VIVA_GUIDE.md](VIVA_GUIDE.md) for a concise oral-exam summary.
