#!/bin/bash
# launch_2.5.sh — Start Stock Screener 2.5 in the background on port 8502.
# Logs go to ~/Desktop/stock_screener_2.5/logs/streamlit_2.5.log
# Usage: bash launch_2.5.sh
#   Stop: bash launch_2.5.sh stop

APPDIR=~/Desktop/stock_screener_2.5
VENV=~/Desktop/stock_screener/venv
LOGFILE=$APPDIR/logs/streamlit_2.5.log
PIDFILE=$APPDIR/streamlit_2.5.pid
PORT=8502

case "${1:-start}" in

  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
      echo "Already running (PID $(cat $PIDFILE)) on port $PORT"
      exit 0
    fi
    mkdir -p "$APPDIR/logs"
    cd "$APPDIR"
    source "$VENV/bin/activate"
    nohup streamlit run dashboard.py \
      --server.port $PORT \
      --server.headless true \
      --browser.gatherUsageStats false \
      --server.runOnSave false \
      > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Stock Screener 2.5 started (PID $!) on http://localhost:$PORT"
    echo "Logs: $LOGFILE"
    ;;

  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat $PIDFILE)" 2>/dev/null && echo "Stopped." || echo "Process not found."
      rm -f "$PIDFILE"
    else
      echo "No PID file found — trying port kill..."
      kill $(lsof -t -i :$PORT) 2>/dev/null && echo "Stopped." || echo "Nothing on port $PORT."
    fi
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  *)
    echo "Usage: bash launch_2.5.sh [start|stop|restart]"
    ;;
esac
