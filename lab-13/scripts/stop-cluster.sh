#!/usr/bin/env bash
set -euo pipefail

CHAPTER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CHAPTER_DIR"

if [[ -f .env ]]; then
  source "$CHAPTER_DIR/scripts/load-env.sh"
  normalize_chapter_env "$CHAPTER_DIR/.env"
fi

if [[ "${1:-}" == "--volumes" ]]; then
  echo "This permanently deletes all three MongoDB data volumes."
  read -r -p "Type DELETE-CHAPTER13-DATA to continue: " confirmation
  if [[ "$confirmation" != "DELETE-CHAPTER13-DATA" ]]; then
    echo "Cancelled; persistent data was not removed."
    exit 1
  fi
  docker compose --env-file .env down --volumes
elif [[ $# -eq 0 ]]; then
  docker compose --env-file .env down
else
  echo "Usage: $0 [--volumes]" >&2
  exit 2
fi
