"""Security-only inspection tools for the AEGIS local agent.

The agent is exclusively a cybersecurity threat scanner. Every tool here is a fixed, read-only security check that
uses Windows security interfaces (Microsoft Defender cmdlets, CIM, event logs, the registry, Authenticode). There is no
general file search, storage analysis, performance monitoring or project inspection, and no tool accepts a command
line. The only action is a Microsoft Defender quick scan, which requires the user's explicit confirmation.

Findings are labelled carefully: "detected" only when Microsoft Defender reports a threat; everything else is
"suspicious" or "review", with the evidence that caused it. Absence of findings is never reported as "safe".
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from policy import APPDATA, HOME, LOCALAPPDATA, PolicyError, find_secrets, redact

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
EXECUTABLE_EXTENSIONS = {".exe", ".scr", ".pif", ".com", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                         ".ps1", ".psm1", ".hta", ".msi", ".jar", ".lnk", ".dll", ".cpl", ".iso", ".img"}
DOCUMENT_DISGUISES = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".txt", ".xls", ".xlsx", ".zip", ".mp4", ".invoice"}
TEXT_EXTENSIONS = {".py", ".js", ".ts", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".properties", ".conf",
                   ".tf", ".tfvars", ".sh", ".ps1", ".example", ".xml", ""}
PUA_HINTS = ("toolbar", "coupon", "optimizer", "driver updater", "driver booster", "pc cleaner", "speedup", "speed up",
             "search protect", "browser assistant", "registry cleaner", "web companion", "mywebsearch", "conduit",
             "ask toolbar", "systweak", "reimage", "segurazo", "bytefence", "pc accelerate", "advanced systemcare")
LOLBIN_PATTERNS = [
    ("PowerShell encoded command", re.compile(r"(?i)powershell(\.exe)?\b.*\s-(e|en|enc|encodedcommand)\s+[A-Za-z0-9+/=]{20,}")),
    ("Hidden PowerShell window", re.compile(r"(?i)powershell(\.exe)?\b.*-w(indowstyle)?\s+hidden")),
    ("Download-and-execute", re.compile(r"(?i)(downloadstring|downloadfile|invoke-webrequest|iwr\s|curl\s+-|wget\s).*(iex|invoke-expression|start-process)|iex\s*\(\s*\(?new-object")),
    ("Script host or HTA launcher", re.compile(r"(?i)\b(mshta|wscript|cscript)(\.exe)?\b")),
    ("Rundll32 with script or URL", re.compile(r"(?i)rundll32(\.exe)?\b.*(javascript:|http[s]?://|\.dat|\\temp\\)")),
    ("Regsvr32 remote scriptlet", re.compile(r"(?i)regsvr32(\.exe)?\b.*(/i:http|scrobj)")),
    ("Certutil download/decode", re.compile(r"(?i)certutil(\.exe)?\b.*(-urlcache|-decode)")),
    ("Execution policy bypass", re.compile(r"(?i)-(ep|executionpolicy)\s+bypass")),
]
PS_LOG_PATTERNS = [
    ("Base64 decoding", re.compile(r"(?i)FromBase64String")), ("Invoke-Expression", re.compile(r"(?i)\b(iex|invoke-expression)\b")),
    ("Web download", re.compile(r"(?i)(DownloadString|DownloadFile|Net\.WebClient|Invoke-WebRequest)")),
    ("AMSI bypass attempt", re.compile(r"(?i)amsiutils|amsiInitFailed")), ("Credential dumping keyword", re.compile(r"(?i)mimikatz|sekurlsa|lsass")),
    ("Defender tampering", re.compile(r"(?i)Set-MpPreference\s+.*-Disable|Add-MpPreference\s+.*-Exclusion")),
]
COMMON_PORTS = {20, 21, 22, 25, 53, 80, 110, 123, 143, 389, 443, 465, 587, 636, 853, 993, 995, 3389, 5222, 5228, 8080, 8443}


@dataclass
class Context:
    policy: Any
    cancel: Any
    progress: Callable[[str, float | None], None]


def _ps(script: str, timeout: int = 120, env: dict | None = None) -> Any:
    """Run a fixed PowerShell security query (never user-supplied text) and parse its JSON output."""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                             f"$ErrorActionPreference='Stop'; {script} | ConvertTo-Json -Depth 4 -Compress"],
                            capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
                            encoding="utf-8", errors="replace", env={**os.environ, **(env or {})})
    if result.returncode != 0:
        raise RuntimeError(redact((result.stderr or result.stdout).strip())[:300] or "PowerShell query failed")
    out = result.stdout.strip()
    data = json.loads(out) if out else []
    return data if isinstance(data, list) else [data]


def _try(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except Exception as error:  # a single unavailable interface must not stop the whole scan
        return None, f"{type(error).__name__}: {redact(str(error))[:200]}"


USER_WRITABLE = [str(Path(os.environ.get("TEMP", str(LOCALAPPDATA / "Temp")))).lower(), str(HOME / "Downloads").lower(),
                 str(APPDATA).lower(), str(LOCALAPPDATA / "Temp").lower(), "c:\\users\\public", "c:\\programdata"]


def _user_writable(path: str | None) -> bool:
    lowered = str(path or "").lower()
    return any(lowered.startswith(prefix) for prefix in USER_WRITABLE if prefix)


def _signatures(paths: list[str]) -> dict[str, str]:
    """Authenticode status for executables (Valid / NotSigned / HashMismatch / ...)."""
    unique = [p for p in dict.fromkeys(paths) if p and os.path.isfile(p)][:80]
    if not unique:
        return {}
    rows = _ps("$p = $env:AEGIS_PATHS | ConvertFrom-Json; $p | ForEach-Object { $s = Get-AuthenticodeSignature -LiteralPath $_; "
               "[pscustomobject]@{path=$_; status=[string]$s.Status; signer=if ($s.SignerCertificate) {$s.SignerCertificate.Subject} else {$null}} }",
               timeout=180, env={"AEGIS_PATHS": json.dumps(unique)})
    return {r["path"]: (r["status"] + (f" ({r['signer'].split(',')[0]})" if r.get("signer") else "")) for r in rows}


def _sha256(path: str, limit: int = 200 * 1024 * 1024) -> str | None:
    try:
        if os.path.getsize(path) > limit:
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------- 10. Microsoft Defender

def defender_status(args, ctx):
    status = _ps("Get-MpComputerStatus | Select-Object AMServiceEnabled,AntivirusEnabled,RealTimeProtectionEnabled,"
                 "BehaviorMonitorEnabled,IoavProtectionEnabled,OnAccessProtectionEnabled,IsTamperProtected,AMRunningMode,"
                 "AntivirusSignatureVersion,AntivirusSignatureAge,@{n='SignatureUpdated';e={$_.AntivirusSignatureLastUpdated.ToString('o')}},"
                 "@{n='QuickScanEnd';e={if ($_.QuickScanEndTime) {$_.QuickScanEndTime.ToString('o')}}},"
                 "@{n='FullScanEnd';e={if ($_.FullScanEndTime) {$_.FullScanEndTime.ToString('o')}}}")[0]
    threats, threat_error = _try(_ps, "Get-MpThreat | Select-Object ThreatID,ThreatName,SeverityID,IsActive,DidThreatExecute")
    detections, detection_error = _try(_ps, "Get-MpThreatDetection | Sort-Object InitialDetectionTime -Descending | Select-Object -First 25 "
                                            "ThreatID,@{n='Detected';e={$_.InitialDetectionTime.ToString('o')}},ProcessName,ActionSuccess,"
                                            "@{n='Resources';e={($_.Resources | Select-Object -First 3) -join '; '}}")
    products, product_error = _try(_ps, "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | "
                                        "Select-Object displayName,productState")
    # productState bits 12-13 = 0x1000 means the product's real-time protection is on.
    registered = [{"product": p.get("displayName"), "real_time_on": bool(int(p.get("productState") or 0) & 0x1000)} for p in (products or [])]
    names = {t["ThreatID"]: t["ThreatName"] for t in (threats or [])}
    severity = {0: "unknown", 1: "low", 2: "moderate", 4: "high", 5: "severe"}
    protections = {k: status.get(k) for k in ("AntivirusEnabled", "RealTimeProtectionEnabled", "BehaviorMonitorEnabled",
                                               "IoavProtectionEnabled", "OnAccessProtectionEnabled", "IsTamperProtected")}
    issues = [name for name, enabled in protections.items() if enabled is False]
    age = status.get("AntivirusSignatureAge")
    if isinstance(age, int) and 3 < age < 65535:  # 65535 is Defender's "never updated / unknown" value
        issues.append(f"Antivirus signatures are {age} days old")
    for key in ("SignatureUpdated", "QuickScanEnd", "FullScanEnd"):
        if not isinstance(status.get(key), str):
            status[key] = None
    return {"source": "Microsoft Defender (Get-MpComputerStatus, Get-MpThreat, Get-MpThreatDetection)",
            "running_mode": status.get("AMRunningMode"), "protections": protections, "protection_issues": issues,
            "registered_antivirus_products": registered,
            "other_antivirus_active": any(p["real_time_on"] and "defender" not in str(p["product"]).lower() for p in registered),
            "signature_version": status.get("AntivirusSignatureVersion"), "signature_updated": status.get("SignatureUpdated"),
            "last_quick_scan": status.get("QuickScanEnd"), "last_full_scan": status.get("FullScanEnd"),
            "detected_threats": [{"name": t.get("ThreatName"), "severity": severity.get(t.get("SeverityID"), t.get("SeverityID")),
                                  "active": t.get("IsActive"), "executed": t.get("DidThreatExecute")} for t in (threats or [])],
            "recent_detections": [{"threat": names.get(d["ThreatID"], d["ThreatID"]), "detected": d.get("Detected"),
                                   "process": d.get("ProcessName"), "action_succeeded": d.get("ActionSuccess"),
                                   "resources": redact(str(d.get("Resources") or ""))} for d in (detections or [])],
            "unavailable": [e for e in (threat_error, detection_error, product_error) if e],
            "note": "Detections listed here are confirmed by Microsoft Defender. No detections does not prove the laptop is clean. "
                    "When another antivirus product is registered and active, Defender normally runs in passive mode or is off."}


def defender_quick_scan(args, ctx):
    ctx.progress("Microsoft Defender quick scan running (this usually takes a few minutes)…", None)
    _ps("Start-MpScan -ScanType QuickScan; [pscustomobject]@{ok=$true}", timeout=1800)
    result = defender_status(args, ctx)
    result["note"] = ("Quick scan completed by Microsoft Defender. Any threat it found was handled according to your Defender "
                      "settings. " + result["note"])
    return result


# --------------------------------------------------------------------------- 1-2. suspicious files

def suspicious_files(args, ctx):
    """Executables and scripts in common malware drop locations, with signature status and SHA-256 for IOC matching."""
    roots = [HOME / "Downloads", Path(os.environ.get("TEMP", str(LOCALAPPDATA / "Temp"))),
             APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup", Path("C:\\Users\\Public")]
    candidates = []
    for root in roots:
        if not root.exists():
            continue
        depth0 = len(root.parts)
        for current, dirs, files in os.walk(root, onerror=lambda _e: None):
            if ctx.cancel.is_set():
                break
            if len(Path(current).parts) - depth0 >= 3:
                dirs[:] = []
            for name in files:
                lower = name.lower()
                suffix = Path(lower).suffix
                if suffix not in EXECUTABLE_EXTENSIONS:
                    continue
                parts = lower.split(".")
                reasons = [f"{suffix} file in {root.name}"]
                if len(parts) > 2 and f".{parts[-2]}" in DOCUMENT_DISGUISES:
                    reasons.append("double extension: a document-like name ending in an executable type")
                if re.search(r"\s{3,}\.", name) or "\u202e" in name:
                    reasons.append("padded or right-to-left name hiding the real extension")
                candidates.append({"path": os.path.join(current, name), "reasons": reasons})
    ctx.progress(f"Checking signatures of {len(candidates)} files…", 0.5)
    signed = _try(_signatures, [c["path"] for c in candidates if Path(c["path"]).suffix.lower() in {".exe", ".dll", ".msi", ".scr", ".cpl", ".ps1"}])[0] or {}
    findings = []
    for index, item in enumerate(candidates[:150]):
        if ctx.cancel.is_set():
            break
        signature = signed.get(item["path"])
        if signature and not signature.startswith("Valid"):
            item["reasons"].append(f"Authenticode signature: {signature}")
        try:
            stat = os.stat(item["path"])
        except OSError:
            continue
        level = "suspicious" if len(item["reasons"]) > 1 else "review"
        findings.append({"path": item["path"], "size": stat.st_size, "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                         "signature": signature, "sha256": _sha256(item["path"]), "level": level, "reasons": item["reasons"]})
        if index % 20 == 0:
            ctx.progress(f"Hashed {index + 1} of {len(candidates)} files…", 0.5 + 0.5 * index / max(len(candidates), 1))
    findings.sort(key=lambda f: (f["level"] != "suspicious", f["path"]))
    return {"locations": [str(r) for r in roots], "findings": findings, "total": len(findings),
            "suspicious": sum(1 for f in findings if f["level"] == "suspicious"),
            "note": "Files are flagged by location, name tricks and missing/invalid signatures. That is not a malware verdict; "
                    "only Microsoft Defender detections (or an IOC match) are confirmed evidence. File contents were not opened."}


# --------------------------------------------------------------------------- 4. processes

def suspicious_processes(args, ctx):
    rows = _ps("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine")
    names = {r["ProcessId"]: r["Name"] for r in rows}
    to_check = [r["ExecutablePath"] for r in rows if r.get("ExecutablePath") and not str(r["ExecutablePath"]).lower().startswith("c:\\windows\\")]
    ctx.progress(f"Checking signatures of {len(set(to_check))} non-Windows executables…", 0.4)
    signed = _try(_signatures, to_check)[0] or {}
    findings = []
    for r in rows:
        path, command = r.get("ExecutablePath"), str(r.get("CommandLine") or "")
        reasons = []
        if path and _user_writable(path):
            reasons.append("runs from a user-writable folder (Temp, Downloads, AppData, Public or ProgramData)")
        signature = signed.get(path)
        store_app = str(path or "").lower().startswith("c:\\program files\\windowsapps\\")  # MSIX: signed per package, not per file
        if signature and not signature.startswith("Valid") and not store_app:
            reasons.append(f"executable signature: {signature}")
        techniques = [label for label, pattern in LOLBIN_PATTERNS if pattern.search(command)]
        reasons += techniques
        parent = names.get(r.get("ParentProcessId"), "")
        if r["Name"].lower() in {"powershell.exe", "cmd.exe", "wscript.exe", "mshta.exe"} and parent.lower() in {
                "winword.exe", "excel.exe", "outlook.exe", "powerpnt.exe", "acrord32.exe"}:
            reasons.append(f"shell started by an Office/PDF application ({parent})")
        if reasons:
            # Suspicious needs real evidence: an attack technique, a tampered signature, an Office-spawned shell, or an
            # unsigned binary running from a user-writable folder. Unsigned software in protected folders is "review".
            unsigned = bool(signature) and not signature.startswith("Valid") and not store_app
            strong = (bool(techniques) or "HashMismatch" in str(signature) or any("Office/PDF" in x for x in reasons)
                      or (unsigned and _user_writable(path)))
            findings.append({"pid": r["ProcessId"], "name": r["Name"], "path": path, "parent": parent, "signature": signature,
                             "level": "suspicious" if strong else "review",
                             "reasons": reasons, "command_line": redact(command)[:200] if any(p.search(command) for _, p in LOLBIN_PATTERNS) else None})
    return {"process_count": len(rows), "findings": findings,
            "note": "Processes are flagged by location, signature and command-line techniques common in attacks. Nothing was "
                    "terminated. A flag is a reason to review, not proof of compromise."}


# --------------------------------------------------------------------------- 3 & 7. persistence

def persistence_entries(args, ctx):
    import winreg
    entries = []
    for hive, hive_name in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        for key_path in (r"Software\Microsoft\Windows\CurrentVersion\Run", r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
                         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, value, _ = winreg.EnumValue(key, index)
                        entries.append({"type": "registry run key", "location": f"{hive_name}\\{key_path}", "name": name, "command": str(value)})
            except OSError:
                continue
    for folder in (APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
                   Path("C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\StartUp")):
        if folder.exists():
            entries += [{"type": "startup folder", "location": str(folder), "name": i.name, "command": str(i)} for i in folder.iterdir()
                        if i.name.lower() != "desktop.ini"]
    ctx.progress("Reading scheduled tasks…", 0.4)
    tasks, task_error = _try(_ps, "Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } | Select-Object TaskName,TaskPath,"
                                  "@{n='State';e={[string]$_.State}},@{n='Actions';e={($_.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments)\" }) -join '; '}}")
    for t in tasks or []:
        entries.append({"type": "scheduled task", "location": t.get("TaskPath"), "name": t.get("TaskName"), "command": str(t.get("Actions") or ""), "state": t.get("State")})
    ctx.progress("Reading auto-start services…", 0.7)
    services, service_error = _try(_ps, "Get-CimInstance Win32_Service | Where-Object { $_.StartMode -eq 'Auto' } | Select-Object Name,DisplayName,PathName,State")
    for s in services or []:
        path = str(s.get("PathName") or "")
        if path.lower().startswith(("c:\\windows\\", "\"c:\\windows\\")):
            continue
        entry = {"type": "auto-start service", "location": s.get("Name"), "name": s.get("DisplayName"), "command": path, "state": s.get("State")}
        if " " in path.split(".exe")[0] and not path.startswith('"') and ".exe" in path.lower():
            entry["unquoted_service_path"] = True
        entries.append(entry)
    findings = []
    for entry in entries:
        command = entry["command"]
        reasons = [label for label, pattern in LOLBIN_PATTERNS if pattern.search(command)]
        exe = re.match(r'^\s*"?([A-Za-z]:\\[^"]+?\.(exe|bat|cmd|ps1|vbs|js|dll|scr))', command, re.I)
        exe_path = exe.group(1) if exe else None
        if exe_path and _user_writable(exe_path):
            reasons.append("starts a program from a user-writable folder")
        if exe_path and not os.path.exists(exe_path):
            reasons.append("target program no longer exists")
        if entry.get("unquoted_service_path"):
            reasons.append("unquoted service path with spaces (privilege-escalation weakness)")
        entry["command"] = redact(command)[:260]
        if reasons:
            findings.append({**entry, "reasons": reasons, "level": "suspicious" if any(r in [l for l, _ in LOLBIN_PATTERNS] for r in reasons) else "review"})
    return {"entries_checked": len(entries), "by_type": {t: sum(1 for e in entries if e["type"] == t) for t in {e["type"] for e in entries}},
            "findings": findings, "unavailable": [e for e in (task_error, service_error) if e],
            "note": "Run keys, startup folders, non-Microsoft scheduled tasks and non-Windows auto-start services were read. Nothing was changed."}


# --------------------------------------------------------------------------- 5. network

def network_connections(args, ctx):
    rows = _ps("$procs = @{}; Get-Process | ForEach-Object { $procs[$_.Id] = @($_.ProcessName, $_.Path) }; "
               "Get-NetTCPConnection | Where-Object { $_.State -in 'Established','Listen' } | ForEach-Object { "
               "[pscustomobject]@{local=$_.LocalAddress; lport=$_.LocalPort; remote=$_.RemoteAddress; rport=$_.RemotePort; "
               "state=[string]$_.State; pid=$_.OwningProcess; name=$procs[[int]$_.OwningProcess][0]; path=$procs[[int]$_.OwningProcess][1]} }")
    listening, established, findings, remote_ips = [], [], [], set()
    for r in rows:
        if r["state"] == "Listen":
            exposed = r["local"] in {"0.0.0.0", "::"}
            listening.append({"port": r["lport"], "address": r["local"], "process": r.get("name"), "exposed_on_all_interfaces": exposed})
            if exposed and _user_writable(r.get("path")):
                findings.append({"level": "suspicious", "process": r.get("name"), "path": r.get("path"),
                                 "reasons": [f"listens on all interfaces (port {r['lport']}) from a user-writable folder"]})
            continue
        try:
            ip = ipaddress.ip_address(r["remote"])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            continue
        remote_ips.add(str(ip))
        established.append({"process": r.get("name"), "remote": f"{ip}:{r['rport']}", "local_port": r["lport"]})
        reasons = []
        if _user_writable(r.get("path")):
            reasons.append("connection owned by a program in a user-writable folder")
        if r["rport"] not in COMMON_PORTS and r["rport"] < 49152:
            reasons.append(f"uncommon remote port {r['rport']}")
        if reasons:
            findings.append({"level": "review", "process": r.get("name"), "path": r.get("path"), "remote": f"{ip}:{r['rport']}", "reasons": reasons})
    return {"established_public_connections": len(established), "connections": established[:60], "remote_ips": sorted(remote_ips),
            "listening_ports": sorted(listening, key=lambda l: l["port"])[:80], "findings": findings,
            "note": "Public remote IPs are compared with the platform's stored threat indicators by the server. Uncommon ports and "
                    "programs in user folders are reasons to review, not proof of compromise. No connection was closed."}


# --------------------------------------------------------------------------- 6 & 8. PUAs, vulnerabilities and posture

def _installed_programs():
    import winreg
    programs = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            with key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, index)) as sub:
                            value = lambda n: (lambda: winreg.QueryValueEx(sub, n)[0])
                            name = value("DisplayName")()
                            row = {"name": name}
                            for field, reg in (("publisher", "Publisher"), ("version", "DisplayVersion")):
                                try:
                                    row[field] = value(reg)()
                                except OSError:
                                    row[field] = None
                            programs[name] = row
                    except OSError:
                        continue
    return sorted(programs.values(), key=lambda p: p["name"].lower())


def unwanted_applications(args, ctx):
    programs = _installed_programs()
    flagged = [dict(p, reasons=["name matches a common potentially-unwanted-program pattern"]) for p in programs
               if any(h in p["name"].lower() for h in PUA_HINTS)]
    unknown = [p["name"] for p in programs if not p.get("publisher")]
    return {"installed_programs_checked": len(programs), "potentially_unwanted": flagged, "without_publisher": unknown[:40],
            "note": "Potentially unwanted means the name matches common adware/optimizer patterns; it is not a malware verdict."}


def security_posture(args, ctx):
    """Patch level, firewall and hardening settings; the verifiable part of a vulnerability check."""
    os_info = _ps("Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber")[0]
    hotfixes, hotfix_error = _try(_ps, "Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 5 "
                                       "HotFixID,Description,@{n='InstalledOn';e={$_.InstalledOn.ToString('yyyy-MM-dd')}}")
    firewall, firewall_error = _try(_ps, "Get-NetFirewallProfile | Select-Object Name,Enabled")
    smb1, smb_error = _try(_ps, "Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol")
    import winreg
    def reg(path, name):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None
    uac = reg(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "EnableLUA")
    rdp_denied = reg(r"SYSTEM\CurrentControlSet\Control\Terminal Server", "fDenyTSConnections")
    findings = []
    last_patch = (hotfixes or [{}])[0].get("InstalledOn") if hotfixes else None
    if last_patch:
        age = (datetime.now() - datetime.strptime(last_patch, "%Y-%m-%d")).days
        if age > 45:
            findings.append({"level": "review", "issue": f"Most recent Windows update was installed {age} days ago ({last_patch})."})
    for profile in firewall or []:
        if not profile.get("Enabled"):
            findings.append({"level": "suspicious", "issue": f"Windows Firewall is turned off for the {profile['Name']} profile."})
    if smb1 and smb1[0].get("EnableSMB1Protocol"):
        findings.append({"level": "suspicious", "issue": "SMBv1 is enabled (exploited by WannaCry-style worms)."})
    if uac == 0:
        findings.append({"level": "suspicious", "issue": "User Account Control (UAC) is disabled."})
    if rdp_denied == 0:
        findings.append({"level": "review", "issue": "Remote Desktop connections are allowed."})
    return {"windows": f"{os_info['Caption']} (version {os_info['Version']}, build {os_info['BuildNumber']})",
            "recent_updates": hotfixes or [], "firewall_profiles": firewall or [], "smb1_enabled": (smb1 or [{}])[0].get("EnableSMB1Protocol") if smb1 else None,
            "uac_enabled": None if uac is None else bool(uac), "remote_desktop_allowed": None if rdp_denied is None else rdp_denied == 0,
            "findings": findings, "unavailable": [e for e in (hotfix_error, firewall_error, smb_error) if e],
            "note": "Known vulnerabilities in individual installed programs cannot be verified locally without a vulnerability "
                    "database; this check covers patch recency and hardening settings that can be verified."}


# --------------------------------------------------------------------------- 11. PowerShell activity

def powershell_activity(args, ctx):
    events, error = _try(_ps, "Get-WinEvent -LogName 'Microsoft-Windows-PowerShell/Operational' -MaxEvents 400 -ErrorAction SilentlyContinue | "
                              "Where-Object { $_.Id -in 4103,4104 } | Select-Object Id,@{n='Time';e={$_.TimeCreated.ToString('o')}},Message", timeout=120)
    findings = []
    for event in events or []:
        message = str(event.get("Message") or "")
        hits = [label for label, pattern in PS_LOG_PATTERNS if pattern.search(message)]
        if hits:
            match = next(p.search(message) for label, p in PS_LOG_PATTERNS if label == hits[0])
            start = max(0, match.start() - 60)
            findings.append({"time": event.get("Time"), "event_id": event.get("Id"), "indicators": hits,
                             "excerpt": redact(message[start:start + 160]).replace("\r", " ").replace("\n", " ")})
    return {"events_checked": len(events or []), "findings": findings[:30], "total_findings": len(findings),
            "unavailable": [error] if error else [],
            "note": "PowerShell script-block and module logs were scanned for attack techniques; only short redacted excerpts are "
                    "shown. Administrator-written scripts can trigger these indicators legitimately."}


# --------------------------------------------------------------------------- 9. exposed credentials (approved project folders only)

def secret_scan(args, ctx):
    root = ctx.policy.content_path(args.get("path"))
    findings, scanned = [], 0
    for current, dirs, files in os.walk(root, onerror=lambda _e: None):
        if ctx.cancel.is_set():
            break
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}]
        for name in files:
            path = os.path.join(current, name)
            if Path(name).suffix.lower() not in TEXT_EXTENSIONS and not name.lower().startswith(".env"):
                continue
            try:
                if os.path.getsize(path) > 1024 * 1024:
                    continue
                lowered = path.lower()
                example = (any(p in lowered for p in ("\\test", "\\docs\\", "\\examples\\")) or lowered.endswith((".example", ".sample"))
                           or name.lower().startswith("test_"))
                with open(path, encoding="utf-8", errors="replace") as handle:
                    scanned += 1
                    for number, line in enumerate(handle, 1):
                        for kind, masked, risk in find_secrets(line):
                            findings.append({"file": path, "line": number, "type": kind, "value": masked,
                                             "risk": "low" if example and risk != "critical" else risk,
                                             **({"context": "test or example file"} if example else {})})
            except OSError:
                continue
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (order[f["risk"]], f["file"], f["line"]))
    return {"root": str(root), "files_scanned": scanned, "findings": findings[:100], "total_findings": len(findings),
            "by_risk": {r: sum(1 for f in findings if f["risk"] == r) for r in order if any(f["risk"] == r for f in findings)},
            "note": "Only configuration and source files in the approved project folder were read. Values are redacted."}


# --------------------------------------------------------------------------- consolidated threat scan

def threat_scan(args, ctx):
    steps = [("Microsoft Defender status", defender_status), ("Suspicious files", suspicious_files),
             ("Running processes", suspicious_processes), ("Persistence (startup, tasks, services)", persistence_entries),
             ("Network connections", network_connections), ("Potentially unwanted applications", unwanted_applications),
             ("Patch level and hardening", security_posture), ("PowerShell activity", powershell_activity)]
    report = {}
    for index, (label, func) in enumerate(steps):
        if ctx.cancel.is_set():
            report["cancelled_before"] = label
            break
        ctx.progress(f"{label}…", index / len(steps))
        inner = Context(ctx.policy, ctx.cancel, lambda message, fraction=None, base=index: ctx.progress(message, (base + (fraction or 0)) / len(steps)))
        result, error = _try(func, args, inner)
        report[func.__name__] = result if result is not None else {"unavailable": [error]}
    return report


@dataclass(frozen=True)
class Tool:
    func: Callable
    description: str
    args: tuple[str, ...] = ()
    requires_confirmation: bool = False
    confirmation_reason: str = ""


# The complete registry. Anything not listed here cannot run.
TOOLS: dict[str, Tool] = {
    "threat_scan": Tool(threat_scan, "Consolidated security scan: Defender, suspicious files, processes, persistence, network, PUAs, posture, PowerShell logs."),
    "defender_status": Tool(defender_status, "Microsoft Defender protection status and detected threats."),
    "suspicious_files": Tool(suspicious_files, "Executables and scripts in common malware locations, with signature status and hashes."),
    "suspicious_processes": Tool(suspicious_processes, "Running processes with security indicators (location, signature, attack command lines)."),
    "persistence_entries": Tool(persistence_entries, "Startup entries, scheduled tasks and auto-start services used for persistence."),
    "network_connections": Tool(network_connections, "Established public connections and listening ports, for IOC matching."),
    "unwanted_applications": Tool(unwanted_applications, "Potentially unwanted applications among installed programs."),
    "security_posture": Tool(security_posture, "Patch recency, firewall, SMBv1, UAC and remote-desktop settings."),
    "powershell_activity": Tool(powershell_activity, "Suspicious techniques in PowerShell security logs."),
    "secret_scan": Tool(secret_scan, "Exposed API keys, passwords and private keys in an approved project folder (redacted).", ("path",)),
    "defender_quick_scan": Tool(defender_quick_scan, "Run a Microsoft Defender quick scan.", (), True,
                                "Microsoft Defender will scan common malware locations for a few minutes and use noticeable CPU. "
                                "If it finds a threat, Defender handles it according to your Defender settings (normally quarantine). "
                                "The agent itself does not delete or change anything."),
}
