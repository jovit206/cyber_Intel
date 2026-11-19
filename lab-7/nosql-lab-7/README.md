# AEGIS-CTI

AEGIS-CTI is a FastAPI/MongoDB threat-intelligence dashboard. Public-source
records are retained with source provenance; the application does not invent
threat names, indicators, scores, or locations.

## Run the app

1. Install Python dependencies: `python -m pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and set `MONGO_URI` and `MONGO_DATABASE`.
3. For VirusTotal reputation lookups, set `VIRUSTOTAL_API_KEY` in the process
   environment or local `.env`. Do not commit a key.
4. Start the API and frontend:
   `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`.
5. Open `http://localhost:8000`. Set `FRONTEND_ORIGIN` when the frontend is
   served from a separate origin. `FRONTEND_API_URL` configures `/config.js`;
   `public/config.js` defaults port 3000 to the API at port 8000.

The public feed, Galaxy, and annual-report sync jobs run on app startup and
again every 24 hours while the API process is running. The feed-sync status,
including the last successful time and failures, is available in the Live
Threat Stream header and `/api/data-source-status`.

## Public threat-intelligence sources

The daily MISP feed sync reads each `manifest.json`, compares the manifest
timestamp and metadata fingerprint to MongoDB, and downloads only new or
changed events. Event downloads use at most eight concurrent workers, retry
timeouts and transient HTTP errors, and log progress. A quick first run can be
capped per feed:

```powershell
python -m backend.collectors.misp_feeds --max-events 25
```

Omit `--max-events` to process every changed event. The same command can be
run manually; repeated runs are idempotent. Feed events are stored by
feed-scoped MISP UUID in `events`, with the original UUID and feed URL retained.
Startup/scheduled syncs are capped by `SYNC_STARTUP_MAX_EVENTS` (default 25)
and report imports by `SYNC_STARTUP_MAX_REPORTS` (default 2); raise them or run
the module commands without caps to pull more history.
Attributes are stored separately in `attributes` and reference the event UUID.
They include the source-provided type, value, category, `to_ids` flag, tags,
and timestamps.

| Feed | Manifest | Events in manifest |
|---|---|---|
| CIRCL OSINT | <https://www.circl.lu/doc/misp/feed-osint/manifest.json> | ~1,681 |
| Botvrij.eu | <https://www.botvrij.eu/data/feed-osint/manifest.json> | ~435 |
| ThreatFox (abuse.ch) | <https://threatfox.abuse.ch/downloads/misp/manifest.json> | ~2,009 events holding several million indicators |

Each feed publishes continuously; the sync runs at least once per day (and
on app startup), so the database converges to the feeds' newest events. A
capped run (`--max-events 25`) is the recommended quick first run; omit the
flag to process every changed event.

Threat names and aliases are synchronized daily from the MISP Galaxy raw JSON
clusters `threat-actor`, `malpedia`, and `tool` in
<https://github.com/MISP/misp-galaxy>. The database stores source-provided
cluster names, aliases, descriptions, and explicit metadata; absent country or
other metadata is not guessed.

Annual security report PDFs are discovered from
<https://github.com/jacobdjwilson/awesome-annual-security-reports>. New or
changed files are downloaded and converted to Markdown with Microsoft
MarkItDown. The `reports` collection keeps repository metadata and the `passages`
collection stores text extracted from those PDFs. Passage and entity counts
are computed from converted source text. Galaxy-name matches use a common-word
blocklist and case-sensitive matching; sync logs the most frequent extracted
names for manual false-positive review. For a quick report import:

```powershell
python -m backend.collectors.reports --max-reports 2
```

VirusTotal API v3 reputation results are cached in `virustotal_cache`, keyed
by indicator type and normalized value. The scheduled worker performs at most
one lookup every 15 minutes (at most 96 per day, below the free-tier 4-per-
minute and 500-per-day limits). A 404 is cached as a not-found result; quota
responses are not cached as reputation results and are surfaced in the UI.
VirusTotal is optional; the public MISP and Galaxy sources do not require
credentials.

## Database safety and source-derived lab data

Application startup does not drop or replace collections. Before removing
legacy records, take a backup and review the exact cleanup scope. This
machine has no `mongodump` binary installed; `python backup_db.py` exports
every collection of the configured database to timestamped JSON files under
`backups/` (BSON types preserved via `bson.json_util`) and writes a
`manifest.json` with per-collection counts.

Lab 7.1 requires synchronized Galaxy clusters and attributes. Temporary
benchmark records copy real source attributes and are removed by their exact
benchmark IDs after the measurement.

Lab 7.2 creates 100,000 documents in `threat_reports_real` only after source
records exist. These benchmark-only documents are deterministic copies of
real events, attributes, report metadata, or extracted report passages. To
reach the target BSON size, the original source text/value is repeated in a
clearly marked payload field; no synthetic threat statement, sensor, severity,
or indicator is added. These rows are benchmark material, not separate live
intelligence. Run:

```powershell
python -m backend.lab_7_1_schema_analysis
python -m backend.lab_7_2_working_set
python -m backend.lab_7_2_activity_monitor --iterations 1
```

The archived Node implementation and its synthetic data seeder have been
retired; the production API and lab scripts are under `backend/`.

## Tests

Run the focused Python tests with:

```powershell
python -m unittest discover -s test -p "test_*.py"
```

With the API server running, `python smoke_test.py` exercises every
endpoint and static page against the live data (feed stream, threat
details, reports, search, WiredTiger metrics, Lab 7.2 analysis and
random-read benchmark) and fails on any empty section.
