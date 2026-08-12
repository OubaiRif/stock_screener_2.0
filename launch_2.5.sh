#!/bin/bash
# launch_2.5.sh — Portable launcher for Stock Screener 2.5
# Works from any location as long as folder structure is intact.
# Usage: bash launch_2.5.sh [start|stop|restart|status]

APPDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$APPDIR/venv"
LOGFILE="$APPDIR/logs/streamlit_2.5.log"
PIDFILE="$APPDIR/streamlit_2.5.pid"
PORT=8502

_is_running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-start}" in

  start)
    if _is_running; then
      echo "Already running (PID $(cat "$PIDFILE")) on http://localhost:$PORT"
      exit 0
    fi
    # Kill anything stale on the port first
    lsof -t -i ":$PORT" | xargs kill -9 2>/dev/null || true
    sleep 1
    mkdir -p "$APPDIR/logs"
    cd "$APPDIR"
    source "$VENV/bin/activate"
    nohup streamlit run "$APPDIR/dashboard.py" \
      --server.port $PORT \
      --server.headless true \
      --browser.gatherUsageStats false \
      --server.runOnSave false \
      > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 2
    if _is_running; then
      echo "✓ Stock Screener 2.5 started (PID $(cat "$PIDFILE")) → http://localhost:$PORT"
      echo "  Logs: $LOGFILE"
    else
      echo "✗ Failed to start. Check logs: $LOGFILE"
      tail -20 "$LOGFILE"
    fi
    ;;

  stop)
    if _is_running; then
      kill "$(cat "$PIDFILE")" 2>/dev/null
      rm -f "$PIDFILE"
      echo "Stopped."
    else
      lsof -t -i ":$PORT" | xargs kill -9 2>/dev/null && echo "Stopped (port kill)." || echo "Nothing running."
    fi
    ;;

  restart)
    bash "${BASH_SOURCE[0]}" stop
    sleep 1
    bash "${BASH_SOURCE[0]}" start
    ;;

  status)
    if _is_running; then
      echo "Running (PID $(cat "$PIDFILE")) → http://localhost:$PORT"
    else
      echo "Not running."
    fi
    ;;

  *)
    echo "Usage: bash launch_2.5.sh [start|stop|restart|status]"
    ;;
esac
