"""
engine/indicators.py — Computes all technical indicators using pandas-ta.
Reads from price_history, writes to indicators table.
"""
import logging, sys, os
from datetime import datetime

import pandas as pd
try:
    import pandas_ta as ta
    PANDAS_TA_AVAILABLE = True
except ImportError:
    ta = None
    PANDAS_TA_AVAILABLE = False
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import (
    EMA_SHORT, EMA_MID, EMA_LONG,
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD, ATR_PERIOD, ADX_PERIOD,
    STOCH_K, STOCH_D, WILLIAMS_R, OBV_EMA, ZSCORE_WINDOW
)
from engine.db import get_conn
from engine.fetcher import load_daily_history

logger = logging.getLogger(__name__)


def compute_indicators(ticker: str, df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Compute all indicators for a ticker.
    If df is None, loads from DB. Returns df with indicator columns appended.
    """
    ticker = ticker.upper()

    if df is None:
        df = load_daily_history(ticker)

    if df.empty or len(df) < 30:
        logger.warning("Not enough data to compute indicators for %s", ticker)
        return df

    # ── Rename for pandas-ta compatibility (needs Title Case) ────────────────
    df = df.copy()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col.lower() in df.columns:
            df.rename(columns={col.lower(): col}, inplace=True)

    # ── Trend indicators ──────────────────────────────────────────────────────
    df[f"ema_{EMA_SHORT}"]  = ta.ema(df["Close"], length=EMA_SHORT)
    df[f"ema_{EMA_MID}"]    = ta.ema(df["Close"], length=EMA_MID)
    df[f"ema_{EMA_LONG}"]   = ta.ema(df["Close"], length=EMA_LONG)

    macd_df = ta.macd(df["Close"], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    if macd_df is not None and not macd_df.empty:
        # pandas-ta MACD output order: MACD, MACDh (histogram), MACDs (signal)
        cols = list(macd_df.columns)
        macd_col   = next((c for c in cols if "MACD_" in c and "MACDh" not in c and "MACDs" not in c), cols[0])
        hist_col   = next((c for c in cols if "MACDh" in c), cols[1])
        signal_col = next((c for c in cols if "MACDs" in c), cols[2])
        df["macd"]        = macd_df[macd_col]
        df["macd_hist"]   = macd_df[hist_col]
        df["macd_signal"] = macd_df[signal_col]

    adx_df = ta.adx(df["High"], df["Low"], df["Close"], length=ADX_PERIOD)
    if adx_df is not None and not adx_df.empty:
        df["adx"] = adx_df.iloc[:, 0]   # ADX column

    df["obv"]     = ta.obv(df["Close"], df["Volume"])
    df["obv_ema"] = ta.ema(df["obv"], length=OBV_EMA)

    # ── Oscillators / Mean reversion ─────────────────────────────────────────
    df["rsi"] = ta.rsi(df["Close"], length=RSI_PERIOD)

    bb_df = ta.bbands(df["Close"], length=BB_PERIOD, std=BB_STD)
    if bb_df is not None and not bb_df.empty:
        df["bb_lower"] = bb_df.iloc[:, 0]
        df["bb_mid"]   = bb_df.iloc[:, 1]
        df["bb_upper"] = bb_df.iloc[:, 2]
        df["bb_pct_b"] = bb_df.iloc[:, 4] if bb_df.shape[1] > 4 else None

    # Z-score of close vs rolling mean
    roll_mean = df["Close"].rolling(ZSCORE_WINDOW).mean()
    roll_std  = df["Close"].rolling(ZSCORE_WINDOW).std()
    df["zscore"] = (df["Close"] - roll_mean) / roll_std

    stoch_df = ta.stoch(df["High"], df["Low"], df["Close"], k=STOCH_K, d=STOCH_D)
    if stoch_df is not None and not stoch_df.empty:
        df["stoch_k"] = stoch_df.iloc[:, 0]
        df["stoch_d"] = stoch_df.iloc[:, 1]

    df["williams_r"] = ta.willr(df["High"], df["Low"], df["Close"], length=WILLIAMS_R)
    df["atr"]        = ta.atr(df["High"], df["Low"], df["Close"], length=ATR_PERIOD)

    # ── Volume context ────────────────────────────────────────────────────────
    avg_vol_20       = df["Volume"].rolling(20).mean()
    df["rel_volume"] = df["Volume"] / avg_vol_20

    # VWAP approximation (daily — uses typical price * volume / cumsum volume)
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    df["vwap"]    = (typical_price * df["Volume"]).cumsum() / df["Volume"].cumsum()

    # ── Support / Resistance ──────────────────────────────────────────────────
    df["support_20d"]    = df["Low"].rolling(20).min()
    df["resistance_20d"] = df["High"].rolling(20).max()

    return df


def save_indicators(ticker: str, df: pd.DataFrame) -> int:
    """
    Persist the latest indicator row (most recent date) to the indicators table.
    Upserts on (ticker, date). Returns number of rows upserted.
    """
    ticker = ticker.upper()
    if df.empty:
        return 0

    # We save the last N rows (avoid re-computing what's already saved)
    conn    = get_conn()
    latest  = conn.execute(
        "SELECT MAX(date) AS d FROM indicators WHERE ticker = ?", (ticker,)
    ).fetchone()["d"]
    conn.close()

    if latest:
        cutoff = pd.to_datetime(latest)
        df = df[df.index > cutoff]

    if df.empty:
        logger.debug("No new indicator rows to save for %s", ticker)
        return 0

    col_map = {
        f"ema_{EMA_SHORT}": "ema_20", f"ema_{EMA_MID}": "ema_50", f"ema_{EMA_LONG}": "ema_200",
        "macd": "macd", "macd_signal": "macd_signal", "macd_hist": "macd_hist",
        "adx": "adx", "obv": "obv", "obv_ema": "obv_ema",
        "rsi": "rsi",
        "bb_upper": "bb_upper", "bb_mid": "bb_mid", "bb_lower": "bb_lower", "bb_pct_b": "bb_pct_b",
        "zscore": "zscore", "stoch_k": "stoch_k", "stoch_d": "stoch_d",
        "williams_r": "williams_r", "atr": "atr",
        "rel_volume": "rel_volume", "vwap": "vwap",
        "support_20d": "support_20d", "resistance_20d": "resistance_20d",
    }

    conn  = get_conn()
    count = 0
    for date, row in df.iterrows():
        date_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)
        vals = {db_col: _safe(row.get(src_col)) for src_col, db_col in col_map.items()}
        db_cols    = ["ticker", "date"] + list(vals.keys())
        db_vals    = [ticker, date_str] + list(vals.values())
        placeholders = ", ".join(["?"] * len(db_vals))
        update     = ", ".join(f"{c} = excluded.{c}" for c in vals.keys())
        conn.execute(f"""
            INSERT INTO indicators ({", ".join(db_cols)}) VALUES ({placeholders})
            ON CONFLICT(ticker, date) DO UPDATE SET {update}
        """, db_vals)
        count += 1

    conn.commit()
    conn.close()
    logger.info("Saved %d indicator rows for %s", count, ticker)
    return count


def refresh_indicators(ticker: str) -> pd.DataFrame:
    """Convenience: fetch history → compute → save. Returns df with indicators."""
    df = load_daily_history(ticker)
    df = compute_indicators(ticker, df)
    save_indicators(ticker, df)
    return df


def get_latest_indicators(ticker: str) -> dict:
    """Return the most recent indicator row as a dict."""
    ticker = ticker.upper()
    conn   = get_conn()
    row    = conn.execute("""
        SELECT * FROM indicators WHERE ticker = ? ORDER BY date DESC LIMIT 1
    """, (ticker,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def _safe(val):
    """Convert numpy types / NaN to Python-native or None."""
    if val is None:
        return None
    try:
        if np.isnan(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    return val


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    df = refresh_indicators("VOO")
    ind = get_latest_indicators("VOO")
    print(ind)
