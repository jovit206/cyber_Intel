import io
import os
import random
from pathlib import Path
from bson import BSON, Binary
from bson.errors import InvalidDocument
from bson.json_util import dumps
from gridfs import GridFSBucket
from PIL import Image, ImageDraw
from pymongo import MongoClient, DESCENDING
from pymongo.errors import DocumentTooLarge, OperationFailure, WriteError

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "images"
URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017")
ATLAS = os.getenv("MONGO_URI_ATLAS")
THUMB = (64, 96)
BUCKET = "post_images"
LIMIT = 16 * 1024 * 1024                        # BSON document limit

# a small upload, two ordinary photos, a print-resolution file, and one that
# cannot fit a document at all
SAMPLES = [("Harbour at Dusk", (400, 600), 85),
           ("Rooftops in the Rain", (1000, 1500), 85),
           ("Night Market", (2000, 3000), 88),
           ("Salt Flats", (3500, 5250), 90),
           ("Observatory 6K", (6000, 9000), 95)]


def draw(title, size, quality, seed):
    """One sample image, cached on disk.  The grain is what makes the file size
    realistic - a flat gradient compresses to nothing and measures nothing."""
    path = IMAGES / (title.lower().replace(" ", "_") + ".jpg")
    if path.exists():
        return path
    w, h = size
    rnd = random.Random(seed)
    top, bot = [tuple(rnd.randrange(20, 200) for _ in range(3)) for _ in range(2)]
    img = Image.new("RGB", size)
    d, band = ImageDraw.Draw(img), max(1, h // 200)
    for y in range(0, h, band):                 # vertical gradient
        t = y / h
        d.rectangle([0, y, w, y + band],
                    fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    img = Image.blend(img, Image.effect_noise(size, 28).convert("RGB"), 0.18)
    IMAGES.mkdir(exist_ok=True)
    img.save(path, "JPEG", quality=quality)
    return path


def thumbnail(path):
    """The 64x96 preview - small, bounded, always rendered, so it may be embedded."""
    img = Image.open(path)
    img.draft("RGB", THUMB)                     # decode straight to ~thumb size
    buf = io.BytesIO()
    img.convert("RGB").resize(THUMB).save(buf, "JPEG", quality=70)
    return buf.getvalue()


def mb(n):
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1 << 20 else f"{n / 1024:.0f} KB"


def main():
    print("=== 0  Five sample images ===")
    if not IMAGES.exists():
        print(f"  drawing them once into {IMAGES.name}/ (the 6K one takes a moment)")
    samples = []
    for i, (title, size, quality) in enumerate(SAMPLES):
        path = draw(title, size, quality, i)
        raw = path.read_bytes()
        samples.append({"_id": i + 1, "title": title, "path": path, "raw": raw,
                        "size": size, "thumb": thumbnail(path)})
        print(f"  {title:<21} {size[0]:>5}x{size[1]:<5} {len(raw):>11,} B ({mb(len(raw)):>8})"
              f"   thumb {len(samples[-1]['thumb']):,} B")
    total = sum(len(s["raw"]) for s in samples)

    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    print(f"\nConnected to MongoDB {client.server_info()['version']} at {URI}")
    db = client["blog"]
    for name in ("image_posts_embedded", "image_posts",
                 f"{BUCKET}.files", f"{BUCKET}.chunks"):
        db.drop_collection(name)

    # --- design A: the JPEG inline in the post document ---
    print("\n=== 1  Design A - the image embedded as BinData ===")
    for s in samples:
        doc = {"_id": s["_id"], "title": s["title"], "author": "arjun",
               "image": {"filename": s["path"].name, "content_type": "image/jpeg",
                         "width": s["size"][0], "height": s["size"][1],
                         "data": Binary(s["raw"])}}
        s["embedded"] = None
        try:
            size = len(BSON.encode(doc))        # what the driver would send
            db["image_posts_embedded"].insert_one(doc)
            s["embedded"] = size
            print(f"  {s['title']:<21} {size:>11,} B document - "
                  f"{len(s['raw']) / size:.1%} of it is the image")
        except (InvalidDocument, DocumentTooLarge, WriteError, OperationFailure) as e:
            print(f"  {s['title']:<21} {len(s['raw']):>11,} B image  -> [REJECTED] "
                  f"{type(e).__name__}: {str(e).split(' - ')[0]}")
    stored = [s for s in samples if s["embedded"]]
    print(f"  stored {len(stored)}/{len(samples)} - the limit is a ceiling on what a "
          "user may upload, and it fails at write time")
    print( "  every one of them is ~100% image, so BinData travels with every read of "
           "the post")

    # --- design B: GridFS, with the thumbnail and the metadata embedded ---
    bucket = GridFSBucket(db, bucket_name=BUCKET)
    posts = []
    for s in samples:
        with open(s["path"], "rb") as fh:
            fid = bucket.upload_from_stream(s["path"].name, fh,
                                            metadata={"post_id": s["_id"],
                                                      "width": s["size"][0],
                                                      "height": s["size"][1]})
        posts.append({"_id": s["_id"], "title": s["title"], "author": "arjun",
                      "image": {"gridfs_id": fid, "bucket": BUCKET,
                                "filename": s["path"].name, "content_type": "image/jpeg",
                                "width": s["size"][0], "height": s["size"][1],
                                "bytes": len(s["raw"]), "thumb": Binary(s["thumb"])}})
    db["image_posts"].insert_many(posts)
    lean = sum(len(BSON.encode(p)) for p in posts)
    chunks = db[f"{BUCKET}.chunks"].count_documents({})

    print("\n=== 2  Design B - GridFS, with the thumbnail embedded ===")
    print(f"  blog.image_posts  {len(posts)} posts, {lean:,} B in total - all "
          f"{len(samples)} stored, including the one design A could not hold")
    print(f"  blog.{BUCKET}  {db[f'{BUCKET}.files'].count_documents({})} files, "
          f"{chunks} chunks of 255 KB ({total:,} B, out of the documents entirely)")
    print(f"  every post is ~{lean // len(posts):,} B whatever the upload is; the "
          f"{len(stored)} design A stored come to {sum(s['embedded'] for s in stored) // lean}x that")

    print("\n  the gallery page - 1 query, thumbnails in hand, none of the "
          f"{mb(total)} moves:")
    for p in db["image_posts"].find().sort("image.bytes", DESCENDING).limit(3):
        print(f"    {p['title']:<21} {mb(p['image']['bytes']):>9}  "
              f"thumb {len(p['image']['thumb']):,} B inline")

    big = samples[-1]
    p = db["image_posts"].find_one({"_id": big["_id"]})
    raw = bucket.open_download_stream(p["image"]["gridfs_id"]).read()
    print(f"\n  the full image, only when the reader opens {big['title']!r}:")
    print(f"    GridFS read -> {len(raw):,} B, identical to the file on disk: "
          f"{raw == big['raw']} ({len(raw):,} B > the {LIMIT:,} B document limit)")

    stats = db[f"{BUCKET}.files"].aggregate([
        {"$group": {"_id": None, "n": {"$sum": 1}, "bytes": {"$sum": "$length"},
                    "max": {"$max": "$length"}}}]).next()
    print(f"\n  and the files are documents, so they can be reported on:")
    print(f"    {stats['n']} files, {mb(stats['bytes'])} total, largest "
          f"{mb(stats['max'])} - a BinData field has a length the server "
          "will not sum for you")

    print("\n=== 3  Why, in one line each ===")
    for line in ("16 MB cap           - a hard ceiling on the upload, failing at write time",
                 "read amplification  - BinData travels with every read, even a title listing",
                 "write amplification - re-cropping rewrites the whole document",
                 "no file queries     - sizes and dimensions cannot be grouped or ranged",
                 "embed               - the thumbnail: small, bounded, always rendered",
                 "reference           - the file: GridFS above ~1 MB, or S3 plus a URL when",
                 "                      the bytes need a CDN in front"):
        print("  " + line)

    (HERE / "activity_3_2_try_it_yourself_posts.json").write_text(
        dumps(list(db["image_posts"].find().sort("_id")), indent=2))
    (HERE / "activity_3_2_try_it_yourself_sizes.json").write_text(dumps({
        "images": [{"post_id": s["_id"], "title": s["title"], "file": s["path"].name,
                    "width": s["size"][0], "height": s["size"][1],
                    "image_bytes": len(s["raw"]), "thumb_bytes": len(s["thumb"]),
                    "embedded_document_bytes": s["embedded"]} for s in samples],
        "image_bytes_total": total, "post_bytes_total": lean, "gridfs_chunks": chunks,
        "document_limit": LIMIT, "rejected_by_limit": len(samples) - len(stored)},
        indent=2))
    print(f"\n[OK] Exported the {len(posts)} post documents, the size comparison, "
          f"and {IMAGES.name}/ ({len(samples)} images)")

    if ATLAS:
        import certifi
        acl = MongoClient(ATLAS, serverSelectionTimeoutMS=20000, tlsCAFile=certifi.where())
        adb = acl["blog"]
        for name in ("image_posts", f"{BUCKET}.files", f"{BUCKET}.chunks"):
            adb.drop_collection(name)
        abucket = GridFSBucket(adb, bucket_name=BUCKET)
        for s, doc in zip(samples, posts):
            with open(s["path"], "rb") as fh:
                doc["image"]["gridfs_id"] = abucket.upload_from_stream(
                    s["path"].name, fh, metadata={"post_id": s["_id"]})
            adb["image_posts"].insert_one(doc)
        print(f"[OK] Mirrored {len(posts)} posts and their images to Atlas -> "
              f"blog.image_posts + blog.{BUCKET}")
        acl.close()
    else:
        print("[SKIP] Atlas mirror: MONGO_URI_ATLAS not set")
    client.close()


if __name__ == "__main__":
    main()
