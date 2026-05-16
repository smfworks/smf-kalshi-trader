#!/bin/bash
# Kalshi Daily Run — Cron entry point
# Gabriel, CFO — SMF Works
#
# Run this from cron:
#   0 10 * * * cd ~/workspace && ./scripts/daily_run.sh
#
# Or manually:
#   ./scripts/daily_run.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(dirname "$SCRIPT_DIR")"

cd "$WORKSPACE"

echo "============================================================"
echo "KALSHI DAILY RUN — $(date '+%Y-%m-%d %H:%M %Z')"
echo "============================================================"

python3 scripts/daily_run.py "$@"

echo ""
echo "✓ Done — $(date '+%Y-%m-%d %H:%M %Z')"
