"""Lab 3.2 - Migrate from Referenced to Embedded Design
5 posts + 10 comments in two collections, then the same data as one
embedded collection, with the real number of queries counted either way.
"""
import json, os
from pathlib import Path
from pymongo import MongoClient, monitoring

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017")
ATLAS = os.getenv("MONGO_URI_ATLAS")


class Counter(monitoring.CommandListener):
    """Counts the read commands actually sent to the server."""
    n = 0

    def started(self, e):
        if e.command_name in ("find", "aggregate", "getMore"):
            Counter.n += 1

    def succeeded(self, e): pass
    def failed(self, e): pass


POSTS = [
    {"_id": 1, "title": "Why NoSQL Matters for AI", "author": "arjun", "tags": ["nosql", "ai"]},
    {"_id": 2, "title": "MongoDB Aggregation Tips", "author": "sneha", "tags": ["mongodb", "aggregation"]},
    {"_id": 3, "title": "Vector Search Deep Dive", "author": "kavya", "tags": ["vector", "search"]},
    {"_id": 4, "title": "CAP Theorem Explained", "author": "arjun", "tags": ["cap", "distributed"]},
    {"_id": 5, "title": "Graph Databases for Fraud", "author": "rahul", "tags": ["graph", "fraud"]},
]

# 10 comments: post 1 gets 4, post 2 gets 3, post 3 gets 2, post 4 gets 1, post 5 gets none
COMMENTS = [
    {"post_id": 1, "author": "reader1", "text": "Great overview!", "created": "2025-03-01"},
    {"post_id": 1, "author": "reader2", "text": "I still prefer SQL", "created": "2025-03-02"},
    {"post_id": 1, "author": "reader3", "text": "Bookmarked this", "created": "2025-03-02"},
    {"post_id": 1, "author": "reader4", "text": "Any benchmarks?", "created": "2025-03-03"},
    {"post_id": 2, "author": "reader1", "text": "$lookup is powerful", "created": "2025-03-04"},
    {"post_id": 2, "author": "reader5", "text": "Cover $facet next", "created": "2025-03-04"},
    {"post_id": 2, "author": "reader6", "text": "Very practical", "created": "2025-03-05"},
    {"post_id": 3, "author": "reader2", "text": "HNSW vs IVF?", "created": "2025-03-06"},
    {"post_id": 3, "author": "reader7", "text": "How does it scale?", "created": "2025-03-07"},
    {"post_id": 4, "author": "reader3", "text": "Good for interviews", "created": "2025-03-08"},
]


def main():
    client = MongoClient(URI, serverSelectionTimeoutMS=5000, event_listeners=[Counter()])
    print(f"Connected to MongoDB {client.server_info()['version']} at {URI}")
    db = client["blog"]

    # --- Step 1: referenced design - posts and comments in separate collections ---
    for name in ("posts", "comments", "posts_embedded"):
        db.drop_collection(name)
    db["posts"].insert_many(POSTS)
    db["comments"].insert_many(COMMENTS)
    db["comments"].create_index("post_id")
    print(f"\n[OK] Referenced design: {db['posts'].count_documents({})} posts, "
          f"{db['comments'].count_documents({})} comments (index on post_id)")

    # --- Step 2: read a post with its comments via $lookup ---
    print("\n=== Step 2  Referenced read of post 1 ===")
    Counter.n = 0
    post = next(db["posts"].aggregate([
        {"$match": {"_id": 1}},
        {"$lookup": {"from": "comments", "localField": "_id",
                     "foreignField": "post_id", "as": "comments"}}]))
    lookup_q = Counter.n
    print(f"  $lookup  -> {post['title']} + {len(post['comments'])} comments "
          f"({lookup_q} query, 2 collections)")

    # the same read without $lookup: fetch the post, then fetch its comments
    Counter.n = 0
    p = db["posts"].find_one({"_id": 1})
    c = list(db["comments"].find({"post_id": p["_id"]}))
    naive_q = Counter.n
    print(f"  2 round trips -> {p['title']} + {len(c)} comments ({naive_q} queries)")

    # --- Step 3: migrate to the embedded design, server-side, in one pipeline ---
    db["posts"].aggregate([
        {"$lookup": {"from": "comments", "localField": "_id",
                     "foreignField": "post_id", "as": "comments"}},
        {"$set": {"comment_count": {"$size": "$comments"},
                  "comments": {"$map": {  # drop post_id and _id, they are now redundant
                      "input": "$comments", "as": "c",
                      "in": {"author": "$$c.author", "text": "$$c.text", "created": "$$c.created"}}}}},
        {"$merge": {"into": "posts_embedded", "whenMatched": "replace"}}])
    emb = db["posts_embedded"]
    print(f"\n[OK] Step 3  Migrated -> blog.posts_embedded "
          f"({emb.count_documents({})} posts, "
          f"{sum(d['comment_count'] for d in emb.find({}, {'comment_count': 1}))} embedded comments)")

    # --- Step 4: the same read against the embedded design ---
    print("\n=== Step 4  Query count to display one post with its comments ===")
    Counter.n = 0
    e = emb.find_one({"_id": 1})
    emb_q = Counter.n
    print(f"  embedded -> {e['title']} + {len(e['comments'])} comments ({emb_q} query, 1 collection)")
    print(f"\n  {'design':<34}{'queries':<9}collections  joined at")
    print(f"  {'referenced, app-side join':<34}{naive_q:<9}{2:<13}the application")
    print(f"  {'referenced, $lookup':<34}{lookup_q:<9}{2:<13}the server")
    print(f"  {'embedded':<34}{emb_q:<9}{1:<13}nowhere - already one document")
    print("\n  Reads get cheaper (1 document, no join); writes get dearer - a new")
    print("  comment now rewrites the post, and comments cannot be queried alone.")

    # --- export ---
    (HERE / "activity_3_2_referenced.json").write_text(json.dumps(
        {"posts": list(db["posts"].find({})),
         "comments": list(db["comments"].find({}, {"_id": 0}))}, indent=2, default=str))
    (HERE / "activity_3_2_embedded.json").write_text(json.dumps(
        list(emb.find({})), indent=2, default=str))
    print("\n[OK] Exported activity_3_2_referenced.json + activity_3_2_embedded.json")

    if ATLAS:
        import certifi
        acl = MongoClient(ATLAS, serverSelectionTimeoutMS=10000, tlsCAFile=certifi.where())
        for name, docs in (("posts", POSTS), ("comments", list(db["comments"].find({}))),
                           ("posts_embedded", list(emb.find({})))):
            acl["blog"].drop_collection(name)
            acl["blog"][name].insert_many(docs)
        print("[OK] Mirrored posts, comments, posts_embedded to Atlas -> blog")
        acl.close()
    else:
        print("[SKIP] Atlas mirror: MONGO_URI_ATLAS not set")
    client.close()


if __name__ == "__main__":
    main()
