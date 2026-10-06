from dataclasses import dataclass
import os
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
