"""Read-only integration checks against a running server (default http://localhost:8000).

Never calls POST /api/lab9/run-latency and never triggers the partition simulation.
Usage: python test/test_integration.py [base_url]
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as res:
        return res.getcode(), res.headers.get("content-type", ""), res.read()


urls = [
    ("/", "text/html"),
    ("/config.js", "javascript"),
    ("/app.js", "javascript"),
    ("/cross-source.js", "javascript"),
    ("/reliability.js", "javascript"),
    ("/health", "application/json"),
    ("/api/lab8/status", "application/json"),
    ("/api/lab9/latency", "application/json"),
    ("/api/lab9/partition", "application/json"),
    ("/api/metrics", "application/json"),
    ("/api/events?page=1&limit=5", "application/json"),
    ("/api/cross-source/summary", "application/json"),
]

print("=" * 60)
print("INTEGRATION & REGRESSION TEST SUITE")
print("=" * 60)

for path, expected_type in urls:
    code, ctype, _ = get(path)
    assert code == 200, f"{path} expected 200 got {code}"
    assert expected_type in ctype, f"{path} expected {expected_type} in {ctype}"
    print(f"[PASS] {path:<30} -> {code} ({ctype.split(';')[0]})")

# Removed assets must stay gone
for path in ("/lab8.js", "/lab9.js"):
    try:
        get(path)
    except urllib.error.HTTPError as err:
        assert err.code == 404, f"{path} expected 404 got {err.code}"
        print(f"[PASS] {path:<30} -> 404 (removed)")
    else:
        raise AssertionError(f"{path} is still being served")

html = get("/")[2].decode()
required = [
    "THREAT INTELLIGENCE", "SYSTEM MONITORING", "PERFORMANCE &amp; RELIABILITY",
    "Live Threat Stream", "Top Threats", "Report Corpus", "Search &amp; Enrich",
    "Cross-Source Analysis", "Working Set Monitor", "Write Concern Latency", "Network Reliability",
    'data-view="latency"', 'data-view="partition"',
    'id="view-latency"', 'id="view-partition"', 'id="latency-content"', 'id="partition-content"',
]
forbidden = ["Lab 7", "Lab 8", "Lab 9", "LAB 7", "LAB 8", "LAB 9", "lab8-", "lab9-", "lab8.js", "lab9.js"]
for item in required:
    assert item in html, f"Missing from index.html: {item}"
for item in forbidden:
    # comments are not visible, so only flag text outside <!-- -->
    stripped = html
    while "<!--" in stripped:
        start = stripped.index("<!--")
        end = stripped.index("-->", start) + 3
        stripped = stripped[:start] + stripped[end:]
    assert item not in stripped, f"Unexpected legacy label in index.html: {item}"
import re
for asset in ("style.css", "app.js", "cross-source.js", "reliability.js"):
    assert re.search(rf'(?:href|src)="{re.escape(asset)}\?v=[0-9a-f]{{10}}"', html), f"{asset} is not cache-busted"
print(f"[PASS] index.html markup: {len(required)} required present, {len(forbidden)} legacy labels absent, assets cache-busted")

partition = json.loads(get("/api/lab9/partition")[2])
for key in ("baseline_writes", "partition_w1_writes", "partition_majority_writes", "cluster_readiness", "simulation"):
    assert key in partition, f"/api/lab9/partition missing {key}"
assert partition["simulation"]["automatic_execution"] is False
for key in ("detected_primary", "eligible_partition_target", "healthy", "members"):
    assert key in partition["cluster_readiness"], f"cluster_readiness missing {key}"
print("[PASS] /api/lab9/partition response shape")

latency = json.loads(get("/api/lab9/latency")[2])
assert isinstance(latency["configurations"], list) and latency["configurations"]
for cfg in latency["configurations"]:
    for key in ("label", "status", "avg_display", "operation_count"):
        assert key in cfg, f"latency configuration missing {key}"
print("[PASS] /api/lab9/latency response shape")

print("=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)
