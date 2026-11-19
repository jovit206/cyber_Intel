# Chapter 15 Execution Report

**Scope:** Labs 15.1 and 15.2, including local Docker build and runtime checks.  
**Execution date:** 2026-11-11.  
**MongoDB target:** the previously absent, dedicated `tasktracker` database on the existing local
MongoDB replica-set deployment. No credentials or connection string are recorded in this report.

## Status summary

| Area | Status | Evidence |
|---|---|---|
| FastAPI authentication and task CRUD | PASS | Real MongoDB integration suite: 7 passed |
| Tenant isolation and role-based access | PASS | Cross-tenant and regular-user/admin integration tests passed |
| MongoDB-backed rate limiting | PASS | Integration test returned 429 and a positive `Retry-After` |
| Seed dataset | PASS | 500 records: exactly 100 for each of five tenants |
| Tenant collection migration | PASS | Five collections created; source collection retained |
| Migration integrity and isolation | PASS | 500 records checked; no missing/extra IDs, field mismatches, or cross-tenant records |
| Measured strategy benchmark | PASS | Six measurements written to CSV; SVG generated from the same run |
| Docker image build | PASS | `jovit206/lab-15:1.0` built locally |
| Docker API runtime | PASS | `/health` returned `{"status":"ok"}`; runtime UID/GID is 10001 |
| Docker command override | PASS | `python -m scripts.validate_migration` returned `PASS` inside the image |
| Docker Hub publication | NOT RUN | No push was requested or authorized |

## Implementation files

- `README.md`
- `Dockerfile`, `.dockerignore`, `.gitignore`, `.env.example`, `requirements.txt`
- `app/__init__.py`, `app/main.py`, `app/models.py`, `app/database.py`, `app/auth.py`,
  `app/dependencies.py`, `app/rate_limiter.py`
- `app/routes/__init__.py`, `app/routes/users.py`, `app/routes/tasks.py`
- `scripts/__init__.py`, `scripts/seed_data.py`, `scripts/migrate_tenants.py`,
  `scripts/validate_migration.py`, `scripts/benchmark_strategies.py`, `scripts/test_api.py`
- `tests/conftest.py`, `tests/test_tasks.py`, `tests/test_tenant_isolation.py`,
  `tests/test_rbac.py`, `tests/test_rate_limit.py`
- `reports/architecture_recommendation.md`, `reports/migration_validation.md`,
  `reports/performance_comparison.csv`, `reports/performance_comparison.svg`

## Commands executed and actual results

### Dependency installation and syntax

- Installed `lab-15/requirements.txt` into the repository's existing `.venv`.
- `python -m compileall -q lab-15/app lab-15/scripts lab-15/tests` — **PASS**.

### API integration suite

Ran `python -m pytest -q` from `lab-15` against MongoDB. The test fixture used a generated
`tasktracker_test_*` database and dropped that exact generated database in teardown.

- **Result:** 7 passed, 0 failed.
- Covered registration/login and bad credentials, CRUD, cross-tenant read/update/delete isolation,
  admin versus regular-user access, tenant override rejection, invalid bearer tokens, and
  MongoDB-backed rate limiting.
- One non-failing dependency warning reported that the installed Starlette version deprecates
  using `httpx` with `TestClient`; the tested calls succeeded. No speculative dependency change was
  made for this warning.

The first test run exposed invalid reserved `.test` email domains and a rate-limit test that left
shared in-process limiter configuration modified. Test emails were changed to valid
`example.com` addresses, and limiter settings are now restored in `finally`. The complete suite
was rerun successfully after both corrections.

### Seed and migration

Before seeding, queried the target and observed `database_exists: False`, no collections, and zero
tasks. This confirmed that no prior `tasktracker` data would be overwritten.

Executed, in order:

1. `python -m scripts.seed_data`
2. `python -m scripts.migrate_tenants`
3. `python -m scripts.validate_migration`

Results:

- Seed created 500 documents, with 100 in each of `tenant_1` through `tenant_5`.
- Migration created five tenant collections and migrated 500 documents.
- Validation status: **PASS**.
- Per tenant: source 100, destination 100, missing IDs 0, extra IDs 0, field mismatches 0,
  wrong-tenant documents 0.
- Original `tasks` collection was retained.

The generated evidence is in `reports/migration_validation.md`.

### Benchmark measurements

Ran `python -m scripts.benchmark_strategies` with 25 iterations per operation on the 500-document
dataset. These values are observations from this machine and local MongoDB instance, not
generalized performance claims.

| Strategy | Operation | Iterations | Average seconds |
|---|---|---:|---:|
| Document-level | List one tenant | 25 | 0.007405108 |
| Collection-per-tenant | List one tenant | 25 | 0.004865492 |
| Document-level | Create one task | 25 | 0.032608792 |
| Collection-per-tenant | Create one task | 25 | 0.015811792 |
| Document-level | Cross-tenant analytics | 25 | 0.003719324 |
| Collection-per-tenant | Cross-tenant analytics | 25 | 0.024336248 |

The benchmark verified that both strategies returned the same cross-tenant status counts before
recording timings. `reports/performance_comparison.csv` and
`reports/performance_comparison.svg` contain the measurements.

### Docker

- `docker build -t jovit206/lab-15:1.0 .` — **PASS**.
- Started a temporary container on the existing MongoDB Docker network and queried
  `http://127.0.0.1:18015/health` — **PASS**, returned `{"status":"ok"}`.
- Verified the API process ran as `uid=10001(tasktracker) gid=10001(tasktracker)`.
- Ran `python -m scripts.validate_migration` by overriding the image command — **PASS**, 500
  documents and all tenant integrity checks verified.
- The temporary API container was stopped after testing.
- No image was pushed to Docker Hub.

## Docker reproduction

From `lab-15`:

```powershell
docker build -t jovit206/lab-15:1.0 .
docker run --rm --network <mongo-docker-network> -p 8000:8000 `
  -e MONGO_URI="<MongoDB URI reachable from the Docker network>" `
  -e TASKTRACKER_DB_NAME=tasktracker `
  -e JWT_SECRET="<random secret with at least 32 characters>" `
  jovit206/lab-15:1.0
```

To run a script instead of the default Uvicorn command, override the entrypoint. For example:

```powershell
docker run --rm --network <mongo-docker-network> `
  -e MONGO_URI="<MongoDB URI reachable from the Docker network>" `
  -e TASKTRACKER_DB_NAME=tasktracker `
  --entrypoint python jovit206/lab-15:1.0 -m scripts.validate_migration
```

## Remaining manual steps and boundaries

- Configure production MongoDB TLS, authentication, least-privilege users, secret management,
  monitoring, and network policy before deployment.
- The measured dataset contains only 500 documents; repeat the benchmark with representative
  production data and load before making capacity decisions.
- Review the migration evidence and determine a cutover/rollback window before application reads
  are switched to tenant collections. The current migration intentionally retains the shared
  source.
- Push to Docker Hub only after explicitly authorizing publication and confirming the account.
- The image is built locally and not published. No external service or paid resource was created.
