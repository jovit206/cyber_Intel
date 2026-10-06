from __future__ import annotations

import argparse
import hashlib
import logging
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from pymongo import UpdateOne
from pymongo.database import Database

from backend.collectors.misp_feeds import _get_json
from backend.config import settings
from backend.database import close_database, connect_database

logger = logging.getLogger("aegis.reports")
REPORT_REPOSITORY = "jacobdjwilson/awesome-annual-security-reports"
REPORT_TREE_URL = settings.github_tree_url
REPORT_ROOT = f"https://raw.githubusercontent.com/{REPORT_REPOSITORY}/main/"
MAX_PASSAGE_CHARS = 1800
COMMON_NAME_BLOCKLIST = {
    "action", "explorer", "do not", "tool", "malpedia", "unknown", "other",
    "none", "null", "generic", "windows", "linux", "android", "office",
    "global", "august", "leverage", "inc", "net", "takedown", "ransomware",
}


def _report_identity(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def _vendor_from_filename(filename: str) -> str | None:
    stem = Path(filename).stem
    match = re.match(r"([A-Za-z][A-Za-z0-9.]*)[-_]", stem)
    return match.group(1) if match else None


def _chunks(markdown: str) -> list[str]:
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", markdown)]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if not paragraph:
            continue
        while len(paragraph) > MAX_PASSAGE_CHARS:
            prefix = paragraph[:MAX_PASSAGE_CHARS]
            boundary = prefix.rfind(" ")
            if boundary > MAX_PASSAGE_CHARS // 2:
                prefix = prefix[:boundary]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(prefix)
            paragraph = paragraph[len(prefix):].strip()
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) > MAX_PASSAGE_CHARS and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _extractor(threats: list[dict]) -> tuple[re.Pattern[str] | None, dict[str, str], dict[str, str]]:
    alias_to_name: dict[str, str] = {}
    threat_ids: dict[str, str] = {}
    for threat in threats:
        name = threat.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        values = [name, *(threat.get("aliases") or [])]
        for value in values:
            if not isinstance(value, str):
                continue
            alias = value.strip()
            words = alias.split()
            if not alias or alias.casefold() in COMMON_NAME_BLOCKLIST:
                continue
            if len(words) > 1 and not all(
                word[0].isupper() for word in words if word and word[0].isalpha()
            ):
                continue
            if len(words) == 1 and not alias[0].isupper():
                continue
            alias_to_name[alias] = name
            threat_ids[name] = threat["_id"]
    if not alias_to_name:
        return None, {}, {}
    choices = sorted(alias_to_name, key=len, reverse=True)
    expression = re.compile(
        r"(?<![\w])(?:" + "|".join(re.escape(alias) for alias in choices) + r")(?![\w])"
    )
    return expression, alias_to_name, threat_ids


def _report_entities(
    text: str,
    expression: re.Pattern[str] | None,
    alias_to_name: dict[str, str],
    threat_ids: dict[str, str],
) -> tuple[list[str], list[str]]:
    if expression is None:
        return [], []
    names = list(dict.fromkeys(
        alias_to_name[match.group(0)]
        for match in expression.finditer(text)
        if match.group(0) in alias_to_name
    ))
    return names, [threat_ids[name] for name in names if name in threat_ids]


def _pdf_items(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
        raise RuntimeError("GitHub report-tree response did not include a tree array")
    if payload.get("truncated"):
        raise RuntimeError("GitHub report-tree response was truncated; refusing an incomplete report sync")
    items = [
        item for item in payload["tree"]
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"].lower().endswith(".pdf")
        and "annual security reports" in item["path"].lower()
    ]
    return sorted(items, key=lambda item: item["path"].casefold())


def _download_pdf(client: httpx.Client, url: str, destination: Path) -> int:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with client.stream("GET", url) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 3:
                        delay = min(30, 2 ** attempt)
                        logger.warning("Retrying report download after HTTP %d in %d seconds", response.status_code, delay)
                        import time

                        time.sleep(delay)
                        continue
                response.raise_for_status()
                size = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
                        size += len(chunk)
                return size
        except (httpx.TimeoutException, httpx.RequestError) as error:
            last_error = error
            if attempt == 3:
                break
            import time

            time.sleep(min(30, 2 ** attempt))
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"Report download returned HTTP {error.response.status_code}: {url}") from error
    raise RuntimeError(f"Report download failed after 4 attempts: {url}") from last_error


def _convert_pdf(pdf_path: Path) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError as error:
        raise RuntimeError(
            "PDF conversion requires MarkItDown; install dependencies from requirements.txt"
        ) from error
    converted = MarkItDown().convert(str(pdf_path))
    text = converted.text_content
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"MarkItDown did not extract text from {pdf_path.name}")
    return text


def sync_reports(
    database: Database,
    *,
    max_reports: int | None = None,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    if max_reports is not None and max_reports < 1:
        raise ValueError("max_reports must be at least 1")
    owns_client = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": "AEGIS-CTI-Report-Sync/1.0"},
    )
    now = datetime.now(timezone.utc)
    imported = skipped = 0
    try:
        items = _pdf_items(_get_json(http, REPORT_TREE_URL))
        items.sort(
            key=lambda item: (
                int(re.search(r"20\d{2}", item["path"]).group(0))
                if re.search(r"20\d{2}", item["path"]) else 0,
                item["path"].casefold(),
            ),
            reverse=True,
        )
        if max_reports is not None:
            items = items[:max_reports]
        threats = list(database["threats"].find(
            {"source": "misp-galaxy"},
            {"_id": 1, "name": 1, "aliases": 1},
        ))
        expression, alias_to_name, threat_ids = _extractor(threats)
        extracted_frequency: Counter[str] = Counter()
        for index, item in enumerate(items, start=1):
            path = item["path"]
            report_id = f"report:{_report_identity(path)}"
            source_sha = item.get("sha")
            existing = database["reports"].find_one(
                {"_id": report_id},
                {"source_sha": 1, "conversion_status": 1},
            )
            if existing and source_sha and existing.get("source_sha") == source_sha and existing.get("conversion_status") == "complete":
                skipped += 1
                logger.info("[Reports] Reusing unchanged report %d/%d: %s", index, len(items), path)
                continue

            filename = Path(path).name
            pdf_url = REPORT_ROOT + quote(path, safe="/")
            logger.info("[Reports] Downloading report %d/%d: %s", index, len(items), path)
            try:
                with tempfile.TemporaryDirectory(prefix="aegis-report-") as temp_dir:
                    pdf_path = Path(temp_dir) / filename
                    file_size = _download_pdf(http, pdf_url, pdf_path)
                    markdown = _convert_pdf(pdf_path)
            except Exception as error:
                logger.error("[Reports] Failed to download/convert %s: %s. Skipping.", path, error)
                continue

            passage_texts = _chunks(markdown)
            passage_docs = []
            report_entities: set[str] = set()
            for chunk_index, passage_text in enumerate(passage_texts):
                entities, linked_threat_ids = _report_entities(
                    passage_text,
                    expression,
                    alias_to_name,
                    threat_ids,
                )
                report_entities.update(entities)
                extracted_frequency.update(entities)
                passage_docs.append({
                    "_id": f"{report_id}:{chunk_index}",
                    "report_id": report_id,
                    "page": None,
                    "chunk_index": chunk_index,
                    "text": passage_text,
                    "entities": entities,
                    "threat_ids": linked_threat_ids,
                    "source_pdf": pdf_url,
                    "is_current": True,
                })

            if existing:
                database["passages"].update_many(
                    {"report_id": report_id},
                    {"$set": {"is_current": False}},
                )
            if passage_docs:
                database["passages"].bulk_write([
                    UpdateOne(
                        {"_id": passage["_id"]},
                        {
                            "$set": {
                                key: value for key, value in passage.items()
                                if key != "_id"
                            },
                            "$setOnInsert": {"_id": passage["_id"]},
                        },
                        upsert=True,
                    )
                    for passage in passage_docs
                ], ordered=False)

            year_match = re.search(r"20\d{2}", path)
            report_doc = {
                "_id": report_id,
                "source_repository": REPORT_REPOSITORY,
                "repo_path": path,
                "filename": filename,
                "year": int(year_match.group(0)) if year_match else None,
                "vendor": _vendor_from_filename(filename),
                "title": Path(filename).stem,
                "pdf_url": pdf_url,
                "source_sha": source_sha,
                "pdf_bytes": file_size,
                "chunk_count": len(passage_docs),
                "entity_count": len(report_entities),
                "conversion_status": "complete",
                "ingested_at": now,
            }
            database["reports"].update_one(
                {"_id": report_id},
                {
                    "$set": {key: value for key, value in report_doc.items() if key != "_id"},
                    "$setOnInsert": {"_id": report_id},
                },
                upsert=True,
            )
            imported += 1

        result = {"available_reports": len(items), "imported_reports": imported, "unchanged_reports": skipped}
        database["cti_sync_state"].update_one(
            {"_id": "annual-reports"},
            {"$set": {
                "source_feed": f"https://github.com/{REPORT_REPOSITORY}",
                "last_successful_sync": datetime.now(timezone.utc),
                "last_report_count": len(items),
                "last_imported_report_count": imported,
                "last_error": None,
                "frequent_extracted_names": [
                    {"name": name, "passage_count": count}
                    for name, count in extracted_frequency.most_common(25)
                ],
            }},
            upsert=True,
        )
        logger.info("[Reports] Sync complete: %s", result)
        logger.info(
            "[Reports] Review these frequent extracted names for false positives: %s",
            extracted_frequency.most_common(25),
        )
        return result
    except Exception as error:
        database["cti_sync_state"].update_one(
            {"_id": "annual-reports"},
            {"$set": {
                "source_feed": f"https://github.com/{REPORT_REPOSITORY}",
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
    parser = argparse.ArgumentParser(description="Sync PDFs and extracted passages from the public report repository.")
    parser.add_argument("--max-reports", type=int, help="Limit the newest reports for a quick first run.")
    args = parser.parse_args()
    database = connect_database()
    try:
        sync_reports(database, max_reports=args.max_reports)
    finally:
        close_database()


if __name__ == "__main__":
    main()
