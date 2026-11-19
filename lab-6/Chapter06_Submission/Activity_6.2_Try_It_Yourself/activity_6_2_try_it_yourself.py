import json
import os
import sys
import timeit
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from rich.console import Console
from rich.table import Table

try:
    from dotenv import load_dotenv

    for _env in (Path(__file__).resolve().parent / ".env",
                 Path(__file__).resolve().parent.parent / ".env",
                 Path(__file__).resolve().parent.parent.parent / ".env"):
        if _env.exists():
            load_dotenv(_env)
            break
except ImportError:
    pass

DB_NAME = "chapter_06"
COLL_NAME = "products_perf"
INDEX_NAME = "idx_category_price_rating"  # the one index Activity 6.2 kept
TIMED_RUNS = 500
OUT_JSON = Path(__file__).resolve().parent / "activity_6_2_try_it_yourself_covered_query.json"

console = Console()
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
            kwargs["serverSelectionTimeoutMS"] = 10000
        _client = MongoClient(uri, **kwargs)
        _client.admin.command("ping")  # fail fast on a bad URI
        print(f"[OK] Connected to MongoDB at {uri.split('@')[-1]}")
    return _client


def close_client():
    global _client
    if _client is not None:
        _client.close()
        _client = None
        print("[OK] MongoDB connection closed.")


def banner(text: str, char: str = "=", width: int = 72) -> None:
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)
    print()


# A query is covered when the filter, the sort AND every returned field are in
# one index, so MongoDB answers from the index keys and never reads a document.
INDEXED = {"category": 1, "price": 1, "rating": 1}  # the fields of INDEX_NAME
FILTER = {"category": "Electronics", "price": {"$gte": 100, "$lte": 150}}
SORT = [("price", 1)]

# The same filter and sort with four projections. Only the last is covered.
VARIANTS = [
    {"id": "A", "label": "No projection (whole document)", "projection": None,
     "why": "returns every field, so each match is fetched"},
    {"id": "B", "label": "Indexed fields, _id left in", "projection": INDEXED,
     "why": "_id is returned by default and is not in the index"},
    {"id": "C", "label": "Indexed fields + name (not indexed)", "projection": {"_id": 0, **INDEXED, "name": 1},
     "why": "name is not in the index"},
    {"id": "D", "label": "Indexed fields only, _id excluded", "projection": {"_id": 0, **INDEXED},
     "why": "every returned field comes from the index keys"},
]


def explain(col, projection):
    """Run the explain command with executionStats verbosity."""
    cmd = {"find": col.name, "filter": FILTER, "sort": dict(SORT)}
    if projection:
        cmd["projection"] = projection
    return col.database.command("explain", cmd, verbosity="executionStats")


def plan_stages(plan):
    """Flatten the winning plan into a list like ['PROJECTION_COVERED', 'IXSCAN']."""
    node = plan["queryPlanner"]["winningPlan"]
    node = node.get("queryPlan", node)  # slot-based engine nests the plan one level down
    stages = []
    while node:
        stages.append(node["stage"])
        node = node.get("inputStage")
    return stages


def time_query(col, projection):
    """Average wall-clock milliseconds over TIMED_RUNS executions."""
    total = timeit.timeit(lambda: list(col.find(FILTER, projection, sort=SORT)), number=TIMED_RUNS)
    return total / TIMED_RUNS * 1000


def main():
    banner("Lab 6.2 Try It Yourself: Covered Queries")

    col = get_client()[DB_NAME][COLL_NAME]
    total_docs = col.estimated_document_count()
    index_info = col.index_information()
    if total_docs == 0 or INDEX_NAME not in index_info:
        sys.exit(
            f"[FAIL] {DB_NAME}.{COLL_NAME} has no {INDEX_NAME}. Run Activity 6.2 first:\n"
            f"       python ../Activity_6.2/activity_6_2_index_strategies.py"
        )
    print(f"[OK] Querying {total_docs:,} products in {DB_NAME}.{COLL_NAME} (read-only).")
    index_key = dict(index_info[INDEX_NAME]["key"])
    print(f"     Index: {INDEX_NAME} {index_key}")
    print(f"     Filter: {FILTER}  Sort: {dict(SORT)}\n")

    results = []
    for v in VARIANTS:
        plan = explain(col, v["projection"])
        stages = plan_stages(plan)
        fetch = "FETCH" in stages
        stats = plan["executionStats"]
        results.append({
            "variant": v["id"],
            "label": v["label"],
            "projection": v["projection"],
            "stages": " > ".join(stages),
            "fetch_stage": fetch,
            "totalKeysExamined": stats["totalKeysExamined"],
            "totalDocsExamined": stats["totalDocsExamined"],
            "nReturned": stats["nReturned"],
            "covered": not fetch and stats["totalDocsExamined"] == 0,
            "why": v["why"],
            "avg_ms": time_query(col, v["projection"]),
        })

    table = Table(title="explain(): Does the projection make the query covered?")
    table.add_column("", style="cyan")
    table.add_column("Projection")
    table.add_column("Winning plan")
    table.add_column("Keys", justify="right")
    table.add_column("Docs", justify="right")
    table.add_column("Returned", justify="right")
    table.add_column(f"Avg ms ({TIMED_RUNS} runs)", justify="right")
    table.add_column("Covered")
    for r in results:
        table.add_row(
            r["variant"], r["label"], r["stages"],
            f"{r['totalKeysExamined']:,}", f"{r['totalDocsExamined']:,}", f"{r['nReturned']:,}",
            f"{r['avg_ms']:.3f}",
            "[green]YES[/green]" if r["covered"] else "[red]no[/red]",
        )
    console.print(table)

    print()
    for r in results:
        print(f"  {r['variant']}: {'covered' if r['covered'] else 'not covered'} -- {r['why']}.")

    # --- Verify the covered query -------------------------------------------
    covered_row, full_row = results[-1], results[0]
    print(f"\n=== Verification (variant {covered_row['variant']}) ===")
    if covered_row["covered"]:
        print(f"[OK] FETCH stage eliminated: winning plan is {covered_row['stages']},"
              f" totalDocsExamined = {covered_row['totalDocsExamined']}.")
    else:
        print(f"[FAIL] Variant {covered_row['variant']} still fetches documents: {covered_row['stages']}")
    print(f"     Uncovered ({full_row['variant']}) examined {full_row['totalDocsExamined']:,} documents for the same "
          f"{full_row['nReturned']:,} results.")
    print(f"     Covered speedup vs {full_row['variant']}: {full_row['avg_ms'] / covered_row['avg_ms']:.1f}x "
          f"({full_row['avg_ms']:.3f} -> {covered_row['avg_ms']:.3f} ms per query).")
    print("\n  Rules for a covered query: filter, sort and projection use only fields in one")
    print("  index; exclude _id unless it is in that index; and none of the fields may be an")
    print("  array (a multikey index like idx_tags can never cover a query).")

    sample = list(col.find(FILTER, covered_row["projection"], sort=SORT, limit=5))
    print(f"\n  First 5 results of the covered query: {sample}")

    # --- MongoDB JSON for the submission ---------------------------------
    payload = {
        "database": DB_NAME,
        "collection": COLL_NAME,
        "index": {"name": INDEX_NAME, "key": index_key},
        "filter": FILTER,
        "sort": dict(SORT),
        "timed_runs": TIMED_RUNS,
        "variants": results,
        "covered_query_verified": covered_row["covered"],
        "covered_query_sample": sample,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=json_util.default))
    print(f"\n[OK] Wrote results JSON -> {OUT_JSON.name}")

    banner("Lab 6.2 Try It Yourself Complete")
    close_client()


if __name__ == "__main__":
    main()
