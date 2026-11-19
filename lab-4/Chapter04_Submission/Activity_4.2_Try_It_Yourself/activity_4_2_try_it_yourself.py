"""Lab 4.2 Try It Yourself - An idempotent bulk import

Task: implement a bulk import function that can be run multiple times without
creating duplicates, using upsert logic for each document.

"Idempotent" here means a precise, checkable thing: run the import twice and
the second run must report `upserted=0, modified=0` and leave the collection
count unchanged. The script asserts exactly that rather than asking you to
take it on trust.

Prerequisite (for the CSV source; otherwise it falls back to the products
collection loaded by load_amazon_products.py):
    python load_amazon_products.py

Run:
    python lab_02_try_it_yourself.py
    python lab_02_try_it_yourself.py --rows 2000 --runs 4
    python lab_02_try_it_yourself.py --json
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import ASCENDING, UpdateOne
from pymongo.errors import BulkWriteError
from rich.console import Console
from rich.table import Table

# --- self-contained MongoDB connection --------------------------------------
# This activity folder runs on its own -- there is no shared package to install.
# Settings come from MONGO_URI / MONGO_DB in the environment or a .env beside
# this script, falling back to a local mongod on the default port.
import os
from pathlib import Path

from pymongo import MongoClient

try:
    from dotenv import load_dotenv
    for _env in (Path(__file__).resolve().parent / ".env",
                 Path(__file__).resolve().parent.parent / ".env"):
        if _env.exists():
            load_dotenv(_env)
            break
except ImportError:
    pass

_client = None


def get_client():
    """Return a singleton MongoClient."""
    global _client
    if _client is None:
        uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        kwargs = {"serverSelectionTimeoutMS": 5000}
        if uri.startswith("mongodb+srv://"):
            import certifi  # macOS Python often lacks system root CAs
            kwargs["tlsCAFile"] = certifi.where()
        _client = MongoClient(uri, **kwargs)
        _client.admin.command("ping")
        print(f"[OK] Connected to MongoDB at {uri.split('@')[-1]}")
    return _client


def get_db(db_name=None):
    """Return a database handle."""
    return get_client()[db_name or os.getenv("MONGO_DB", "nosql_labs")]


def reset_collection(db_name, collection_name):
    """Drop and recreate a collection."""
    db = get_db(db_name)
    if collection_name in db.list_collection_names():
        db.drop_collection(collection_name)
        print(f"[OK] Dropped collection: {db_name}.{collection_name}")
    return db[collection_name]


def close_client():
    """Close the connection."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        print("[OK] MongoDB connection closed.")


def banner(text, char="=", width=72):
    """Print a section banner."""
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)
    print()
# ----------------------------------------------------------------------------

console = Console()

DB_NAME = "nosql_labs"
SOURCE_COLL = "products"
TARGET_COLL = "catalog_import"
NAIVE_COLL = "catalog_import_naive"
KEY_FIELD = "asin"

HERE = Path(__file__).resolve().parent
PRODUCTS_CSV = HERE / "amazon_products.csv"

CAPTURED: dict = {"runs": [], "naive_comparison": {}, "checks": {}}

csv.field_size_limit(sys.maxsize)


# ---------------------------------------------------------------------------
# The importable records
# ---------------------------------------------------------------------------
def read_from_csv(limit):
    """Stream `limit` rows out of the raw export."""
    with PRODUCTS_CSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            if i >= limit:
                break
            try:
                price = float(row["price"] or 0)
                stars = float(row["stars"] or 0)
            except ValueError:
                continue
            yield {
                "asin": row["asin"],
                "title": row["title"],
                "price": round(price, 2),
                "stars": stars,
                "category_id": int(float(row["category_id"] or 0)),
            }


def read_from_collection(db, limit):
    """Fallback source: records already in nosql_labs.products."""
    cursor = db[SOURCE_COLL].find(
        {}, {"asin": 1, "title": 1, "price": 1, "metrics.stars": 1,
             "category_id": 1, "_id": 0}).limit(limit)
    for doc in cursor:
        yield {
            "asin": doc["asin"],
            "title": doc["title"],
            "price": doc["price"],
            "stars": doc.get("metrics", {}).get("stars", 0),
            "category_id": doc.get("category_id", 0),
        }


def load_records(db, limit):
    if PRODUCTS_CSV.exists():
        return list(read_from_csv(limit)), f"{PRODUCTS_CSV.name} (raw CSV)"
    if db[SOURCE_COLL].estimated_document_count():
        return list(read_from_collection(db, limit)), f"{DB_NAME}.{SOURCE_COLL}"
    sys.exit(f"[FAIL] No source data.\n"
             f"       Expected {PRODUCTS_CSV.name} here, or a loaded "
             f"{DB_NAME}.{SOURCE_COLL}.\n"
             f"       Run:  python load_amazon_products.py")


# ---------------------------------------------------------------------------
# The function the activity asks for
# ---------------------------------------------------------------------------
def bulk_import(col, records, key_field=KEY_FIELD, batch_size=1000):
    """Import `records` so that running it again changes nothing.

    Three things make this idempotent:

      1. Every write is an upsert keyed on `key_field`, so a record that is
         already present is updated in place instead of inserted again.
      2. The update body uses only $set -- a literal assignment. Re-applying it
         writes the same values, so MongoDB reports modified=0. An $inc here
         would move the field on every run and destroy the property.
      3. First-import metadata goes in $setOnInsert, so it is written when the
         document is created and left alone afterwards. Putting a timestamp in
         $set (or adding $currentDate) would rewrite the document every run --
         the count would stay stable, but the data would not.

    ordered=False lets the remaining operations proceed if one fails, which is
    what you want for an import you intend to re-run.
    """
    stats = {"requested": len(records), "matched": 0, "modified": 0,
             "upserted": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    for start in range(0, len(records), batch_size):
        chunk = records[start:start + batch_size]
        operations = []
        for record in chunk:
            key = record[key_field]
            mutable = {k: v for k, v in record.items() if k != key_field}
            operations.append(UpdateOne(
                {key_field: key},
                {
                    "$set": mutable,
                    "$setOnInsert": {key_field: key, "firstImportedAt": now},
                },
                upsert=True,
            ))
        if not operations:
            continue
        try:
            result = col.bulk_write(operations, ordered=False)
        except BulkWriteError as exc:
            details = exc.details
            stats["errors"] += len(details.get("writeErrors", []))
            result = None
            stats["matched"] += details.get("nMatched", 0)
            stats["modified"] += details.get("nModified", 0)
            stats["upserted"] += details.get("nUpserted", 0)
        if result is not None:
            stats["matched"] += result.matched_count
            stats["modified"] += result.modified_count
            stats["upserted"] += result.upserted_count

    stats["collection_count"] = col.count_documents({})
    return stats


# ---------------------------------------------------------------------------
# Demonstration
# ---------------------------------------------------------------------------
def run_table(runs):
    table = Table(title="Import runs", title_style="bold cyan", header_style="bold")
    for header in ("Run", "Requested", "Upserted", "Matched", "Modified",
                   "Docs in collection"):
        table.add_column(header, justify="right", no_wrap=True)
    for i, stats in enumerate(runs, 1):
        table.add_row(str(i), f"{stats['requested']:,}", f"{stats['upserted']:,}",
                      f"{stats['matched']:,}", f"{stats['modified']:,}",
                      f"{stats['collection_count']:,}")
    console.print(table)


def naive_comparison(db, records):
    """What the same import looks like without upsert logic."""
    print("\n=== For contrast: the same import with plain insert_many ===")
    col = reset_collection(DB_NAME, NAIVE_COLL)
    counts = []
    for run in (1, 2):
        try:
            col.insert_many([dict(r) for r in records], ordered=False)
        except BulkWriteError as exc:
            print(f"  run {run}: BulkWriteError, "
                  f"{len(exc.details.get('writeErrors', []))} write errors")
        counts.append(col.count_documents({}))
        print(f"  after run {run}: {counts[-1]:,} documents")

    doubled = len(counts) == 2 and counts[1] == counts[0] * 2
    print(f"  -> {'DUPLICATED' if doubled else 'did not duplicate'}: "
          f"{counts[0]:,} became {counts[1]:,}")
    print("     No key, no upsert, so every run inserts a fresh copy of every")
    print("     record. This is the failure mode the activity is guarding against.")
    CAPTURED["naive_comparison"] = {
        "counts_per_run": counts, "duplicated": doubled,
        "note": "insert_many with no unique key re-inserts everything each run",
    }
    return counts


def main():
    parser = argparse.ArgumentParser(
        description="Lab 4.2 Try It Yourself - idempotent bulk import")
    parser.add_argument("--rows", type=int, default=1000,
                        help="records to import (default 1000)")
    parser.add_argument("--runs", type=int, default=3,
                        help="how many times to run the import (default 3)")
    parser.add_argument("--batch", type=int, default=1000, help="bulk batch size")
    parser.add_argument("--json", metavar="PATH", nargs="?",
                        const="lab_02_tiy_results.json", default=None,
                        help="write the run statistics to JSON")
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be at least 2: the whole point is comparing "
                     "a second run against the first.")

    banner("Lab 4.2 Try It Yourself: an idempotent bulk import")

    db = get_db(DB_NAME)
    records, source = load_records(db, args.rows)
    print(f"[OK] Loaded {len(records):,} records from {source}.")

    col = reset_collection(DB_NAME, TARGET_COLL)
    # A unique index turns "must not duplicate" from a convention into a
    # constraint the server enforces, whatever the client does.
    col.create_index([(KEY_FIELD, ASCENDING)], unique=True, name=f"{KEY_FIELD}_unique")
    print(f"[OK] Target {DB_NAME}.{TARGET_COLL} with a unique index on {KEY_FIELD}.\n")

    runs = []
    for run in range(1, args.runs + 1):
        stats = bulk_import(col, records, batch_size=args.batch)
        stats["run"] = run
        runs.append(stats)
        print(f"  run {run}: upserted={stats['upserted']:,} "
              f"matched={stats['matched']:,} modified={stats['modified']:,} "
              f"count={stats['collection_count']:,}")
    print()
    run_table(runs)
    CAPTURED["runs"] = runs

    # --- the actual assertions -------------------------------------------
    first, second = runs[0], runs[1]
    unique_keys = len({r[KEY_FIELD] for r in records})
    checks = {
        "run 1 inserted every unique record": first["upserted"] == unique_keys,
        "run 2 inserted nothing": second["upserted"] == 0,
        "run 2 modified nothing": second["modified"] == 0,
        "collection count stable across runs":
            len({r["collection_count"] for r in runs}) == 1,
        "count equals unique source keys":
            first["collection_count"] == unique_keys,
    }
    verdict = Table(title="Idempotency checks", title_style="bold cyan",
                    header_style="bold")
    verdict.add_column("Check", overflow="fold")
    verdict.add_column("Result", no_wrap=True)
    for label, ok in checks.items():
        verdict.add_row(label, "[green]PASS[/green]" if ok else "[red]FAIL[/red]")
    console.print(verdict)
    CAPTURED["checks"] = {
        **{k: bool(v) for k, v in checks.items()},
        "unique_source_keys": unique_keys,
        "duplicate_keys_in_source": len(records) - unique_keys,
    }

    if len(records) != unique_keys:
        print(f"  Note: the source held {len(records) - unique_keys:,} duplicate "
              f"{KEY_FIELD} values.")
        print(f"  Upsert collapsed them, which is why run 1 upserted "
              f"{unique_keys:,} and not {len(records):,}.")

    # --- what $setOnInsert bought us --------------------------------------
    sample = col.find_one({}, {"_id": 0, KEY_FIELD: 1, "firstImportedAt": 1})
    print(f"\n  firstImportedAt on {sample[KEY_FIELD]}: {sample['firstImportedAt']}")
    print(f"  It was written by $setOnInsert during run 1 and left untouched by")
    print(f"  runs 2-{args.runs}. Had it gone in $set, every run would have")
    print(f"  rewritten it -- the document count would still be stable, but the")
    print(f"  data would change on every import, which is not idempotent.")

    naive_comparison(db, records)

    if all(checks.values()):
        print(f"\n[OK] All idempotency checks passed across {args.runs} runs.")
    else:
        print(f"\n[!!] Some idempotency checks FAILED -- see the table above.")

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = HERE / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"database": DB_NAME, "collection": TARGET_COLL, "source": source,
             "records_requested": len(records), "runs_executed": args.runs,
             "results": CAPTURED},
            indent=2, default=json_util.default, ensure_ascii=False))
        print(f"[OK] Wrote results JSON -> {out}  ({out.stat().st_size / 1024:,.1f} KB)")

    banner("Lab 4.2 Try It Yourself Complete")
    close_client()


if __name__ == "__main__":
    main()
