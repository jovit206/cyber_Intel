from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.env import load_chapter16_env

load_chapter16_env()

from common.dataset import build_expected
from pymongo import MongoClient

from mongodb.schema import create_schema


def seed(database):
    create_schema(database)
    expected = build_expected()
    for collection_name, records in (("students", expected["students"]), ("courses", expected["courses"])):
        collection = database[collection_name]
        id_field = "student_id" if collection_name == "students" else "course_id"
        for document in records:
            collection.replace_one({id_field: document[id_field]}, document, upsert=True)
    return {"students": database.students.count_documents({}), "courses": database.courses.count_documents({})}


def main():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27018")
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
        print(seed(client[os.getenv("MONGO_DATABASE", "university_db")]))


if __name__ == "__main__":
    main()
