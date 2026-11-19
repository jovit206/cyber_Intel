from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def main() -> int:
    if not os.getenv("MONGO_URI", "").strip():
        raise SystemExit("Set MONGO_URI before running API integration tests.")
    test_dir = Path(__file__).resolve().parents[1] / "tests"
    return int(pytest.main([
        "-q",
        str(test_dir),
    ]))


if __name__ == "__main__":
    sys.exit(main())
