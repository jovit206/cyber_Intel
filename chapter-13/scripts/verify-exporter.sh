#!/usr/bin/env bash
set -euo pipefail

CHAPTER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CHAPTER_DIR"

docker compose --env-file .env --profile tools run --build --rm --no-deps \
  --entrypoint python failover-test /app/verify-exporter.py
