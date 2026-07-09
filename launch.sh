#!/bin/bash
# launch.sh — Start Stock Screener 2.0 silently in the background
# Opens browser automatically. No terminal window stays open.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/logs/streamlit.pid"
LOG_FILE="$SCRIPT_DIR/logs/streamlit.log"
PORT=8501

cd "$SCRIPT_DIR"
mkdir -p "$SCRIPT_DIR/logs"

# ── Check if already running ──────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stock Screener already running (PID $PID)"
        echo "Opening browser..."
        xdg-open "http://localhost:$PORT" 2>/dev/null &
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# ── Start Streamlit in background ─────────────────────────────────────────────
source "$SCRIPT_DIR/venv/bin/activate"

nohup streamlit run "$SCRIPT_DIR/dashboard.py" \
    --server.port $PORT \
    --server.headless true \
    --browser.gatherUsageStats false \
    --server.runOnSave false \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "✓ Stock Screener started (PID $(cat $PID_FILE))"

# ── Wait for server to be ready then open browser ────────────────────────────
echo "Waiting for server..."
for i in $(seq 1 15); do
    sleep 1
    if curl -s "http://localhost:$PORT" > /dev/null 2>&1; then
        echo "✓ Server ready — opening browser"
        xdg-open "http://localhost:$PORT" 2>/dev/null &
        exit 0
    fi
done

echo "⚠ Server took too long to start. Check logs/streamlit.log"
echo "  Try opening http://localhost:$PORT manually"
