"""Activity 2.1 (Try it yourself) - Connect to several MongoDB deployments at the same time and compare them.

Author: Jovit Chawla (24BTRCL089)

Task: run more than one MongoDB deployment simultaneously, connect to all of them from one script and compare
their server_info(). Deployments used: the jovit-lab02-mongo Docker container, the native MongoDB Windows
service and - when MONGO_URI_ATLAS is configured - MongoDB Atlas.

All clients are opened concurrently (one thread each). For every deployment the script records server_info()
and hello fields plus a median round-trip latency, prints a side-by-side table, stores the comparison in each
reachable deployment (jovit_lab02.server_comparison) and exports it to results/.
"""
from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from common import DATABASE, Recorder, client_for, deployments, mask

FIELDS = [
    ("version", lambda i, h: i.get("version")),
    ("gitVersion", lambda i, h: (i.get("gitVersion") or "")[:12]),
    ("target OS", lambda i, h: i.get("buildEnvironment", {}).get("target_os")),
    ("distmod", lambda i, h: i.get("buildEnvironment", {}).get("distmod") or "-"),
    ("bits", lambda i, h: i.get("bits")),
    ("allocator", lambda i, h: i.get("allocator")),
    ("OpenSSL", lambda i, h: (i.get("openssl", {}) or {}).get("running", "-")),
    ("storage engines", lambda i, h: ", ".join(i.get("storageEngines", []))),
    ("max wire version", lambda i, h: h.get("maxWireVersion")),
    ("topology", lambda i, h: h.get("setName") or ("mongos" if h.get("msg") == "isdbgrid" else "standalone")),
    ("writable primary", lambda i, h: h.get("isWritablePrimary")),
    ("max BSON size (MB)", lambda i, h: round(h.get("maxBsonObjectSize", 0) / 2**20)),
]


def inspect(name: str, uri: str) -> dict:
    client = client_for(uri)
    try:
        client.admin.command("ping")
        samples = []
        for _ in range(20):
            started = time.perf_counter()
            client.admin.command("ping")
            samples.append((time.perf_counter() - started) * 1000)
        info, hello = client.server_info(), client.admin.command("hello")
        row = {label: fn(info, hello) for label, fn in FIELDS}
        row["ping ms (median of 20)"] = round(statistics.median(samples), 3)
        return {"name": name, "uri": mask(uri), "reachable": True, "fields": row, "client": client}
    except Exception as exc:  # noqa: BLE001
        client.close()
        return {"name": name, "uri": mask(uri), "reachable": False, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}


def main() -> None:
    rec = Recorder("activity_2_1_try_it_yourself", "Activity 2.1 (Try it yourself) - Compare MongoDB deployments")
    targets = deployments()
    if "atlas" not in targets:
        rec.log("  note: MONGO_URI_ATLAS is not configured, so Atlas is not part of this run")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda item: inspect(*item), targets.items()))
    rec.log(f"  opened {len(results)} connections concurrently in {(time.perf_counter() - started) * 1000:.0f} ms")
    up = [r for r in results if r["reachable"]]
    for r in results:
        if not r["reachable"]:
            rec.log(f"  {r['name']}: UNREACHABLE ({r['uri']}) {r['error']}")

    rec.section("Side-by-side comparison (live server_info() / hello)")
    labels = [label for label, _ in FIELDS] + ["ping ms (median of 20)"]
    rec.log(f"  {'field':<24}" + "".join(f"{r['name'] + ' ' + r['uri']:<36}" for r in up))
    differences = []
    for label in labels:
        values = [r["fields"][label] for r in up]
        if len({str(v) for v in values}) > 1 and label != "ping ms (median of 20)":
            differences.append(label)
        rec.log(f"  {label:<24}" + "".join(f"{str(v):<36}" for v in values))
    rec.section("What differs")
    rec.log(f"  fields that differ between deployments: {', '.join(differences) or 'none'}")

    comparison = {"run_at": rec.environment["run_at_utc"], "student": rec.environment["student"],
                  "deployments": [{k: v for k, v in r.items() if k != "client"} for r in results],
                  "differences": differences}
    for r in up:
        coll = r["client"][DATABASE]["server_comparison"]
        inserted = coll.insert_one(dict(comparison)).inserted_id
        rec.log(f"  stored comparison in {r['name']}:{DATABASE}.server_comparison (_id {inserted})")
        r["client"].close()
    rec.data = comparison
    rec.save()


if __name__ == "__main__":
    main()
