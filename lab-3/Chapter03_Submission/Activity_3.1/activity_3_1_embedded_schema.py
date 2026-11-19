import json, os
from pathlib import Path
from pymongo import MongoClient

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017")
ATLAS = os.getenv("MONGO_URI_ATLAS")

# Part 2: title, code, instructor, schedule are required; code must look like CS401
VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["title", "code", "instructor", "schedule"],
    "properties": {
        "title": {"bsonType": "string"},
        "code": {"bsonType": "string", "pattern": r"^[A-Z]{2,4}\d{3}$"},
        "instructor": {"bsonType": "string"},
        "schedule": {"bsonType": "object", "required": ["days", "time", "room"],
                     "properties": {"days": {"bsonType": "array", "minItems": 1},
                                    "time": {"bsonType": "string"},
                                    "room": {"bsonType": "string"}}},
        "enrolled_students": {"bsonType": "array", "items": {
            "bsonType": "object", "required": ["student_id", "name", "email"]}},
    }}}

# Part 1: schedule and roster embedded in the course document
COURSES = [
    {"title": "Database Systems", "code": "CS401", "instructor": "Dr. Priya Sharma", "credits": 4,
     "schedule": {"days": ["Monday", "Wednesday"], "time": "10:00-11:30", "room": "LH-201"},
     "enrolled_students": [
         {"student_id": "S1001", "name": "Arjun Mehta", "email": "arjun@college.edu", "grade": "A"},
         {"student_id": "S1002", "name": "Sneha Reddy", "email": "sneha@college.edu", "grade": "B+"},
         {"student_id": "S1003", "name": "Rahul Kumar", "email": "rahul@college.edu", "grade": "A-"}]},

    {"title": "Machine Learning", "code": "CS402", "instructor": "Dr. Anil Gupta", "credits": 3,
     "schedule": {"days": ["Tuesday", "Thursday"], "time": "14:00-15:30", "room": "LH-305"},
     "enrolled_students": [
         {"student_id": "S1001", "name": "Arjun Mehta", "email": "arjun@college.edu", "grade": None},
         {"student_id": "S1005", "name": "Kavya Nair", "email": "kavya@college.edu", "grade": "A"}]},

    {"title": "Natural Language Processing", "code": "CS403", "instructor": "Dr. Priya Sharma", "credits": 3,
     "schedule": {"days": ["Monday", "Wednesday", "Friday"], "time": "09:00-10:00", "room": "LH-102"},
     "enrolled_students": [
         {"student_id": "S1002", "name": "Sneha Reddy", "email": "sneha@college.edu", "grade": "B+"},
         {"student_id": "S1007", "name": "Priya Das", "email": "priya.d@college.edu", "grade": "A-"},
         {"student_id": "S1008", "name": "Neha Gupta", "email": "neha@college.edu", "grade": "B"}]},

    {"title": "Operating Systems", "code": "CS310", "instructor": "Dr. Meera Joshi", "credits": 4,
     "schedule": {"days": ["Monday", "Thursday"], "time": "11:45-13:15", "room": "LH-204"},
     "enrolled_students": [
         {"student_id": "S1004", "name": "Divya Patel", "email": "divya@college.edu", "grade": "A"}]},
]

# Must all be rejected by the validator
INVALID = [
    {"title": "No Code", "instructor": "Dr. Nobody",
     "schedule": {"days": ["Monday"], "time": "10:00-11:00", "room": "LH-9"}},
    {"title": "Bad Code Format", "code": "cs-401", "instructor": "Dr. Nobody",
     "schedule": {"days": ["Monday"], "time": "10:00-11:00", "room": "LH-9"}},
    {"title": "No Schedule", "code": "CS999", "instructor": "Dr. Nobody"},
]


def mirror_to_atlas(docs, db_name, col_name):
    if not ATLAS:
        return print("[SKIP] Atlas mirror: MONGO_URI_ATLAS not set")
    import certifi
    client = MongoClient(ATLAS, serverSelectionTimeoutMS=10000, tlsCAFile=certifi.where())
    client[db_name].drop_collection(col_name)
    client[db_name].create_collection(col_name, validator=VALIDATOR)
    client[db_name][col_name].insert_many(docs)
    print(f"[OK] Mirrored {len(docs)} docs to Atlas -> {db_name}.{col_name}")
    client.close()


def main():
    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    print(f"Connected to MongoDB {client.server_info()['version']} at {URI}")
    db = client["university"]

    # --- Part 2: create the collection WITH validation ---
    db.drop_collection("courses")
    db.create_collection("courses", validator=VALIDATOR)
    col = db["courses"]
    print(r"[OK] university.courses created; required: title, code, instructor, schedule"
          "\n     code must match ^[A-Z]{2,4}\\d{3}$, schedule must have days/time/room")

    # --- Part 1: insert, then find courses meeting on Monday ---
    col.insert_many(COURSES)
    print(f"\n[OK] Inserted {len(COURSES)} courses")

    print("\n=== Courses on Monday   {'schedule.days': 'Monday'} ===")
    for c in col.find({"schedule.days": "Monday"}):
        print(f"  {c['code']}  {c['title']:<30} {c['schedule']['time']}  {c['schedule']['room']}")

    # --- Part 2: prove the validator rejects bad documents ---
    print("\n=== Validation tests (all must be rejected) ===")
    for doc in INVALID:
        try:
            col.insert_one(doc)
            print(f"  [FAIL] '{doc['title']}' was accepted")
        except Exception as e:
            print(f"  [REJECTED] {doc['title']:<16} {e.details['errmsg']}")

    # --- $push a new student into an existing roster ---
    col.update_one({"code": "CS403"}, {"$push": {"enrolled_students": {
        "student_id": "S1009", "name": "Amit Verma", "email": "amit@college.edu", "grade": None}}})
    print(f"\n[OK] $push -> CS403 roster is now "
          f"{len(col.find_one({'code': 'CS403'})['enrolled_students'])} students")

    # --- Part 3: students in courses taught by one instructor ---
    print("\n=== Students taught by Dr. Priya Sharma ($unwind + $group = distinct) ===")
    for s in col.aggregate([
            {"$match": {"instructor": "Dr. Priya Sharma"}},
            {"$unwind": "$enrolled_students"},
            {"$group": {"_id": "$enrolled_students.student_id",
                        "name": {"$first": "$enrolled_students.name"},
                        "courses": {"$addToSet": "$code"}}},
            {"$sort": {"_id": 1}}]):
        print(f"  {s['_id']}  {s['name']:<14} {', '.join(sorted(s['courses']))}")

    # --- export ---
    docs = list(col.find({}))
    for d in docs:
        d["_id"] = str(d["_id"])
    (HERE / "activity_3_1_courses.json").write_text(json.dumps(docs, indent=2, default=str))
    (HERE / "activity_3_1_schema_validator.json").write_text(json.dumps(VALIDATOR, indent=2))
    print(f"\n[OK] Exported {len(docs)} documents -> activity_3_1_courses.json")

    mirror_to_atlas(list(col.find({})), "university", "courses")
    client.close()


if __name__ == "__main__":
    main()
