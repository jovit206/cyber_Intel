#!/bin/bash
# Packages the chapter into Chapter02_Submission.zip, excluding caches.
set -e
cd "$(dirname "$0")/.."
rm -f Chapter02_Submission.zip
zip -r Chapter02_Submission.zip Chapter02_Submission \
    -x "*/__pycache__/*" "*.pyc" "*/.DS_Store" >/dev/null
echo "[OK] Chapter02_Submission.zip"
unzip -l Chapter02_Submission.zip | tail -3
