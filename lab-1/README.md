# NoSQL Chapter 01 Activities

**Course:** 23CSE528 - NoSQL Databases using MongoDB

## What this is

Four Python scripts, each self-contained in its own activity folder, that
model the Chapter 01 questions as real MongoDB experiments instead of
hand-written prose. Every performance claim in the accompanying report is
backed by an `explain("executionStats")` call against a live `mongod`
instance - nothing is asserted without a measurement.

## Requirements

- Python 3.10+
- `pip install pymongo`
- A running MongoDB server reachable at `mongodb://localhost:27017`
  (override with the `MONGO_URI` environment variable). The scripts do
  **not** fall back to a mock - if no server answers, they fail loudly.

## Running

Each folder is independent - no shared config module to import:

```bash
cd activity_1_1_classify_database_products
python3 lab_1_1_classify_database_products.py

cd ../activity_1_1_try_it_yourself
python3 lab_1_1_try_it_yourself.py

cd ../activity_1_2_map_application
python3 lab_1_2_map_application.py

cd ../activity_1_2_try_it_yourself
python3 lab_1_2_try_it_yourself.py
```

All four connect to the `nosql_labs` database, drop and recreate their own
collections on every run (idempotent), and seed all synthetic data with
`random.seed(42)` and a fixed base timestamp - document counts and every
`explain()` stage/docsExamined/nReturned number reproduce identically across
runs. Only wall-clock timing figures vary naturally between runs on live
hardware.

## Outputs

Each script writes its own MongoDB JSON export (extended JSON,
`mongoimport`-compatible, `_id` first) into its own folder:

| Folder | Script | JSON export |
|---|---|---|
| `activity_1_1_classify_database_products/` | `lab_1_1_classify_database_products.py` | `database_products.json` |
| `activity_1_1_try_it_yourself/` | `lab_1_1_try_it_yourself.py` | `multi_model_products.json` |
| `activity_1_2_map_application/` | `lab_1_2_map_application.py` | `app_domain_mapping.json` |
| `activity_1_2_try_it_yourself/` | `lab_1_2_try_it_yourself.py` | `ecommerce_domain_mapping.json` |

Re-import any export with:

```bash
mongoimport --db nosql_labs --collection <name> --jsonArray --drop --file <file>.json
```

Verify the data landed:

```bash
mongosh --eval 'use nosql_labs; db.getCollectionNames(); db.ride_history.countDocuments({})'
```

## Report

`Chapter01_Activity_Report.docx` contains, per activity: objective, task,
measured-results tables, verdict paragraphs (the actual answers to the exam
questions, grounded in the measured numbers), the full numbered Python
script, and screenshots of the script's console output against the live
server.
