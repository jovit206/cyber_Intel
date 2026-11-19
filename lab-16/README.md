# Chapter 16 — Polyglot Persistence with MongoDB, Cassandra, and Neo4j

This chapter implements two executable labs:

1. **Lab 16.1:** model a shared university enrollment dataset as documents, partitioned query
   tables, and a relationship graph; run equivalent queries against all three databases.
2. **Lab 16.2:** provide a Python fraud-analysis service that combines transaction, event-history,
   and transfer-network data, reports partial failures, and protects each backend with an
   independent circuit breaker.

All databases run in an isolated Chapter 16 Compose network with dedicated volume names and
container names. The project does not stop or reuse containers from previous chapters. MongoDB
binds to `127.0.0.1:27020` because this machine already uses port 27018; Cassandra binds to
`127.0.0.1:9043`, Neo4j HTTP to `127.0.0.1:7475`, and Bolt to `127.0.0.1:7688`.

## Database roles

| Database | Chapter 16 responsibility | Model and strengths | Trade-offs |
|---|---|---|---|
| MongoDB | Student/course operational documents and fraud transactions | Flexible documents and indexed reads; embed identifiers for common lookups | Maintain duplicated enrollment arrays or normalize and transact carefully |
| Cassandra | Student/course lookup projections and time-ordered fraud events | Partition-key reads and ordered clustering columns suit known access patterns and append histories | Requires tables for each access pattern; no general joins or unrestricted scan in the design |
| Neo4j | Enrollment/prerequisite relationships and transfer paths | Parameterized graph traversal expresses relationship questions directly | Traversal cost depends on graph degree/depth; bound paths and result counts |

No database is universally faster. Choose by access pattern, scale, operational skills, consistency
needs, and measured workload. Chapter 16.1 query results are compared by record IDs; the source
dataset has **10 students, 5 courses, and 24 enrollments**.

## Prerequisites and installation

- Docker Desktop with Compose plugin.
- Python 3.12+ (the repository's existing `.venv` may also be used) and PowerShell.
- The database services require a few GB of memory. Cassandra's first startup may take a few
  minutes.
- MongoDB, Cassandra, and Neo4j are provided by this isolated Compose project; no host database
  installation is needed.

From the repository root:

```powershell
Set-Location .\lab-16
Copy-Item .env.example .env
```

Set a private local Neo4j password in `.env` (do not commit that file). Alternatively, generate
one for the current PowerShell session before running Compose:

```powershell
$bytes = New-Object byte[] 24
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$env:NEO4J_PASSWORD = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','A').Replace('/','B')
```

If the password is stored in `.env`, load it into the current shell for the `cypher-shell` check
and other PowerShell commands that read the process environment:

```powershell
$env:NEO4J_PASSWORD = (Get-Content .env | Where-Object { $_ -like 'NEO4J_PASSWORD=*' }).Split('=', 2)[1]
```

Compose reads `.env` from the current directory and PowerShell process environment. For host-side
Python commands, the application and seed scripts load `.env` automatically. `.env` is ignored by
Git. Never put credentials into the source files, command history, reports, or Docker image.

Install dependencies into the existing virtual environment, or create a new one:

```powershell
..\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If creating a fresh environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On modern Windows Python, the Cassandra driver uses its gevent reactor because Python removed
`asyncore`; Linux Docker builds the driver's libev extension. The pinned driver range keeps the
Windows gevent reactor available. The driver's deprecation warning for that reactor is noted in
the execution report.

## Start the isolated Docker environment

```powershell
docker compose config --quiet
docker compose up -d mongo16 cassandra16 neo4j16
docker compose ps
```

Wait until MongoDB, Cassandra, and Neo4j report `healthy`:

```powershell
do {
    Start-Sleep -Seconds 10
    docker compose ps
    $mongoHealth = docker inspect --format '{{.State.Health.Status}}' chapter16-mongodb
    $cassandraHealth = docker inspect --format '{{.State.Health.Status}}' chapter16-cassandra
    $neo4jHealth = docker inspect --format '{{.State.Health.Status}}' chapter16-neo4j
} while ($mongoHealth -ne 'healthy' -or $cassandraHealth -ne 'healthy' -or $neo4jHealth -ne 'healthy')
```

Check service reachability from the Chapter 16 Docker network:

```powershell
docker exec chapter16-mongodb mongosh --quiet --eval "db.adminCommand('ping').ok"
docker exec chapter16-cassandra nodetool status
docker exec chapter16-neo4j cypher-shell -u neo4j -p $env:NEO4J_PASSWORD "RETURN 1"
```

MongoDB has no authentication enabled in this local-only lab container; its port is bound only to
loopback. Neo4j uses the configured local password. Cassandra uses its development default
authentication configuration and a single-node `SimpleStrategy` keyspace. Do not copy these
development choices into production.

## Lab 16.1 — University enrollment models

### Dataset and schemas

The canonical seed data is in `lab-16.1/common/dataset.py`. Seed operations are idempotent.

- **MongoDB:** `university_db.students` and `university_db.courses`; unique indexes on
  `student_id` and `course_id`, plus an index on course `student_ids`.
- **Cassandra:** `university_data.student_courses` partitioned by `student_id`, and
  `university_data.course_students` partitioned by `course_id`; both use clustering keys for
  deterministic row order. Schema is also listed in `lab-16.1/cassandra/schema.cql`.
- **Neo4j:** unique constraints on Student and Course IDs, searchable secondary indexes, and
  `(:Student)-[:ENROLLED_IN]->(:Course)` relationships. Schema is in
  `lab-16.1/neo4j/schema.cypher`.

### Seed all databases and compare the queries

From `lab-16` after the databases are healthy:

```powershell
python .\scripts\run_lab161.py
```

The runner seeds each engine, runs the same three questions, compares returned student/course
IDs, and writes `lab-16.1/query_comparison_results.json`. A mismatch raises an error and exits
nonzero.

Equivalent access patterns:

| Question | MongoDB | Cassandra | Neo4j |
|---|---|---|---|
| Courses for one student | Read the student's `course_ids`; indexed `$in` lookup on courses | Partition lookup in `student_courses WHERE student_id = ?` | Parameterized `Student-[:ENROLLED_IN]->Course` traversal |
| Students enrolled in a course | Read course `student_ids`; indexed lookup on students | Partition lookup in `course_students WHERE course_id = ?` | Parameterized reverse traversal from Course to Student |
| Students sharing any course with a student | Read course IDs, match those IDs against the student array | Read student's course partition, then issue one course-partition read per course and deduplicate | One two-hop pattern joins the student's courses to other students |

MongoDB provides compact document reads but duplicates enrollment membership. Cassandra requires
separate query-oriented tables and multiple bounded reads for a multi-course overlap. Neo4j's
relationship traversal is concise for overlap and prerequisite paths; performance still depends
on graph shape and traversal bounds. Lab 16.1 timings are not treated as a universal cross-engine
benchmark.

## Hybrid architecture activity

Read [`lab-16.1/hybrid_architecture.md`](lab-16.1/hybrid_architecture.md). It assigns MongoDB as
the operational source of truth, Cassandra as the append-oriented audit/event history, and Neo4j
as the prerequisite and learning-path graph. It includes sample data/queries, synchronization
using an outbox and idempotent projections, plus failure/replay considerations.

## Lab 16.2 — FraudQueryService

### Models and API

`lab-16.2/app/fraud_query_service.py` exposes:

- `get_transaction(txn_id)` — indexed MongoDB lookup in `fraud_detection.transactions`.
- `get_user_profile(user_id)` — prepared Cassandra query for the most recent ten events from
  `fraud_data.user_profiles`, partitioned by user and clustered by descending event time.
- `detect_fraud_rings(user_id, max_hops=3)` — parameterized Neo4j transfer path query. Hop depth
  is validated to 1–5 and at most 50 paths are returned; user input is always a Cypher parameter.
- `analyze_user(user_id, txn_id)` — merges available results, includes actual backend failures,
  and sets `analysis_status` to `partial` when any backend fails. Missing transaction data remains
  `null`; it is not replaced with sample values.

`app/database.py` creates a PyMongo pool with `maxPoolSize=10`, a Cassandra cluster/session with
bounded executor threads and token-aware routing, and a Neo4j driver pool capped at ten
connections. `DatabaseConnections.close()` closes every resource and reports close failures.
Connection strings and the Neo4j password are loaded from environment variables or the ignored
`.env` file.

### Circuit breaker

`app/circuit_breaker.py` tracks each backend independently. Three consecutive operation errors
open that backend for 30 seconds; after the interval, exactly one concurrent call acts as a
half-open probe. Success resets the counter and closes the circuit; another failure reopens it.
Circuit state transitions are logged. The service reports open circuits as unavailable backends
and continues collecting results from working systems.

### Run the end-to-end demo

Seed and execute a sample analysis:

```powershell
python -m app.seed_demo
python -m app.demo
```

The sample uses `TXN001`, `USER001`, three ordered events, and the transfer path
`USER001 -> USER002 -> USER003 -> USER004`. The seed uses upserts and is safe to rerun against
the dedicated Chapter 16 services.

## Tests

Run the full test suite from the Chapter 16 directory:

```powershell
python -m pytest -q .\lab-16.2\tests
```

The unit tests mock database methods for lookup, missing data, partial failures, query parameter
validation, path limits, and circuit recovery. The integration tests connect to the live
Chapter 16 containers to create schemas, seed all three university models, compare results, and
run a complete fraud analysis. Integration tests skip an unavailable backend; review the pytest
summary to distinguish a skip from a real pass.

## Benchmark

After running the fraud demo seed, run measured operations (default 100 iterations):

```powershell
Set-Location .\lab-16.2
$env:BENCHMARK_ITERATIONS = "100"
python -m benchmarks.benchmark
```

The script uses `time.perf_counter()` and writes average, minimum, maximum, and median seconds to
`lab-16.2/reports/latency_comparison.csv`; it generates a millisecond average/median chart at
`lab-16.2/reports/latency_comparison.png`. Measurements include client/driver overhead on this
machine and depend on Docker warm-up, data, and server load. The assignment's example ranges are
reference expectations only and are not hardcoded into results.

The measured 50-iteration run on this environment produced:

| Backend/operation | Average | Minimum | Maximum | Median |
|---|---:|---:|---:|---:|
| MongoDB transaction lookup | 2.226 ms | 1.373 ms | 3.479 ms | 2.126 ms |
| Cassandra recent events | 5.418 ms | 3.872 ms | 11.081 ms | 5.204 ms |
| Neo4j graph traversal | 13.803 ms | 7.988 ms | 72.063 ms | 12.308 ms |
| Combined fraud analysis | 24.442 ms | 15.930 ms | 93.134 ms | 21.421 ms |

## Docker image and service

The image is named `jovit206/lab-16:1.0`. Build without publishing:

```powershell
docker compose build fraud-service
```

Then start all services, including a one-shot service that waits for all dependencies to become
healthy, ensures the schemas, seeds the small demonstration data, and prints an analysis:

```powershell
docker compose up -d
docker compose ps
docker compose logs fraud-service
```

The Python container uses the internal service DNS names and does not receive the host port
mapping. To run it again after the first one-shot run:

```powershell
docker compose run --rm fraud-service
```

Image commands:

```powershell
docker build -t jovit206/lab-16:1.0 .
docker push jovit206/lab-16:1.0
```

Only the local build is authorized by this assignment. **Do not run the push command until you
choose to publish manually.** The image contains application code and dependencies, not `.env`
credentials or database data.

## Evidence checklist

- [ ] `docker compose ps` shows all three database services healthy.
- [ ] `python .\scripts\run_lab161.py` reports all three equivalent queries as consistent.
- [ ] `python -m pytest -q .\lab-16.2\tests` reports the live database tests separately from unit tests.
- [ ] `python -m app.demo` returns a completed analysis, or clearly marks unavailable backends.
- [ ] Benchmark CSV and PNG are present under `lab-16.2/reports/`.
- [ ] Docker image is built locally and no registry push has occurred.

## Cleanup

Stop only Chapter 16 containers while retaining their volumes:

```powershell
docker compose down
```

To erase the Chapter 16 database files as well, remove only the explicitly named volumes after
you have confirmed they contain no data you need:

```powershell
docker volume rm chapter16_mongo_data chapter16_cassandra_data chapter16_neo4j_data chapter16_neo4j_logs
```

Do not use `docker compose down -v` if the lab data should be retained. No prior-chapter container,
database, or volume is selected by these commands.

## Main application integration

The root application's Chapter 16 routes are admin-only read probes and fraud evidence queries.
They read separate `CHAPTER16_*` connection settings and do not infer health from the Chapter 18
databases. Cassandra is pinned to the requested 4.1 release in this Compose file. To attach the
root backend to the Chapter 16 services, start the Chapter 16 Compose project and then use
`docker-compose.integration.yml` as an override with the root Compose file. Configure
`CHAPTER16_NEO4J_PASSWORD` in the root `.env`; do not commit credentials.

The root API intentionally refuses Chapter 16 start/stop requests with HTTP 409 because no
allowlisted service orchestrator is installed. The audit timeline is explicitly labelled as the
existing MongoDB platform audit log; Cassandra audit outbox delivery and exactly-once recovery are
not integrated into the root deployment. The standalone labs remain available without claiming
that those background operations are running.
