# Chapter 18 — Comparative Multi-Database Threat Intelligence

Chapter 18 is Project 7 in **Engineering → Technology & Innovation**. Lab 18.1 provides an administrator-only workspace that normalizes one imported threat dataset and writes to isolated MongoDB, Cassandra and Neo4j structures. Lab 18.2 provides observed query comparisons and a review-focused comparative report.

## Safety and data boundaries

- MongoDB defaults to the isolated `threat_intel_lab18` database. It creates `full_threats`, `import_runs`, `analyst_reviews`, `benchmark_runs` and `investigations`; it does not alter the existing application collections.
- Cassandra defaults to the isolated `threat_intel_lab18` keyspace and uses Chapter 18 query tables. Choose a replication factor appropriate to the configured cluster.
- Neo4j records use the `Threat`, `Source`, `Target`, `ThreatType` and `Indicator` labels, with explicit `RELATED_TO` relationships.
- `threat_id` is the idempotency key. An identical retry is counted as a duplicate. A conflicting reuse is rejected instead of overwriting the earlier record.
- Relationship edges are followed only when imported records explicitly name related threat IDs. A shared indicator, target, source or transaction reference is evidence for analyst review, not proof of maliciousness or a false positive.
- Imported records are source-provided and are not independently verified. `test_fixture` provenance is excluded from benchmark eligibility and imported-data analytics.

## Requirements and launch

The root `requirements.txt` includes the Cassandra and Neo4j drivers and ReportLab used by this feature. For a standalone Chapter 18 environment, install `pip install -r lab-18/requirements-chapter18.txt` in addition to the application requirements. The Dockerfile copies Chapter 18 schemas and supports the optional `INSTALL_CHAPTER18_POLYGLOT=true` build argument for installing this dependency file into the image.

The normal application MongoDB connection is required. Cassandra and Neo4j are optional; without configuration they appear as **not configured** and their operations are **not tested**. Configure these backend environment variables as needed:

| Variable | Default / meaning |
|---|---|
| `LAB18_MONGO_DATABASE` | `threat_intel_lab18`; must be an identifier beginning with a letter, followed by letters, digits or underscores. |
| `LAB18_CASSANDRA_HOSTS` | Comma-separated contact points. Empty disables Cassandra. |
| `LAB18_CASSANDRA_PORT` | `9042`. |
| `LAB18_CASSANDRA_KEYSPACE` | `threat_intel_lab18`; identifier only. |
| `LAB18_CASSANDRA_DATACENTER` | `datacenter1`; identifier used by NetworkTopologyStrategy. |
| `LAB18_CASSANDRA_REPLICATION_FACTOR` | `1`; set for the topology before creating the keyspace. |
| `LAB18_CASSANDRA_USERNAME`, `LAB18_CASSANDRA_PASSWORD` | Optional pair; provide both or neither. |
| `LAB18_NEO4J_URI` | Empty disables Neo4j; e.g. `bolt://neo4j:7687`. |
| `LAB18_NEO4J_USERNAME`, `LAB18_NEO4J_PASSWORD` | Neo4j credentials; password has no default. |
| `LAB18_NEO4J_DATABASE` | `neo4j`. |

Supply credentials through the deployment's secret/environment mechanism. Do not put live credentials in source control or the frontend. No passwords are returned by status routes.

For a local all-in-one deployment, the optional Compose overlay adds Cassandra and Neo4j while reusing the root application's MongoDB replica set and network. Set `LAB18_NEO4J_PASSWORD` in the shell or an untracked root `.env` file, then run `docker compose -f docker-compose.yml -f lab-18/docker-compose.yml up -d --build`. The overlay uses isolated named volumes for Cassandra and Neo4j. The tracked deletion of the root `.env.example` is preserved; this README is the Chapter 18 configuration reference.

## Use

1. Sign in as an administrator and open Project 7. Every Chapter 18 API route independently checks administrator authorization.
2. Check Settings or Overview for database connectivity. Use **Prepare isolated MongoDB schema** before importing. Cassandra and Neo4j schemas are provisioned only when those services are configured and their adapter setup is invoked.
3. Import normalized records as JSON. Each record needs `threat_id`, `source`, `from_source`, `threat_type`, and an ISO timestamp. Optional fields include `indicator`, `target`, `severity`, `confidence` (0–1 or 0–100), `related_threats`, `transaction_ids`, coordinates and provenance. An import batch is limited to 10,000 records; the target dataset is 100,000 records.
4. Search imported records and create bounded investigations. Review decisions must include an analyst rationale.
5. Run the comparison with exact source, date range and at least one exact target or indicator. The comparison requires 100,000 imported MongoDB records and at least one record matching each requested filter. Four operations run against each connected, schema-ready database: source/time search, target/indicator search, daily type aggregation and explicit relationship traversal. Each operation has one warm-up and ten timed executions; median and p95 are computed from the observed timings.
6. Download a CSV or PDF report from Comparative Report. Unavailable backends are marked `not_tested`; no substitute latency is generated.

The CSV/PDF exports report data count, provenance, operation outcomes and measured statistics. Benchmark results are not published as static sample numbers.

## API surface

All routes are under `/api/chapter18` and require an administrator session: `GET /status`, `POST /schema`, `POST /imports`, `GET /threats`, `GET /analytics`, `GET|POST /investigations`, `GET|POST /reviews`, `GET|POST /benchmarks`, and `GET /benchmarks/{run_id}/report?format=csv|pdf`.

## Deployment notes

Start the application using the root Docker/Compose workflow or the Chapter 18 overlay above. Chapter 18 schemas are namespaced separately from Chapters 13–17. `INSTALL_CHAPTER18_POLYGLOT=true` installs the standalone Chapter 18 dependency list; when it is false, the root application dependencies still include the Cassandra and Neo4j drivers. Real integration checks require running MongoDB, Cassandra and Neo4j services with working credentials; a successful unit test alone does not establish connectivity.
