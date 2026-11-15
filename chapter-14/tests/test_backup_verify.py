import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_verify as backup


class BackupTests(unittest.TestCase):
    def test_scoped_backup_command_and_timestamped_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                return Mock(returncode=0, stdout="", stderr="")

            with patch.object(backup.shutil, "which", side_effect=lambda name: name):
                result = backup.run_backup(
                    "mongodb://example.invalid",
                    root,
                    database="chapter14_restore_test_run_1000",
                    collection=backup.TEST_COLLECTION,
                    run_process=fake_run,
                )
            self.assertTrue(result.success)
            self.assertTrue(result.directory.name.startswith("chapter14_backup_"))
            command = commands[0]
            self.assertEqual(Path(command[0]).name, "mongodump")
            self.assertIn("--uri", command)
            self.assertIn("--out", command)
            self.assertIn("--db", command)
            self.assertIn("--collection", command)
            self.assertTrue((result.directory / backup.OWNERSHIP_MARKER).is_file())

    def test_failed_backup_is_not_marked_as_owned_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(backup.shutil, "which", side_effect=lambda name: name):
                result = backup.run_backup(
                    "mongodb://example.invalid",
                    Path(temp_dir),
                    run_process=Mock(return_value=Mock(returncode=2, stdout="", stderr="failed")),
                )
            self.assertFalse(result.success)
            marker = json.loads((result.directory / backup.OWNERSHIP_MARKER).read_text(encoding="utf-8"))
            self.assertEqual(marker["status"], "failed")

    def test_retention_removes_only_expired_marked_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = root / "chapter14_backup_old"
            old.mkdir()
            (old / backup.OWNERSHIP_MARKER).write_text(json.dumps({
                "format": backup.MARKER_FORMAT,
                "version": 1,
                "status": "success",
                "created_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            }), encoding="utf-8")
            failed = root / "chapter14_backup_failed_old"
            failed.mkdir()
            (failed / backup.OWNERSHIP_MARKER).write_text(json.dumps({
                "format": backup.MARKER_FORMAT,
                "version": 1,
                "status": "failed",
                "created_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            }), encoding="utf-8")
            unrelated = root / "customer-backup"
            unrelated.mkdir()
            expired = backup.cleanup_expired_backups(root)
            self.assertEqual(set(expired), {old.resolve(), failed.resolve()})
            self.assertTrue(unrelated.exists())

    def test_restore_guard_rejects_non_test_namespace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                backup._restore(
                    "mongodb://example.invalid",
                    Path(temp_dir),
                    Path(temp_dir),
                    "production",
                    "users",
                )

    def test_scoped_dump_removes_uri_database_to_avoid_conflicting_db_option(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backup_dir = root / "chapter14_backup_test"
            with patch.object(backup.shutil, "which", side_effect=lambda name: name):
                command, effective_uri = backup._tool_command(
                    "mongodump",
                    "mongodb://user:password@example.invalid/admin?authSource=admin",
                    root,
                    backup_dir,
                    "--db",
                    "chapter14_restore_test_case_1000",
                    "--out",
                )
            self.assertEqual(effective_uri, "mongodb://user:password@example.invalid/?authSource=admin")
            self.assertIn(effective_uri, command)

    def test_namespace_restore_removes_default_uri_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            namespace = "chapter14_restore_test_case_1000.backup_verification"
            with patch.object(backup.shutil, "which", side_effect=lambda name: name):
                command, effective_uri = backup._tool_command(
                    "mongorestore",
                    "mongodb://user:password@example.invalid/admin?authSource=admin",
                    root,
                    root / "chapter14_backup_test",
                    "--drop",
                    f"--nsInclude={namespace}",
                )
            self.assertEqual(effective_uri, "mongodb://user:password@example.invalid/?authSource=admin")
            self.assertIn("--drop", command)
            self.assertIn(f"--nsInclude={namespace}", command)

    def test_docker_fallback_preserves_full_backup_uri_database_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            host_uri = "mongodb://source:source-secret@example.invalid/appdb?authSource=admin"
            tools_uri = "mongodb://tools:tools-secret@mongo.example.invalid/admin?authSource=admin"
            with patch.dict(os.environ, {
                "MONGODB_TOOLS_DOCKER_IMAGE": "mongo:7.0.45",
                "MONGODB_TOOLS_DOCKER_NETWORK": "test-network",
                "MONGODB_TOOLS_URI": tools_uri,
            }), patch.object(
                backup.shutil,
                "which",
                side_effect=lambda name: "docker.exe" if name == "docker" else None,
            ):
                command, effective_uri = backup._tool_command(
                    "mongodump", host_uri, root, root / "chapter14_backup_test", "--out"
                )
            self.assertEqual(effective_uri, "mongodb://tools:tools-secret@mongo.example.invalid/appdb?authSource=admin")
            self.assertIn("--network", command)

    def test_datetime_verification_accepts_pymongo_naive_utc_values(self):
        expected = datetime(2026, 1, 1, tzinfo=timezone.utc)
        actual = datetime(2026, 1, 1)
        self.assertTrue(backup._same_utc_datetime(actual, expected))

    @patch.object(backup.schedule, "every")
    def test_scheduler_registers_an_hourly_backup(self, every):
        every.return_value.hour.do = Mock()
        backup.register_hourly_backup("mongodb://example.invalid", Path("."), Mock())
        every.return_value.hour.do.assert_called_once()


if __name__ == "__main__":
    unittest.main()
