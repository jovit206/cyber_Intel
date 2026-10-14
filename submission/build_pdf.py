#!/usr/bin/env python3
"""Build the submission PDF.

Inputs (both optional, written only after the facts are verified):
  submission/publication.json   {"repository": "user/lab7-threat-intel", "tags": ["v1.0.0","latest"], "verified_at": "...", "digest": "sha256:..."}
  submission/verification.json  {"generated_at": "...", "checks": [{"name": "...", "result": "PASS|FAIL|MANUAL", "detail": "..."}]}

Without publication.json the Docker Hub URL is printed as PENDING; nothing is invented.
Rendering uses Microsoft Edge/Chrome in headless mode (no extra Python packages).

    python submission/build_pdf.py
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PDF = HERE / "Lab7-8-9_Docker_Submission.pdf"
OUT_HTML = HERE / "submission.html"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def load(name: str) -> dict:
    try:
        return json.loads((HERE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def e(text: object) -> str:
    return html.escape(str(text))


def main() -> int:
    pub = load("publication.json")
    ver = load("verification.json")
    repo = pub.get("repository")
    tags = pub.get("tags") or []
    published = bool(repo and tags and pub.get("verified_at"))
    primary_tag = (tags[0] if tags else "latest")
    image = f"{repo}:{primary_tag}" if published else "jovit206/threat-intelligence-platform:latest"
    url = f"https://hub.docker.com/r/{repo}" if published else None

    if published:
        hub_block = f"""
        <table class="kv">
          <tr><th>Docker Hub repository</th><td><a href="{e(url)}">{e(url)}</a></td></tr>
          <tr><th>Published tags</th><td>{e(', '.join(tags))}</td></tr>
          <tr><th>Image digest</th><td><code>{e(pub.get('digest', 'not recorded'))}</code></td></tr>
          <tr><th>Verified on Docker Hub</th><td>{e(pub.get('verified_at'))}</td></tr>
          <tr><th>Pull command</th><td><code>docker pull {e(image)}</code></td></tr>
        </table>"""
    else:
        hub_block = """
        <div class="pending"><strong>Docker Hub URL: PENDING.</strong> The image has not been published yet, so no
        repository URL is stated here. After <code>docker login</code> and a verified push, rebuild this document
        with <code>python submission/build_pdf.py</code>.</div>
        <table class="kv">
          <tr><th>Planned repository</th><td><code>DOCKERHUB_USERNAME/lab7-threat-intel</code> (tags <code>v1.0.0</code>, <code>latest</code>)</td></tr>
          <tr><th>Build from source meanwhile</th><td><code>docker compose up -d --build</code></td></tr>
        </table>"""

    checks = ver.get("checks", [])
    if checks:
        rows = "".join(
            f"<tr><td>{e(c.get('name'))}</td><td class='res {e(str(c.get('result', '')).lower())}'>{e(c.get('result'))}</td>"
            f"<td>{e(c.get('detail', ''))}</td></tr>" for c in checks)
        verification = (f"<p>Recorded {e(ver.get('generated_at', ''))}. Results are copied from the commands actually run; "
                        f"items marked MANUAL require a person to run them.</p>"
                        f"<table class='grid'><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        verification = "<p>No verification record was supplied.</p>"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Lab 7-8-9 Docker submission</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  body {{ font: 10.5pt/1.45 "Segoe UI", Arial, sans-serif; color: #1d1a1f; }}
  h1 {{ font-size: 21pt; margin: 0 0 4pt; color: #392c3b; }}
  h2 {{ font-size: 13pt; margin: 16pt 0 6pt; padding-bottom: 3pt; border-bottom: 1.5px solid #a65349; color: #392c3b; }}
  .sub {{ color: #6f606d; margin-bottom: 10pt; }}
  code {{ font-family: Consolas, "Courier New", monospace; font-size: 9.2pt; background: #f1ebe3; padding: 1pt 4pt; border-radius: 3pt; }}
  pre {{ font: 9pt/1.4 Consolas, "Courier New", monospace; background: #f6f1ea; border: 1px solid #e4d7c9; border-radius: 5pt; padding: 7pt 9pt; white-space: pre-wrap; margin: 4pt 0 8pt; }}
  table {{ border-collapse: collapse; width: 100%; margin: 4pt 0 8pt; }}
  .kv th {{ text-align: left; width: 32%; padding: 5pt 8pt; background: #f6f1ea; border: 1px solid #e4d7c9; vertical-align: top; }}
  .kv td {{ padding: 5pt 8pt; border: 1px solid #e4d7c9; }}
  .grid th, .grid td {{ border: 1px solid #e4d7c9; padding: 4pt 7pt; text-align: left; vertical-align: top; font-size: 9.5pt; }}
  .grid th {{ background: #392c3b; color: #fff; }}
  .res {{ font-weight: 700; white-space: nowrap; }} .res.pass {{ color: #2e7d32; }} .res.fail {{ color: #c62828; }} .res.manual {{ color: #8b602f; }}
  .pending {{ background: #fff4e0; border: 1px solid #e0b060; border-left: 4px solid #c47a00; padding: 8pt 10pt; border-radius: 4pt; margin: 4pt 0; }}
  .safety {{ background: #fdf0ee; border: 1px solid #e3b5ae; border-left: 4px solid #a65349; padding: 8pt 12pt; border-radius: 4pt; }}
  ul {{ margin: 4pt 0 6pt 16pt; padding: 0; }} li {{ margin: 2pt 0; }}
  a {{ color: #79536f; }}
</style></head><body>
<h1>Threat Intelligence Platform: Labs 7, 8 and 9</h1>
<div class="sub">Docker deployment and submission information &middot; generated {e(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}</div>

<h2>Project description</h2>
<p>A FastAPI / MongoDB threat-intelligence dashboard (Lab 7) running on a three-member MongoDB 7.0 replica set
<code>rs0</code> (Lab 8), together with the Lab 9 write-concern latency benchmark and network-partition
experiment. It is delivered as one application image plus the official <code>mongo:7.0</code> image, orchestrated by
Docker Compose with persistent named volumes.</p>

<h2>Docker Hub</h2>
{hub_block}

<h2>Start the complete application</h2>
<pre>cp .env.example .env
mkdir -p labs/lab9/results
# set APP_IMAGE={e(image)} in .env, then:
docker compose pull
docker compose up -d
# open http://localhost:8000</pre>
<p>Compose starts the three MongoDB members, initialises <code>rs0</code> only if needed (idempotent), then starts the
application. Data lives in named volumes and survives <code>stop</code>, <code>down</code> and upgrades.</p>

<h2>Features</h2>
<ul>
  <li><b>Lab 7</b>: Live Threat Stream, Top Threats, Report Corpus, Search &amp; Enrich (VirusTotal, 24 h cache), Cross-Source Analysis, Working Set Monitor (live WiredTiger cache telemetry).</li>
  <li><b>Lab 8</b>: three-member replica set, member and primary monitoring, failover observer (<code>labs/lab8</code>).</li>
  <li><b>Lab 9.1</b>: write-concern latency for <code>w:1</code>, <code>w:majority</code>, <code>w:all</code> (numeric <code>w</code> = member count) with <code>j:false</code>/<code>j:true</code>, 1,000 inserts each; JSON/CSV artifacts; historical vs live results separated in the UI.</li>
  <li><b>Lab 9.2</b>: confirmed, reversible network-partition simulation with prechecks, timestamps, honest write outcomes and verified recovery.</li>
</ul>

<h2>Lab 9.1 experiment (manual)</h2>
<pre>docker compose exec lab7-backend python labs/lab9/lab_01_write_concern_latency.py
# options: --docs 200   --dry-run   --replace-historical</pre>
<p>Reports are written to <code>labs/lab9/results/lab_01_&lt;timestamp&gt;.json</code> and <code>.csv</code>.
MongoDB has no <code>w: "all"</code>; it is run as <code>w:3</code> and the mapping is printed and saved.</p>

<h2>Lab 9.2 experiment (manual)</h2>
<pre>python labs/lab9/lab_02_simulate_partition.py --dry-run   # prechecks only
python labs/lab9/lab_02_simulate_partition.py             # type the confirmation text when asked</pre>
<p>Report: <code>labs/lab9/results/lab_02_partition_&lt;timestamp&gt;.json</code>. Losing the majority is a separate,
separately confirmed phase (<code>--phase majority-loss</code>).</p>

<h2>Safety notes</h2>
<div class="safety"><ul>
  <li>The partition experiment and the live benchmark never run automatically (build, start-up, tests, publication).</li>
  <li>Only a secondary is isolated by default; the primary is never selected. Containers, volumes, replica-set configuration and documents are not removed.</li>
  <li><code>docker compose down -v</code> deletes database volumes and is not part of any procedure.</li>
  <li>No secrets are in the image: <code>.env</code> is excluded; the VirusTotal key is read by the backend at run time only.</li>
  <li>MongoDB is bound to loopback host ports and runs without authentication inside the internal network.</li>
</ul></div>

<h2>Verification summary</h2>
{verification}
</body></html>"""

    OUT_HTML.write_text(page, encoding="utf-8")
    browser = next((b for b in BROWSERS if Path(b).exists()), None) or shutil.which("msedge") or shutil.which("chromium")
    if not browser:
        print(f"HTML written to {OUT_HTML}; no Chrome/Edge found to print it to PDF.", file=sys.stderr)
        return 2
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()], check=True, capture_output=True, timeout=120)
    print(f"PDF written: {OUT_PDF} ({OUT_PDF.stat().st_size:,} bytes)  docker hub published: {published}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
