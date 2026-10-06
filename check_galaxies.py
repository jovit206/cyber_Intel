"""Check galaxy linkage after deeper sync."""
import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(".env")
db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                 serverSelectionTimeoutMS=5000)[os.getenv("MONGO_DATABASE", "chapter07")]

print("events:", db["events"].count_documents({}))
print("events with galaxies:", db["events"].count_documents({"galaxies": {"$ne": []}}))
print("attributes with threat_ids:", db["attributes"].count_documents({"threat_ids": {"$ne": []}}))
for event in db["events"].find({"galaxies": {"$ne": []}},
                               {"info": 1, "source_feed_name": 1, "galaxies": 1}).limit(6):
    values = [g["value"] for g in event["galaxies"]][:4]
    print(f"  {event['source_feed_name']} | {event['info'][:70]} | {values}")
