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
COL = "courses_with_prereqs"          # separate collection; Activity 3.1's stays intact
POINTS = {"A+": 10, "A": 9, "A-": 8, "B+": 7, "B": 6, "B-": 5, "C+": 4, "C": 3, "D": 2, "F": 0}

# Activity 3.1's validator plus the prerequisite edges (not required - CS101 has none)
VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["title", "code", "instructor", "schedule"],
    "properties": {
        "title": {"bsonType": "string"},
        "code": {"bsonType": "string", "pattern": r"^[A-Z]{2,4}\d{3}$"},
        "instructor": {"bsonType": "string"},
        "schedule": {"bsonType": "object", "required": ["days", "time", "room"]},
        "prerequisites": {"bsonType": "array", "items": {
            "bsonType": "object", "required": ["code"],
            "properties": {"code": {"bsonType": "string", "pattern": r"^[A-Z]{2,4}\d{3}$"},
                           "title": {"bsonType": "string"},
                           "min_grade": {"enum": list(POINTS)}}}},
    }}}


def course(code, title, instructor, days, time, room, roster, prereqs):
    return {"code": code, "title": title, "instructor": instructor, "credits": 3,
            "schedule": {"days": days, "time": time, "room": room},   # embedded
            "enrolled_students": roster,                              # embedded
            "prerequisites": prereqs}                                 # referenced


def pre(code, title, min_grade):
    return {"code": code, "title": title, "min_grade": min_grade}


def student(sid, name, grade=None):
    return {"student_id": sid, "name": name,
            "email": f"{name.split()[0].lower()}@college.edu", "grade": grade}


CATALOG = [
    course("CS101", "Programming Fundamentals", "Dr. Meera Joshi", ["Monday", "Wednesday"],
           "08:00-09:00", "LH-101", [student("S1001", "Arjun Mehta", "A")], []),
    course("CS210", "Discrete Mathematics", "Dr. Anil Gupta", ["Tuesday", "Friday"],
           "09:00-10:00", "LH-110", [student("S1003", "Rahul Kumar", "A-")], []),
    course("CS201", "Data Structures", "Dr. Meera Joshi", ["Monday", "Thursday"],
           "10:00-11:30", "LH-105", [student("S1002", "Sneha Reddy", "C")],
           [pre("CS101", "Programming Fundamentals", "C")]),
    course("CS301", "Algorithms", "Dr. Anil Gupta", ["Wednesday", "Friday"],
           "11:45-13:15", "LH-203", [student("S1001", "Arjun Mehta", "B+")],
           [pre("CS201", "Data Structures", "C"), pre("CS210", "Discrete Mathematics", "D")]),
    course("CS310", "Operating Systems", "Dr. Meera Joshi", ["Monday", "Thursday"],
           "11:45-13:15", "LH-204", [student("S1004", "Divya Patel", "A")],
           [pre("CS201", "Data Structures", "C")]),
    course("CS401", "Database Systems", "Dr. Priya Sharma", ["Monday", "Wednesday"],
           "10:00-11:30", "LH-201", [student("S1003", "Rahul Kumar")],
           [pre("CS301", "Algorithms", "C")]),
    course("CS402", "Machine Learning", "Dr. Anil Gupta", ["Tuesday", "Thursday"],
           "14:00-15:30", "LH-305", [student("S1005", "Kavya Nair")],
           [pre("CS301", "Algorithms", "B"), pre("CS210", "Discrete Mathematics", "C")]),
    course("CS403", "Natural Language Processing", "Dr. Priya Sharma",
           ["Monday", "Wednesday", "Friday"], "09:00-10:00", "LH-102",
           [student("S1002", "Sneha Reddy")], [pre("CS402", "Machine Learning", "C")]),
]

# Completed courses, for the eligibility check
TRANSCRIPTS = {
    "Arjun Mehta": {"CS101": "A", "CS210": "B+", "CS201": "A-", "CS301": "B+"},
    "Sneha Reddy": {"CS101": "B+", "CS201": "C"},
}


def chain(col, code):
    """Whole transitive prerequisite chain in one server-side $graphLookup."""
    return next(col.aggregate([
        {"$match": {"code": code}},
        {"$graphLookup": {"from": COL, "startWith": "$prerequisites.code",
                          "connectFromField": "prerequisites.code", "connectToField": "code",
                          "as": "chain", "depthField": "depth"}},
        {"$project": {"_id": 0, "code": 1, "title": 1, "direct": "$prerequisites.code",
                      "chain": {"$sortArray": {"input": "$chain.code", "sortBy": 1}}}}]), None)


def main():
    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    print(f"Connected to MongoDB {client.server_info()['version']} at {URI}")
    db = client["university"]
    db.drop_collection(COL)
    db.create_collection(COL, validator=VALIDATOR)
    col = db[COL]
    col.insert_many(CATALOG)
    print(f"[OK] Seeded {len(CATALOG)} courses with referenced prerequisites\n")
    for c in CATALOG:
        print(f"  {c['code']}  {c['title']:<28} prereqs: "
              f"{', '.join(p['code'] for p in c['prerequisites']) or '-none-'}")

    print("\n=== Q1 Direct prerequisites resolved with $lookup on the reference ===")
    for row in col.aggregate([{"$match": {"code": "CS402"}},
                              {"$lookup": {"from": COL, "localField": "prerequisites.code",
                                           "foreignField": "code", "as": "prereqs"}}]):
        for p in row["prereqs"]:
            print(f"  CS402 requires {p['code']} {p['title']:<24} ({p['instructor']})")

    print("\n=== Q2 Full chain with one $graphLookup ===")
    for code in ("CS403", "CS401"):
        c = chain(col, code)
        print(f"  {c['code']} direct: {', '.join(c['direct'])}  ->  all: {', '.join(c['chain'])}")

    print("\n=== Q3 Reverse direction - what does passing CS201 unlock? ===")
    for c in col.find({"prerequisites.code": "CS201"}):
        print(f"  {c['code']}  {c['title']}")

    print("\n=== Q4 Adding a prerequisite is one $push; cycles refused first ===")
    for target, new in (("CS401", "CS310"), ("CS101", "CS403")):
        if target in chain(col, new)["chain"]:
            print(f"  [BLOCKED] {target} <- {new} would create a cycle")
        else:
            edge = pre(new, col.find_one({"code": new})["title"], "C")
            col.update_one({"code": target}, {"$push": {"prerequisites": edge}})
            print(f"  [OK] {target} now also requires {new}")

    print("\n=== Q5 Eligibility: transcript checked against every edge ===")
    for target in ("CS402", "CS401"):
        c = col.find_one({"code": target})
        for name, done in TRANSCRIPTS.items():
            missing = [f"{p['code']} not taken" if p["code"] not in done
                       else f"{p['code']} {done[p['code']]}<{p['min_grade']}"
                       for p in c["prerequisites"]
                       if p["code"] not in done or POINTS[done[p["code"]]] < POINTS[p["min_grade"]]]
            print(f"  {target}  {name:<14} {'ELIGIBLE' if not missing else 'NOT eligible: ' + ', '.join(missing)}")

    docs = list(col.find({}))
    for d in docs:
        d["_id"] = str(d["_id"])
    (HERE / "activity_3_1_try_it_yourself_courses.json").write_text(json.dumps(docs, indent=2, default=str))
    (HERE / "activity_3_1_try_it_yourself_prereq_graph.json").write_text(
        json.dumps([chain(col, d["code"]) for d in docs], indent=2))
    print(f"\n[OK] Exported {len(docs)} courses + the resolved prerequisite graph")

    if ATLAS:
        import certifi
        acl = MongoClient(ATLAS, serverSelectionTimeoutMS=10000, tlsCAFile=certifi.where())
        acl["university"].drop_collection(COL)
        acl["university"].create_collection(COL, validator=VALIDATOR)
        acl["university"][COL].insert_many(list(col.find({})))
        print(f"[OK] Mirrored {col.count_documents({})} docs to Atlas -> university.{COL}")
        acl.close()
    client.close()


if __name__ == "__main__":
    main()
