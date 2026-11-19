#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHAPTER_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
KEYFILE="${1:-$CHAPTER_DIR/secrets/mongodb-keyfile}"
FORCE="${2:-}"

if [[ $# -gt 2 || ( $# -eq 2 && "$FORCE" != "--force" ) ]]; then
  echo "Usage: $0 [keyfile-path] [--force]" >&2
  exit 2
fi

if [[ -e "$KEYFILE" && "$FORCE" != "--force" ]]; then
  echo "Keyfile already exists; refusing to rotate a live cluster key. Use --force only during a planned rotation." >&2
  exit 1
fi

mkdir -p -- "$(dirname -- "$KEYFILE")"
umask 077
TEMP_FILE="$(mktemp "$(dirname -- "$KEYFILE")/.mongodb-keyfile.XXXXXX")"
trap 'rm -f -- "$TEMP_FILE"' EXIT

openssl rand -base64 756 | tr -d '\n' > "$TEMP_FILE"
chmod 0400 "$TEMP_FILE"
mv -f -- "$TEMP_FILE" "$KEYFILE"
trap - EXIT

echo "Created MongoDB keyfile at $KEYFILE with mode 0400."
