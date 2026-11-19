"""Count events in the local DB by source feed, and attributes."""
import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(".env")
db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                 serverSelectionTimeoutMS=5000)[os.getenv("MONGO_DATABASE", "chapter07")]

total_events = db["events"].count_documents({})
total_attrs = db["attributes"].count_documents({})
print(f"TOTAL EVENTS: {total_events}")
print(f"TOTAL ATTRIBUTES (indicators): {total_attrs}")
print("\nEvents by source feed:")
for row in db["events"].aggregate([
    {"$group": {"_id": "$source_feed_name", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}},
]):
    print(f"  {row['_id']}: {row['count']}")
print("\nAttributes by source feed:")
for row in db["attributes"].aggregate([
    {"$group": {"_id": "$source_feed_name", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}},
]):
    print(f"  {row['_id']}: {row['count']}")
print("\nOther collections:")
for name in ("threats", "reports", "passages", "virustotal_cache", "threat_reports_real", "cti_sync_state"):
    print(f"  {name}: {db[name].count_documents({})}")
# attributes missing a feed (legacy None source_feed_name)?
print("\nEvents with missing source_feed_name:",
      db["events"].count_documents({"source_feed_name": None}))
