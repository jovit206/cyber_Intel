#!/usr/bin/env python3
"""Write/read continuously and optionally step down the current replica-set primary."""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus
from uuid import uuid4

from pymongo import MongoClient, ReadPreference
from pymongo.errors import AutoReconnect, OperationFailure, PyMongoError
from pymongo.write_concern import WriteConcern


def build_uri() -> str:
    username = os.environ.get("MONGO_INITDB_ROOT_USERNAME")
    password = os.environ.get("MONGO_INITDB_ROOT_PASSWORD")
    hosts = os.environ.get("MONGODB_HOSTS")
    if not username or not password or not hosts:
        raise RuntimeError(
            "Set MONGO_INITDB_ROOT_USERNAME, MONGO_INITDB_ROOT_PASSWORD, "
            "and MONGODB_HOSTS (run this lab through Docker Compose)."
        )
    return (
        f"mongodb://{quote_plus(username)}:{quote_plus(password)}@{hosts}/admin"
        "?replicaSet=rs0&authSource=admin&retryWrites=true&w=majority"
        "&serverSelectionTimeoutMS=5000&connectTimeoutMS=5000"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=120, help="Run time in seconds (default: 120).")
    parser.add_argument("--interval", type=float, default=1.0, help="Delay between successful operations.")
    parser.add_argument(
        "--stepdown",
        action="store_true",
        help="Request a 60-second stepdown of the current primary (use only on this lab cluster).",
    )
    args = parser.parse_args()
    if args.duration < 1 or args.interval < 0:
        parser.error("--duration must be positive and --interval cannot be negative.")
    return args


def main() -> None:
    args = parse_args()
    client = MongoClient(build_uri(), appname="chapter13-failover-test")
    probes = client["lab13"].get_collection(
        "failover_probes",
        write_concern=WriteConcern(w="majority", wtimeout=10_000),
        read_preference=ReadPreference.PRIMARY,
    )
    client.admin.command("ping")
    initial_primary = client.admin.command("hello").get("primary")
    if not initial_primary:
        raise RuntimeError("The replica set has no elected primary.")
    print(f"Connected to rs0. Current primary: {initial_primary}", flush=True)

    started = time.monotonic()
    last_success = started
    sequence = 0
    stepdown_requested = False
    failover_confirmed = False
    previous_primary = initial_primary

    try:
        while time.monotonic() - started < args.duration:
            try:
                sequence += 1
                document_id = str(uuid4())
                probes.insert_one(
                    {
                        "_id": document_id,
                        "sequence": sequence,
                        "created_at": datetime.now(timezone.utc),
                    }
                )
                stored = probes.find_one({"_id": document_id})
                if stored is None or stored["sequence"] != sequence:
                    raise RuntimeError(f"Read-after-write verification failed for operation {sequence}.")

                hello = client.admin.command("hello")
                current_primary = hello.get("primary")
                if not current_primary:
                    raise RuntimeError("The replica set currently has no elected primary.")
                last_success = time.monotonic()
                print(
                    f"operation={sequence} write=majority read=ok primary={current_primary}",
                    flush=True,
                )

                if args.stepdown and not stepdown_requested:
                    previous_primary = current_primary
                    print(f"Requesting rs.stepDown(60) on {previous_primary}...", flush=True)
                    stepdown_requested = True
                    try:
                        client.admin.command({"replSetStepDown": 60})
                    except (AutoReconnect, OperationFailure) as error:
                        print(f"Primary stepped down; waiting for election ({error.__class__.__name__}).", flush=True)

                if stepdown_requested and current_primary != previous_primary:
                    if not failover_confirmed:
                        print(
                            f"Failover confirmed: new primary is {current_primary}; "
                            "majority writes and reads have resumed.",
                            flush=True,
                        )
                    failover_confirmed = True
            except PyMongoError as error:
                print(f"Temporary MongoDB interruption; driver will retry: {error.__class__.__name__}", flush=True)
                if time.monotonic() - last_success > 90:
                    raise RuntimeError("No successful database operation for 90 seconds.") from error
                time.sleep(min(args.interval, 1.0))
                continue

            time.sleep(args.interval)
    finally:
        client.close()

    if args.stepdown and not failover_confirmed:
        raise RuntimeError(
            "The run ended before a different primary was observed. "
            "Run longer with --duration or inspect rs.status()."
        )
    print(f"Completed {sequence} verified write/read operations.", flush=True)


if __name__ == "__main__":
    main()
