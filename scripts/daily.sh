#!/usr/bin/env bash
# Daily collection used by cron / launchd.
#
# Usage:
#   ./scripts/daily.sh                 # incremental fetch + report + charts
#   PYTHON_BIN=/path/to/python3 ./scripts/daily.sh --full
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
mkdir -p logs output/charts
exec "$PYTHON_BIN" -m goldtrack run --record-run "$@" >> logs/daily.log 2>&1
