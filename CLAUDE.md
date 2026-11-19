# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI + MongoDB threat-intelligence platform ("AEGIS") built up across a NoSQL course: Lab 7 (dashboard, schema/working-set analysis), Lab 8 (3-member replica set `rs0`), Lab 9 (write-concern latency, network partition), Lab 10/11 and Chapters 13–18 (HA/Kubernetes, backup/monitoring, multi-tenant SaaS, polyglot persistence, RAG/vector search, multi-database threat intel). The root `backend/` + `public/` is one integrated app; `lab-13/` … `lab-18/` are mostly **self-contained sub-projects** with their own README, Compose file, requirements and tests (chapter 13/14/18 functionality is also surfaced in the main app via `backend/routes/chapter1x_*` and `backend/services/chapter1x_*`).

## Commands

```powershell
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000     # needs MONGO_URI / MONGO_DATABASE in .env

# Root tests (unittest-based; pytest also works)
python -m unittest discover -s test -p "test_*.py"
python -m unittest test.test_write_concern                         # one module
python -m unittest test.test_python_backend.NormalizationTests     # one class / append .test_name for one test

python smoke_test.py          # against a running server; fails on any empty section

# Sub-project tests (run from that chapter dir)
cd lab-15; python -m pytest -q
cd lab-16; python -m pytest -q .\lab-16.2\tests
```

Lab scripts are run as modules from the repo root, e.g. `python -m backend.lab_7_2_working_set`, `python lab9/lab_01_write_concern_latency.py --dry-run`, `python lab9/lab_02_simulate_partition.py --dry-run` (the partition script is destructive to the cluster without `--dry-run` and asks for typed confirmation). `python backup_db.py` exports every collection to `backups/` as JSON (no `mongodump` on this machine).

## AI Threat Assistant

- `/api/ask` and `/api/ask/stream` (NDJSON events `meta`, `token`…, `final` | `error`) in `backend/routes/chapter17_compat.py` delegate to `backend/services/assistant.py`, which routes each question (`platform`, `threat`, `mitigation`, `general_security`, `unrelated`) and streams from Ollama (`stream_chat_answer` in `llm_provider.py`). Conversations persist in `rag_demo.conversations` / `conversation_metadata` only after a `final` event.
- Platform questions are answered from `backend/services/platform_knowledge.py`: an in-memory BM25 index over Markdown docs (including `backend/knowledge/PLATFORM_OVERVIEW.md`, the curated, code-verified overview), Python docstrings, live API routes, sidebar navigation, Compose files and live Mongo stats. Rebuilt only when files change. Keep `PLATFORM_OVERVIEW.md` accurate when features change.
- Threat questions use `threat_retrieval.py`; answers failing `validate_grounded_answer` are replaced by the cited records. The admin RAG workspace (`/api/rag/query`, `query_rag`) is a separate, older pipeline.
- FastAPI here wraps included routers (`_IncludedRouter`), so `app.routes` has no `/api/...` paths; use `backend/utils/routes.py` (`iter_routes`, `route_paths`).
- Ollama runs on the host; the container reaches it at `http://host.docker.internal:11434` (allowed as a local HTTP host).

## Deployment — important machine-specific caveat

The running deployment on this machine is the pre-existing containers `fresh-mongo1/2/3` (replica set `rs0`, members advertised as `mongo1..3`, network aliases on `fresh-mongo-net`, host ports 37017–37019) + `fresh-backend` (port `127.0.0.1:8000`, image `aegis-cti-final:lab-rename-20261119`, bind mount `G:\Projects\nosql-lab-7\lab9\results` → `/app/lab9/results` with `LAB9_RESULTS_DIR=/app/lab9/results`, Ollama env vars set). Rollback containers: `fresh-backend-pre-lab-rename-20261119` (before the `lab-1x` folder rename) and `fresh-backend-pre-audit-20261119`. Wallet/registration operations need multi-document transactions, i.e. the replica set — a standalone `mongod` (the Windows service on 27017) makes them fail with "could not be safely committed".

The Lab 9.3 failover tests (`lab9/lab_03_failover_recovery.py`) run only on the isolated replica set `rsfail` from `lab9/failover-cluster/docker-compose.yml` (`netrel-mongo1..3`, network `netrel-net`, no host ports); the script refuses containers without the `aegis.purpose=lab9-isolated-failover-tests` label. Results go to `lab9/results/lab_03_failover_latest.json` and appear on the Network Reliability page.

Restart the main deployment with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-project.ps1 [-NoBrowser]
```

Do **not** use `docker compose up` on the root `docker-compose.yml` as a fix/restart — it describes a different `mongo1/2/3` stack on port 27017 and must not run alongside the separate Chapter 10 `bank-primary/bank-backup/bank-dr` containers. Never recreate containers or delete volumes without asking.

## Backend architecture

- `backend/main.py` is the composition root: a `lifespan` connects MongoDB (`backend/database.py`, module-level `client`/`database`, stored on `app.state.database`), starts an APScheduler `BackgroundScheduler` (feed/Galaxy/report sync on startup then every 24h, plus samplers — see `backend/collectors/`), includes ~25 routers, and finally mounts `public/` as static files at `/` (so routes must be registered before the mount).
- **Auth/entitlement middleware** (`require_platform_session` in `main.py`) guards every `/api/*` path except a small `public_api` allowlist, plus `/` and `/index.html`. It resolves the `aegis_session` cookie (SHA-256 hashed) against `subscription_sessions` / `subscription_users`, makes `manager` role read-only (non-GET → 403), and for non-admins reserves a per-month feature quota via `backend/services/entitlements.py` (`FEATURE_PATHS` maps paths to features; 402 on exhaustion; reservation released if the handler raises or returns ≥400). Adding a new public endpoint means editing that allowlist; adding a metered feature means editing `FEATURE_PATHS`.
- A second middleware (`track_system_operations` → `backend/services/operations.py`) records request telemetry in `system_operations` (route template, status, duration only — no bodies, query values, or user identifiers).
- Layering: `routes/` (thin FastAPI routers, Pydantic models, often `_admin_user` / `_audit` from `routes/admin.py` for admin-only endpoints) → `services/` (logic, Mongo access) → `collectors/` (external CTI sources: MISP feeds, Galaxies, reports, VirusTotal/ThreatFox). `*_compat.py` routers are URL aliases for course-specified endpoints backed by the same services.
- `backend/config.py` holds a `settings` object loaded from env / `.env` (with `*_FILE` secret fallbacks). `backend/services/llm_provider.py` selects the RAG chat provider server-side (Ollama default; OpenAI-compatible if legacy RAG settings exist or `LLM_PROVIDER` is set).
- Frontend is vanilla JS/CSS in `public/` (one `*.js` per dashboard section, `index.html` SPA, `/account` sign-in page, `config.js` for API base URL). `.bak` / `.metadata.json` files there are not used by the app.

## Data-integrity rules (project-wide conventions)

- Never invent threat names, indicators, scores, or locations; records keep source provenance. Benchmark/lab data must be derived from real source records and clearly marked, and removed by exact benchmark IDs.
- App startup must not drop or replace collections. Chapter-specific features write to isolated databases/collections (e.g. Chapter 18 uses `threat_intel_lab18`; Chapter 14 restores only into `chapter14_restore_test_*`).
- Secrets (VirusTotal key, etc.) live only in the ignored `.env`; never expose them in HTML/JS, API responses, or logs.

Further docs: `ADMIN_GUIDE.md` (admin bootstrap via `backend/bootstrap_*.py`), `BILLING_AND_ENTITLEMENTS.md`, `SECURITY_CENTER.md`, and each `lab-13`…`lab-18` `README.md`.
