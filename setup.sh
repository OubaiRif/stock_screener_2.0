#!/bin/bash
# setup.sh — Full installer for Stock Screener 2.0
# Run once after cloning or copying the folder:
#   bash setup.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="python3"
PORT=8501

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Stock Screener 2.0 — Setup Installer  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Step 1: Python check ──────────────────────────────────────────────────────
echo "[ 1/6 ] Checking Python..."
if ! command -v python3 &>/dev/null; then
    echo "✗ Python3 not found. Install it with: sudo apt install python3"
    exit 1
fi
PYVER=$(python3 --version)
echo "✓ $PYVER found"

# ── Step 2: Create virtual environment ───────────────────────────────────────
echo "[ 2/6 ] Setting up virtual environment..."
cd "$SCRIPT_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

source "$SCRIPT_DIR/venv/bin/activate"

# ── Step 3: Install dependencies ─────────────────────────────────────────────
echo "[ 3/6 ] Installing dependencies..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --quiet
    echo "✓ Dependencies installed"
else
    echo "✗ requirements.txt not found"
    exit 1
fi

# ── Step 4: Initialize database ──────────────────────────────────────────────
echo "[ 4/6 ] Initializing database..."
mkdir -p "$SCRIPT_DIR/logs"
python3 run.py init
echo "✓ Database ready"

# ── Step 5: Install cron job ──────────────────────────────────────────────────
echo "[ 5/6 ] Setting up nightly pipeline..."
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
LOG="$SCRIPT_DIR/logs/cron.log"
CRON_JOB="15 21 * * 1-5 $PYTHON_BIN $SCRIPT_DIR/run.py nightly >> $LOG 2>&1"

if crontab -l 2>/dev/null | grep -q "run.py nightly"; then
    echo "✓ Cron job already installed"
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "✓ Cron job installed (runs Mon-Fri at 21:15 UTC / 4:15pm ET)"
fi

# ── Step 6: Create desktop shortcut ──────────────────────────────────────────
echo "[ 6/6 ] Creating desktop shortcut..."
DESKTOP_FILE="$HOME/Desktop/Stock Screener.desktop"
cat > "$DESKTOP_FILE" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Stock Screener 2.0
Comment=Personal stock screening and trading assistant
Exec=bash $SCRIPT_DIR/launch.sh
Icon=utilities-system-monitor
Terminal=false
Categories=Finance;
StartupNotify=true
DESKTOP
chmod +x "$DESKTOP_FILE"
# Trust the desktop file on Ubuntu
gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
echo "✓ Desktop shortcut created"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✓ Setup complete!                      ║"
echo "╠══════════════════════════════════════════╣"
echo "║   To start:  bash launch.sh              ║"
echo "║   To stop:   bash stop.sh                ║"
echo "║   Or double-click the desktop shortcut   ║"
echo "╚══════════════════════════════════════════╝"
echo ""
