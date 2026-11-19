# Lab 1 - Jovit Chawla (24BTRCL089)

My own implementation of the Chapter 1 activities. The full write-up (aim, objectives, theory, procedure, complete
source code, captured execution output, results and conclusion) is in [REPORT.md](REPORT.md), generated from the
real run results in [results/](results/).

The parent `lab-1/` folder holds a reference submission by another student. It was used only to understand the
activity requirements; no code, data, results or credentials from it are used here.

| File | Activity |
|---|---|
| `activity_1_1_classify_products.py` | 1.1 - classify S3, Memcached, Couchbase, HBase, Neptune |
| `activity_1_1_try_it_yourself.py` | 1.1 TIY - ScyllaDB, InfluxDB, ArangoDB |
| `activity_1_2_map_application.py` | 1.2 - ride-sharing domains + social-media analytics |
| `activity_1_2_try_it_yourself.py` | 1.2 TIY - food-delivery platform (my chosen domain) |
| `common.py` | connection, `explain` summaries, timing, result recording |
| `build_report.py` | regenerates `REPORT.md` from `results/` |

## Run

```powershell
python -m pip install -r requirements.txt          # pymongo
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_all.ps1
```

Needs a MongoDB server at `mongodb://localhost:27017` (the Windows MongoDB service), or set `MONGO_URI` in a
`.env` copied from `.env.example` (git-ignored; never commit real credentials). Every script uses only the
`jovit_lab01` database and drops/recreates only its own collections, so runs are repeatable.
