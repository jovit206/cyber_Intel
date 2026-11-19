"""Lab 7.2: periodic WiredTiger and GitHub annual report monitor."""

from __future__ import annotations

import argparse
import logging
import re
import time
from typing import Any

import httpx

from backend.config import settings
from backend.database import close_database, connect_database

logger = logging.getLogger("aegis.lab72.monitor")


def format_bytes(value: int | float | None) -> str:
    if not value:
        return "0 B"
    sizes = ("B", "KB", "MB", "GB", "TB")
    scaled = float(value)
    unit = 0
    while scaled >= 1024 and unit < len(sizes) - 1:
        scaled /= 1024
        unit += 1
    return f"{scaled:.2f} {sizes[unit]}"


def fetch_upstream_reports(client: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = client is None
    http = client or httpx.Client(timeout=5)
    try:
        response = http.get(
            settings.github_tree_url,
            headers={"User-Agent": "AEGIS-CTI-WorkingSet-Monitor/1.0"},
        )
        if response.status_code != 200:
            return {"error": f"GitHub API HTTP {response.status_code}"}
        parsed = response.json()
        items = parsed.get("tree")
        if not isinstance(items, list):
            return {"error": "GitHub tree response did not include a tree array"}
        files = []
        for item in items:
            path = item.get("path")
            if not isinstance(path, str) or not path.lower().endswith(".pdf"):
                continue
            if "Annual Security Reports" not in path:
                continue
            filename = path.rsplit("/", 1)[-1]
            year_match = re.search(r"20\d\d", path)
            files.append({
                "_id": re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE),
                "path": path,
                "filename": filename,
                "year": int(year_match.group(0)) if year_match else None,
                "size": item.get("size"),
            })
        return {"files": files}
    except (httpx.HTTPError, ValueError) as error:
        return {"error": str(error)}
    finally:
        if owns_client:
            http.close()


def sample_monitor_state(database, previous: dict[str, int] | None) -> tuple[dict[str, Any], dict[str, int] | None]:
    current_status = database.client.admin.command({"serverStatus": 1})
    cache = (current_status.get("wiredTiger") or {}).get("cache")
    if not cache:
        return {"cache_available": False}, previous
    current = {
        "pagesRequested": cache.get("pages requested from the cache", 0),
        "pagesRead": cache.get("pages read into cache", 0),
        "pagesEvictedApp": cache.get("eviction pages evicted by application threads", 0),
        "bytesInCache": cache.get("bytes currently in the cache", 0),
        "maxBytes": cache.get("maximum bytes configured") or 1,
        "dirtyBytes": cache.get("tracked dirty bytes in the cache", 0),
    }
    hit_ratio = 1.0
    delta_requested = delta_read = 0
    if previous:
        delta_requested = current["pagesRequested"] - previous["pagesRequested"]
        delta_read = current["pagesRead"] - previous["pagesRead"]
        if delta_requested > 0:
            hit_ratio = max(0, 1 - delta_read / delta_requested)
    return {
        "cache_available": True,
        "metrics": current,
        "hit_ratio": hit_ratio,
        "delta_requested": delta_requested,
        "delta_read": delta_read,
    }, current


def monitor_once(database, previous: dict[str, int] | None, client: httpx.Client | None = None) -> tuple[dict[str, Any], dict[str, int] | None]:
    cache_result, current = sample_monitor_state(database, previous)
    reports = list(database["reports"].find({}, {"_id": 1, "title": 1}))
    upstream = fetch_upstream_reports(client)
    if "files" in upstream:
        existing_ids = {report["_id"] for report in reports}
        pending = [item for item in upstream["files"] if item["_id"] not in existing_ids]
        report_summary = {
            "upstream_total": len(upstream["files"]),
            "in_database": len(reports),
            "pending": len(pending),
        }
    else:
        report_summary = {"in_database": len(reports), "error": upstream["error"]}
    return {**cache_result, "reports": report_summary}, current


def run_monitor(max_iterations: int | None = None) -> None:
    database = connect_database()
    previous = None
    iteration = 0
    try:
        logger.info("Connected to MongoDB database %s", settings.mongo_database)
        while True:
            iteration += 1
            try:
                snapshot, previous = monitor_once(database, previous)
                if not snapshot["cache_available"]:
                    logger.warning("WiredTiger cache metrics are not available.")
                else:
                    metrics = snapshot["metrics"]
                    cache_pct = metrics["bytesInCache"] / max(metrics["maxBytes"], 1) * 100
                    logger.info(
                        "Sample %d: hit ratio %.2f%%; cache %s/%s (%.2f%%); dirty %s; app evictions %s; report scan %s",
                        iteration,
                        snapshot["hit_ratio"] * 100,
                        format_bytes(metrics["bytesInCache"]),
                        format_bytes(metrics["maxBytes"]),
                        cache_pct,
                        format_bytes(metrics["dirtyBytes"]),
                        metrics["pagesEvictedApp"],
                        snapshot["reports"],
                    )
            except Exception:
                logger.exception("Monitor iteration %d failed", iteration)
            if max_iterations and iteration >= max_iterations:
                break
            time.sleep(10)
    finally:
        close_database()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int)
    args = parser.parse_args()
    run_monitor(args.iterations)


if __name__ == "__main__":
    main()
