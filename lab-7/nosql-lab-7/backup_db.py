"""Backup chapter07 to JSON files (mongodump is not installed on this machine)."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from bson import json_util

load_dotenv(".env")
uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
dbname = os.getenv("MONGO_DATABASE", "chapter07")
backup_root = Path(__file__).resolve().parent / "backups"
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_dir = backup_root / f"{dbname}-{stamp}"
backup_dir.mkdir(parents=True, exist_ok=True)

client = MongoClient(uri, serverSelectionTimeoutMS=5000)
db = client[dbname]
manifest = {}
for name in sorted(db.list_collection_names()):
    rows = list(db[name].find())
    target = backup_dir / f"{name}.json"
    with target.open("w", encoding="utf-8") as handle:
        handle.write(json_util.dumps(rows, indent=1))
    manifest[name] = len(rows)
    print(f"backed up {name}: {len(rows)} docs -> {target.name} ({target.stat().st_size:,} bytes)")

with (backup_dir / "manifest.json").open("w", encoding="utf-8") as handle:
    json.dump({"database": dbname, "uri": uri, "backed_up_at": stamp,
               "collections": manifest, "method": "pymongo json_util export (mongodump unavailable)"},
              handle, indent=2)
print(f"backup complete: {backup_dir}")
