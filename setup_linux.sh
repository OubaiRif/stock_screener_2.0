#!/bin/bash
# setup_linux.sh — Stock Screener 2.0 setup for Linux
# Usage: bash setup_linux.sh

set -e

echo "=============================================="
echo "  Stock Screener 2.0 — Linux Setup"
echo "=============================================="

# ── Check Python ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found. Install with:"
    echo "   sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PYTHON_VERSION found"

# ── Create virtual environment ────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo "→ Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

source venv/bin/activate

# ── Install dependencies ──────────────────────────────────────────────────────
echo "→ Installing dependencies (this may take a few minutes)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✓ Dependencies installed"

# ── Initialize database ───────────────────────────────────────────────────────
echo "→ Initializing database..."
python3 run.py init
echo "✓ Database initialized"

# ── Create logs directory ─────────────────────────────────────────────────────
mkdir -p logs
echo "✓ Logs directory ready"

# ── Make scripts executable ───────────────────────────────────────────────────
chmod +x launch.sh stop.sh setup_cron.sh 2>/dev/null || true

echo ""
echo "=============================================="
echo "  Setup complete!"
echo "=============================================="
echo ""
echo "  Next steps:"
echo "  1. Add your NewsAPI key in config.py (optional)"
echo "  2. Add tickers:  python3 run.py add AAPL MSFT SPY"
echo "  3. Fetch data:   python3 run.py nightly"
echo "  4. Launch app:   bash launch.sh"
echo "  5. Open:         http://localhost:8501"
echo ""
