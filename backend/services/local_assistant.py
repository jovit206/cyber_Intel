"""Laptop security requests -> local security agent -> IOC matching -> explanation.

The local agent is exclusively a cybersecurity threat scanner. Requests are mapped to one of the security checks by
fixed rules; general laptop-management requests (storage, duplicates, CPU/RAM, file search, cleanup, software
management) are recognised and politely refused. The backend also refuses any tool outside
``local_agent.SECURITY_TOOLS``, so the restriction does not depend on the chat model.

Results are rendered deterministically (exact paths, counts and evidence); the LLM adds only a short explanation.
Public IP addresses and file hashes found by a scan are matched against the platform's stored threat indicators
(MongoDB ``attributes``), never sent to external services.
"""
from __future__ import annotations

import re
import time
from typing import Any, Iterator

from backend.services import local_agent
from backend.services.llm_provider import LLMProviderError, stream_chat_answer

POLL_SECONDS = 0.7
MAX_JOB_SECONDS = 30 * 60
DEFAULT_PROJECT = "G:\\Projects\\nosql-lab-7"

_MACHINE = re.compile(r"\b(laptop|computer|pc|machine|system|device|windows|my files|my drive|[a-z] drive)\b|\b[a-z]:\\", re.I)
_PLATFORM_MECHANISM = re.compile(r"^\s*(how|why)\s+(does|do|is|are)\s+(the|this|my)\s+(platform|application|app|assistant|"
                                 r"chatbot|backend|agent)\b", re.I)
_REQUEST = re.compile(r"\b(scan|check|inspect|find|look for|search|detect|identify|analy[sz]e|audit|review|show|list|report|"
                      r"is there|are there|any|investigate|examine|run|tell me|what)\b", re.I)

# Security checks, most specific first.
_SECURITY_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), tool) for p, tool in [
    (r"\b(run|start)\b.*\b(defender|quick)\s*scan\b|\bdefender quick scan\b", "defender_quick_scan"),
    (r"\b(api keys?|secrets?|credentials?|passwords?|tokens?|private keys?)\b.*\b(project|code|repo|files?)\b|"
     r"\b(project|code|repo)\b.*\b(api keys?|secrets?|credentials?|passwords?|tokens?)\b", "secret_scan"),
    (r"\b(defender|antivirus|anti-virus|av)\b.*\b(status|enabled|on|working|protection|detections?|threats? found)\b|"
     r"\b(status of|is) (windows )?defender\b|\bdefender\b", "defender_status"),
    (r"\b(network|connections?|listening ports?|open ports?|ports?|remote (ip|host)s?|c2|beacon\w*)\b", "network_connections"),
    (r"\b(processes|process|running programs?|task manager)\b", "suspicious_processes"),
    (r"\b(startup|start-?up|autostart|auto-start|persistence|scheduled tasks?|services?|run keys?)\b", "persistence_entries"),
    (r"\b(unwanted (programs?|applications?|apps?|software)|puas?|adware|bloatware|spyware apps?)\b", "unwanted_applications"),
    (r"\b(vulnerab\w*|patch\w*|updates? (missing|installed)|missing updates|firewall|smb ?v?1|uac|hardening|security settings|exposure)\b", "security_posture"),
    (r"\b(powershell|script ?block|security logs?|event logs?)\b", "powershell_activity"),
    (r"\b(suspicious|malicious|dangerous) (files?|executables?|scripts?|downloads?)\b|\b(executables?|scripts?) .*(suspicious|malicious)\b", "suspicious_files"),
    (r"\b(malware|virus(es)?|trojans?|ransomware|threats?|infect\w*|compromis\w*|hack\w*|rootkits?|backdoors?|keyloggers?|"
     r"spyware|iocs?|indicators? of compromise|security scan|full scan|security check)\b", "threat_scan"),
]]

# General laptop management: recognised so it can be refused, never executed.
_GENERAL_RULES = [re.compile(p, re.I) for p in [
    r"\b(largest|biggest|large|huge)\s+(files?|folders?)\b", r"\bduplicate (files?|photos?|images?|documents?|copies)\b|\bfind duplicates?\b",
    r"\b(disk|storage|drive)\s+(space|usage|cleanup)\b|\bfree (up )?space\b|\bstorage\b.*\bfolders?\b", r"\btemp(orary)? files?\b|\bjunk\b|\bclean ?up\b",
    r"\b(ram|memory usage|cpu usage|cpu load|processor usage|performance|battery|speed up)\b", r"\b(search for|find|locate|where is) (a |the |my )?file\b|\bfile (named|called)\b",
    r"\b(python|pip) (packages|version)|\b(node|npm|git) version\b|\binstalled packages\b|\boutdated (packages|dependencies)\b",
    r"\bdocker (containers?|images?|space|usage)\b|\bhow much space\b", r"\bproject structure\b|\bfolder structure\b|\binspect (my |the )?project\b",
    r"\b(install|uninstall|upgrade|reinstall)\b|\bupdate (my )?(software|apps?|programs?|drivers?|python|packages)\b", r"\b(delete|remove|move|rename|organi[sz]e|copy|backup) (my )?files?\b",
    r"\b(photos?|pictures?|videos?|music|emails?|browser history|documents folder)\b", r"\bkill (the )?process\b|\bterminate\b|\bclose (the )?app",
]]

REFUSAL = ("My local agent is restricted to cybersecurity inspection of this laptop, so I can't help with general laptop "
           "management such as storage, files, performance or software installation. I can scan for malware and suspicious "
           "files, suspicious processes, startup/persistence entries, unusual network connections, potentially unwanted "
           "applications, Microsoft Defender status, patch and hardening gaps, suspicious PowerShell activity, exposed "
           "credentials in your approved project folder, and match what I find against the platform's threat indicators.")


def classify(question: str) -> tuple[str, Any] | None:
    """("security", (tool, args)) | ("refuse", None) | None when the request is not about this laptop."""
    text = question.strip()
    if _PLATFORM_MECHANISM.search(text):
        return None
    security = next((tool for pattern, tool in _SECURITY_RULES if pattern.search(text)), None)
    general = any(pattern.search(text) for pattern in _GENERAL_RULES)
    machine = bool(_MACHINE.search(text))
    if security and (machine or security in {"secret_scan", "defender_status", "defender_quick_scan"} or
                     (_REQUEST.search(text) and re.search(r"\b(my|this|our)\b", text, re.I))):
        args = {"path": DEFAULT_PROJECT} if security == "secret_scan" else {}
        return "security", (security, args)
    if general and (machine or re.search(r"\bmy\b", text, re.I)) and _REQUEST.search(text) or (general and machine):
        return "refuse", None
    return None


# ------------------------------------------------------------------ IOC matching against the platform's indicators

def match_iocs(database, result: dict) -> dict:
    """Match public IPs and file hashes from a scan against stored threat indicators (indexed, no external calls)."""
    values: dict[str, str] = {}
    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "remote_ips" and isinstance(value, list):
                    values.update({str(v): "ip" for v in value})
                elif key == "sha256" and isinstance(value, str):
                    values[value.lower()] = "sha256"
                else:
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)
    collect(result)
    if not values or database is None:
        return {"checked": len(values), "matches": [], "indicator_source": "platform MongoDB attributes"}
    rows = database["attributes"].find(
        {"normalized_indicator": {"$in": list(values)}, "is_current": {"$ne": False}},
        {"_id": 0, "normalized_indicator": 1, "indicator_type": 1, "source_feed_name": 1, "event_id": 1, "comment": 1},
    ).limit(50)
    matches = [{"indicator": r["normalized_indicator"], "kind": values.get(r["normalized_indicator"]), "feed": r.get("source_feed_name"),
                "type": r.get("indicator_type"), "event": r.get("event_id"), "comment": (r.get("comment") or "")[:120]} for r in rows]
    return {"checked": len(values), "ips": sum(1 for k in values.values() if k == "ip"),
            "hashes": sum(1 for k in values.values() if k == "sha256"), "matches": matches,
            "indicator_source": "platform MongoDB attributes (public threat feeds)"}


# ------------------------------------------------------------------ rendering

def _list(items, fields, limit=12) -> str:
    lines = []
    for item in (items or [])[:limit]:
        reasons = "; ".join(item.get("reasons") or [])
        head = " · ".join(str(item[f]) for f in fields if item.get(f) not in (None, "", []))
        lines.append(f"- [{item.get('level', 'review')}] {head}" + (f" — {reasons}" if reasons else ""))
    more = len(items or []) - limit
    return "\n".join(lines) + (f"\n- …and {more} more" if more > 0 else "") if lines else "- none"


def _render_one(tool: str, r: dict) -> str:
    if not r:
        return "- not available"
    if r.get("unavailable") and len(r) == 1:
        return f"- not available: {'; '.join(r['unavailable'])}"
    if tool in {"defender_status", "defender_quick_scan"}:
        av = ", ".join(f"{p['product']} ({'real-time on' if p['real_time_on'] else 'off'})" for p in r.get("registered_antivirus_products", []))
        detected = r.get("detected_threats") or []
        return (f"- Defender mode: {r.get('running_mode')}; registered antivirus: {av or 'unknown'}\n"
                f"- Defender protections off: {', '.join(r.get('protection_issues') or []) or 'none'}"
                + (" (expected while another antivirus is active)" if r.get("other_antivirus_active") else "")
                + f"\n- Confirmed detections: {len(detected)}" + "".join(f"\n  - {d['name']} (severity {d['severity']}, active: {d['active']})" for d in detected[:10])
                + (f"\n- Recent detection events: {len(r.get('recent_detections') or [])}" if r.get("recent_detections") else ""))
    if tool == "suspicious_files":
        return (f"- {r.get('total', 0)} executables/scripts in Downloads, Temp, Startup and Public ({r.get('suspicious', 0)} with extra warning signs)\n"
                + _list(r.get("findings"), ["path", "signature"], 10))
    if tool == "suspicious_processes":
        return f"- {r.get('process_count')} processes checked, {len(r.get('findings') or [])} flagged\n" + _list(r.get("findings"), ["name", "pid", "path"], 10)
    if tool == "persistence_entries":
        return f"- {r.get('entries_checked')} persistence entries checked {r.get('by_type')}\n" + _list(r.get("findings"), ["type", "name", "command"], 10)
    if tool == "network_connections":
        exposed = [l for l in r.get("listening_ports") or [] if l.get("exposed_on_all_interfaces")]
        return (f"- {r.get('established_public_connections')} established connections to public IPs; {len(exposed)} ports listening on all interfaces\n"
                + _list(r.get("findings"), ["process", "remote", "path"], 10))
    if tool == "unwanted_applications":
        return (f"- {r.get('installed_programs_checked')} installed programs checked, {len(r.get('potentially_unwanted') or [])} potentially unwanted\n"
                + _list(r.get("potentially_unwanted"), ["name", "publisher"], 10))
    if tool == "security_posture":
        updates = r.get("recent_updates") or []
        return (f"- {r.get('windows')}; latest update: {updates[0].get('HotFixID') + ' on ' + updates[0].get('InstalledOn') if updates else 'unknown'}\n"
                f"- Firewall: {', '.join(p['Name'] + (' on' if p.get('Enabled') else ' OFF') for p in r.get('firewall_profiles') or []) or 'unknown'}; "
                f"SMBv1: {r.get('smb1_enabled')}; UAC: {r.get('uac_enabled')}; Remote Desktop allowed: {r.get('remote_desktop_allowed')}\n"
                + "\n".join(f"- [{f['level']}] {f['issue']}" for f in r.get("findings") or []))
    if tool == "powershell_activity":
        return (f"- {r.get('events_checked')} PowerShell log events checked, {r.get('total_findings', 0)} with attack indicators\n"
                + "\n".join(f"- {f['time']}: {', '.join(f['indicators'])} — {f['excerpt'][:120]}" for f in (r.get("findings") or [])[:8]))
    if tool == "secret_scan":
        rows = [dict(f, level=f["risk"], reasons=[f["type"]]) for f in r.get("findings") or []]
        return (f"- {r.get('total_findings', 0)} potential secrets in {r.get('files_scanned', 0)} files under {r.get('root')} {r.get('by_risk')}\n"
                + _list(rows, ["file", "line", "value"], 15))
    return "- (no renderer)"


TITLES = {"defender_status": "Microsoft Defender", "suspicious_files": "Suspicious files", "suspicious_processes": "Processes",
          "persistence_entries": "Persistence", "network_connections": "Network", "unwanted_applications": "Potentially unwanted applications",
          "security_posture": "Patch level and hardening", "powershell_activity": "PowerShell activity"}


def render(tool: str, result: dict, iocs: dict) -> str:
    if tool == "threat_scan":
        body = "\n\n".join(f"**{TITLES[name]}**\n{_render_one(name, result.get(name) or {})}" for name in TITLES if name in result)
    else:
        body = _render_one(tool, result)
    if iocs.get("checked"):
        if iocs["matches"]:
            body += ("\n\n**IOC matches (platform threat intelligence)**\n" + "\n".join(
                f"- [confirmed match] {m['kind']} {m['indicator']} is listed by the {m['feed']} feed ({m['type']})" for m in iocs["matches"]))
        else:
            body += (f"\n\n**IOC matching**\n- {iocs['checked']} indicators from this scan ({iocs.get('ips', 0)} public IPs, {iocs.get('hashes', 0)} "
                     "file hashes) were compared with the platform's stored threat indicators: no match. That only means they are "
                     "not in these feeds; it is not proof that they are safe.")
    return body


def _explain(question: str, rendered: str) -> Iterator[str]:
    system = ("You are the AEGIS security assistant reporting a read-only security scan of the user's own Windows laptop. "
              "In 3 to 5 short sentences, summarise the most important findings and what the user should check next. Rules: "
              "call something malware only if Microsoft Defender detected it or an IOC match confirms it; otherwise say it is "
              "suspicious or needs review and give the evidence; never say the laptop is safe or clean; do not suggest deleting "
              "or killing anything automatically; use only facts that appear in the results; secrets are redacted and must not "
              f"be reconstructed.\n\nResults:\n{rendered[:5000]}")
    yield from stream_chat_answer(system, question, max_tokens=260)


# ------------------------------------------------------------------ pipeline

def events(actor: dict, question: str, *, database=None, job_id: str | None = None) -> Iterator[dict]:
    decision = None if job_id else classify(question)
    if decision and decision[0] == "refuse":
        yield {"type": "done", "mode": "local_out_of_scope", "answer": REFUSAL}
        return
    if not local_agent.allowed(actor):
        yield {"type": "done", "mode": "local_not_allowed", "answer": "Laptop security scanning is limited to administrators and "
               "approved accounts, so I can't scan this computer from your account."}
        return
    status = local_agent.status(actor)
    yield {"type": "agent_status", "connected": status.get("connected"), "message": status.get("message")}
    if not status.get("connected"):
        yield {"type": "done", "mode": "local_agent_offline", "answer": (
            "I can't reach the local security agent, so no scan was run. " + (status.get("message") or "") +
            " When it is running, ask again.").strip()}
        return
    try:
        if job_id:
            job = local_agent.job(actor, job_id)
        else:
            tool, args = decision[1]
            job = local_agent.start(actor, tool, args)
            if job.get("status") == "confirmation_required":
                yield {"type": "confirmation_required", **{k: job.get(k) for k in ("confirmation_id", "tool", "description", "reason", "expires_in_seconds")}}
                yield {"type": "done", "mode": "local_confirmation_required", "answer": (
                    f"This needs your permission before I run it: {job.get('description')} {job.get('reason')} "
                    "Use the confirmation dialog to allow or decline. Nothing has been done yet.")}
                return
        started = time.monotonic()
        while job.get("status") == "running" and time.monotonic() - started < MAX_JOB_SECONDS:
            yield {"type": "agent_progress", "job_id": job["id"], "tool": job["tool"], "message": job.get("progress"),
                   "fraction": job.get("fraction"), "elapsed_seconds": job.get("elapsed_seconds")}
            time.sleep(POLL_SECONDS)
            job = local_agent.job(actor, job["id"])
    except local_agent.LocalAgentError as error:
        yield {"type": "done", "mode": "local_agent_error", "answer": f"The local security agent could not complete this: {error.message}"}
        return
    tool, state = job.get("tool"), job.get("status")
    if state == "running":
        yield {"type": "done", "mode": "local_timeout", "answer": "The scan is still running after 30 minutes; I stopped waiting. You can cancel it."}
        return
    if state in {"rejected", "failed"}:
        yield {"type": "done", "mode": f"local_{state}", "answer": f"The {tool.replace('_', ' ')} check was {state}: {job.get('error')}"}
        return
    result = job.get("result") or {}
    try:
        iocs = match_iocs(database, result)
    except Exception:  # IOC matching is best-effort; the scan result is still reported
        iocs = {"checked": 0, "matches": []}
    rendered = render(tool, result, iocs)
    header = "The scan was cancelled; these are the partial results.\n\n" if state == "cancelled" else ""
    explanation = []
    try:
        for chunk in _explain(question, rendered):
            explanation.append(chunk)
            yield {"type": "token", "text": chunk}
    except LLMProviderError:
        explanation = []
    intro = "".join(explanation).strip()
    footer = ("\n\nAll checks were read-only: nothing was deleted, quarantined, stopped or changed. 'Suspicious' and 'review' "
              "are reasons to look closer, not malware verdicts; only Microsoft Defender detections and IOC matches are confirmed evidence.")
    answer = (header + (intro + "\n\n" if intro else "") + rendered + footer).strip()
    yield {"type": "done", "mode": "local_agent" if state == "completed" else "local_cancelled", "answer": answer,
           "job": {"id": job["id"], "tool": tool, "status": state, "elapsed_seconds": job.get("elapsed_seconds")},
           "ioc_matches": len(iocs.get("matches", []))}
