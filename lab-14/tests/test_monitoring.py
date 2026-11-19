import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitoring_dashboard as monitoring


class MonitoringTests(unittest.TestCase):
    def test_server_status_metrics_and_missing_wiredtiger_values(self):
        client = Mock()
        client.admin.command.return_value = {
            "connections": {"current": 3, "available": 97},
            "opcounters": {"insert": 2, "query": 5},
            "wiredTiger": {"cache": {
                "bytes currently in the cache": 800,
                "bytes read into cache": 200,
                "pages requested from the cache": 100,
                "pages read into cache": 15,
            }},
        }
        metrics = monitoring.retrieve_server_metrics(client)
        self.assertEqual(metrics["connections_current"], 3)
        self.assertEqual(metrics["connections_available"], 97)
        self.assertEqual(metrics["opcounters"]["insert"], 2)
        self.assertEqual(metrics["cache_bytes_current"], 800)
        self.assertEqual(metrics["cache_bytes_read"], 200)
        self.assertEqual(metrics["cache_hit_ratio_percent"], 85)

        client.admin.command.return_value = {"connections": {}, "opcounters": {}}
        metrics = monitoring.retrieve_server_metrics(client)
        self.assertIsNone(metrics["cache_bytes_current"])
        self.assertIsNone(metrics["cache_hit_ratio_percent"])

    def test_standalone_is_reported_as_not_applicable(self):
        client = Mock()
        client.admin.command.side_effect = monitoring.OperationFailure(
            "not running with --replset", code=76
        )
        result = monitoring.retrieve_replica_status(client)
        self.assertFalse(result["applicable"])

    def test_replication_lag_and_cache_alerts(self):
        now = datetime.now(timezone.utc)
        client = Mock()
        client.admin.command.return_value = {
            "members": [
                {"name": "mongo1:27017", "stateStr": "PRIMARY", "optimeDate": now},
                {"name": "mongo2:27017", "stateStr": "SECONDARY",
                 "optimeDate": now - timedelta(seconds=15)},
            ]
        }
        replica = monitoring.retrieve_replica_status(client)
        self.assertEqual(replica["primary"], "mongo1:27017")
        self.assertAlmostEqual(replica["lags"][0]["lag_seconds"], 15, delta=0.1)
        alerts = monitoring.build_alerts({"cache_hit_ratio_percent": 79}, replica)
        self.assertEqual({alert.metric for alert in alerts}, {
            "cache_hit_ratio_percent",
            "replication_lag_seconds:mongo2:27017",
        })

    def test_standalone_table_and_log_file(self):
        table = monitoring.format_metrics_table(
            {"connections_current": 2, "connections_available": 10, "opcounters": {},
             "cache_bytes_current": None, "cache_bytes_read": None,
             "cache_hit_ratio_percent": None},
            {"applicable": False},
        )
        self.assertIn("not applicable", table)
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "logs" / "metrics.log"
            logger = monitoring.configure_logging(log_path)
            logger.info("snapshot test")
            for handler in logger.handlers:
                handler.flush()
            self.assertTrue(log_path.is_file())
            self.assertIn("snapshot test", log_path.read_text(encoding="utf-8"))
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()

    @patch.object(monitoring.requests, "post")
    def test_webhook_payload_cooldown_and_http_error(self, post):
        logger = Mock(spec=logging.Logger)
        alerter = monitoring.WebhookAlerter(
            "https://example.invalid/webhook", cooldown_seconds=300, logger=logger
        )
        alert = monitoring.Alert("cache_hit_ratio_percent", 70, 80, "2026-01-01T00:00:00Z")
        post.return_value = Mock(status_code=200)
        self.assertTrue(alerter.send(alert))
        self.assertFalse(alerter.send(alert))
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertIn("cache_hit_ratio_percent", payload["text"])
        self.assertIn("70.00", payload["text"])
        self.assertIn("80.00", payload["text"])
        self.assertIn(alert.timestamp, payload["text"])
        self.assertEqual(post.call_args.kwargs["timeout"], 10)

        post.reset_mock()
        post.side_effect = requests.ConnectionError("mock failure")
        failed_alerter = monitoring.WebhookAlerter(
            "https://example.invalid/webhook", cooldown_seconds=300, logger=logger
        )
        self.assertFalse(failed_alerter.send(alert))
        self.assertFalse(failed_alerter.send(alert))
        post.assert_called_once()

    @patch.object(monitoring.requests, "post")
    def test_webhook_timeout_and_http_status_errors_are_handled(self, post):
        logger = Mock(spec=logging.Logger)
        alert = monitoring.Alert("replication_lag_seconds:mongo2", 12, 10, "2026-01-01T00:00:00Z")
        post.side_effect = requests.Timeout("mock timeout")
        timeout_alerter = monitoring.WebhookAlerter(
            "https://example.invalid/webhook", logger=logger
        )
        self.assertFalse(timeout_alerter.send(alert))

        response = Mock(status_code=503)
        http_error = requests.HTTPError("mock HTTP status")
        http_error.response = response
        post.side_effect = http_error
        http_alerter = monitoring.WebhookAlerter(
            "https://example.invalid/webhook", logger=logger
        )
        self.assertFalse(http_alerter.send(alert))
        self.assertEqual(post.call_count, 2)

    def test_interval_must_be_positive(self):
        with self.assertRaises(ValueError):
            monitoring.run_monitoring(Mock(), Mock(), Mock(), interval_seconds=0)

    @patch.object(monitoring, "monitor_once")
    @patch.object(monitoring.time, "sleep", side_effect=KeyboardInterrupt)
    def test_monitoring_uses_thirty_second_default_interval(self, sleep, monitor_once):
        with self.assertRaises(KeyboardInterrupt):
            monitoring.run_monitoring(Mock(), Mock(), Mock())
        monitor_once.assert_called_once()
        sleep.assert_called_once_with(30)


if __name__ == "__main__":
    unittest.main()
