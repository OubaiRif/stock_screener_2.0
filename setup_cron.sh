#!/bin/bash
# setup_cron.sh — Install the nightly cron job
# Cron runs in UTC. NYSE closes at 16:00 ET = 21:00 UTC.
# We run at 21:15 UTC (safely after close) regardless of your local timezone.
# Run once: bash setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python"
LOG="$SCRIPT_DIR/logs/cron.log"

CRON_JOB="15 21 * * 1-5 $PYTHON $SCRIPT_DIR/run.py nightly >> $LOG 2>&1"

(crontab -l 2>/dev/null | grep -q "run.py nightly") && {
    echo "Cron job already installed."
    exit 0
}

(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
echo "✓ Cron job installed: runs at 21:15 UTC (after NYSE close), Mon–Fri"
echo "  This works correctly regardless of your local timezone."
