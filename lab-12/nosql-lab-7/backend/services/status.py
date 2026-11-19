from datetime import datetime, timezone
from threading import Lock
from typing import Any

SOURCE_NAMES = ("misp", "virustotal", "malwarebazaar")
_lock = Lock()
_sources: dict[str, dict[str, Any]] = {
    name: {
        "configured": False,
        "last_successful_fetch": None,
        "records_received": 0,
        "records_inserted": 0,
        "duplicates_skipped": 0,
        "last_error": None,
        "next_scheduled_fetch": None,
    }
    for name in SOURCE_NAMES
}


def set_configured(name: str, configured: bool) -> None:
    with _lock:
        _sources[name]["configured"] = configured


def start_fetch(name: str) -> None:
    with _lock:
        _sources[name]["last_error"] = None


def finish_fetch(name: str, received: int, inserted: int, duplicates: int) -> None:
    with _lock:
        source = _sources[name]
        source.update({
            "last_successful_fetch": datetime.now(timezone.utc).isoformat(),
            "records_received": received,
            "records_inserted": inserted,
            "duplicates_skipped": duplicates,
            "last_error": None,
        })


def fail_fetch(name: str, message: str) -> None:
    with _lock:
        _sources[name]["last_error"] = message


def set_next_fetch(name: str, next_fetch: datetime | None) -> None:
    with _lock:
        _sources[name]["next_scheduled_fetch"] = (
            next_fetch.astimezone(timezone.utc).isoformat() if next_fetch else None
        )


def snapshot() -> dict[str, dict[str, Any]]:
    with _lock:
        return {name: dict(value) for name, value in _sources.items()}
