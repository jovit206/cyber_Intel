from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from pymongo import UpdateOne
from pymongo.database import Database

from backend.collectors.misp_feeds import _get_json
from backend.database import close_database, connect_database

logger = logging.getLogger("aegis.galaxies")
GALAXY_TYPES = ("threat-actor", "malpedia", "tool")
GALAXY_ROOT = "https://raw.githubusercontent.com/MISP/misp-galaxy/main/clusters/"
BATCH_SIZE = 500


def _aliases(meta: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in ("aliases", "synonyms"):
        value = meta.get(key)
        if isinstance(value, str):
            aliases.append(value)
        elif isinstance(value, list):
            aliases.extend(item for item in value if isinstance(item, str) and item)
    return list(dict.fromkeys(aliases))


def normalize_galaxy(payload: Any, galaxy_type: str, source_url: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
        raise ValueError(f"Invalid MISP Galaxy document: {source_url}")
    clusters = []
    for cluster in payload["values"]:
        if not isinstance(cluster, dict):
            continue
        cluster_uuid = cluster.get("uuid")
        name = cluster.get("value")
        if not isinstance(cluster_uuid, str) or not cluster_uuid or not isinstance(name, str) or not name:
            continue
        meta = cluster.get("meta") if isinstance(cluster.get("meta"), dict) else {}
        clusters.append({
            "_id": f"{galaxy_type}:{cluster_uuid}",
            "source": "misp-galaxy",
            "source_url": source_url,
            "galaxy_type": galaxy_type,
            "cluster_uuid": cluster_uuid,
            "name": name,
            "type": galaxy_type,
            "description": cluster.get("description"),
            "aliases": _aliases(meta),
            "country": meta.get("country"),
            "meta": meta,
        })
    return clusters


def sync_galaxies(
    database: Database,
    *,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    owns_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
    total = 0
    fetched = 0
    now = datetime.now(timezone.utc)
    try:
        operations = []
        for galaxy_type in GALAXY_TYPES:
            source_url = urljoin(GALAXY_ROOT, f"{galaxy_type}.json")
            logger.info("[Galaxy] Fetching %s", source_url)
            payload = _get_json(http, source_url)
            clusters = normalize_galaxy(payload, galaxy_type, source_url)
            fetched += 1
            total += len(clusters)
            operations.extend(
                UpdateOne(
                    {"_id": cluster["_id"]},
                    {
                        "$set": {
                            key: value for key, value in cluster.items()
                            if key != "_id"
                        } | {"synced_at": now},
                        "$setOnInsert": {"_id": cluster["_id"]},
                    },
                    upsert=True,
                )
                for cluster in clusters
            )
            logger.info("[Galaxy] Parsed %d clusters from %s", len(clusters), galaxy_type)

        collection = database["threats"]
        for offset in range(0, len(operations), BATCH_SIZE):
            collection.bulk_write(operations[offset:offset + BATCH_SIZE], ordered=False)
        database["cti_sync_state"].update_one(
            {"_id": "misp-galaxy"},
            {"$set": {
                "source_feed": GALAXY_ROOT,
                "last_successful_sync": now,
                "last_fetched_file_count": fetched,
                "last_cluster_count": total,
                "last_error": None,
            }},
            upsert=True,
        )
        logger.info("[Galaxy] Sync complete: %d clusters across %d files", total, fetched)
        return {"files": fetched, "clusters": total}
    except Exception as error:
        database["cti_sync_state"].update_one(
            {"_id": "misp-galaxy"},
            {"$set": {
                "source_feed": GALAXY_ROOT,
                "last_attempt": datetime.now(timezone.utc),
                "last_error": str(error),
            }},
            upsert=True,
        )
        raise
    finally:
        if owns_client:
            http.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database = connect_database()
    try:
        sync_galaxies(database)
    finally:
        close_database()


if __name__ == "__main__":
    main()
