from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def as_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def normalize_timestamp(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value if value < 100_000_000_000 else value / 1000
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        else:
            text = str(value).strip()
            if len(text) == 19 and text[10] == " ":
                text = f"{text.replace(' ', 'T', 1)}+00:00"
            elif len(text) == 19 and "T" in text:
                text = f"{text}+00:00"
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def normalize_indicator(indicator_type: Any, value: Any) -> str | None:
    if not indicator_type or value is None:
        return None
    kind = str(indicator_type).lower()
    indicator = str(value).strip()
    if kind == "url":
        try:
            parts = urlsplit(indicator)
            hostname = parts.hostname.lower() if parts.hostname else ""
            netloc = hostname
            if parts.port:
                netloc = f"{hostname}:{parts.port}"
            if parts.username:
                netloc = f"{parts.username}@{netloc}"
            return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment))
        except ValueError:
            return indicator
    if "ip" in kind or kind in {"domain", "hostname", "md5", "sha1", "sha256"}:
        return indicator.lower()
    return indicator


def deterministic_source_id(
    source: str,
    source_id: Any,
    indicator_type: Any = None,
    indicator: Any = None,
) -> str | None:
    if source_id is not None and source_id != "":
        return str(source_id)
    if not indicator or not indicator_type:
        return None
    normalized = normalize_indicator(indicator_type, indicator) or str(indicator)
    raw = f"{source}\n{indicator_type}\n{normalized}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(
        item if isinstance(item, str) else item.get("name")
        for item in values
        if isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("name"), str))
    ))


def normalize_misp_events(payload: Any) -> list[dict[str, Any]]:
    response = payload.get("response") if isinstance(payload, dict) else None
    rows = response if isinstance(response, list) else [response] if isinstance(response, dict) else (
        payload if isinstance(payload, list) else []
    )
    records: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        event = row.get("Event", row)
        if not isinstance(event, dict):
            continue
        attributes = event.get("Attribute")
        attributes = attributes if isinstance(attributes, list) else []
        tags = _string_list(event.get("Tag"))
        event_id = as_string(_first_present(event.get("id"), event.get("uuid")))
        event_time = normalize_timestamp(event.get("timestamp"))
        event_base = {
            "source": "misp",
            "event_id": event_id,
            "info": as_string(event.get("info")),
            "description": None,
            "date": as_string(event.get("date")),
            "timestamp": event_time,
            "first_seen": None,
            "last_seen": None,
            "threat_level": as_string(event.get("threat_level_id")),
            "published": event.get("published") if isinstance(event.get("published"), bool) else None,
            "tags": tags,
            "references": [],
            "source_reference": as_string(event.get("url")),
            "publisher": as_string((event.get("Orgc") or {}).get("name")),
            "verdict": None,
            "severity": None,
            "confidence": None,
        }

        if not attributes:
            source_id = deterministic_source_id("misp", event_id)
            if source_id:
                raw_event = {key: value for key, value in event.items() if key != "Attribute"}
                records.append({
                    **event_base,
                    "source_id": source_id,
                    "attribute_id": None,
                    "indicator": None,
                    "normalized_indicator": None,
                    "indicator_type": None,
                    "category": None,
                    "value": None,
                    "comment": None,
                    "raw_source": {"event": raw_event},
                })
            continue

        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            value = as_string(attribute.get("value"))
            indicator_type = as_string(attribute.get("type"))
            source_id = deterministic_source_id(
                "misp",
                _first_present(
                    attribute.get("uuid"),
                    attribute.get("id"),
                    f"{event_id}:{indicator_type}:{value}"
                    if event_id and indicator_type and value else None,
                ),
                indicator_type,
                value,
            )
            if not source_id:
                continue
            safe_attribute = {key: item for key, item in attribute.items() if key != "data"}
            records.append({
                **event_base,
                "source_id": source_id,
                "attribute_id": as_string(attribute.get("id")),
                "indicator": value,
                "normalized_indicator": normalize_indicator(indicator_type, value),
                "indicator_type": indicator_type,
                "category": as_string(attribute.get("category")),
                "value": value,
                "comment": as_string(attribute.get("comment")),
                "timestamp": normalize_timestamp(_first_present(attribute.get("timestamp"), event.get("timestamp"))),
                "first_seen": normalize_timestamp(attribute.get("first_seen")),
                "last_seen": normalize_timestamp(attribute.get("last_seen")),
                "tags": list(dict.fromkeys(tags + _string_list(attribute.get("Tag")))),
                "references": attribute.get("references") if isinstance(attribute.get("references"), list) else [],
                "source_reference": as_string(attribute.get("url")) or event_base["source_reference"],
                "raw_source": {
                    "event": {
                        key: event.get(key)
                        for key in ("id", "uuid", "info", "date", "timestamp", "threat_level_id", "published", "Tag")
                        if key in event
                    },
                    "attribute": safe_attribute,
                },
            })
    return records


def normalize_malwarebazaar_records(payload: Any) -> list[dict[str, Any]]:
    samples = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(samples, list):
        return []
    records = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        hash_field = next(
            ((kind, field) for kind, field in (
                ("sha256", "sha256_hash"),
                ("sha1", "sha1_hash"),
                ("md5", "md5_hash"),
            ) if sample.get(field)),
            None,
        )
        if not hash_field:
            continue
        indicator_type, field = hash_field
        indicator = str(sample[field])
        source_id = deterministic_source_id(
            "malwarebazaar",
            _first_present(sample.get("sha256_hash"), sample.get("sha1_hash"), sample.get("md5_hash")),
            indicator_type,
            indicator,
        )
        if not source_id:
            continue
        records.append({
            "source": "malwarebazaar",
            "source_id": source_id,
            "info": as_string(sample.get("signature")),
            "publisher": None,
            "event_id": None,
            "attribute_id": None,
            "indicator": indicator,
            "normalized_indicator": normalize_indicator(indicator_type, indicator),
            "indicator_type": indicator_type,
            "category": as_string(sample.get("file_type")),
            "value": indicator,
            "timestamp": normalize_timestamp(sample.get("timestamp")),
            "first_seen": normalize_timestamp(sample.get("first_seen")),
            "last_seen": normalize_timestamp(sample.get("last_seen")),
            "threat_type": as_string(sample.get("signature")),
            "severity": None,
            "confidence": None,
            "description": None,
            "threat_level": None,
            "published": None,
            "tags": _string_list(sample.get("tags")),
            "comment": None,
            "references": sample.get("references") if isinstance(sample.get("references"), list) else [],
            "source_reference": as_string(sample.get("permalink")),
            "file_name": as_string(sample.get("file_name")),
            "file_type": as_string(sample.get("file_type")),
            "delivery_method": as_string(sample.get("delivery_method")),
            "reporter": as_string(sample.get("reporter")),
            "raw_source": sample,
        })
    return records


def normalize_virustotal_report(payload: Any, candidate: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    stats = attributes.get("last_analysis_stats")
    if not isinstance(stats, dict):
        stats = {}
    indicator = as_string(candidate.get("indicator"))
    indicator_type = as_string(candidate.get("indicator_type"))
    source_id = deterministic_source_id(
        "virustotal", data.get("id"), indicator_type, indicator
    )
    if not source_id or not indicator or not indicator_type:
        return None
    last_analysis = normalize_timestamp(attributes.get("last_analysis_date"))
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    counts = {
        f"{key}_count": stats.get(key) if isinstance(stats.get(key), int) else None
        for key in ("malicious", "suspicious", "harmless", "undetected")
    }
    return {
        "source": "virustotal",
        "source_id": source_id,
        "info": as_string(attributes.get("meaningful_name")),
        "publisher": None,
        "event_id": None,
        "attribute_id": None,
        "indicator": indicator,
        "normalized_indicator": normalize_indicator(indicator_type, indicator),
        "indicator_type": indicator_type,
        "category": None,
        "value": indicator,
        "timestamp": last_analysis,
        "first_seen": None,
        "last_seen": None,
        "analysis_timestamp": last_analysis,
        "last_analysis_date": last_analysis,
        "reputation": attributes.get("reputation") if isinstance(attributes.get("reputation"), int) else None,
        **counts,
        "categories": attributes.get("categories"),
        "threat_type": None,
        "severity": None,
        "confidence": None,
        "description": None,
        "threat_level": None,
        "published": None,
        "tags": _string_list(attributes.get("tags")),
        "comment": None,
        "references": [links["self"]] if isinstance(links.get("self"), str) else [],
        "source_reference": as_string(links.get("self")),
        "raw_source": data,
    }


def virustotal_endpoint(indicator_type: str, indicator: str) -> str | None:
    kind = indicator_type.lower()
    safe_indicator = indicator.replace("/", "%2F")
    if kind in {"sha256", "sha1", "md5"}:
        return f"https://www.virustotal.com/api/v3/files/{safe_indicator}"
    if kind in {"ip", "ip-src", "ip-dst", "ipv4", "ipv6"}:
        return f"https://www.virustotal.com/api/v3/ip_addresses/{safe_indicator}"
    if kind in {"domain", "hostname"}:
        return f"https://www.virustotal.com/api/v3/domains/{safe_indicator}"
    if kind == "url":
        url_id = base64.urlsafe_b64encode(indicator.encode("utf-8")).decode("ascii").rstrip("=")
        return f"https://www.virustotal.com/api/v3/urls/{url_id}"
    return None
