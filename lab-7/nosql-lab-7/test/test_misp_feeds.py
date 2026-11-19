import unittest
from unittest.mock import MagicMock

import httpx

from backend.collectors.misp_feeds import (
    FEEDS,
    _manifest_fingerprint,
    _get_json,
    normalize_misp_feed_event,
    sync_feed,
)


class MispFeedNormalizationTests(unittest.TestCase):
    def test_event_metadata_and_attributes_are_kept_separately(self):
        event_uuid = "d2b51bb7-2cba-45df-89d4-123456789abc"
        event, attributes = normalize_misp_feed_event(
            {
                "Event": {
                    "uuid": event_uuid,
                    "info": "Observed command-and-control infrastructure",
                    "date": "2026-09-03",
                    "timestamp": "1788480188",
                    "threat_level_id": "2",
                    "Orgc": {"name": "Example CERT"},
                    "Tag": [{"name": "tlp:white"}],
                    "Attribute": [{
                        "uuid": "34a97ac1-2a5e-40cf-a312-abcdef123456",
                        "type": "ip-dst",
                        "value": "203.0.113.10",
                        "category": "Network activity",
                        "to_ids": True,
                        "Tag": [{"name": "confidence-level:50"}],
                    }],
                    "Galaxy": [{
                        "type": "threat-actor",
                        "GalaxyCluster": [{
                            "uuid": "0371a6d9-9255-4c3b-8f6a-123456789abc",
                            "value": "Example Actor",
                            "meta": {"aliases": ["Example Group"]},
                        }],
                    }],
                },
            },
            feed_name="botvrij",
            feed_url=FEEDS["botvrij"],
            event_uuid=event_uuid,
            manifest_metadata={
                "info": "Manifest title",
                "date": "2026-09-03",
                "timestamp": 1788480188,
                "Orgc": {"name": "Manifest Publisher"},
                "Tag": [{"name": "type:OSINT"}],
            },
        )

        self.assertEqual(event["misp_uuid"], event_uuid)
        self.assertEqual(event["source_feed_name"], "botvrij")
        self.assertEqual(event["publisher"], "Example CERT")
        self.assertEqual(event["attribute_count"], 1)
        self.assertEqual(event["galaxies"][0]["value"], "Example Actor")
        self.assertNotIn("Attribute", event)
        self.assertEqual(len(attributes), 1)
        self.assertEqual(attributes[0]["event_uuid"], event_uuid)
        self.assertEqual(attributes[0]["type"], "ip-dst")
        self.assertEqual(attributes[0]["value"], "203.0.113.10")
        self.assertTrue(attributes[0]["to_ids"])

    def test_manifest_comparison_detects_only_changed_records(self):
        metadata = {"timestamp": 10, "info": "Original"}
        same = {
            "manifest_timestamp": 10,
            "manifest_fingerprint": _manifest_fingerprint(metadata),
        }
        changed = {
            "manifest_timestamp": 11,
            "manifest_fingerprint": _manifest_fingerprint(metadata),
        }

        from backend.collectors.misp_feeds import _event_needs_fetch

        self.assertFalse(_event_needs_fetch(same, metadata))
        self.assertTrue(_event_needs_fetch(changed, metadata))
        self.assertTrue(_event_needs_fetch(None, metadata))


class MispFeedSyncTests(unittest.TestCase):
    def test_unchanged_manifest_event_is_not_downloaded_again(self):
        event_uuid = "d2b51bb7-2cba-45df-89d4-123456789abc"
        metadata = {"timestamp": 1788480188, "info": "Same event"}
        events = MagicMock()
        events.find.return_value = [{
            "misp_uuid": event_uuid,
            "manifest_timestamp": metadata["timestamp"],
            "manifest_fingerprint": _manifest_fingerprint(metadata),
        }]
        sync_state = MagicMock()
        database = MagicMock()
        database.__getitem__.side_effect = lambda name: {
            "events": events,
            "cti_sync_state": sync_state,
        }[name]
        requests = []

        def respond(request):
            requests.append(request.url.path)
            return httpx.Response(200, json={event_uuid: metadata})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = sync_feed(
                database,
                "botvrij",
                FEEDS["botvrij"],
                client=client,
                workers=1,
            )

        self.assertEqual(result["fetched_events"], 0)
        self.assertEqual(result["inserted_events"], 0)
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].endswith("/manifest.json"))
        sync_state.update_one.assert_called_once()

    def test_retryable_server_error_retries_and_succeeds(self):
        calls = []
        delays = []

        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = _get_json(client, "https://feed.example/manifest.json", sleep=delays.append)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(delays, [1.0])


if __name__ == "__main__":
    unittest.main()
