from dataclasses import dataclass
import os
import re
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _poll_interval(name: str, fallback: int) -> int:
    raw = os.getenv(name, str(fallback)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer number of seconds") from error
    if value < 60:
        return 60
    return value


@dataclass(frozen=True)
class Settings:
    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_database: str = os.getenv("MONGO_DATABASE", "chapter07")
    misp_api_url: str = os.getenv("MISP_API_URL", "").strip()
    misp_api_key: str = os.getenv("MISP_API_KEY", "").strip()
    virustotal_api_key: str = os.getenv("VIRUSTOTAL_API_KEY", "").strip()
    malwarebazaar_auth_key: str = os.getenv("MALWAREBAZAAR_AUTH_KEY", "").strip()
    misp_initial_lookback: str = os.getenv("MISP_INITIAL_LOOKBACK", "7d").strip()
    misp_poll_interval_seconds: int = _poll_interval("MISP_POLL_INTERVAL_SECONDS", 300)
    virustotal_poll_interval_seconds: int = _poll_interval("VIRUSTOTAL_POLL_INTERVAL_SECONDS", 900)
    malwarebazaar_poll_interval_seconds: int = _poll_interval("MALWAREBAZAAR_POLL_INTERVAL_SECONDS", 300)
    virustotal_query_misp_indicators: bool = os.getenv(
        "VIRUSTOTAL_QUERY_MISP_INDICATORS", "false"
    ).strip().lower() == "true"
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").strip()
    frontend_api_url: str = os.getenv("FRONTEND_API_URL", "").strip()
    sync_startup_max_events: int = int(os.getenv("SYNC_STARTUP_MAX_EVENTS", "25") or 25)
    sync_startup_max_reports: int = int(os.getenv("SYNC_STARTUP_MAX_REPORTS", "2") or 2)
    github_tree_url: str = os.getenv(
        "GITHUB_REPORT_TREE_URL",
        "https://api.github.com/repos/jacobdjwilson/awesome-annual-security-reports/git/trees/main?recursive=1",
    ).strip()


settings = Settings()


def virustotal_key_status(value: str | None = None) -> str:
    """Classify the configured VirusTotal key without ever returning or logging it.

    "missing"            - not set
    "unexpected_format"  - set, but not 64 hexadecimal characters (VirusTotal keys are)
    "ok"                 - has the expected shape (says nothing about validity)
    """
    key = settings.virustotal_api_key if value is None else value
    if not key:
        return "missing"
    return "ok" if re.fullmatch(r"[0-9a-fA-F]{64}", key) else "unexpected_format"


def virustotal_key_diagnosis(value: str | None = None) -> dict[str, object]:
    """Structural facts about the configured key. Never includes the key or any of its characters."""
    key = settings.virustotal_api_key if value is None else value
    info: dict[str, object] = {
        "status": virustotal_key_status(key),
        "length": len(key or ""),
        "has_whitespace": bool(re.search(r"\s", key or "")),
        "has_quotes": bool(re.search(r"[\"']", key or "")),
        "contains_64_hex": False,
        "extra_before": 0,
        "extra_after": 0,
    }
    match = re.search(r"[0-9a-fA-F]{64}", key or "")
    if match and info["status"] == "unexpected_format":
        info["contains_64_hex"] = True
        info["extra_before"] = match.start()
        info["extra_after"] = len(key) - match.end()
    return info


def startup_report() -> list[tuple[str, str]]:
    """Human-readable configuration findings as (level, message). Never contains secret values."""
    findings: list[tuple[str, str]] = []
    uri = settings.mongo_uri
    hosts = re.sub(r"^mongodb(\+srv)?://([^@/]*@)?", "", uri).split("/")[0].split("?")[0]
    in_container = os.path.exists("/.dockerenv")
    findings.append(("info", f"MongoDB hosts: {hosts} | database: {settings.mongo_database}"))
    if in_container and re.search(r"(^|,)(localhost|127\.0\.0\.1)(:|,|$)", hosts):
        findings.append(("warning",
                         "MONGO_URI points at localhost, which is this container, not MongoDB. "
                         "Use the compose service names (mongo1,mongo2,mongo3) or leave MONGO_URI to docker-compose.yml."))
    if "replicaSet=" not in uri and "," in hosts:
        findings.append(("warning", "MONGO_URI lists several hosts but has no replicaSet=... option."))
    key = virustotal_key_status()
    if key == "missing":
        findings.append(("info", "VirusTotal: no API key configured; enrichment is disabled (public feeds still work)."))
    elif key == "unexpected_format":
        findings.append(("warning", "VirusTotal: VIRUSTOTAL_API_KEY is not 64 hexadecimal characters and will be rejected. "
                                    "Remove stray quotes, spaces or prefixes in the backend .env file."))
    else:
        findings.append(("info", "VirusTotal: API key configured (value not logged)."))
    return findings
