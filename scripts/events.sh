#!/usr/bin/env bash
# Incremental GDELT event collection + gold/event overlay chart.
#
# Usage:
#   ./scripts/events.sh                 # incremental fetch, then XAU overlay chart
#   ./scripts/events.sh --start 2026-08-01 --end 2026-10-31
#   PYTHON_BIN=/path/to/python3 ./scripts/events.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
mkdir -p logs output/charts

"$PYTHON_BIN" -m goldtrack events fetch --record-run "$@" >> logs/events.log 2>&1
"$PYTHON_BIN" -m goldtrack events chart --symbol XAU >> logs/events.log 2>&1
