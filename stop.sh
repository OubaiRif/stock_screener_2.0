#!/bin/bash
# stop.sh — Stop the Stock Screener background process

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/logs/streamlit.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Stock Screener is not running (no PID file found)"
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm -f "$PID_FILE"
    echo "✓ Stock Screener stopped (PID $PID)"
else
    echo "Stock Screener was not running"
    rm -f "$PID_FILE"
fi
