# Chapter 15 — Multi-Tenant SaaS API and Tenant Data Isolation

This chapter builds a FastAPI task service with JWT authentication and tenant-scoped
authorization, then seeds and migrates a 500-document dataset from one shared collection into
five tenant-specific collections. It measures the two storage strategies on the local MongoDB
deployment and writes both a CSV and SVG comparison. The migration leaves the original
`tasks` collection intact as its source and rollback reference.

## Labs and outcomes

### Lab 15.1: Multi-Tenant Task API

- FastAPI endpoints for registration, login, current-user lookup, health, and task CRUD.
- Password hashes use Argon2; signed JWTs carry the user identity and tenant identity.
- The first registered user for a tenant receives the `admin` role. Later users are regular users;
  role assignment is server-side and request models reject unknown fields.
- Every task query includes the tenant from the authenticated user. Admins can manage their
  tenant's tasks; regular users can access only their own tasks.
- A MongoDB-backed fixed-window request limiter is applied to API routers. Limits are configurable.

### Lab 15.2: Tenant Data Migration and Performance

- Idempotently seed five tenants with 100 identifiable tasks each (500 total).
- Copy, without deleting the source, to `tasks_tenant_tenant_1` through
  `tasks_tenant_tenant_5`, preserving document IDs and fields.
- Validate exact counts, identifiers, fields, and tenant boundaries.
- Benchmark per-tenant list, create/delete, and cross-tenant analytics; report measured averages
  in `reports/performance_comparison.csv` and `reports/performance_comparison.svg`.

## Prerequisites

- Python 3.12 or newer (the Docker image uses Python 3.12).
- MongoDB 5.0+ reachable from the host or Docker network. A replica set is recommended for
  production; this lab does not require transactions.
- Docker Desktop only if running the container image.
- For PowerShell, open a terminal in `lab-15`.

Install dependencies in a virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set the connection and signing secret in the current PowerShell session. Use a user with
permissions only on the dedicated lab database where possible:

```powershell
$env:MONGO_URI = "mongodb://<username>:<password>@<host>:27017/?authSource=admin"
$env:TASKTRACKER_DB_NAME = "tasktracker"
$env:JWT_SECRET = "<random-secret-at-least-32-characters>"
```

Do not commit real `.env` files, passwords, signing secrets, or connection strings. `.env.example`
contains placeholders only.

## Main application tenant workflow

The main AEGIS application also exposes cookie-session-authenticated case routes under
`/api/cases`. It uses organization memberships and additive `tenant_id` fields on new case records;
legacy documents remain query-scoped by their existing `organization_id`. This is separate from
the standalone chapter's bearer-token task API. The main application's case limit is 30 requests
per user per rolling minute, enforced by MongoDB and returned as HTTP 429 with `Retry-After`.

An optional compatibility migration is provided at `scripts/migrate_cases_tenant_id.py`. It reports
eligible legacy cases by default and writes only when explicitly invoked with `--apply`. It was not
run as part of this implementation. Review the dry-run count and take a database backup before
applying it; it does not delete or replace existing fields or documents.

## Run the API (Lab 15.1)

From `lab-15`:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Confirm readiness and open the interactive API documentation:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Start-Process http://127.0.0.1:8000/docs
```

Routes:

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register` | Register a user; first user in a tenant becomes admin |
| POST | `/auth/login` | Authenticate and return a bearer token |
| GET | `/users/me` | Return the authenticated user's profile |
| POST | `/tasks/` | Create a task for the caller's tenant/user |
| GET | `/tasks/` | List tasks visible to the caller (`status`, `limit`, `skip` supported) |
| GET | `/tasks/{task_id}` | Get an authorized task |
| PUT | `/tasks/{task_id}` | Update an authorized task |
| DELETE | `/tasks/{task_id}` | Delete an authorized task |

Use the returned `access_token` as `Authorization: Bearer <token>`. Tenant scope comes from the
verified token and database user record, not from task request fields. The database creates unique
email and tenant/username indexes, tenant-first task indexes, and a TTL index for expired rate
limit records when the service starts.

## Seed, migrate, validate (Lab 15.2)

Use a **new, dedicated** database name for the lab. The scripts intentionally refuse to seed
colliding unrelated documents, require exactly five expected tenant IDs for migration, and never
drop the source collection.

```powershell
$env:TASKTRACKER_DB_NAME = "tasktracker"
python -m scripts.seed_data
python -m scripts.migrate_tenants
python -m scripts.validate_migration
```

Expected seeded output: five tenants, 100 marked documents each (500 total). Migration output
reports five destination collections and 500 documents. The validator writes
`reports/migration_validation.md`; its status must be `PASS`, with 100 source and destination
records per tenant, preserved IDs and fields, and no cross-tenant records.

The migration is repeatable because destination writes are replace/upserts keyed by the original
`_id`. The shared source collection remains untouched. For rollback, continue using `tasks` and
do not remove tenant collections until the migration is reviewed and no longer needed.

## Run tests

The suite is an integration suite and requires a reachable MongoDB URI. It creates an isolated,
randomly named `tasktracker_test_*` database and drops only that exact test database in fixture
teardown. It does not use or clear the migration database.

```powershell
python -m pytest -q
# Or use the guarded helper:
python -m scripts.test_api
```

Tests cover task CRUD, login, invalid credentials/tokens, tenant isolation for read/update/delete,
regular-user versus admin permissions, rejection of tenant override fields, and MongoDB rate
limiting. A test failure or interrupted run may leave its uniquely named test database behind;
inspect it before removing it.

## Compare strategies

After seeding and migrating:

```powershell
$env:BENCHMARK_ITERATIONS = "25"
python -m scripts.benchmark_strategies
```

Actual timings are written to `reports/performance_comparison.csv`; the companion
`reports/performance_comparison.svg` is generated from those same measurements. Results are
machine-, workload-, cache-, and server-dependent and are not promises about production
performance. The benchmark requires exactly 100 source and destination tasks for each of the
five expected tenants. Create tests use a unique temporary ObjectId and remove only that
benchmark record afterward; no seeded task is updated or deleted.

The recorded 25-iteration run on this environment measured 0.007405108 s versus 0.004865492 s
for one-tenant listing, 0.032608792 s versus 0.015811792 s for create, and 0.003719324 s versus
0.024336248 s for cross-tenant analytics (document-level versus collection-per-tenant). These
are specific to the local 500-document dataset; see
[`CHAPTER_15_EXECUTION_REPORT.md`](CHAPTER_15_EXECUTION_REPORT.md) for all test and runtime
evidence.

## Architecture discussion

See [`reports/architecture_recommendation.md`](reports/architecture_recommendation.md) for a
comparison of isolation, query behavior, analytics, backups, schema changes, scale, and
maintenance. The recommendation is to begin with tenant-keyed documents for most SaaS products,
with strict query helpers and adversarial isolation tests. Use per-tenant collections or a hybrid
only when measured performance or business/regulatory requirements warrant the additional
catalog, index, migration, and operational work. A separate collection is not a substitute for
authentication, authorization, MongoDB privileges, or encryption.

## Docker

Build from this directory:

```powershell
docker build -t jovit206/lab-15:1.0 .
```

Run on the same Docker network as MongoDB. Replace placeholders locally; do not put real
credentials in the Dockerfile or checked-in files:

```powershell
docker run --rm --name chapter15-api `
  --network <mongo-docker-network> `
  -p 8000:8000 `
  -e MONGO_URI="mongodb://<username>:<password>@<mongo-service>:27017/?authSource=admin" `
  -e TASKTRACKER_DB_NAME=tasktracker `
  -e JWT_SECRET="<random-secret-at-least-32-characters>" `
  jovit206/lab-15:1.0
```

The image runs Uvicorn as an unprivileged user. Its default command starts the API. The Dockerfile
does not copy tests, `.env`, or generated migration/performance reports. No Docker Hub push is
performed by this project workflow.

## Troubleshooting

- **MongoDB connection timeout:** check `MONGO_URI`, server reachability, authentication database,
  firewall, and Docker network/service DNS. `localhost` inside a container refers to that container.
- **JWT secret validation error:** configure `JWT_SECRET` with at least 32 characters.
- **Duplicate email/username:** email is globally unique; username is unique within one tenant.
- **Migration refuses to run:** inspect `tasks` tenant IDs and counts. This workflow requires
  exactly `tenant_1`…`tenant_5`, exactly 100 source tasks each, and a dedicated database.
- **Migration validator fails:** inspect `reports/migration_validation.md`; do not delete the
  source collection as a workaround.
- **Rate limit response:** `429` includes `Retry-After`; tune `RATE_LIMIT_REQUESTS` and
  `RATE_LIMIT_WINDOW_SECONDS` for the deployment.
- **Container cannot resolve MongoDB:** use a shared Docker network and the MongoDB container or
  service DNS name, not the host-only address.

## Cleanup

Stop the API with `Ctrl+C` or stop its container:

```powershell
docker stop chapter15-api
```

Only remove the dedicated lab database after confirming that it contains no user data. The seed,
migration, and API scripts do not provide a database-wide cleanup command by design. For test
databases, review the exact `tasktracker_test_*` name before removal; never use a broad wildcard
cleanup against MongoDB or the reports directory.
