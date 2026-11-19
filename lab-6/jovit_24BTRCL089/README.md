# Lab 6 - Jovit Chawla (24BTRCL089)

My own implementation of the Chapter 6 (indexing) activities. The full write-up (aim, objectives, theory,
procedure, complete source code, captured execution output, results and conclusion) is in [REPORT.md](REPORT.md),
generated from the real run results in [results/](results/).

`../Chapter06_Submission/` is a reference submission by another student, used only to understand the activities;
nothing from it is used here.

| File | Activity |
|---|---|
| `activity_6_1_index_design.py` | 6.1 - design indexes for a 5-query workload, explain before/after |
| `activity_6_1_try_it_yourself.py` | 6.1 TIY - measure index usage with `$indexStats`, find unused/redundant indexes |
| `activity_6_2_index_strategies.py` | 6.2 - write cost vs read benefit, ESR key order, trade-off analysis |
| `activity_6_2_try_it_yourself.py` | 6.2 TIY - covered queries and the multikey limitation |
| `catalog.py`, `common.py` | product generator + workload + index design; connection, explain, timing, results |
| `build_report.py` | regenerates `REPORT.md` from `results/` |

## Run

```powershell
python -m pip install -r requirements.txt          # pymongo
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_all.ps1   # runs 6.1 -> 6.1 TIY -> 6.2 -> 6.2 TIY, then the report
```

Needs MongoDB at `mongodb://localhost:27017` (Windows service), or `MONGO_URI` in a git-ignored `.env` copied from
`.env.example`. Only the `jovit_lab06` database is used.
