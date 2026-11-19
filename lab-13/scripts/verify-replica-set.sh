#!/usr/bin/env bash
set -euo pipefail

CHAPTER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CHAPTER_DIR"

if [[ ! -f .env ]]; then
  echo "Missing lab-13/.env." >&2
  exit 1
fi

source "$CHAPTER_DIR/scripts/load-env.sh"
normalize_chapter_env "$CHAPTER_DIR/.env"
set -a
source "$CHAPTER_DIR/.env"
set +a

docker compose --env-file .env exec -T mongo1 mongosh --quiet \
  --username "$MONGO_INITDB_ROOT_USERNAME" \
  --password "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --file /dev/stdin < scripts/verify-replica-set.js
