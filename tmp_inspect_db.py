import os, json, sys
sys.path.insert(0, r"C:\Users\hp\Downloads\nosql-lab-7")
try:
    from backend.config import settings
    print("MONGO_URI=", settings.mongo_uri)
    print("MONGO_DATABASE=", settings.mongo_database)
except Exception as e:
    print("config error:", e)
try:
    from backend.database import connect_database
    db = connect_database()
    print("Collections:", db.list_collection_names())
    for name in sorted(db.list_collection_names()):
        cnt = db[name].count_documents({})
        print(f"\n{name}: {cnt} docs")
        for i, doc in enumerate(db[name].find().limit(3)):
            keys = list(doc.keys())
            print(" keys:", keys)
            # print compact doc for first
            if i == 0:
                print(" sample:", json.dumps(doc, default=str)[:2000])
except Exception as e:
    print("db error:", repr(e))
