#!/bin/bash
# setup_mac.sh — Stock Screener 2.0 setup for macOS
# Usage: bash setup_mac.sh

set -e

echo "=============================================="
echo "  Stock Screener 2.0 — macOS Setup"
echo "=============================================="

# ── Check Python ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found."
    echo "   Install via Homebrew: brew install python3"
    echo "   Or download from: https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PYTHON_VERSION found"

# ── Check for Homebrew (optional but recommended) ─────────────────────────────
if command -v brew &>/dev/null; then
    echo "✓ Homebrew found"
else
    echo "ℹ  Homebrew not found — continuing without it"
fi

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
chmod +x launch.sh stop.sh 2>/dev/null || true

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
