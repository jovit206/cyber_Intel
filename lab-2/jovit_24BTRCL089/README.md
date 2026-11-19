# Lab 2 - Jovit Chawla (24BTRCL089)

My own implementation of the Chapter 2 activities. The full write-up (aim, objectives, theory, procedure, complete
source code, captured execution output, results and conclusion) is in [REPORT.md](REPORT.md), generated from the
real run results in [results/](results/).

`../Chapter02_Submission/` is a reference submission by another student, used only to understand the activities.
Nothing from it is used here - including its `.env`, which holds that student's Atlas credentials.

| File | Activity |
|---|---|
| `activity_2_1_environment_check.py` | 2.1 - verify Python, PyMongo, Docker, Docker/native/Atlas MongoDB, optional packages |
| `activity_2_1_try_it_yourself.py` | 2.1 TIY - connect to all deployments at once and compare `server_info()` |
| `activity_2_2_bookstore_crud.py` | 2.2 - bookstore CRUD with embedded documents and arrays |
| `activity_2_2_try_it_yourself.py` | 2.2 TIY - +10% price for books published before 2010, verified |
| `books.py`, `common.py` | seed data; connection/env handling and result recording |
| `build_report.py` | regenerates `REPORT.md` from `results/` |

## Run

```powershell
python -m pip install -r requirements.txt
docker run -d --name jovit-lab02-mongo -p 127.0.0.1:27028:27017 -v jovit-lab02-data:/data/db mongo:7.0 --wiredTigerCacheSizeGB 0.25   # first time only
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_all.ps1     # starts the container if it is stopped
```

Connection strings: defaults are the Docker container (`localhost:27028`) and the native Windows service
(`localhost:27017`). To add Atlas, copy `.env.example` to `.env` and set `MONGO_URI_ATLAS` to **your own**
connection string. `.env` is git-ignored; never commit real credentials. Only the `jovit_lab02` database is used.
