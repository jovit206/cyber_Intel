"""Read-only integration checks against a running server (default http://localhost:8000).

Never calls POST /api/lab9/run-latency and never triggers the partition simulation.
Usage: python test/test_integration.py [base_url]
"""
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
from urllib.error import HTTPError

import os

# Only honour a positional base URL when run as a script; under pytest sys.argv holds pytest's own arguments.
if __name__ == "__main__" and len(sys.argv) > 1:
    BASE = sys.argv[1].rstrip("/")
else:
    BASE = os.getenv("INTEGRATION_BASE_URL", "http://localhost:8000").rstrip("/")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as res:
        return res.getcode(), res.headers.get("content-type", ""), res.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urls = [
    ("/account", "text/html"),
    ("/account.js", "javascript"),
    ("/account-summary.js", "javascript"),
    ("/banking-finance.js", "javascript"),
    ("/config.js", "javascript"),
    ("/app.js", "javascript"),
    ("/cross-source.js", "javascript"),
    ("/reliability.js", "javascript"),
    ("/admin.js", "javascript"),
    ("/manager.js", "javascript"),
    ("/health", "application/json"),
]

print("=" * 60)
print("INTEGRATION & REGRESSION TEST SUITE")
print("=" * 60)

for path, expected_type in urls:
    code, ctype, _ = get(path)
    assert code == 200, f"{path} expected 200 got {code}"
    assert expected_type in ctype, f"{path} expected {expected_type} in {ctype}"
    print(f"[PASS] {path:<30} -> {code} ({ctype.split(';')[0]})")

# The dashboard redirects to the sign-in page before any protected markup is returned.
try:
    urllib.request.build_opener(NoRedirect).open(BASE + "/", timeout=10)
except HTTPError as err:
    assert err.code == 303 and err.headers.get("location", "").startswith("/account"), f"/ expected auth redirect, got {err.code}"
    print("[PASS] /                              -> 303 sign-in required")
else:
    raise AssertionError("Anonymous dashboard request was not redirected")

# Removed assets must stay gone
for path in ("/lab8.js", "/lab9.js"):
    try:
        get(path)
    except urllib.error.HTTPError as err:
        assert err.code == 404, f"{path} expected 404 got {err.code}"
        print(f"[PASS] {path:<30} -> 404 (removed)")
    else:
        raise AssertionError(f"{path} is still being served")

html = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(encoding="utf-8")
required = [
    "THREAT INTELLIGENCE", "SYSTEM MONITORING", "PERFORMANCE &amp; RELIABILITY",
    "Live Threat Stream", "Top Threats", "Report Corpus", "Search &amp; Enrich",
    "Cross-Source Analysis", "Working Set Monitor", "Write Concern Latency", "Network Reliability",
    "Admin Dashboard", 'id="view-admin"',
    'data-view="latency"', 'data-view="partition"',
    'id="view-latency"', 'id="view-partition"', 'id="latency-content"', 'id="partition-content"',
    'id="financial-activity"', 'id="financial-filters"', 'id="financial-summary"',
    'id="financial-detail-dialog"', 'id="banking-plans"', 'id="banking-add-money"',
    'id="banking-topup-dialog"', 'id="banking-confirm-dialog"', 'Banking Operations',
]
forbidden = ["Security Center", "Database Security", "view-security", "security.js", "Lab 7", "Lab 8", "Lab 9", "LAB 7", "LAB 8", "LAB 9", "lab8-", "lab9-", "lab8.js", "lab9.js"]
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
assert html.count('id="financial-activity"') == 1, "Personal financial history must have one Banking Operations section"
assert 'id="account-summary"' not in html, "Duplicate account/plan summary must not appear on the threat dashboard"
assert html.count('class="sidebar-brand" href="/account"') == 1, "Platform logo must open the signed-in account area"
assert 'class="nav-link" href="/account"' not in html, "Separate Account sidebar item must be removed"
assert html.count('class="nav-link" data-view="lab10-banking"') == 1, "Sidebar must contain one Banking Operations link"
import re
for asset in ("style.css", "app.js", "cross-source.js", "reliability.js", "lab10.js", "banking-finance.js", "admin.js", "manager.js"):
    assert re.search(rf'(?:href|src)="{re.escape(asset)}(?:\?v=[0-9a-f]{{10}})?"', html), f"{asset} is not referenced"
assert 'href="/account"' in html and 'id="upgrade-dialog"' in html
print(f"[PASS] index.html markup: {len(required)} required present, {len(forbidden)} legacy labels absent, assets cache-busted")

try:
    get("/security.js")
except urllib.error.HTTPError as err:
    assert err.code == 404, f"/security.js expected 404 after removing the frontend, got {err.code}"
else:
    raise AssertionError("The removed Security Center frontend script is still being served")

account_html = get("/account")[2].decode()
for text in ("Your Account", "Threat Intelligence Platform", "Welcome back", "ACCOUNT DETAILS", "account-signout", "Open Banking Operations", 'id="subscriptions"', 'id="account-plan-cards"'):
    assert text in account_html, f"Missing from separate account page: {text}"
assert 'id="account-register" class="account-form" hidden' in account_html, "Registration must be hidden on initial auth view"
assert 'Back to Threat Intelligence' not in account_html
for financial_ui in ("Add Money", "Choose your monthly plan", "account-plans", "topup-dialog", 'id="financial-activity"', "Transaction History"):
    assert financial_ui not in account_html, f"Financial UI should only appear in Banking Operations: {financial_ui}"
assert 'id="view-subscriptions"' not in html, "Account page must remain separate from the main dashboard"
plans = json.loads(get("/api/subscriptions/plans")[2])
prices = {plan["name"]: (plan["price"], plan["monthly_credits"]) for plan in plans["plans"]}
assert prices == {"free": (0, 100), "plus": (299, 1000), "pro": (799, 5000)}, prices
print("[PASS] Account-only page and Free/Plus/Pro plan catalog in Banking Operations")
try:
    get("/api/subscriptions/me")
except urllib.error.HTTPError as err:
    assert err.code == 401, f"Anonymous account endpoint expected 401, got {err.code}"
else:
    raise AssertionError("Anonymous account endpoint unexpectedly succeeded")
print("[PASS] Subscription profile requires authentication")
try:
    get("/api/subscriptions/entitlements")
except urllib.error.HTTPError as err:
    assert err.code == 401, f"Plan entitlements expected 401, got {err.code}"
else:
    raise AssertionError("Plan entitlements unexpectedly succeeded anonymously")
for path, method, payload in (
    ("/api/subscriptions/purchase-quote/plus", "GET", None),
    ("/api/subscriptions/financial-activity", "GET", None),
    ("/api/subscriptions/financial-activity/WLT-ANON-1", "GET", None),
    ("/api/subscriptions/wallet/top-up", "POST", b'{"amount":100,"idempotency_key":"anonymous-test-01"}'),
):
    request = urllib.request.Request(BASE + path, data=payload, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as err:
        assert err.code == 401, f"{method} {path} expected 401, got {err.code}"
    else:
        raise AssertionError(f"Anonymous {method} {path} unexpectedly succeeded")
print("[PASS] Purchase quote and wallet top-up require authentication")

for path in ("/api/meta", "/api/events?page=1&limit=5", "/api/lab8/status", "/api/lab9/latency",
             "/api/security/overview",
             "/api/lab9/partition", "/api/lab10/overview", "/api/virustotal/recent", "/api/cross-source/summary",
             "/api/admin/dashboard", "/api/admin/users", "/api/admin/transactions",
             "/api/admin/subscriptions", "/api/admin/wallets/summary", "/api/admin/audit"):
    request = urllib.request.Request(BASE + path)
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as err:
        assert err.code == 401, f"Anonymous {path} expected 401, got {err.code}"
    else:
        raise AssertionError(f"Anonymous protected route unexpectedly succeeded: {path}")
print("[PASS] Threat, VirusTotal, Experiment 8/9/10 APIs require authentication")

print("=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)
