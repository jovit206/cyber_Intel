"""Lab 4.1 Try It Yourself - Text search with $regex

Task: add a text search capability using the $regex operator to find products
whose name contains a search string, case-insensitively.

The Amazon export calls the product name `title`, so that is the field searched.

Prerequisite:
    python load_amazon_products.py

Run:
    python lab_01_try_it_yourself.py
    python lab_01_try_it_yourself.py --term "stainless steel"
    python lab_01_try_it_yourself.py --json
    python lab_01_try_it_yourself.py --compare-text-index
"""

import argparse
import json
import re
import sys
from pathlib import Path

from bson import json_util
from rich.console import Console
from rich.table import Table

# --- self-contained MongoDB connection --------------------------------------
# This activity folder runs on its own -- there is no shared package to install.
# Settings come from MONGO_URI / MONGO_DB in the environment or a .env beside
# this script, falling back to a local mongod on the default port.
import os
from pathlib import Path

from pymongo import MongoClient

try:
    from dotenv import load_dotenv
    for _env in (Path(__file__).resolve().parent / ".env",
                 Path(__file__).resolve().parent.parent / ".env"):
        if _env.exists():
            load_dotenv(_env)
            break
except ImportError:
    pass

_client = None


def get_client():
    """Return a singleton MongoClient."""
    global _client
    if _client is None:
        uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        kwargs = {"serverSelectionTimeoutMS": 5000}
        if uri.startswith("mongodb+srv://"):
            import certifi  # macOS Python often lacks system root CAs
            kwargs["tlsCAFile"] = certifi.where()
        _client = MongoClient(uri, **kwargs)
        _client.admin.command("ping")
        print(f"[OK] Connected to MongoDB at {uri.split('@')[-1]}")
    return _client


def get_db(db_name=None):
    """Return a database handle."""
    return get_client()[db_name or os.getenv("MONGO_DB", "nosql_labs")]


def reset_collection(db_name, collection_name):
    """Drop and recreate a collection."""
    db = get_db(db_name)
    if collection_name in db.list_collection_names():
        db.drop_collection(collection_name)
        print(f"[OK] Dropped collection: {db_name}.{collection_name}")
    return db[collection_name]


def close_client():
    """Close the connection."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        print("[OK] MongoDB connection closed.")


def banner(text, char="=", width=72):
    """Print a section banner."""
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)
    print()
# ----------------------------------------------------------------------------

console = Console()

DB_NAME = "nosql_labs"
COLL_NAME = "products"

CAPTURED: dict = {}


def short(text, width=58):
    text = str(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def show(rows, title, total=None, query=None, limit=8):
    """Render search hits as a table and record them for --json."""
    materialized = []
    for row in rows:
        if len(materialized) >= limit:
            break
        materialized.append(row)

    table = Table(title=title, title_style="bold cyan", header_style="bold")
    table.add_column("Title", overflow="fold")
    table.add_column("Category", overflow="fold")
    table.add_column("Price", justify="right")
    for row in materialized:
        table.add_row(short(row.get("title", "")),
                      short(row.get("category", ""), 24),
                      f"${row.get('price', 0):,.2f}")
    console.print(table)
    if not materialized:
        console.print("  [dim](no matches)[/dim]")

    CAPTURED[title] = {"shown": len(materialized), "documents": materialized}
    if query is not None:
        CAPTURED[title]["filter"] = query
    if total is not None:
        CAPTURED[title]["total_matching"] = total
    return materialized


# ---------------------------------------------------------------------------
# The search capability itself
# ---------------------------------------------------------------------------
def name_filter(term, case_sensitive=False, anchored=False, escape=True):
    """Build a $regex filter matching `term` inside the product title.

    escape=True runs the term through re.escape first. Search terms come from
    users, and an unescaped "(" or "*" is either a syntax error or -- worse --
    a silently different query. Only turn it off to pass a deliberate pattern.
    """
    pattern = re.escape(term) if escape else term
    if anchored:
        pattern = "^" + pattern
    regex = {"$regex": pattern}
    if not case_sensitive:
        # "i" is what makes this case-insensitive. Without it, /cotton/ will
        # not match "Cotton".
        regex["$options"] = "i"
    return {"title": regex}


def search_products_by_name(col, term, case_sensitive=False, anchored=False,
                            escape=True, page=1, page_size=8, projection=None):
    """Paginated case-insensitive name search. Returns (results, total)."""
    query = name_filter(term, case_sensitive, anchored, escape)
    projection = projection or {"title": 1, "category": 1, "price": 1, "_id": 0}
    total = col.count_documents(query)
    cursor = (col.find(query, projection)
                 .skip((page - 1) * page_size)
                 .limit(page_size))
    return list(cursor), total, query


# Words that are frequent in any English text and make useless demo searches.
STOPWORDS = {
    "with", "for", "and", "the", "from", "your", "that", "this", "you", "are",
    "not", "all", "can", "has", "have", "will", "our", "more", "than", "when",
    "into", "over", "out", "off", "per", "each", "any", "also", "use", "used",
    "using", "made", "make", "great", "perfect", "high", "quality", "premium",
    "best", "new", "free", "easy", "super", "ultra",
}


def pick_terms(col, sample=800):
    """Choose demo search terms from the titles actually loaded.

    Hardcoding "nike" or "stainless steel" produces empty tables whenever the
    collection is a small sample, so the terms are derived from the data.
    """
    from collections import Counter

    words = Counter()
    first_words = Counter()
    for doc in col.find({}, {"title": 1, "_id": 0}).limit(sample):
        title = doc.get("title", "")
        tokens = re.findall(r"[A-Za-z]{4,}", title)
        words.update(t.lower() for t in tokens)
        if tokens:
            first_words[tokens[0]] += 1

    common = [w for w, _ in words.most_common(80) if w not in STOPWORDS]
    contains = common[0] if common else "cotton"

    # A pair that genuinely co-occurs, so the $and demo returns rows.
    pair = None
    for i, a in enumerate(common[:12]):
        for b in common[i + 1:12]:
            if col.count_documents({"$and": [name_filter(a), name_filter(b)]}, limit=1):
                pair = (a, b)
                break
        if pair:
            break
    pair = pair or (contains, contains)

    prefix = next((w for w, _ in first_words.most_common(10)
                   if w.lower() not in STOPWORDS), "The")
    exclude = next((w for w in common[1:] if w != contains), "women")
    return {"contains": contains, "pair": pair, "prefix": prefix, "exclude": exclude}


def find_stage(node, wanted):
    """Return the first sub-plan whose stage is `wanted`."""
    if isinstance(node, dict):
        if node.get("stage") == wanted:
            return node
        for value in node.values():
            found = find_stage(value, wanted)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_stage(item, wanted)
            if found:
                return found
    return None


def plan_stages(explain_doc):
    """Collect every `stage` name in an explain plan, outermost first."""
    stages = []

    def walk(node):
        if isinstance(node, dict):
            if "stage" in node:
                stages.append(node["stage"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(explain_doc.get("queryPlanner", {}).get("winningPlan", {}))
    return stages


def main():
    parser = argparse.ArgumentParser(
        description="Lab 4.1 Try It Yourself - $regex product name search")
    parser.add_argument("--term", default=None,
                        help="search string (default: run the built-in demos)")
    parser.add_argument("--page-size", type=int, default=8)
    parser.add_argument("--compare-text-index", action="store_true",
                        help="also build a $text index on title and compare it "
                             "with $regex (slow on a full 1.43M load)")
    parser.add_argument("--json", metavar="PATH", nargs="?",
                        const="lab_01_tiy_results.json", default=None,
                        help="write all search results to JSON")
    args = parser.parse_args()

    banner("Lab 4.1 Try It Yourself: text search with $regex")

    col = get_db(DB_NAME)[COLL_NAME]
    total_docs = col.estimated_document_count()
    if total_docs == 0:
        sys.exit(f"[FAIL] {DB_NAME}.{COLL_NAME} is empty.\n"
                 f"       Run:  python load_amazon_products.py")
    print(f"[OK] Searching {total_docs:,} products on the `title` field.\n")

    # --- the requested capability -----------------------------------------
    if args.term:
        rows, total, query = search_products_by_name(
            col, args.term, page_size=args.page_size)
        show(rows, f"Products whose name contains {args.term!r} (case-insensitive)",
             total=total, query=query, limit=args.page_size)
        print(f"\n  {total:,} products matched.")
        text_term = args.term
    else:
        text_term = run_demos(col, args.page_size)

    if args.compare_text_index:
        compare_text_index(col, text_term)

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = Path(__file__).resolve().parent / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"database": DB_NAME, "collection": COLL_NAME,
             "documents_searched": total_docs, "results": CAPTURED},
            indent=2, default=json_util.default, ensure_ascii=False))
        print(f"\n[OK] Wrote results JSON -> {out}  ({out.stat().st_size / 1024:,.1f} KB)")

    banner("Lab 4.1 Try It Yourself Complete")
    close_client()


def run_demos(col, page_size):
    terms = pick_terms(col)
    print(f"  Demo terms chosen from the loaded titles: {terms}\n")
    CAPTURED["0. demo terms"] = terms

    # 1. The basic requirement: case-insensitive "contains".
    term = terms["contains"]
    rows, total, query = search_products_by_name(col, term, page_size=page_size)
    show(rows, f"1. Case-insensitive contains: {term!r}",
         total=total, query=query, limit=page_size)

    # 2. Prove the "i" option is what does the work.
    print("\n  Why $options: 'i' matters -- same term, case sensitivity toggled:")
    comparison = Table(header_style="bold")
    for header in ("Term", "Filter", "Matches"):
        comparison.add_column(header, overflow="fold")
    for probe in (term.lower(), term.capitalize(), term.upper()):
        for sensitive in (False, True):
            _, n, q = search_products_by_name(col, probe, case_sensitive=sensitive,
                                              page_size=1)
            comparison.add_row(repr(probe),
                               json.dumps(q["title"]),
                               f"{n:,}")
    console.print(comparison)
    CAPTURED["2. case sensitivity comparison"] = {
        "note": "the same term matches far more documents once $options:'i' is set"
    }

    # 3. Search terms are user input, so escape them.
    raw = f"({terms['contains']}"
    print(f"\n  Escaping user input -- searching for {raw!r}:")
    _, escaped_n, escaped_q = search_products_by_name(col, raw, page_size=1)
    print(f"    escaped   -> pattern {escaped_q['title']['$regex']!r}  "
          f"{escaped_n:,} matches")
    try:
        _, raw_n, raw_q = search_products_by_name(col, raw, escape=False, page_size=1)
        print(f"    unescaped -> pattern {raw_q['title']['$regex']!r}  "
              f"{raw_n:,} matches  <- a DIFFERENT query, not what the user asked")
    except Exception as exc:
        print(f"    unescaped -> {type(exc).__name__}: {str(exc)[:90]}")
    CAPTURED["3. escaping user input"] = {
        "term": raw, "escaped_pattern": escaped_q["title"]["$regex"],
        "escaped_matches": escaped_n,
    }

    # 4. Two words that need not be adjacent: AND two regexes.
    words = list(terms["pair"])
    query = {"$and": [name_filter(w) for w in words]}
    total = col.count_documents(query)
    rows = list(col.find(query, {"title": 1, "category": 1, "price": 1, "_id": 0})
                   .limit(page_size))
    show(rows, f"4. $and of two regexes: title contains {' AND '.join(words)}",
         total=total, query=query, limit=page_size)

    # 5. Anchored prefix search.
    prefix = terms["prefix"]
    rows, total, query = search_products_by_name(col, prefix, anchored=True,
                                                 page_size=page_size)
    show(rows, f"5. Anchored: title STARTS WITH {prefix!r}",
         total=total, query=query, limit=page_size)

    # 6. Negation.
    query = {"$and": [name_filter(term),
                      {"title": {"$not": re.compile(re.escape(terms["exclude"]), re.I)}}]}
    total = col.count_documents(query)
    rows = list(col.find(query, {"title": 1, "category": 1, "price": 1, "_id": 0})
                   .limit(page_size))
    show(rows, f"6. $not: contains {term!r} but NOT {terms['exclude']!r}",
         total=total, query=query, limit=page_size)

    # 7. What this costs. This is the part worth internalising.
    explain_regex_cost(col, terms)
    return terms["contains"]


def explain_regex_cost(col, terms):
    """Show what each regex shape costs, measured rather than asserted.

    The interesting number is NOT whether an IXSCAN appears -- it appears for
    every shape here. It is the index BOUNDS, and therefore how many index keys
    have to be walked.
    """
    print("\n  What $regex costs -- measured with explain('executionStats'):")
    col.create_index("title", name="title_1")
    db = col.database
    word = terms["contains"]
    # Anchored probes use the most common FIRST word, so the narrow-bounds row
    # actually returns documents rather than proving the point on an empty set.
    capitalized = terms["prefix"]

    probes = [
        (f"/{word}/i   case-insensitive, unanchored",
         name_filter(word)),
        (f"/{word}/    case-sensitive, unanchored",
         name_filter(word, case_sensitive=True)),
        (f"/^{capitalized}/   case-sensitive, ANCHORED",
         name_filter(capitalized, case_sensitive=True, anchored=True)),
        (f"/^{capitalized}/i  case-insensitive, anchored",
         name_filter(capitalized, anchored=True)),
    ]

    table = Table(header_style="bold")
    for header in ("Query shape", "Index bounds", "Keys examined", "Returned", "ms"):
        table.add_column(header, overflow="fold")

    captured = {}
    total_keys = col.estimated_document_count()
    for label, query in probes:
        plan = db.command("explain", {"find": col.name, "filter": query},
                          verbosity="executionStats")
        stats = plan["executionStats"]
        ixscan = find_stage(plan["queryPlanner"]["winningPlan"], "IXSCAN") or {}
        bounds = ixscan.get("indexBounds", {}).get("title", ["n/a"])[0]
        keys = stats["totalKeysExamined"]
        table.add_row(label, str(bounds), f"{keys:,}",
                      f"{stats['nReturned']:,}", str(stats["executionTimeMillis"]))
        captured[label] = {
            "index_bounds": bounds,
            "keys_examined": keys,
            "docs_examined": stats["totalDocsExamined"],
            "returned": stats["nReturned"],
            "execution_ms": stats["executionTimeMillis"],
            "walked_whole_index": keys >= total_keys,
        }

    console.print(table)
    CAPTURED["7. what each regex shape costs"] = captured

    print(f"  Every shape reports an IXSCAN, so the stage name tells you nothing.")
    print(f"  The bounds do. An unanchored or case-insensitive regex gets the")
    print(f'  bounds ["", {{}}) -- the entire index -- so it walks all')
    print(f"  {total_keys:,} keys and filters. Only a case-SENSITIVE, ^-anchored")
    print(f"  regex gets a real range, and it touches a handful of keys instead.")
    print(f"  At 1.43M documents that is the difference between a search box and")
    print(f"  a table scan. For genuinely case-insensitive search at scale, use a")
    print(f"  $text index or a strength-2 collation -- see --compare-text-index.")


def compare_text_index(col, term):
    """Build a $text index and contrast it with the $regex approach."""
    print("\n  Building a $text index on title (one-off cost)...")
    col.create_index([("title", "text")], name="title_text")

    regex_n = col.count_documents(name_filter(term))
    text_n = col.count_documents({"$text": {"$search": term}})

    table = Table(title=f"$regex vs $text for {term!r}", title_style="bold cyan",
                  header_style="bold")
    for header in ("Approach", "Filter", "Matches", "Index"):
        table.add_column(header, overflow="fold")
    table.add_row("$regex", f'{{"title": {{"$regex": "{term}", "$options": "i"}}}}',
                  f"{regex_n:,}", "title_1, but walks every key")
    table.add_row("$text", f'{{"$text": {{"$search": "{term}"}}}}',
                  f"{text_n:,}", "title_text")
    console.print(table)

    CAPTURED["8. $regex vs $text"] = {
        "term": term, "regex_matches": regex_n, "text_matches": text_n,
    }
    print("  They are not interchangeable. $text is word-tokenised and stemmed,")
    print("  so it matches whole words (and misses substrings inside a word);")
    print("  $regex matches raw substrings anywhere. Different counts are expected.")


if __name__ == "__main__":
    main()
