#!/usr/bin/env python3
"""Lab 9.1 - Measure write concern latency.

For every configuration this inserts DOCS small documents (default 1,000) and records elapsed time,
average latency per write, P50/P99, and acknowledged / timed-out / failed counts:

    w:1  |  w:majority  |  w:all      each with  j:false  and  j:true

About w:all: MongoDB does not accept ``w: "all"`` (the server reports "No write concern mode named
'all'"). Acknowledgement by every member is requested with the number of data-bearing members
(``w: 3`` for a three-member set). The number is read from the replica-set configuration at run time and
the mapping is printed and saved with the results.

Run it manually (it is never started automatically). Inside the Docker deployment:

    docker compose exec lab7-backend python labs/lab9/lab_01_write_concern_latency.py

Results are written to labs/lab9/results/ as timestamped JSON + CSV. By default the benchmark writes to
NEW collections (cap_latency_test.run_<timestamp>_<config>) so earlier measurements are never
overwritten; pass --replace-historical to refresh the collections the dashboard's "historical" view reads.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from backend.services import write_concern as core  # noqa: E402

DEFAULT_URI = "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lab 9.1 write concern latency benchmark")
    parser.add_argument("--uri", default=os.getenv("REPLICA_SET_URI") or os.getenv("MONGO_URI") or DEFAULT_URI,
                        help="replica-set connection string (default: $REPLICA_SET_URI, $MONGO_URI or the compose members)")
    parser.add_argument("--docs", type=int, default=int(os.getenv("DOC_COUNT", core.DEFAULT_DOCS)),
                        help="documents per configuration (default 1000)")
    parser.add_argument("--wtimeout-ms", type=int, default=core.DEFAULT_WTIMEOUT_MS,
                        help="per-write write-concern timeout in milliseconds")
    parser.add_argument("--database", default=core.DEFAULT_DATABASE)
    parser.add_argument("--replace-historical", action="store_true",
                        help="drop and refill cap_latency_test.data_<config> (the dashboard's historical view)")
    parser.add_argument("--results-dir", default=None, help="where to write the JSON/CSV artifacts")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate configuration and connectivity, print the plan, write nothing")
    args = parser.parse_args(argv)
    if not 1 <= args.docs <= 100_000:
        parser.error("--docs must be between 1 and 100000")
    if args.wtimeout_ms < 100:
        parser.error("--wtimeout-ms must be at least 100")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("=" * 78)
    print("LAB 9.1: WRITE CONCERN LATENCY BENCHMARK")
    print("=" * 78)

    client = MongoClient(args.uri, serverSelectionTimeoutMS=10_000)
    try:
        client.admin.command("ping")
        members = core.data_bearing_members(client)
        topology = core.topology_snapshot(client)
    except PyMongoError as exc:
        print(f"[ERROR] Cannot reach the replica set: {exc}")
        print("        Run this inside the Docker network, e.g. "
              "'docker compose exec lab7-backend python labs/lab9/lab_01_write_concern_latency.py'.")
        return 2

    print(f"[OK] Replica set {topology['set']}: primary {topology['primary']}, {members} data-bearing members")
    for member in topology["members"]:
        print(f"       {member['name']:<18} {member['state']:<10} health={member['health']}")
    configs = core.build_configurations(members)
    print("\nPlanned configurations "
          f"({args.docs:,} inserts each, wtimeout {args.wtimeout_ms} ms):")
    for c in configs:
        print(f"  {c.requested:<12} j:{str(c.journal).lower():<5} -> w={c.w!r:<11} {c.label}")
    all_cfg = next((c for c in configs if c.mapping), None)
    if all_cfg:
        print(f"\nNote: {all_cfg.mapping}")

    if args.dry_run:
        print("\n[dry-run] Nothing was written.")
        client.close()
        return 0

    print()
    try:
        result = core.run_experiment(
            client, docs=args.docs, wtimeout_ms=args.wtimeout_ms, database=args.database,
            replace_historical=args.replace_historical, progress=print,
        )
    except core.ExperimentError as exc:
        print(f"[ERROR] {exc}")
        return 3
    finally:
        client.close()

    print("\n" + "=" * 78)
    print(core.format_table(result))
    print("=" * 78)
    print("\nWhat the numbers say:")
    for line in result["interpretation"]:
        print(f"  - {line}")
    print("\nStorage: " + ("historical collections replaced (data_<config>)." if args.replace_historical
                           else f"new collections cap_latency_test.run_{result['run_id']}_<config>; "
                                "existing historical data untouched."))
    paths = core.save_artifacts(result, Path(args.results_dir) if args.results_dir else None)
    print("Artifacts:")
    for kind, path in paths.items():
        print(f"  {kind:<7} {path}")
    print("\nLab 9.1 completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
