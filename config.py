"""
config.py — Central configuration for the stock screener.
Edit this file to set your API keys and preferences.
"""

import os

# ── Version ───────────────────────────────────────────────────────────────────
APP_VERSION = "2.5.0"

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "screener.db")
LOG_PATH = os.path.join(BASE_DIR, "logs", "screener.log")

# ── API Keys (free tiers) ─────────────────────────────────────────────────────
# NewsAPI  → https://newsapi.org/register  (free: 100 req/day)
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# StockTwits → no key needed for public endpoints

# ── Data fetch settings ───────────────────────────────────────────────────────
HISTORY_DAYS_EXTENDED = 730   # ~2 years for EMA-200 etc.
INTRADAY_INTERVAL     = "5m"  # yfinance: 1m 5m 15m 30m 60m
INTRADAY_PERIOD       = "1d"  # fetch today's bars only

# ── Indicator windows ─────────────────────────────────────────────────────────
EMA_SHORT     = 20
EMA_MID       = 50
EMA_LONG      = 200
RSI_PERIOD    = 14
MACD_FAST     = 12
MACD_SLOW     = 26
MACD_SIGNAL   = 9
BB_PERIOD     = 20
BB_STD        = 2.0
ATR_PERIOD    = 14
ADX_PERIOD    = 14
STOCH_K       = 14
STOCH_D       = 3
WILLIAMS_R    = 14
OBV_EMA       = 20
ZSCORE_WINDOW = 20

# ── Composite score weights (must sum to 1.0) ─────────────────────────────────
SCORE_WEIGHT_TECHNICAL   = 0.50
SCORE_WEIGHT_FUNDAMENTAL = 0.25
SCORE_WEIGHT_SENTIMENT   = 0.25

# ── Strategies ────────────────────────────────────────────────────────────────
STRATEGIES = ["trend", "mean_reversion", "rubber_band", "breakout_volume", "unassigned"]

# Swing prediction horizons (trading days) per strategy
STRATEGY_HORIZONS = {
    "trend":            20,
    "mean_reversion":    7,
    "rubber_band":       5,
    "breakout_volume":   5,
    "unassigned":        5,
}

# ETFs: no fundamentals; macro rubric planned. Excluded from swing predictions only.
SWING_EXCLUDE_TICKERS = {"GLD", "IAU", "QQQ", "VOO"}

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"

# ── Demo mode ─────────────────────────────────────────────────────────────────
# Set DEMO_MODE=true in environment or .streamlit/secrets.toml for Streamlit Cloud
import os as _os
def _get_demo_mode():
    # Check environment variable first
    if _os.getenv("DEMO_MODE", "").lower() == "true":
        return True
    # Then check Streamlit secrets (Streamlit Cloud deployment)
    try:
        import streamlit as st
        return str(st.secrets.get("DEMO_MODE", "false")).lower() == "true"
    except Exception:
        return False

DEMO_MODE = _get_demo_mode()

# In demo mode, use the sanitized demo database
if DEMO_MODE:
    DB_PATH = _os.path.join(BASE_DIR, "demo_screener.db")
