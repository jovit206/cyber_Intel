#!/usr/bin/env bash
set -euo pipefail

CHAPTER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CHAPTER_DIR"

if [[ ! -f .env ]]; then
  echo "Missing chapter-13/.env. Copy .env.example to .env and set independent random hex passwords." >&2
  exit 1
fi

source "$CHAPTER_DIR/scripts/load-env.sh"
normalize_chapter_env "$CHAPTER_DIR/.env"
set -a
# The local .env file is trusted, shell-compatible KEY=value configuration.
source "$CHAPTER_DIR/.env"
set +a

for name in MONGO_INITDB_ROOT_USERNAME MONGO_INITDB_ROOT_PASSWORD MONGO_EXPORTER_USERNAME MONGO_EXPORTER_PASSWORD; do
  if [[ -z "${!name:-}" || "${!name}" == REPLACE_* ]]; then
    echo "$name must be set to a non-placeholder value in chapter-13/.env." >&2
    exit 1
  fi
done

if [[ ! -s "$CHAPTER_DIR/secrets/mongodb-keyfile" ]]; then
  "$CHAPTER_DIR/scripts/generate-keyfile.sh"
fi
docker compose --env-file .env up -d --wait --wait-timeout 180 mongo1 mongo2 mongo3

docker compose --env-file .env exec -T mongo1 mongosh --quiet \
  --username "$MONGO_INITDB_ROOT_USERNAME" \
  --password "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --file /dev/stdin < scripts/init-replica-set.js

echo "Waiting for a PRIMARY and two SECONDARY members..."
deadline=$((SECONDS + 180))
until docker compose --env-file .env exec -T mongo1 mongosh --quiet \
  --username "$MONGO_INITDB_ROOT_USERNAME" \
  --password "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin \
  --file /dev/stdin < scripts/verify-replica-set.js >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    docker compose --env-file .env logs --tail 100 mongo1 mongo2 mongo3
    echo "Replica set did not elect one PRIMARY and two SECONDARY members within 180 seconds." >&2
    exit 1
  fi
  sleep 5
done

docker compose --env-file .env exec -T mongo1 mongosh --quiet \
  --username "$MONGO_INITDB_ROOT_USERNAME" \
  --password "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --file /dev/stdin < scripts/create-exporter-user.js
docker compose --env-file .env up -d --force-recreate mongodb-exporter
"$CHAPTER_DIR/scripts/verify-replica-set.sh"
echo "Prometheus metrics endpoint: http://127.0.0.1:19216/metrics"
