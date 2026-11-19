# Chapter 14 Execution Report

**Execution date:** 2026-11-11  
**Scope:** Labs 14.1 and 14.2 only. No prior chapter files were modified.

## Outcome

Both labs were implemented and exercised against the existing local MongoDB `rs0` deployment.
The deployment reported one PRIMARY and two SECONDARY members. All final restore benchmark runs
passed document-count, per-document value/timestamp, and modified-value replacement checks.

## Files created

- [README.md](./README.md) – requirements, Windows PowerShell instructions, limitations, and cleanup.
- [monitoring_dashboard.py](./monitoring_dashboard.py) – `serverStatus`, WiredTiger, replica status,
  lag alerts, logging, and optional Slack/Discord webhooks.
- [backup_verify.py](./backup_verify.py) – timestamped backups, hourly scheduler, ownership-aware
  retention, guarded restore verification, and benchmarks.
- [requirements.txt](./requirements.txt) and [.env.example](./.env.example).
- [tests/test_monitoring.py](./tests/test_monitoring.py) and
  [tests/test_backup_verify.py](./tests/test_backup_verify.py).
- [reports/backup_restore_benchmarks.csv](./reports/backup_restore_benchmarks.csv).
- [reports/rpo_rto_report.md](./reports/rpo_rto_report.md).

Runtime logs and generated backups are present locally under `logs/` and `backups/`; the
repository's existing ignore rules exclude those data directories from Git.

## Lab 14.1 – Monitoring

**PASS**

- Authenticated connection and `serverStatus` retrieval against the existing MongoDB replica set.
- Live one-shot output was written to `logs/metrics.log`; the observed snapshot included 24 current
  connections, 13,083 available, 99.97% cache hit ratio, primary `mongo1:27017`, and 0.00-second
  optime lag for both secondaries. These are point-in-time measurements, not static guarantees.
- Unit tests exercised the 30-second interval, standalone `replSetGetStatus` handling, missing
  WiredTiger fields, cache warning below 80%, replication-lag warning above 10 seconds, and table
  formatting.
- Slack payload construction, cooldown deduplication, timeout, HTTP error, and connection failure
  handling were tested with mocks. No real webhook was configured or called.

## Lab 14.2 – Backup and restore

**PASS**

- The Windows host did not have `mongodump`/`mongorestore` on `PATH`. The real tests used the
  existing `mongo:7.0.45` image as a short-lived tools container on the existing Docker network;
  no cluster deployment was recreated.
- A one-shot backup CLI run completed successfully against a generated Chapter 14 test database.
- Each restore drill used a new `chapter14_restore_test_...` database, scoped its dump to
  `backup_verification`, and ran `mongorestore --drop` with an exact `--nsInclude` for that
  test-only namespace. The script refuses to reuse an existing generated database or restore to
  any other namespace.
- Final benchmark measurements:

| Documents | Dump size | Backup duration | Restore duration | Verification |
|---:|---:|---:|---:|:---:|
| 1,000 | 234,276 bytes | 1.343151 s | 2.838609 s | PASS |
| 10,000 | 2,388,278 bytes | 3.450714 s | 10.308943 s | PASS |
| 100,000 | 24,378,280 bytes | 7.904497 s | 77.355650 s | PASS |

- The final restore run verified all documents' run ID, original/mutable value equality, timestamp,
  and removal of the mutation marker, as well as total document counts and representative values.
- Benchmark rows were written to
  [reports/backup_restore_benchmarks.csv](./reports/backup_restore_benchmarks.csv).

## Validation evidence

| Check | Result | Evidence |
|---|---|---|
| Python compilation | PASS | `python -m py_compile` on both scripts and test modules |
| Unit tests | PASS | `python -m unittest discover -s .\\lab-14\\tests -v` – 17 tests |
| Live MongoDB connection | PASS | Authenticated `ping`, `serverStatus`, and `replSetGetStatus` |
| Replica-set health | PASS | `rs0`: 1 PRIMARY, 2 SECONDARY |
| Monitoring log creation | PASS | `logs/metrics.log` created and populated |
| 1k/10k/100k backup and restore | PASS | Actual `mongodump`/`mongorestore` in Docker tools container |
| One-shot backup command | PASS | Actual `backup_verify.py --action backup` workflow |
| Seven-day retention safety | PASS | Unit test removes expired success/failure markers only; leaves unrelated directory |
| Webhook delivery | BLOCKED | No endpoint credentials supplied; all request/error behavior mock-tested |
| Real standalone instance | NOT RUN | Existing database is a replica set; standalone response path is mock-tested |
| Continuous 30-second/hourly wait | NOT RUN | Interval and hourly-job registration are unit-tested; long-lived scheduling was not left running |
| Full deployment-wide dump | NOT RUN | Avoided copying unrelated databases; one-shot CLI backup was scoped by the test database in its URI |

## RPO/RTO

- **RPO target:** approximately one hour with successful hourly backups. No outage/data-loss window
  was measured.
- **Measured restore component:** 2.838609 s, 10.308943 s, and 77.355650 s for the three sizes.
- **Application restart and end-to-end recovery:** not measured. See
  [reports/rpo_rto_report.md](./reports/rpo_rto_report.md).

## Debugging notes

Initial real runs exposed and fixed three issues before the final PASS run:

1. `mongodump --out` did not receive its output path immediately after `--out`; argument ordering
   was corrected.
2. A URI database path conflicted with scoped `--db` dumps and mapped namespace restores into the
   URI database. The tool command now removes that path when an explicit namespace is selected,
   retaining authentication query options.
3. BSON stores datetimes at millisecond precision and PyMongo returns naive UTC values by default.
   The test timestamp and comparison were normalized to UTC millisecond precision.

The final unit suite and the live 1k/10k/100k workflows passed after these fixes.

## Remaining manual steps

1. Configure a real webhook URL privately if operational alert delivery is required.
2. On another machine, install MongoDB Database Tools or configure the documented Docker tools
   fallback and a network-resolvable `MONGODB_TOOLS_URI`.
3. Run the hourly scheduler under an appropriate process supervisor if unattended backups are
   required; this lab did not leave a scheduler process running.
4. Review the generated `chapter14_restore_test_...` databases before removing them. The scripts
   intentionally retain test data and never drop a database as cleanup.

## Application API and workspace integration update

The repository now also contains an authenticated Chapter 14 web workspace, persistent 30-second
monitoring samples, threshold alert acknowledgement and signed webhook ingestion, plus a queued
backup/restore worker integration. Hourly and manual backups use a full replica-set dump with
`--oplog`; MongoDB Database Tools prohibit combining `--oplog` with database or collection filters.
The restore drill remains isolated to a uniquely named test namespace. Backups use a separate
`chapter14-backups` volume in the existing Compose configuration. See the **Application workspace
integration** section in the README for routes and configuration.

In the current source pass, the isolated Chapter 14 monitoring and backup test files passed (20
tests). Python compileall, JavaScript syntax, and `git diff --check` also passed. The integrated
worker/API was not started, no full replica-set dump or restore was run, and the updated application
was not browser-verified. Docker approval review was unavailable because of its usage limit; this
report does not treat the earlier standalone runtime results above as evidence for the new worker.
