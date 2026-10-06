import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.collectors.cti import SourceApiError, _request_json, store_records
from backend.routes.api import LIVE_CTI_SOURCES, router
from backend.services.normalization import deterministic_source_id, normalize_timestamp


class NormalizationTests(unittest.TestCase):
    def test_timestamp_is_normalized_to_utc(self):
        self.assertEqual(
            normalize_timestamp("2025-01-01T12:00:00-05:00"),
            "2025-01-01T17:00:00Z",
        )

    def test_timestamp_rejects_invalid_input(self):
        self.assertIsNone(normalize_timestamp("not-a-timestamp"))

    def test_source_id_is_stable_for_indicator_records(self):
        first = deterministic_source_id("virustotal", None, "domain", "EXAMPLE.COM")
        second = deterministic_source_id("virustotal", None, "domain", "example.com")
        self.assertEqual(first, second)


class CollectorTests(unittest.TestCase):
    def test_store_records_deduplicates_by_source_and_id(self):
        database = MagicMock()
        database.__getitem__.return_value.bulk_write.return_value = SimpleNamespace(upserted_count=1)
        records = [
            {"source": "malwarebazaar", "source_id": "sample-1", "indicator_type": "sha256"},
            {"source": "malwarebazaar", "source_id": "sample-1", "indicator_type": "sha256"},
        ]

        result = store_records(database, records)

        self.assertEqual(result, {
            "records_received": 2,
            "records_inserted": 1,
            "duplicates_skipped": 1,
        })
        database.__getitem__.return_value.bulk_write.assert_called_once()

    def test_http_429_preserves_retry_after(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "60"}, json={"error": "limited"})
        )
        with httpx.Client(transport=transport) as client:
            with self.assertRaises(SourceApiError) as error:
                _request_json(client, "GET", "https://example.test/feed")
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.retry_after, "60")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)

    @patch("backend.routes.api.get_database")
    def test_top_threats_reads_only_live_cti_events(self, get_database):
        event = {
            "_id": "misp:record-1",
            "source": "misp",
            "info": "Observed security event",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        collection = MagicMock()
        collection.find.return_value.sort.return_value.limit.return_value = [event]
        database = MagicMock()
        database.__getitem__.return_value = collection
        get_database.return_value = database

        response = self.client.get("/api/threats/top?limit=12")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [event])
        collection.find.assert_called_once_with({"source": {"$in": LIVE_CTI_SOURCES}})

    @patch("backend.routes.api.get_database")
    def test_events_endpoint_excludes_non_live_sources(self, get_database):
        collection = MagicMock()
        collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []
        collection.count_documents.return_value = 0
        database = MagicMock()
        database.__getitem__.return_value = collection
        get_database.return_value = database

        response = self.client.get("/api/events")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [])
        self.assertEqual(response.json()["total"], 0)
        collection.find.assert_called_once_with({
            "source": {"$in": LIVE_CTI_SOURCES},
        })

    @patch("backend.routes.api.get_database")
    def test_threat_details_do_not_fall_back_to_seeded_threats(self, get_database):
        collection = MagicMock()
        collection.find_one.return_value = None
        database = MagicMock()
        database.__getitem__.return_value = collection
        get_database.return_value = database

        response = self.client.get("/api/threats/legacy-fixture")

        self.assertEqual(response.status_code, 404)
        collection.find_one.assert_called_once_with({
            "_id": "legacy-fixture",
            "source": {"$in": LIVE_CTI_SOURCES},
        })

    @patch("backend.main.close_database")
    @patch("backend.main.start_collectors")
    @patch("backend.main._run_misp_feed")
    @patch("backend.main._run_galaxy_sync")
    @patch("backend.main._run_report_sync")
    @patch("backend.main.connect_database")
    def test_app_startup_and_frontend_routes(
        self,
        connect_database,
        run_report_sync,
        run_galaxy_sync,
        run_misp_feed,
        start_collectors,
        close_database,
    ):
        from backend.main import app

        database = MagicMock()
        database["events"].list_indexes.return_value = []
        database.client.admin.command.return_value = {"ok": 1}
        connect_database.return_value = database

        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            self.assertIn("AEGIS_API_BASE_URL", client.get("/config.js").text)
            self.assertIn("<html", client.get("/").text.lower())
        start_collectors.assert_called_once()
        close_database.assert_called_once()


if __name__ == "__main__":
    unittest.main()
