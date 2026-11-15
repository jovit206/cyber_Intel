# Chapter 14 – MongoDB Monitoring, Backup, and Restore Verification

This chapter implements two independent operational exercises: a periodic Python monitoring
dashboard (Lab 14.1) and automated, timestamped MongoDB backups with a guarded restore drill
(Lab 14.2). All restore drills use a newly generated database whose name begins with
`chapter14_restore_test_`; `mongorestore --drop` is rejected for any other namespace.

## Objectives

- Read MongoDB `serverStatus` metrics, optional WiredTiger cache statistics, replica-set status,
  and primary-to-secondary optime lag.
- Log a readable snapshot every 30 seconds and optionally send Slack or Discord webhook alerts
  with a per-metric cooldown.
- Create timestamped `mongodump` backups, record duration and return status, schedule hourly
  backups, and prune only expired backups created and marked by this script.
- Verify recovery of original values after modifying a dedicated test collection and restoring
  its backup; benchmark 1,000, 10,000, and 100,000 documents.

## Prerequisites

- Python 3.10+ and a reachable MongoDB deployment. The configured user needs `serverStatus` and
  replica-set status privileges for the dashboard; backup and restore users need the corresponding
  database dump/restore privileges.
- Install Python dependencies in the repository virtual environment:

  ```powershell
  .\.venv\Scripts\python.exe -m pip install -r .\chapter-14\requirements.txt
  ```

- MongoDB Database Tools (`mongodump` and `mongorestore`) on `PATH`, or Docker with an image that
  contains those tools. On Windows, the Docker fallback writes backups through a bind mount.
- Use a private MongoDB URI in the `MONGO_URI` environment variable. The URI is never printed by
  these scripts. Do not put real passwords, webhook URLs, or keyfiles in this directory or commit
  them. The scripts read process environment variables and do not load dotenv files. For example,
  set the variable privately in PowerShell before running a command:

  ```powershell
  $env:MONGO_URI = "mongodb://<username>:<password>@<mongo-host>:27017/?authSource=admin"
  ```

  Replace placeholders locally. See [.env.example](./.env.example) for the other setting names.

  ## Docker image

  The Dockerfile builds the existing monitoring and backup scripts into
  `jovit206/lab-14:1.0`. It uses Python 3.12 slim, installs the Python dependencies and MongoDB
  Database Tools (`mongodump` / `mongorestore`), and runs as an unprivileged user. The build context
  excludes environment files, credentials, virtual environments, tests, local backups, and logs.
  Only the scripts and requirements are copied into the runtime image. The image creates writable
  `logs/`, `backups/`, and `reports/` directories under `/app/chapter14`.

  From PowerShell, run these commands from `chapter-14`:

  ```powershell
  docker build -t jovit206/lab-14:1.0 .

  # Inspect each script's CLI inside the built image.
  docker run --rm jovit206/lab-14:1.0 python monitoring_dashboard.py --help
  docker run --rm jovit206/lab-14:1.0 python backup_verify.py --help
  ```

  `monitoring_dashboard.py` is the default container command. Set connection details only at
  runtime; do not put credentials in the Dockerfile or image:

  ```powershell
  docker run --rm `
    --env MONGO_URI `
    --env MONGODB_ALERT_WEBHOOK_URL `
    --env MONGODB_ALERT_WEBHOOK_PLATFORM `
    jovit206/lab-14:1.0 `
    python monitoring_dashboard.py --once
  ```

  The named variables must already be set in the PowerShell session. For recurring monitoring,
  run the image without a command override; its default command monitors continuously. To keep logs
  and backups after containers exit, use Docker named volumes:

  ```powershell
  docker volume create lab14-logs
  docker volume create lab14-backups
  docker run --rm `
    --env MONGO_URI `
    --env MONGODB_ALERT_WEBHOOK_URL `
    --env MONGODB_ALERT_WEBHOOK_PLATFORM `
    --mount type=volume,source=lab14-logs,target=/app/chapter14/logs `
    --mount type=volume,source=lab14-backups,target=/app/chapter14/backups `
    jovit206/lab-14:1.0
  ```

  Run the backup script by overriding the image's default command. For example, take one backup:

  ```powershell
  docker run --rm `
    --env MONGO_URI `
    --mount type=volume,source=lab14-backups,target=/app/chapter14/backups `
    jovit206/lab-14:1.0 `
    python backup_verify.py --action backup --backup-dir /app/chapter14/backups
  ```

  To run the hourly scheduler, change `--action backup` to `--action scheduler` and do not use
  `--rm` if the container should remain managed by a restart policy or orchestrator. MongoDB must be
  reachable from the container; do not use `localhost` for a MongoDB server running on the Windows
  host. On Docker Desktop, use `host.docker.internal` for a host service or a service name reachable
  on the attached Docker network.

  The Docker build installs native MongoDB Database Tools, so the backup script does not use its
  Docker tools fallback inside this image. Backup data is not included in the image. No image is
  pushed automatically. After reviewing the image and providing authorization, the manual publish
  commands are:

  ```powershell
  docker login
  docker push jovit206/lab-14:1.0
  ```

  ### Optional Docker tools fallback

  When the database-tools executables are not installed on Windows, set the image and Docker network.
If the Python process connects through a Windows host-mapped port, provide a separate URI resolvable
inside that Docker network:

```powershell
$env:MONGODB_TOOLS_DOCKER_IMAGE = "mongo:7.0.45"
$env:MONGODB_TOOLS_DOCKER_NETWORK = "chapter13-mongo-net"
$env:MONGODB_TOOLS_URI = "mongodb://<username>:<password>@mongo1:27017,mongo2:27017,mongo3:27017/admin?replicaSet=rs0&authSource=admin"
```

Replace placeholders locally; never paste credentials into shared logs or commit them. The network
name and tool URI are deployment-specific. The Docker fallback runs `mongodump`/`mongorestore` in a
short-lived tools container and mounts only the selected backup directory.

## Lab 14.1 – Monitoring dashboard

Set `MONGO_URI`. Optionally set `MONGODB_ALERT_WEBHOOK_URL` and
`MONGODB_ALERT_WEBHOOK_PLATFORM` (`slack` or `discord`). The payload contains the metric, current
value, threshold, and UTC timestamp. A 300-second per-metric cooldown suppresses repeats; failed
webhook attempts are also cooled down. HTTP, timeout, and connection failures are logged without
printing the webhook URL.

Run one snapshot (useful for a connection check):

```powershell
.\.venv\Scripts\python.exe .\chapter-14\monitoring_dashboard.py --once
```

Run continuously; Ctrl+C stops it cleanly:

```powershell
.\.venv\Scripts\python.exe .\chapter-14\monitoring_dashboard.py
```

Output is both printed and appended to `chapter-14\logs\metrics.log`. The recurring interval is
30 seconds. On standalone MongoDB, replica-set monitoring is shown as not applicable. WiredTiger
fields that are absent are displayed as unavailable rather than treated as zero. Cache hit ratio
uses `(pages requested from cache - pages read into cache) / pages requested from cache`; alerts
fire below 80%. Secondary lag is primary `optimeDate` minus each secondary `optimeDate`; alerts
fire above 10 seconds.

## Lab 14.2 – Automated backup and restore verification

### Create a timestamped backup

The default action performs one `mongodump --uri <URI> --out <timestamped-directory>` operation:

```powershell
.\.venv\Scripts\python.exe .\chapter-14\backup_verify.py `
  --action backup `
  --uri $env:MONGO_URI `
  --backup-dir .\chapter-14\backups
```

Backup start, completion, duration, return code, and status are logged in
`chapter-14\logs\backup.log`. Successful directories contain a private ownership marker used by
the retention policy. The script never prunes an unmarked directory or an unrelated file. Marked
successful or failed backup directories older than seven days are removed during scheduled runs.
For collection-scoped backup/restore, the tool removes only the URI's default database path because
the explicit namespace is authoritative; authentication query options such as `authSource` remain
unchanged. For an all-database backup, MongoDB Database Tools follows MongoDB's URI database-scope
rules, so omit a default database from the URI when a full deployment dump is intended. When using
the Docker fallback, the database path from `MONGO_URI` is retained while the tool URI supplies
network-reachable hosts.

### Hourly scheduling

Keep the process running to schedule one backup per hour and run the seven-day retention check:

```powershell
.\.venv\Scripts\python.exe .\chapter-14\backup_verify.py `
  --action scheduler `
  --uri $env:MONGO_URI `
  --backup-dir .\chapter-14\backups
```

The scheduler does not take an immediate backup at startup. Use `--action backup` for an immediate
run. Stop the scheduler with Ctrl+C.

### Safe restore verification

The workflow creates a unique database beginning with `chapter14_restore_test_`, inserts the
requested test records, takes a scoped backup, changes every document, and restores only
`<test-database>.backup_verification` with `--drop --nsInclude=<test-database>.backup_verification`.
It verifies every document's run ID, original/mutable value equality, timestamp, document count,
and removal of modified fields, in addition to checking representative records.
No existing database is reused: if the generated database name already exists, execution stops.

Run the 1,000-document restore drill:

```powershell
.\.venv\Scripts\python.exe .\chapter-14\backup_verify.py `
  --action verify-restore `
  --uri $env:MONGO_URI `
  --backup-dir .\chapter-14\backups
```

The uniquely named test databases are retained for inspection. To remove a test database later,
first verify its exact name starts with `chapter14_restore_test_` and that it contains only this
lab's `backup_verification` collection; do not issue `dropDatabase` against any other namespace.

### Benchmarks

The benchmark runs the same insert, scoped backup, mutation, restore, count, and field checks for
1,000, 10,000, and 100,000 documents. It records actual local durations and BSON dump byte sizes;
no sample timing is substituted. Results are saved to
`chapter-14\reports\backup_restore_benchmarks.csv`.

```powershell
.\.venv\Scripts\python.exe .\chapter-14\backup_verify.py `
  --action benchmark `
  --uri $env:MONGO_URI `
  --backup-dir .\chapter-14\backups `
  --report .\chapter-14\reports\backup_restore_benchmarks.csv
```

The restore measurement includes the `mongorestore` command; application restart time is not
included or inferred.

## RPO and RTO

With hourly successful backups, the planned maximum data-loss window (RPO) is approximately one
hour. This is a target, not a measured incident result. The CSV's restore durations are measured
RTO components for each dataset; they exclude application restart and service-level checks.
`reports/rpo_rto_report.md` records these scope distinctions.

## Troubleshooting

- **Connection refused / selection timeout:** confirm MongoDB is reachable from the Python process
  and `MONGO_URI` is set. Do not print the URI when debugging.
- **Unauthorized server status:** grant the monitoring account the MongoDB privileges needed for
  `serverStatus` and `replSetGetStatus`.
- **Standalone status:** the script reports replica-set monitoring as not applicable.
- **`mongodump` unavailable:** install MongoDB Database Tools, or configure the documented Docker
  fallback. Ensure its container can resolve the tool URI and that the network exists.
- **Docker tools cannot resolve `localhost`:** set `MONGODB_TOOLS_URI` to the MongoDB DNS names
  reachable on `MONGODB_TOOLS_DOCKER_NETWORK`.
- **Restore target conflict:** the test database already exists. The script intentionally refuses
  to overwrite it; choose a different run ID through the Python API or inspect the existing test
  namespace before deciding how to proceed.
- **Insufficient disk space:** benchmarks retain timestamped backups. Free space only after
  identifying the exact generated backup directories; the scheduled retention removes only marked
  backups older than seven days.

## Expected outputs and generated files

- `logs/metrics.log`, `logs/backup.log`: monitoring snapshots and backup outcomes.
- `backups/chapter14_backup_<UTC timestamp>/`: dumps plus ownership marker after successful dumps.
- `reports/backup_restore_benchmarks.csv`: measured benchmark records.
- `reports/rpo_rto_report.md`: planned RPO and measured RTO scope.
- `tests/`: mock-based tests that do not require MongoDB, external webhooks, or database tools.

The local Kubernetes/Atlas environment is not used by these labs. A real webhook endpoint is
optional; tests use mocked HTTP responses so no credentials are required.
