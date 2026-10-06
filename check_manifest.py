"""Compare stored sync state vs live manifest sizes."""
import json
import os

import httpx
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(".env")
db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                 serverSelectionTimeoutMS=5000)[os.getenv("MONGO_DATABASE", "chapter07")]

print("=== cti_sync_state ===")
for row in db["cti_sync_state"].find():
    keys = ("last_manifest_event_count", "last_fetched_event_count",
            "last_inserted_event_count", "last_successful_sync", "last_error")
    print(f"  {row['_id']}: " + ", ".join(f"{k}={row.get(k)}" for k in keys if k in row))

print("\n=== events per feed ===")
for row in db["events"].aggregate([
    {"$group": {"_id": "$source_feed_name", "count": {"$sum": 1}}},
]):
    print(f"  {row['_id']}: {row['count']}")

print("\n=== live manifest sizes ===")
with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as http:
    for name, url in {
        "circl": "https://www.circl.lu/doc/misp/feed-osint/manifest.json",
        "botvrij": "https://www.botvrij.eu/data/feed-osint/manifest.json",
        "threatfox": "https://threatfox.abuse.ch/downloads/misp/manifest.json",
    }.items():
        try:
            response = http.get(url)
            response.raise_for_status()
            manifest = response.json()
            print(f"  {name}: {len(manifest):,} events in manifest")
        except Exception as error:
            print(f"  {name}: FAILED: {error}")
