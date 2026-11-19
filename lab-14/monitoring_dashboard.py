"""Periodic MongoDB health and replica-set monitoring."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from pymongo import MongoClient
from pymongo.errors import OperationFailure

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "logs" / "metrics.log"
DEFAULT_INTERVAL_SECONDS = 30
CACHE_HIT_THRESHOLD_PERCENT = 80.0
REPLICATION_LAG_THRESHOLD_SECONDS = 10.0
STANDALONE_ERROR_CODES = {76, 94, 13436}


@dataclass(frozen=True)
class Alert:
    metric: str
    current_value: float
    threshold: float
    timestamp: str


def configure_logging(log_path: Path = LOG_PATH) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("chapter14.monitoring")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    resolved_log_path = log_path.resolve()
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved_log_path:
            return logger
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def _number(mapping: dict[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def retrieve_server_metrics(client: MongoClient) -> dict[str, Any]:
    """Fetch serverStatus once and normalize optional storage-engine fields."""
    status = client.admin.command("serverStatus")
    connections = status.get("connections") or {}
    wired_tiger = (status.get("wiredTiger") or {}).get("cache") or {}
    requested_pages = _number(wired_tiger, "pages requested from the cache")
    read_pages = _number(wired_tiger, "pages read into cache")
    hit_ratio = None
    if requested_pages and requested_pages > 0 and read_pages is not None:
        hit_ratio = max(0.0, min(100.0, (requested_pages - read_pages) / requested_pages * 100))

    return {
        "connections_current": _number(connections, "current"),
        "connections_available": _number(connections, "available"),
        "opcounters": status.get("opcounters") or {},
        "cache_bytes_current": _number(wired_tiger, "bytes currently in the cache"),
        "cache_bytes_read": _number(wired_tiger, "bytes read into cache"),
        "cache_hit_ratio_percent": hit_ratio,
    }


def _optime_datetime(member: dict[str, Any]) -> datetime | None:
    value = member.get("optimeDate")
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    optime = member.get("optime")
    timestamp = optime.get("ts") if isinstance(optime, dict) else None
    seconds = getattr(timestamp, "time", None)
    if isinstance(seconds, int):
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    return None


def retrieve_replica_status(client: MongoClient) -> dict[str, Any]:
    try:
        result = client.admin.command("replSetGetStatus")
    except OperationFailure as error:
        message = str(error).lower()
        if error.code in STANDALONE_ERROR_CODES or "not running with --replset" in message:
            return {"applicable": False, "primary": None, "members": [], "lags": []}
        raise

    members = result.get("members") or []
    primary = next((member for member in members if member.get("stateStr") == "PRIMARY"), None)
    lags: list[dict[str, Any]] = []
    primary_time = _optime_datetime(primary) if primary else None
    if primary_time:
        for member in members:
            if member.get("stateStr") != "SECONDARY":
                continue
            member_time = _optime_datetime(member)
            if member_time:
                lags.append({
                    "member": member.get("name", "unknown"),
                    "lag_seconds": max(0.0, (primary_time - member_time).total_seconds()),
                })
    return {
        "applicable": True,
        "primary": primary.get("name") if primary else None,
        "members": members,
        "lags": lags,
    }


def build_alerts(metrics: dict[str, Any], replica_status: dict[str, Any]) -> list[Alert]:
    now = datetime.now(timezone.utc).isoformat()
    alerts: list[Alert] = []
    hit_ratio = metrics.get("cache_hit_ratio_percent")
    if hit_ratio is not None and hit_ratio < CACHE_HIT_THRESHOLD_PERCENT:
        alerts.append(Alert("cache_hit_ratio_percent", hit_ratio, CACHE_HIT_THRESHOLD_PERCENT, now))
    for secondary in replica_status.get("lags", []):
        lag = secondary["lag_seconds"]
        if lag > REPLICATION_LAG_THRESHOLD_SECONDS:
            alerts.append(Alert(
                f"replication_lag_seconds:{secondary['member']}",
                lag,
                REPLICATION_LAG_THRESHOLD_SECONDS,
                now,
            ))
    return alerts


def format_metrics_table(metrics: dict[str, Any], replica_status: dict[str, Any]) -> str:
    opcounters = metrics.get("opcounters") or {}
    rows = [
        ("Connections (current)", metrics.get("connections_current")),
        ("Connections (available)", metrics.get("connections_available")),
        ("Opcounters", ", ".join(f"{key}={value}" for key, value in sorted(opcounters.items())) or "unavailable"),
        ("WiredTiger bytes currently in cache", metrics.get("cache_bytes_current")),
        ("WiredTiger bytes read into cache", metrics.get("cache_bytes_read")),
        ("Cache hit ratio", (
            f"{metrics['cache_hit_ratio_percent']:.2f}%"
            if metrics.get("cache_hit_ratio_percent") is not None else "unavailable"
        )),
    ]
    if not replica_status.get("applicable", False):
        rows.append(("Replica set", "not applicable (standalone MongoDB)"))
    else:
        rows.append(("Replica set primary", replica_status.get("primary") or "not elected"))
        for lag in replica_status.get("lags", []):
            rows.append((f"Replication lag ({lag['member']})", f"{lag['lag_seconds']:.2f} seconds"))
        if not replica_status.get("lags"):
            rows.append(("Replication lag", "unavailable"))

    width = max(len("Metric"), *(len(str(label)) for label, _ in rows))
    value_width = max(len("Value"), *(len(str(value)) for _, value in rows))
    border = f"+-{'-' * width}-+-{'-' * value_width}-+"
    lines = [border, f"| {'Metric':<{width}} | {'Value':<{value_width}} |", border]
    lines.extend(f"| {str(label):<{width}} | {str(value):<{value_width}} |" for label, value in rows)
    lines.append(border)
    return "\n".join(lines)


class WebhookAlerter:
    def __init__(
        self,
        webhook_url: str | None,
        *,
        platform: str = "slack",
        cooldown_seconds: float = 300,
        logger: logging.Logger | None = None,
    ) -> None:
        if platform not in {"slack", "discord"}:
            raise ValueError("Webhook platform must be 'slack' or 'discord'.")
        self.webhook_url = webhook_url
        self.platform = platform
        self.cooldown_seconds = cooldown_seconds
        self.logger = logger or logging.getLogger("chapter14.monitoring")
        self._last_attempt: dict[str, float] = {}

    def send(self, alert: Alert) -> bool:
        if not self.webhook_url:
            return False
        now = time.monotonic()
        previous = self._last_attempt.get(alert.metric)
        if previous is not None and now - previous < self.cooldown_seconds:
            return False
        self._last_attempt[alert.metric] = now
        message = (
            f"MongoDB alert: {alert.metric} is {alert.current_value:.2f}; "
            f"threshold is {alert.threshold:.2f}; timestamp {alert.timestamp}."
        )
        payload = {"text": message} if self.platform == "slack" else {"content": message}
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
        except requests.Timeout:
            self.logger.error("Webhook alert timed out for metric %s.", alert.metric)
            return False
        except requests.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            self.logger.error("Webhook returned HTTP %s for metric %s.", status_code, alert.metric)
            return False
        except requests.RequestException as error:
            self.logger.error("Webhook request failed (%s) for metric %s.", type(error).__name__, alert.metric)
            return False
        self.logger.info("Webhook alert sent for metric %s.", alert.metric)
        return True


def monitor_once(client: MongoClient, alerter: WebhookAlerter, logger: logging.Logger) -> str:
    metrics = retrieve_server_metrics(client)
    replica_status = retrieve_replica_status(client)
    table = format_metrics_table(metrics, replica_status)
    logger.info("\n%s", table)
    for alert in build_alerts(metrics, replica_status):
        logger.warning(
            "%s is %.2f (threshold %.2f).",
            alert.metric,
            alert.current_value,
            alert.threshold,
        )
        alerter.send(alert)
    return table


def run_monitoring(
    client: MongoClient,
    alerter: WebhookAlerter,
    logger: logging.Logger,
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("Monitoring interval must be greater than zero.")
    while True:
        monitor_once(client, alerter, logger)
        time.sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Collect one snapshot and exit.")
    args = parser.parse_args(argv)
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        parser.error("Set MONGO_URI in the environment before running the dashboard.")
    logger = configure_logging()
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    alerter = WebhookAlerter(
        os.getenv("MONGODB_ALERT_WEBHOOK_URL"),
        platform=os.getenv("MONGODB_ALERT_WEBHOOK_PLATFORM", "slack").lower(),
        logger=logger,
    )
    try:
        client.admin.command("ping")
        if args.once:
            monitor_once(client, alerter, logger)
        else:
            logger.info("Monitoring started; collecting a snapshot every %s seconds.", DEFAULT_INTERVAL_SECONDS)
            run_monitoring(client, alerter, logger)
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user.")
    except Exception as error:
        logger.error("Monitoring stopped after %s.", type(error).__name__)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
