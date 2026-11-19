# AEGIS Threat Intelligence Platform: verified overview

This overview was written from the repository's code and documentation. It is one of the sources
searched by the AI Threat Assistant. Every statement names the file that implements it.

## What the platform does

AEGIS is a FastAPI and MongoDB threat-intelligence platform built through a NoSQL course. It
ingests public threat-intelligence feeds (MISP feeds such as CIRCL and Botvrij, ThreatFox, MISP
Galaxies, annual security reports, and VirusTotal lookups), stores them in MongoDB, and presents
them in a single-page dashboard. Around that core it implements course experiments on schema
design, working sets, replica sets, write concern, network partitions, transactions, sharding,
high availability, monitoring and backup, multi-tenancy, polyglot persistence, retrieval-augmented
generation (RAG) and a comparative multi-database threat-intelligence capstone.

## Architecture

- Backend: FastAPI application in `backend/main.py`. It connects to MongoDB at start-up
  (`backend/database.py`), starts an APScheduler background scheduler for feed synchronisation and
  metric samplers, registers about 25 API routers from `backend/routes/`, and serves the frontend
  from `public/` as static files.
- Layering: `backend/routes/` contains thin FastAPI routers; `backend/services/` contains business
  logic and MongoDB access; `backend/collectors/` contains the external threat-feed collectors.
- Frontend: plain HTML, CSS and JavaScript in `public/` (no framework). `index.html` is a
  single-page dashboard; each sidebar view has its own script (for example `app.js`,
  `reliability.js`, `lab10.js`, `lab11.js`, `ai-threat-assistant.js`). `account.html` is the
  sign-in and registration page.
- Database: MongoDB 7.0 replica set `rs0` (three members) holds the application database
  `chapter07`. Conversation memory for the assistant is stored in the `rag_demo` database.
  Chapter 18 uses its own database `threat_intel_lab18`.
- LLM: Ollama serving `llama3.2:3b` (configured by `OLLAMA_BASE_URL` and `OLLAMA_CHAT_MODEL`,
  provider selection in `backend/services/llm_provider.py`).
- Deployment: Docker. `Dockerfile` builds the backend image; `docker-compose.yml` defines the
  three MongoDB members, a one-shot replica-set initialiser, Ollama, a model-pull job, the backend
  and the Chapter 14 backup worker.

## Request flow and security

Every `/api/` request passes the `require_platform_session` middleware in `backend/main.py`.
It reads the `aegis_session` cookie, hashes it with SHA-256, looks it up in
`subscription_sessions`, and loads the user from `subscription_users`. Anonymous API calls get
HTTP 401 and anonymous dashboard requests are redirected to `/account`. Accounts with the
`manager` role are read-only. For non-admin users the middleware also reserves one use of a
metered feature for the current month (`backend/services/entitlements.py`); the reservation is
released if the request fails. Passwords are stored as PBKDF2-SHA256 hashes with a random salt
(310,000 iterations, `backend/services/subscriptions.py`). Sign-in is limited to 8 failed attempts
per 15 minutes per identity and client address. Admin-only routers check the role again with
`_admin_user` in `backend/routes/admin.py`.

## Experiments 1 to 18

The repository contains implementations for experiments (labs/chapters) 7 to 11 and 13 to 18.
There is no implementation, documentation or route for Experiments 1 to 6 or for a separate
Experiment 12 in this repository; the course tour (`backend/routes/course_tour.py`) states that no
Chapter 12 workspace is registered. Questions about those experiments cannot be answered from
this platform.

- Experiment 7 (schema analysis and working set): `backend/lab_7_1_schema_analysis.py` analyses the
  synchronised Galaxy clusters and attributes; `backend/lab_7_2_working_set.py` builds 100,000
  derived benchmark documents in `chapter07.threat_reports_real`; `backend/lab_7_2_activity_monitor.py`
  samples WiredTiger cache metrics. UI: Live Threat Stream, Top Threats, Report Corpus,
  Cross-Source Analysis, Search & Enrich and Working Set Monitor.
- Experiment 8 (replica set and failover): `lab8/lab_8_1_replica_set.py` verifies the
  three-member set and replication; `lab8/lab_8_2_failover_monitor.py` forces a step-down and
  observes the election. API: `/api/lab8/status` (admin only).
- Experiment 9 (write concern and network partition): `lab9/lab_01_write_concern_latency.py`
  measures w:1, w:majority and w:3 with and without journaling; `lab9/lab_02_simulate_partition.py`
  isolates one secondary from the Docker network and verifies recovery;
  `lab9/lab_03_failover_recovery.py` runs step-down, replication and node-restart tests on an
  isolated replica set. UI: Write Concern Latency and Network Reliability.
- Experiment 10 (transactions): `lab10/Activity_10.1/lab_10_1_bank_transfer.py` (bank transfers
  in multi-document transactions), `lab10/Activity_10.2/lab_10_2_inventory_reservation.py`
  (inventory reservation with optimistic concurrency and retries), plus retry/backoff and failover
  activities. UI: Banking Operations and Reliability & Inventory.
- Experiment 11 (sharding): `lab-11/` evaluates shard keys and compares timestamp-range
  with hashed-user sharding through `mongos` on the separate `c11-*` sharded cluster. UI: Sharding &
  Horizontal Scalability.
- Experiment 13 (high availability and Kubernetes): `lab-13/` deploys an authenticated
  three-member replica set with Docker Compose and Kubernetes, with Prometheus/Grafana monitoring
  and an optional Atlas Terraform configuration. Admin UI: exercise 13.
- Experiment 14 (monitoring, backup and restore): `lab-14/monitoring_dashboard.py`,
  `lab-14/backup_worker.py` and `lab-14/backup_verify.py`; restore drills only use databases
  named `chapter14_restore_test_*`. Admin UI: exercise 14.
- Experiment 15 (multi-tenant SaaS API): `lab-15/app/` is a FastAPI task service with JWT
  authentication, tenant-scoped authorisation and rate limiting. In the main platform,
  `backend/routes/cases.py` provides multi-tenant cases. Admin UI: exercise 15.
- Experiment 16 (polyglot persistence): `lab-16/` models data in MongoDB, Cassandra and Neo4j and
  provides a fraud-analysis service with per-backend circuit breakers. Admin UI: exercise 16.
- Experiment 17 (vector search and RAG): `backend/routes/vector_rag.py` and
  `backend/services/vector_rag.py` implement document ingestion, embeddings, Vector Search index
  management and grounded question answering. Admin UI: exercise 17.
- Experiment 18 (comparative multi-database threat intelligence): `backend/services/chapter18_*.py`
  imports one threat dataset into MongoDB (`threat_intel_lab18`), Cassandra and Neo4j, compares
  queries and produces reports. Admin UI: exercise 18.

How they connect: Experiment 7 builds the threat-intelligence data and the working-set workload on
the same MongoDB database that Experiment 8 turns into a replica set. Experiment 9 measures that
replica set's write concern and partition behaviour. Experiment 10 adds transactional banking on
top of the replica set. Experiment 11 explores horizontal scaling on a separate sharded cluster.
Experiments 13 and 14 cover deployment, monitoring and backup of the database tier. Experiment 15
adds tenant isolation, 16 adds Cassandra and Neo4j, 17 adds the RAG assistant, and 18 combines the
threat-intelligence data with all three databases in a capstone.

## Banking Operations, wallets and subscriptions

Implemented in `backend/services/subscriptions.py` and `backend/routes/subscriptions.py`; UI in the
Banking Operations view (`public/banking-finance.js`).

- Every account gets a wallet document in `wallets` (balances are simulated INR; no payment
  gateway). Registration creates the user, the wallet, a ₹500 simulated opening balance and the
  plan's credit allocation in one transaction.
- Add Money (`POST /api/subscriptions/wallet/top-up`), plan purchase (`POST
  /api/subscriptions/purchase`) and transfers all run through `transaction_receipt`, which executes
  the change in a MongoDB multi-document transaction (snapshot read concern, majority write concern)
  and retries transient transaction errors with bounded exponential backoff.
- Idempotency: each request carries an `idempotency_key`. The committed result is stored in
  `wallet_operations` (unique key) and a replay returns the original receipt instead of charging
  again. A key reused for a different operation is rejected with HTTP 409.
- Negative balances are prevented by conditional updates: a debit only matches
  `balance >= cost`, and a top-up only matches the balance that was read and stays under the
  plan's wallet limit (₹500 on the Free plan).
- Every committed change writes `wallet_ledger` entries, a canonical `wallet_transactions` row and a
  `subscription_receipts` receipt. Failed operations are rolled back and a `rolled_back` receipt is
  recorded.
- Plans: Free (₹0), Plus (₹299/month) and Pro (₹799/month). Free includes Live Threat Stream and
  Top Threats. Plus has monthly per-feature limits (for example 3 Search & Enrich operations, 10
  Working Set uses); Pro has no limits. Usage counters are stored in `feature_usage`.
- Credits: each plan allocates monthly credits (`credits_allocated`, recorded in `credit_ledger`).
  `charge_feature_credits` can deduct credits atomically, but no route calls it yet, so credits
  are allocated but not consumed by any current feature.
- Refunds: there is no user-facing refund operation. Summaries count rows with a `refunded` status
  or `refund` type if they exist, and failed operations are rolled back rather than refunded.
- Why Add Money can fail with "The operation could not be safely committed. No success was
  reported.": this message is returned when MongoDB rejects the transaction. The most common cause
  is a backend connected to a standalone `mongod`, which does not support multi-document
  transactions. The fix is to connect the backend to the replica set `rs0`.

## Network Reliability and the replica set

The Network Reliability view (`public/reliability.js`, API `/api/lab9/partition`, service
`backend/services/reliability.py`) reads live `replSetGetStatus` from the replica set the backend is
connected to and shows the set name, primary, secondaries, health, sync sources and uptime, plus the
saved partition and failover reports from `lab9/results/`. If the backend is connected to a
standalone server the page shows "Replica set status unavailable: not running with --replSet", which
means the server is not a replica-set member; it never reports a healthy cluster unless MongoDB
confirms it.

A replica set is required because primary election, replication, failover and write concerns above
w:1 only exist when several members replicate the same data. In a three-member set a majority is two:
if the primary becomes unavailable the remaining two members elect a new primary, and w:majority
writes need two members to acknowledge them.

## Known limitations (verified in the November 2026 audit)

- Experiments 1 to 6 and a separate Experiment 12 are not implemented in this repository.
- Plan credits are allocated but no current feature consumes them, and there is no user-facing refund
  operation (see Banking Operations above).
- Wallet operations need a replica set; on a standalone `mongod` they fail safely without changing data.
- The report corpus stores only as many annual reports as `SYNC_STARTUP_MAX_REPORTS` allows (2 by
  default), although the upstream list contains over a thousand.
- Threat-record retrieval is keyword based (case-insensitive word-prefix matching), not semantic, unless
  a Vector Search index and an embedding provider are configured; tangential matches are possible.
- The assistant uses a 3-billion-parameter model (`llama3.2:3b`). It can misattribute details, so
  threat answers are checked against the retrieved records and their citations.
- Answer speed depends on whether Ollama runs on the GPU; on CPU only, long answers take tens of
  seconds.
- The Network Reliability page reports the replica set the backend is connected to. Failover tests that
  stop or step down members run on a separate, isolated replica set so the application is not disrupted.

## Threat-intelligence ingestion and storage

Collectors in `backend/collectors/` run at start-up and every 24 hours (feeds), on their own poll
intervals (VirusTotal, MalwareBazaar), and write to `chapter07`:
`events` (MISP events and feed records), `attributes` (indicators, with a unique MISP attribute UUID
and `is_current` flag), `threats` (MISP Galaxy clusters such as threat actors and malware),
`reports` and `passages` (annual security reports and extracted passages), and `virustotal_cache`.
Records keep their source provenance (`source_feed_name`, `source`) and the application never
invents threat names, indicators or scores. Duplicates are prevented by unique indexes on source
identifiers.

## AI Threat Assistant

Frontend `public/ai-threat-assistant.js`; API `/api/ask`, `/api/ask/stream`, `/api/ask/history`.
Each question is routed by `backend/services/assistant.py`:

- Platform, experiment and technical questions are answered from a searchable knowledge base built
  from the project's documentation, source-code docstrings, the live list of API routes, the
  frontend navigation, the Docker configuration and live MongoDB collection statistics
  (`backend/services/platform_knowledge.py`).
- Specific threat, CVE and IOC questions search the stored MongoDB threat records
  (`backend/services/threat_retrieval.py`) and must cite them; an answer with invalid citations or a
  CVE identifier that is not in the evidence is not shown.
- General cybersecurity questions use the model's general knowledge and are labelled as such.
- Mitigation questions combine any matching platform evidence with clearly labelled general
  guidance.
- Unrelated questions receive an explanation of the assistant's purpose.

Local laptop security agent: `local_agent/aegis_local_agent.py` runs on the Windows host (127.0.0.1:8765) and is
exclusively a read-only cybersecurity scanner. It checks Microsoft Defender status and detections, suspicious executables
in Downloads/Temp/Startup (signature status and SHA-256), suspicious processes, persistence (run keys, startup folders,
scheduled tasks, auto-start services), network connections and listening ports, potentially unwanted applications,
patch level and hardening settings, suspicious PowerShell log entries, and exposed credentials in the approved project
folder (values redacted). Public IPs and file hashes it finds are matched against the platform's stored indicators.
General laptop management (storage, files, performance, software installation) is refused, both by the chat routing
and by a backend allowlist; requests are HMAC-signed, limited to administrators or approved accounts, logged, and
cancellable. The only action, a Microsoft Defender quick scan, requires the user's confirmation.

Answers are generated by Ollama (`llama3.2:3b`) and streamed to the browser as they are produced.
Conversations are stored per user in `rag_demo.conversations` and `rag_demo.conversation_metadata`;
the last few messages are used as memory for follow-up questions. If Ollama is unavailable, platform
questions return the most relevant knowledge-base excerpts and threat questions return the
retrieved evidence, each clearly marked as an extractive fallback; nothing is invented.
