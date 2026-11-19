#!/usr/bin/env bash
# Launcher kept so the documented command `bash labs/lab9/lab_02_simulate_partition.sh` keeps working.
# The implementation (prechecks, confirmation, isolation, observation, recovery, report) is Python:
#     labs/lab9/lab_02_simulate_partition.py        (same flags: --dry-run, --target, --phase, --confirm ...)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for py in python python3 py; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    exec "$py" "$DIR/lab_02_simulate_partition.py" "$@"
  fi
done
echo "Python 3.8+ is required to run the Lab 9.2 script (none found on PATH)." >&2
exit 2
