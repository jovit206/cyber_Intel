"""Configuration, path policy, secret redaction and the action log for the AEGIS local agent.

Everything here is deliberately conservative:
- the only files whose contents are read are those in approved project folders (exposed-credential scan),
  and system, credential, browser-profile and key folders are always excluded;
- secret-like values are redacted before any result leaves the agent;
- every action is appended to a local JSONL log (paths and outcomes only, never file contents).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path(os.environ.get("USERPROFILE", str(Path.home())))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(HOME / "AppData" / "Local")))
APPDATA = Path(os.environ.get("APPDATA", str(HOME / "AppData" / "Roaming")))
STATE_DIR = Path(os.environ.get("AEGIS_AGENT_HOME", str(LOCALAPPDATA / "AegisLocalAgent")))
CONFIG_FILE = STATE_DIR / "config.json"
TOKEN_FILE = STATE_DIR / "agent_token.txt"
LOG_FILE = STATE_DIR / "logs" / "agent-actions.jsonl"

DEFAULT_CONFIG: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8765,
    # The only folders whose files may be read: approved project folders, for the exposed-credential scan.
    "content_roots": ["G:\\Projects\\nosql-lab-7"],
    "max_jobs": 2,
}

# Always excluded, even inside an approved root. Compared case-insensitively as path prefixes.
SYSTEM_EXCLUSIONS = [
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)", "C:\\ProgramData", "C:\\$Recycle.Bin",
    "C:\\System Volume Information", "C:\\Recovery", "C:\\$WinREAgent", "C:\\Config.Msi", "C:\\Documents and Settings",
    "G:\\$Recycle.Bin", "G:\\System Volume Information",
    str(APPDATA / "Microsoft" / "Credentials"), str(LOCALAPPDATA / "Microsoft" / "Credentials"),
    str(APPDATA / "Microsoft" / "Protect"), str(APPDATA / "Microsoft" / "Crypto"), str(LOCALAPPDATA / "Microsoft" / "Vault"),
    str(APPDATA / "Microsoft" / "SystemCertificates"), str(LOCALAPPDATA / "Packages"),
    str(LOCALAPPDATA / "Google" / "Chrome" / "User Data"), str(LOCALAPPDATA / "Microsoft" / "Edge" / "User Data"),
    str(LOCALAPPDATA / "BraveSoftware"), str(APPDATA / "Mozilla" / "Firefox" / "Profiles"),
    str(APPDATA / "Opera Software"), str(LOCALAPPDATA / "Microsoft" / "WindowsApps"),
    str(HOME / ".ssh"), str(HOME / ".gnupg"), str(HOME / ".aws"), str(HOME / ".azure"), str(HOME / ".kube"),
    str(HOME / ".docker"), str(HOME / ".claude"), str(HOME / ".config" / "gcloud"), str(HOME / "NTUSER.DAT"),
    str(APPDATA / "KeePass"), str(APPDATA / "Bitwarden"), str(APPDATA / "1Password"), str(LOCALAPPDATA / "1Password"),
    str(STATE_DIR),
]
EXCLUDED_NAMES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log", "dumpstack.log.tmp"}
SKIP_DIR_NAMES = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox"}


class PolicyError(Exception):
    """A request outside what the agent is allowed to do."""


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/") + "\\"


def load_config() -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    config = {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
    config.pop("scan_roots", None)  # general scans were removed; the agent is a security scanner only
    config["content_roots"] = [r for r in config["content_roots"] if Path(r).exists()]
    return config


def load_token() -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise SystemExit(f"Agent token in {TOKEN_FILE} is too short; delete the file to generate a new one.")
    return token


class Policy:
    def __init__(self, config: dict[str, Any]):
        self.content_roots = [_norm(r) for r in config["content_roots"]]
        self.exclusions = [_norm(p) for p in SYSTEM_EXCLUSIONS]

    def is_excluded(self, path: str | os.PathLike) -> bool:
        normalized = _norm(path)
        if Path(str(path)).name.lower() in EXCLUDED_NAMES:
            return True
        return any(normalized.startswith(prefix) for prefix in self.exclusions)

    def _within(self, path, roots) -> bool:
        normalized = _norm(path)
        return any(normalized.startswith(root) for root in roots)

    def content_path(self, path: str | None) -> Path:
        """A file or folder whose text content may be read."""
        if not self.content_roots:
            raise PolicyError("No project folders are approved for reading file contents.")
        candidate = Path(path or self.content_roots[0].rstrip("\\")).expanduser()
        if not candidate.exists():
            raise PolicyError(f"{candidate} does not exist.")
        if not self._within(candidate, self.content_roots) or self.is_excluded(candidate):
            raise PolicyError(f"File contents can only be read inside the approved project folders: "
                              f"{', '.join(r.rstrip(chr(92)) for r in self.content_roots)}.")
        return candidate


# --------------------------------------------------------------------------- secret redaction

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "high"),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "high"),
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"), "high"),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "high"),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), "critical"),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "medium"),
    ("Connection string with password", re.compile(r"\b(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis|amqp)://[^\s:/@]+:[^\s@/]+@"), "high"),
    ("Assigned secret", re.compile(r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|AUTH[_-]?KEY)[A-Z0-9_]*)\s*[:=]\s*['\"]?([^\s'\"#]{8,})"), "medium"),
]
_PLACEHOLDER = re.compile(r"(?i)^(?:<.*>|\$\{.*\}|x{4,}|\*{4,}|changeme|your[_-].*|example.*|placeholder.*|replace[_-].*|dummy.*|test.*|none|null|true|false)$")


# Assigned values that are code rather than literal secrets: calls, attribute/index access, operators, variables.
_CODE_EXPRESSION = re.compile(r"[()\[\]{}<>,]|^[A-Za-z_][\w]*\.[A-Za-z_]|^(?:self|request|payload|config|args|data|row|user|"
                              r"credentials|form|body|env|value|item|getattr|hashlib|secrets|base64|str|int)\b")


def mask(value: str) -> str:
    return f"{value[:3]}…[redacted, {len(value)} chars]" if len(value) > 6 else "[redacted]"


def find_secrets(line: str) -> list[tuple[str, str, str]]:
    """(kind, masked value, risk) for each secret-like value in a line; placeholders are ignored."""
    found = []
    for kind, pattern, risk in SECRET_PATTERNS:
        for match in pattern.finditer(line):
            value = match.group(2) if kind == "Assigned secret" else match.group(0)
            if kind == "Assigned secret" and (_PLACEHOLDER.match(value) or value.startswith(("$", "%", "os.", "settings."))
                                              or _CODE_EXPRESSION.search(value)):
                continue
            found.append((kind, mask(value), risk))
    return found


def redact(text: str) -> str:
    """Replace every secret-like value in free text before it leaves the agent."""
    for kind, pattern, _risk in SECRET_PATTERNS:
        if kind == "Assigned secret":
            text = pattern.sub(lambda m: m.group(0).replace(m.group(2), mask(m.group(2)))
                               if not _PLACEHOLDER.match(m.group(2)) else m.group(0), text)
        else:
            text = pattern.sub(lambda m: mask(m.group(0)), text)
    return text


def redact_tree(value: Any) -> Any:
    """Redact every string in a JSON-like result, one value at a time.

    Each regex call holds the GIL until it finishes; short per-value calls keep the agent's HTTP threads responsive
    even when a result is large. Dictionary keys are left unchanged.
    """
    if isinstance(value, str):
        return redact(value) if len(value) >= 8 else value
    if isinstance(value, dict):
        return {key: redact_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_tree(item) for item in value]
    return value


# --------------------------------------------------------------------------- action log

_LOG_LOCK = threading.Lock()


def log_action(event: str, **fields: Any) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
    with _LOG_LOCK, LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
