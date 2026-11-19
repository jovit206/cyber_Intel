"""VirusTotal scan handling, tested without network access and without any real credential.

A dummy sentinel key is injected into settings; the HTTP client and database are fakes.
"""
import base64
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import api

SENTINEL_KEY = "ab" * 32            # 64 hex chars: well-formed dummy
MALFORMED_KEY = "Z" + "a" * 66 + "!"  # 68 chars with symbols: malformed dummy
IP_PAYLOAD = {"data": {"id": "8.8.8.8", "type": "ip_address", "attributes": {
    "last_analysis_stats": {"malicious": 3, "suspicious": 1, "harmless": 60, "undetected": 10},
    "country": "US", "as_owner": "Example Net", "last_analysis_date": 1700000000,
    "last_analysis_results": {"EngineA": {"category": "malicious", "result": "malware", "method": "blacklist"}},
}}}


class FakeCursor(list):
    def sort(self, key, direction=1):
        return FakeCursor(sorted(self, key=lambda d: d.get(key) or 0, reverse=direction < 0))

    def limit(self, n):
        return FakeCursor(self[:n])


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def find(self, query=None, *args, **kwargs):
        query = query or {}
        return FakeCursor(d for d in self.docs.values() if all(d.get(k) == v for k, v in query.items()))

    def find_one(self, query, *args, **kwargs):
        return self.docs.get(query.get("_id"))

    def update_one(self, flt, update, upsert=False):
        doc = self.docs.setdefault(flt["_id"], {"_id": flt["_id"]})
        doc.update(update.get("$set", {}))


class FakeDatabase:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, FakeCollection())


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.headers = {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttp:
    """Stands in for httpx.Client; records every request."""
    calls = []
    behaviour = None  # callable(url) -> FakeResponse | raises

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, **kwargs):
        FakeHttp.calls.append({"method": method, "url": url, "headers": kwargs.get("headers", {})})
        return FakeHttp.behaviour(url)


class VirusTotalHandlingTests(unittest.TestCase):
    def setUp(self):
        FakeHttp.calls = []
        FakeHttp.behaviour = lambda url: FakeResponse(200, IP_PAYLOAD)
        self.db = FakeDatabase()
        app = FastAPI()
        app.include_router(api.router)
        self.http = TestClient(app)
        self._patches = [
            patch.object(api, "get_database", lambda: self.db),
            patch.object(api.httpx, "Client", FakeHttp),
        ]
        for p in self._patches:
            p.start()
        self.use_key(SENTINEL_KEY)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        patch.stopall()

    def use_key(self, key):
        stub = SimpleNamespace(virustotal_api_key=key)
        patch.object(api, "settings", stub).start()
        import backend.config as config
        patch.object(config, "settings", stub).start()

    def scan(self, target):
        res = self.http.post("/api/virustotal/scan", json={"target": target})
        return res.status_code, res.json()

    # ---------------------------------------------------------- configuration
    def test_missing_key_is_a_clear_configuration_error_and_makes_no_request(self):
        self.use_key("")
        status, body = self.scan("8.8.8.8")
        self.assertEqual(status, 200)
        self.assertEqual(body["error_code"], "not_configured")
        self.assertIn("VIRUSTOTAL_API_KEY", body["error"])
        self.assertEqual(FakeHttp.calls, [])

    def test_key_status_classification_never_returns_the_key(self):
        from backend.config import virustotal_key_status
        self.assertEqual(virustotal_key_status(""), "missing")
        self.assertEqual(virustotal_key_status(SENTINEL_KEY), "ok")
        self.assertEqual(virustotal_key_status(MALFORMED_KEY), "unexpected_format")

    # ----------------------------------------------------------- auth errors
    def test_401_gives_authentication_error_without_credentials(self):
        FakeHttp.behaviour = lambda url: FakeResponse(401, {"error": {"code": "WrongCredentialsError"}})
        status, body = self.scan("8.8.8.8")
        self.assertEqual(body["error_code"], "auth_failed")
        self.assertIn("401", body["error"])
        self.assertIn(".env", body["error"])
        self.assertNotIn(SENTINEL_KEY, str(body))

    def test_401_with_malformed_key_explains_the_format_problem_without_echoing_it(self):
        self.use_key(MALFORMED_KEY)
        FakeHttp.behaviour = lambda url: FakeResponse(401, {})
        _, body = self.scan("8.8.8.8")
        self.assertEqual(body["error_code"], "auth_failed")
        self.assertIn("expected exactly 64 hexadecimal characters, found 68", body["error"])
        self.assertNotIn(MALFORMED_KEY, str(body))

    def test_malformed_key_with_embedded_valid_run_reports_extra_characters_only(self):
        key = "xy" + "ab" * 32 + "!!"          # 64-hex run with 2 chars before and 2 after
        self.use_key(key)
        FakeHttp.behaviour = lambda url: FakeResponse(401, {})
        _, body = self.scan("8.8.8.8")
        self.assertIn("2 extra character(s) before it and 2 after it", body["error"])
        self.assertNotIn(key, str(body))
        self.assertNotIn("ab" * 32, str(body))

    def test_other_failures_are_distinguished(self):
        cases = [
            (lambda u: FakeResponse(403, {}), "forbidden"),
            (lambda u: FakeResponse(429, {}), "rate_limited"),
            (lambda u: FakeResponse(400, {}), "bad_request"),
            (lambda u: FakeResponse(503, {}), "upstream_error"),
        ]
        for behaviour, code in cases:
            with self.subTest(code=code):
                FakeHttp.behaviour = behaviour
                self.db = FakeDatabase()
                _, body = self.scan("8.8.8.8")
                self.assertEqual(body["error_code"], code)

    def test_timeout_and_network_errors(self):
        def raise_timeout(url):
            raise httpx.TimeoutException("slow")

        def raise_connect(url):
            raise httpx.ConnectError("no route")

        FakeHttp.behaviour = raise_timeout
        self.assertEqual(self.scan("8.8.8.8")[1]["error_code"], "timeout")
        FakeHttp.behaviour = raise_connect
        self.assertEqual(self.scan("8.8.8.8")[1]["error_code"], "unavailable")

    def test_errors_are_not_cached(self):
        FakeHttp.behaviour = lambda url: FakeResponse(401, {})
        self.scan("8.8.8.8")
        self.assertEqual(len(self.db["virustotal_cache"].docs), 0)

    # --------------------------------------------------------------- results
    def test_success_returns_actual_verdict_and_is_cached_for_24_hours(self):
        status, body = self.scan("8.8.8.8")
        self.assertEqual(status, 200)
        self.assertNotIn("error", body)
        self.assertEqual(body["verdict"], "malicious")
        self.assertEqual(body["malicious_count"], 3)
        self.assertEqual(body["country"], "US")
        self.assertFalse(body["cached"])
        cached = next(iter(self.db["virustotal_cache"].docs.values()))
        ttl = cached["expires_at"] - cached["checked_at"]
        self.assertEqual(ttl, timedelta(hours=24))
        self.assertEqual(cached["result"], "found")

    def test_valid_cached_result_needs_no_second_request(self):
        self.scan("8.8.8.8")
        _, again = self.scan("8.8.8.8")
        self.assertEqual(len(FakeHttp.calls), 1)
        self.assertTrue(again["cached"])
        self.assertEqual(again["verdict"], "malicious")

    def test_expired_cache_triggers_a_new_request(self):
        self.scan("8.8.8.8")
        doc = next(iter(self.db["virustotal_cache"].docs.values()))
        doc["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.scan("8.8.8.8")
        self.assertEqual(len(FakeHttp.calls), 2)

    def test_404_is_not_reported_as_a_successful_scan_and_is_remembered(self):
        FakeHttp.behaviour = lambda url: FakeResponse(404, {})
        _, body = self.scan("example.com")
        self.assertIs(body["found"], False)
        self.assertNotIn("verdict", body)
        self.assertNotIn("error", body)
        _, again = self.scan("example.com")
        self.assertEqual(len(FakeHttp.calls), 1)
        self.assertIs(again["found"], False)
        self.assertTrue(again["cached"])
        self.assertEqual(self.http.get("/api/virustotal/recent").json()["count"], 0)

    def test_recent_handles_naive_datetimes_as_returned_by_mongodb(self):
        # pymongo returns naive UTC datetimes; a cached scan with expires_at used to make /recent return 500
        self.scan("8.8.8.8")
        for doc in self.db["virustotal_cache"].docs.values():
            for key in ("checked_at", "expires_at"):
                doc[key] = doc[key].replace(tzinfo=None)
        res = self.http.get("/api/virustotal/recent")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([i["indicator"] for i in res.json()["items"]], ["8.8.8.8"])
        # and a naive-but-expired entry is skipped, not an error
        for doc in self.db["virustotal_cache"].docs.values():
            doc["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
        self.assertEqual(self.http.get("/api/virustotal/recent").json()["count"], 0)

    def test_recent_history_lists_found_scans_only(self):
        self.scan("8.8.8.8")
        FakeHttp.behaviour = lambda url: FakeResponse(404, {})
        self.scan("unknown.example")
        results = sorted(d["result"] for d in self.db["virustotal_cache"].docs.values())
        self.assertEqual(results, ["found", "not_found"])
        recent = self.http.get("/api/virustotal/recent").json()
        self.assertEqual([i["indicator"] for i in recent["items"]], ["8.8.8.8"])

    # ------------------------------------------- endpoint + header per type
    def test_correct_endpoint_and_auth_header_per_indicator_type(self):
        url_target = "https://example.com/path?q=1"
        url_id = base64.urlsafe_b64encode(url_target.encode()).decode().rstrip("=")
        expectations = {
            "8.8.8.8": "/api/v3/ip_addresses/8.8.8.8",
            "example.com": "/api/v3/domains/example.com",
            "d41d8cd98f00b204e9800998ecf8427e": "/api/v3/files/d41d8cd98f00b204e9800998ecf8427e",
            url_target: f"/api/v3/urls/{url_id}",
        }
        for target, path in expectations.items():
            with self.subTest(target=target):
                self.db = FakeDatabase()
                FakeHttp.calls = []
                self.scan(target)
                self.assertEqual(len(FakeHttp.calls), 1)
                call = FakeHttp.calls[0]
                self.assertEqual(call["method"], "GET")
                self.assertTrue(call["url"].endswith(path), call["url"])
                self.assertEqual(call["headers"].get("x-apikey"), SENTINEL_KEY)

    # ------------------------------------------------------------ validation
    def test_invalid_input_is_rejected_before_any_request(self):
        for target in ("", "   ", "999.1.1.1", "not a domain", "d41d8cd98f00b204e9800998ecf8427",
                       "ftp://x.y", "http://", "a" * 3000):
            with self.subTest(target=target[:30]):
                status, body = self.http.post("/api/virustotal/scan", json={"target": target}).status_code, None
                if target.strip():
                    body = self.scan(target)[1]
                    self.assertEqual(body["error_code"], "invalid_indicator")
                else:
                    self.assertEqual(status, 400)
        self.assertEqual(FakeHttp.calls, [])

    def test_all_supported_types_pass_validation(self):
        for target in ("1.1.1.1", "2001:db8::1", "sub.example.co.uk", "https://example.com/a",
                       "d41d8cd98f00b204e9800998ecf8427e", "da39a3ee5e6b4b0d3255bfef95601890afd80709", "a" * 64):
            with self.subTest(target=target[:30]):
                kind = api._detect_indicator_type(target, None)
                self.assertIsNone(api._validate_indicator(kind, target))

    # ----------------------------------------------------- credential hygiene
    def test_key_never_appears_in_any_response_or_log(self):
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("aegis.test").debug("start")
            responses = []
            for behaviour in (lambda u: FakeResponse(200, IP_PAYLOAD), lambda u: FakeResponse(401, {}),
                              lambda u: FakeResponse(404, {}), lambda u: FakeResponse(429, {})):
                FakeHttp.behaviour = behaviour
                self.db = FakeDatabase()
                responses.append(str(self.scan("8.8.8.8")))
            responses.append(str(self.http.get("/api/virustotal/recent").json()))
            responses.append(str(self.http.get("/api/virustotal/report", params={"indicator": "8.8.8.8"}).json()))
        blob = " ".join(responses) + " ".join(logs.output)
        self.assertNotIn(SENTINEL_KEY, blob)


if __name__ == "__main__":
    unittest.main()
