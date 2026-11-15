# Chapter 16 Execution Report

**Execution date:** 2026-11-12  
**Scope:** Lab 16.1 and Lab 16.2; all new files are under `chapter-16/`.  
**Docker Hub:** No image push was performed.

## Summary

| Requirement | Result | Evidence |
|---|---|---|
| Isolated MongoDB, Cassandra, and Neo4j services | PASS | `chapter16-mongodb`, `chapter16-cassandra`, `chapter16-neo4j` all reported healthy |
| Consistent university dataset | PASS | Live seed created 10 students, 5 courses, and 24 enrollments in each database |
| Equivalent Lab 16.1 queries | PASS | Courses-for-student, students-in-course, and shared-course ID sets matched across all engines |
| Fraud analysis against live databases | PASS | `USER001` / `TXN001` returned Mongo transaction, Cassandra events, Neo4j paths, no unavailable backends |
| Automated tests | PASS | 20 passed, 0 failed, 0 skipped against the live databases |
| Actual latency benchmark | PASS | 50 iterations per operation; measurements recorded below and in CSV |
| Docker image `jovit206/lab-16:1.0` | PASS | Local 485,453,153-byte image built and Compose one-shot workflow exited 0 |
| Docker Hub push | NOT RUN | Not authorized or requested |

## Implemented files

- Root: `.dockerignore`, `.env.example`, `.gitignore`, `Dockerfile`, `docker-compose.yml`,
  `requirements.txt`, `README.md`.
- Lab 16.1 shared data and runner: `lab-16.1/common/__init__.py`,
  `lab-16.1/common/dataset.py`, `lab-16.1/common/env.py`,
  `scripts/run_lab161.py`, `lab-16.1/query_comparison_results.json`.
- Lab 16.1 MongoDB: `mongodb/schema.py`, `mongodb/seed_data.py`, `mongodb/queries.py`.
- Lab 16.1 Cassandra: `cassandra/schema.cql`, `cassandra/seed_data.py`,
  `cassandra/queries.py`.
- Lab 16.1 Neo4j: `neo4j/schema.cypher`, `neo4j/seed_data.py`, `neo4j/queries.py`.
- Hybrid design: `lab-16.1/hybrid_architecture.md`.
- Lab 16.2 service: `app/__init__.py`, `app/config.py`, `app/database.py`,
  `app/cassandra_support.py`, `app/fraud_query_service.py`, `app/circuit_breaker.py`,
  `app/models.py`, `app/seed_demo.py`, `app/demo.py`, `app/wait_for_databases.py`,
  `app/entrypoint.py`.
- Lab 16.2 schemas: `cassandra/schema.cql`, `neo4j/schema.cypher`.
- Lab 16.2 benchmark: `benchmarks/__init__.py`, `benchmarks/benchmark.py`,
  `reports/latency_comparison.csv`, `reports/latency_comparison.png`.
- Tests: `tests/conftest.py`, `tests/test_mongodb.py`, `tests/test_cassandra.py`,
  `tests/test_neo4j.py`, `tests/test_circuit_breaker.py`, `tests/test_service_unit.py`,
  `tests/test_integration.py`.

The actual `.env` used to authenticate to the local Neo4j container is ignored by Git and is not
included in this list or the Docker build context.

## Service and dataset execution

The repository already had active Chapter 13 MongoDB containers using port 27018. The first
Chapter 16 Compose attempt exposed that conflict before Chapter 16 MongoDB could bind. The
Chapter 16 host port was moved to 27020; existing containers were left untouched. Cassandra and
Neo4j use the dedicated Chapter 16 ports and volumes.

After correcting Compose settings, observed:

- MongoDB `chapter16-mongodb`: healthy on `127.0.0.1:27020`.
- Cassandra `chapter16-cassandra`: healthy on `127.0.0.1:9043`.
- Neo4j `chapter16-neo4j`: healthy on HTTP `127.0.0.1:7475` and Bolt `127.0.0.1:7688`.
- `docker compose config --quiet`: passed.

Executed `python .\scripts\run_lab161.py`. The live run reported:

```text
MongoDB seed: {'students': 10, 'courses': 5}
Cassandra seed: {'students': 10, 'courses': 5, 'enrollments': 24}
Neo4j seed: {'students': 10, 'courses': 5, 'enrollments': 24}
courses_for_student: consistent
students_in_course: consistent
students_sharing_course: consistent
```

The script compares identifiers from actual MongoDB, Cassandra, and Neo4j query results and
raises on mismatches. Output is saved in `lab-16.1/query_comparison_results.json`.

Executed `python -m app.seed_demo` and `python -m app.demo` from `lab-16.2`. The real merged
analysis returned `TXN001` (risk score 0.85, flagged), Cassandra recent events, connected Neo4j
accounts `USER002`, `USER003`, and `USER004`, and status `completed` with an empty
`unavailable_backends` list. Fraud indicators were derived from the returned transaction and
transfer-path data.

## Test evidence

The full suite was executed against the live Chapter 16 database containers. It contains mocked
service/circuit tests and live MongoDB, Cassandra, and Neo4j schema/query tests plus a complete
fraud-analysis integration test.

- Initial run found two actionable issues: `create_cluster()` did not forward Cassandra's
  positional contact-point argument, and a mocked missing Mongo result was not configured as
  `None`. Both were fixed and the full suite rerun.
- Last full-suite run before adding the final three parameterized backend failure assertions:
  **16 passed, 0 failed, 0 skipped**.
- Final rerun after all cases: **20 passed, 0 failed, 0 skipped** (8 Cassandra gevent-reactor
  deprecation warnings on Windows Python 3.14).
- The Windows Python 3.14 Cassandra driver emits a deprecation warning for its gevent reactor;
  that reactor is used only on Windows with Python 3.12+ because `asyncore` is removed. A
  separate Linux Python 3.12 container confirmed that the published Cassandra driver wheel
  selects `LibevConnection` by default.

Test coverage includes:

- MongoDB collection/index creation and all three university queries.
- Cassandra keyspace/table creation and prepared, partition-key query patterns.
- Neo4j unique constraints, nodes, enrollment edges, and all three parameterized traversals.
- ID-set consistency across all databases.
- Fraud transaction lookup, missing transaction, latest-event query, bounded path query and
  50-path cap, merged output, independent backend failures, partial response, and cleanup.
- Circuit opening after three failures, backend isolation, 30-second recovery, half-open retry,
  and success reset.

## Measured benchmark

Ran 50 iterations per operation with `time.perf_counter()` against the local live databases.
Values below are measurements from this machine, including client/network overhead; they are not
guaranteed service-level latencies.

| Operation | Average (ms) | Minimum (ms) | Maximum (ms) | Median (ms) |
|---|---:|---:|---:|---:|
| MongoDB transaction lookup | 2.226 | 1.373 | 3.479 | 2.126 |
| Cassandra recent events | 5.418 | 3.872 | 11.081 | 5.204 |
| Neo4j graph traversal | 13.803 | 7.988 | 72.063 | 12.308 |
| Combined fraud analysis | 24.442 | 15.930 | 93.134 | 21.421 |

The actual values are in `lab-16.2/reports/latency_comparison.csv`; the chart is
`lab-16.2/reports/latency_comparison.png`. Compared with assignment reference ranges, MongoDB and
Cassandra averages fell within their example ranges in this run; Neo4j was within its broad
reference range. These comparisons are descriptive only.

## Docker image and remaining status

The first Dockerfile approach forced a source build of the Cassandra driver and spent several
minutes compiling its wheel. That attempt was stopped; a disposable Linux Python 3.12 check
confirmed the published binary wheel uses `LibevConnection`, so the Dockerfile now installs the
wheel normally.

- `docker build -t jovit206/lab-16:1.0 .` — **PASS**, image ID
  `sha256:46eeafa7734d68fac23c0f851f9695dd3564c55817e3ca3d17a5c095a3c00dea`, size
  485,453,153 bytes.
- `docker compose up -d` — **PASS**; Compose waited for all three database health checks before
  starting `fraud-service`.
- `chapter16-fraud-service` exited with code 0 after its readiness gate, schema/sample setup, and
  fraud analysis; output showed `analysis_status: completed` and no unavailable backends.
- The image itself contains no `.env` values or embedded Mongo/Neo4j credentials. Credentials are
  supplied only to the runtime Compose service.
- Docker Hub publication remains intentionally unperformed.

The Python container is configured to wait for healthy database services, initialize its small
fraud schema/sample, and run a one-shot analysis. Compose uses only Chapter 16 container names,
network, and named volumes.

## Limitations

- Cassandra uses a one-node `SimpleStrategy` keyspace (replication factor 1), appropriate only for
  this local lab.
- The Windows 3.14 Cassandra gevent reactor is deprecated by the driver; the Linux container
  uses the driver's binary wheel with the libev reactor instead of that Windows-only reactor.
- Fraud indicators are simple lab rules based on the stored risk/status fields and existence of
  transfer paths; they are not a production fraud model.
- The university data is a small synthetic dataset; benchmark timing and data shape do not
  establish production capacity.
- No persistent data volumes were deleted, no previous-chapter containers were stopped, and no
  image was pushed.
