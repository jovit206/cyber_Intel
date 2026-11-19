"""AEGIS local agent: a read-only Windows cybersecurity threat scanner for the AI Threat Assistant.

    python local_agent/aegis_local_agent.py            # start (listens on 127.0.0.1:8765 by default)
    python local_agent/aegis_local_agent.py --print-token

Security model
- Listens on the loopback interface only. The Docker backend reaches it through host.docker.internal.
- Every request must carry an HMAC-SHA256 signature made with the shared token from
  %LOCALAPPDATA%\\AegisLocalAgent\\agent_token.txt, over method, path, timestamp, nonce and body. Requests older than
  60 seconds or with a reused nonce are rejected.
- Only the fixed tools in tools.py can run; there is no command-execution endpoint.
- Tools that change something or contact the network wait for an explicit approval of that exact request.
- Every action is logged to %LOCALAPPDATA%\\AegisLocalAgent\\logs\\agent-actions.jsonl.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from policy import Policy, PolicyError, load_config, load_token, log_action, redact  # noqa: E402
from tools import TOOLS, Context  # noqa: E402

VERSION = "1.0.0"
MAX_SKEW_SECONDS = 60
JOB_TTL_SECONDS = 3600
CONFIRMATION_TTL_SECONDS = 300


class Agent:
    def __init__(self, config: dict, token: str):
        self.config = config
        self.token = token.encode()
        self.policy = Policy(config)
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.confirmations: dict[str, dict] = {}
        self.nonces: dict[str, float] = {}

    # ---------------------------------------------------------------- authentication
    def verify(self, method: str, path: str, headers, body: bytes) -> bool:
        timestamp, nonce, signature = headers.get("X-Aegis-Timestamp", ""), headers.get("X-Aegis-Nonce", ""), headers.get("X-Aegis-Signature", "")
        try:
            skew = abs(time.time() - float(timestamp))
        except ValueError:
            return False
        if skew > MAX_SKEW_SECONDS or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce):
            return False
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode() + hashlib.sha256(body).hexdigest().encode()
        expected = hmac.new(self.token, message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return False
        with self.lock:
            now = time.time()
            self.nonces = {n: t for n, t in self.nonces.items() if now - t < MAX_SKEW_SECONDS * 2}
            if nonce in self.nonces:
                return False
            self.nonces[nonce] = now
        return True

    # ---------------------------------------------------------------- jobs
    def _validate(self, tool_name: str, args: dict) -> tuple:
        tool = TOOLS.get(tool_name)
        if tool is None:
            raise PolicyError(f"Unknown tool '{tool_name}'.")
        if not isinstance(args, dict) or set(args) - set(tool.args):
            raise PolicyError(f"Unsupported arguments for {tool_name}: {sorted(set(args) - set(tool.args))}.")
        clean = {k: v for k, v in args.items() if isinstance(v, (str, int, float)) and len(str(v)) <= 500}
        return tool, clean

    def request(self, tool_name: str, args: dict, requested_by: str) -> dict:
        tool, args = self._validate(tool_name, args)
        if tool.requires_confirmation:
            confirmation_id = secrets.token_urlsafe(16)
            description = f"{tool.description} {('Target: ' + str(args.get('path'))) if args.get('path') else ''}".strip()
            with self.lock:
                self.confirmations[confirmation_id] = {"tool": tool_name, "args": args, "requested_by": requested_by,
                                                       "expires": time.time() + CONFIRMATION_TTL_SECONDS}
            log_action("confirmation_requested", tool=tool_name, args=args, requested_by=requested_by)
            return {"status": "confirmation_required", "confirmation_id": confirmation_id, "tool": tool_name,
                    "description": description, "reason": tool.confirmation_reason, "expires_in_seconds": CONFIRMATION_TTL_SECONDS}
        return self._start(tool_name, args, requested_by)

    def approve(self, confirmation_id: str, approved_by: str, approve: bool) -> dict:
        with self.lock:
            pending = self.confirmations.pop(confirmation_id, None)
        if not pending or pending["expires"] < time.time():
            raise PolicyError("This confirmation has expired or was already used. Ask again.")
        if pending["requested_by"] != approved_by:
            raise PolicyError("Only the account that made the request can approve it.")
        log_action("confirmation_" + ("approved" if approve else "declined"), tool=pending["tool"], args=pending["args"], by=approved_by)
        if not approve:
            return {"status": "declined", "tool": pending["tool"]}
        return self._start(pending["tool"], pending["args"], approved_by, confirmed=True)

    def _start(self, tool_name: str, args: dict, requested_by: str, confirmed: bool = False) -> dict:
        with self.lock:
            now = time.time()
            self.jobs = {k: j for k, j in self.jobs.items() if j["status"] == "running" or now - j["created"] < JOB_TTL_SECONDS}
            if sum(1 for j in self.jobs.values() if j["status"] == "running") >= int(self.config.get("max_jobs", 2)):
                raise PolicyError("Another scan is already running. Wait for it to finish or cancel it.")
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "tool": tool_name, "args": args, "status": "running", "progress": "Starting…",
                   "fraction": None, "result": None, "error": None, "created": now, "finished": None,
                   "cancel": threading.Event(), "requested_by": requested_by}
            self.jobs[job_id] = job
        log_action("job_started", job=job_id, tool=tool_name, args=args, by=requested_by, confirmed=confirmed)
        threading.Thread(target=self._run, args=(job,), name=f"job-{tool_name}", daemon=True).start()
        return self.public(job)

    def _run(self, job: dict) -> None:
        def progress(message, fraction=None):
            job["progress"], job["fraction"] = message, fraction

        started = time.perf_counter()
        try:
            result = TOOLS[job["tool"]].func(job["args"], Context(self.policy, job["cancel"], progress))
            job["result"] = json.loads(redact(json.dumps(result, default=str)))
            job["status"] = "cancelled" if job["cancel"].is_set() else "completed"
        except PolicyError as error:
            job["status"], job["error"] = "rejected", str(error)
        except Exception as error:  # report a safe message; details stay in the local log
            job["status"], job["error"] = "failed", f"{type(error).__name__}: {redact(str(error))[:300]}"
            log_action("job_error", job=job["id"], detail=traceback.format_exc(limit=3)[-800:])
        job["finished"] = time.time()
        log_action("job_finished", job=job["id"], tool=job["tool"], status=job["status"],
                   seconds=round(time.perf_counter() - started, 2), error=job["error"])

    def public(self, job: dict) -> dict:
        return {k: job[k] for k in ("id", "tool", "args", "status", "progress", "fraction", "result", "error")} | {
            "elapsed_seconds": round((job["finished"] or time.time()) - job["created"], 1)}

    def job(self, job_id: str, requested_by: str) -> dict:
        job = self.jobs.get(job_id)
        if not job or job["requested_by"] != requested_by:
            raise KeyError(job_id)
        return job


class Handler(BaseHTTPRequestHandler):
    agent: Agent = None  # set at start-up
    server_version = "AegisLocalAgent/" + VERSION

    def log_message(self, fmt, *args):  # keep the console quiet; actions go to the JSONL log
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            return self._send(413, {"error": "Request too large."})
        body = self.rfile.read(length) if length else b""
        if not self.agent.verify(method, self.path, self.headers, body):
            log_action("auth_rejected", path=self.path, client=self.client_address[0])
            return self._send(401, {"error": "Invalid or missing request signature."})
        try:
            payload = json.loads(body) if body else {}
            user = str(payload.get("requested_by") or self.headers.get("X-Aegis-User") or "unknown")[:120]
            if method == "GET" and self.path == "/health":
                return self._send(200, {"status": "ok", "version": VERSION, "scope": "cybersecurity inspection only",
                                        "content_roots": [r.rstrip("\\") for r in self.agent.policy.content_roots],
                                        "tools": {name: {"description": t.description, "args": list(t.args),
                                                         "requires_confirmation": t.requires_confirmation} for name, t in TOOLS.items()}})
            if method == "POST" and self.path == "/jobs":
                return self._send(202, self.agent.request(str(payload.get("tool")), payload.get("args") or {}, user))
            match = re.fullmatch(r"/jobs/([0-9a-f]{32})(/cancel)?", self.path)
            if match:
                job = self.agent.job(match.group(1), user)
                if match.group(2) and method == "POST":
                    job["cancel"].set()
                    log_action("job_cancel_requested", job=job["id"], by=user)
                return self._send(200, self.agent.public(job))
            match = re.fullmatch(r"/confirmations/([A-Za-z0-9_-]{16,40})", self.path)
            if match and method == "POST":
                return self._send(202, self.agent.approve(match.group(1), user, bool(payload.get("approve"))))
            return self._send(404, {"error": "Not found."})
        except PolicyError as error:
            return self._send(403, {"error": str(error)})
        except KeyError:
            return self._send(404, {"error": "Job not found."})
        except (ValueError, TypeError) as error:
            return self._send(400, {"error": f"Bad request: {error}"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def main() -> int:
    parser = argparse.ArgumentParser(description="AEGIS local Windows inspection agent")
    parser.add_argument("--print-token", action="store_true", help="print the shared token for the backend and exit")
    args = parser.parse_args()
    config, token = load_config(), load_token()
    if args.print_token:
        print(token)
        return 0
    if config["host"] not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing to listen on a non-loopback address.")
    Handler.agent = Agent(config, token)
    server = ThreadingHTTPServer((config["host"], int(config["port"])), Handler)
    log_action("agent_started", host=config["host"], port=config["port"], scope="cybersecurity inspection only",
               content_roots=config["content_roots"], tools=sorted(TOOLS))
    print(f"AEGIS local agent {VERSION} listening on http://{config['host']}:{config['port']} "
          f"(cybersecurity inspection only; credential-scan folders: {', '.join(config['content_roots'])})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log_action("agent_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
