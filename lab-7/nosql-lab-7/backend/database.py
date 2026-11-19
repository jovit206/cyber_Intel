from pymongo import MongoClient
from pymongo.database import Database

from backend.config import settings

client: MongoClient | None = None
database: Database | None = None


def connect_database() -> Database:
    global client, database
    if client is None:
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    database = client[settings.mongo_database]
    return database


def get_database() -> Database:
    if database is None:
        raise RuntimeError("MongoDB is not connected")
    return database


def close_database() -> None:
    global client, database
    if client is not None:
        client.close()
    client = None
    database = None
