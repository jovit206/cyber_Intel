import tempfile
import unittest
from pathlib import Path

from backend.config import _load_secret_fallback


class SecretEnvironmentFallbackTests(unittest.TestCase):
    def test_empty_runtime_value_uses_ignored_env_file_secret(self):
        for name in ("VIRUSTOTAL_API_KEY", "THREATFOX_AUTH_KEY"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                env_file = Path(directory) / ".env"
                env_file.write_text(f"{name}=" + "a" * 64 + "\n", encoding="utf-8")
                environ = {name: ""}

                _load_secret_fallback(name, env_file, environ)

                self.assertEqual(environ[name], "a" * 64)

    def test_nonempty_runtime_value_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("THREATFOX_AUTH_KEY=file-value\n", encoding="utf-8")
            environ = {"THREATFOX_AUTH_KEY": "runtime-value"}

            _load_secret_fallback("THREATFOX_AUTH_KEY", env_file, environ)

            self.assertEqual(environ["THREATFOX_AUTH_KEY"], "runtime-value")

    def test_missing_or_empty_secret_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("THREATFOX_AUTH_KEY=\n", encoding="utf-8")
            environ = {"THREATFOX_AUTH_KEY": ""}

            _load_secret_fallback("THREATFOX_AUTH_KEY", env_file, environ)

            self.assertEqual(environ["THREATFOX_AUTH_KEY"], "")


if __name__ == "__main__":
    unittest.main()
