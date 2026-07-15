"""
engine/accuracy.py — Tracks prediction accuracy over time.
Runs nightly after market close:
  1. Fetches actual closing prices for today
  2. Compares against predictions made for today
  3. Logs results to accuracy_log table
  4. Provides summary stats per ticker and per strategy
"""
import logging, sys, os
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.db import get_conn

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# NIGHTLY SCORING
# ══════════════════════════════════════════════════════════════════════════════

def score_predictions(target_date: str = None) -> list:
    """
    Compare predictions vs actual closes for a given date.
    Default: today. Returns list of result dicts.
    """
    if target_date is None:
        target_date = date.today().isoformat()

    conn = get_conn()
    preds = conn.execute("""
        SELECT p.ticker, p.prediction_type, p.price_mid, p.signal,
               p.composite_score, s.strategy
        FROM   predictions p
        JOIN   stocks s ON s.ticker = p.ticker
        WHERE  p.date = ?
        AND    p.price_mid IS NOT NULL
    """, (target_date,)).fetchall()
    conn.close()

    if not preds:
        logger.info("No predictions found for %s", target_date)
        return []

    tickers = list({r["ticker"] for r in preds})
    actuals = _fetch_actuals(tickers, target_date)

    results = []
    conn = get_conn()
    for pred in preds:
        ticker = pred["ticker"]
        actual = actuals.get(ticker)
        if actual is None:
            logger.debug("No actual close for %s on %s", ticker, target_date)
            continue

        actual_close = actual["close"]
        actual_high  = actual["high"]
        actual_low   = actual["low"]
        predicted    = pred["price_mid"]
        signal       = pred["signal"]

        # Price error
        error_pct = abs(actual_close - predicted) / actual_close * 100

        # Direction accuracy
        # NEUTRAL is not a directional bet — exclude from scoring entirely.
        # Only score when the system committed to BULLISH or BEARISH.
        prev_close = _get_prev_close(ticker, target_date)
        if signal == "NEUTRAL":
            actual_direction = None
            signal_correct   = None
        elif prev_close and prev_close > 0:
            actual_direction = "BULLISH" if actual_close > prev_close else \
                               "BEARISH" if actual_close < prev_close else "NEUTRAL"
            signal_correct = 1 if signal == actual_direction else 0
        else:
            actual_direction = None
            signal_correct   = None

        # Update predictions table with actuals
        conn.execute("""
            UPDATE predictions
            SET actual_close=?, actual_high=?, actual_low=?
            WHERE ticker=? AND date=? AND prediction_type=?
        """, (actual_close, actual_high, actual_low,
              ticker, target_date, pred["prediction_type"]))

        # Insert into accuracy_log
        conn.execute("""
            INSERT INTO accuracy_log
                (ticker, date, prediction_type, predicted_mid, actual_close,
                 error_pct, signal, signal_correct)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
        """, (
            ticker, target_date, pred["prediction_type"],
            round(predicted, 4), round(actual_close, 4),
            round(error_pct, 3), signal, signal_correct
        ))

        result = {
            "ticker":           ticker,
            "date":             target_date,
            "prediction_type":  pred["prediction_type"],
            "strategy":         pred["strategy"],
            "predicted":        round(predicted, 4),
            "actual_close":     round(actual_close, 4),
            "error_pct":        round(error_pct, 3),
            "signal":           signal,
            "actual_direction": actual_direction,
            "signal_correct":   signal_correct,
        }
        results.append(result)
        logger.info("%s: predicted=%.2f actual=%.2f error=%.2f%% direction=%s",
                    ticker, predicted, actual_close, error_pct,
                    "✓" if signal_correct else "✗")

    conn.commit()
    conn.close()
    return results


def _fetch_actuals(tickers: list, target_date: str) -> dict:
    """Fetch actual OHLC for target_date from price_history DB (same source as indicators)."""
    result = {}
    conn   = get_conn()
    for ticker in tickers:
        row = conn.execute("""
            SELECT close, high, low FROM price_history
            WHERE ticker = ? AND date = ?
        """, (ticker, target_date)).fetchone()
        if row:
            result[ticker] = {
                "close": float(row["close"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
            }
    conn.close()
    return result


def _get_prev_close(ticker: str, target_date: str) -> float | None:
    """Get the closing price from the day before target_date."""
    conn = get_conn()
    row  = conn.execute("""
        SELECT close FROM price_history
        WHERE ticker = ? AND date < ?
        ORDER BY date DESC LIMIT 1
    """, (ticker, target_date)).fetchone()
    conn.close()
    return row["close"] if row else None


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY STATS
# ══════════════════════════════════════════════════════════════════════════════

def get_accuracy_summary(days: int = 30) -> dict:
    """
    Return accuracy stats for the last N days.
    Returns dict with overall, per_ticker, and per_strategy breakdowns.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    conn  = get_conn()
    rows  = conn.execute("""
        SELECT a.ticker, a.date, a.prediction_type,
               a.error_pct, a.signal, a.signal_correct,
               s.strategy
        FROM   accuracy_log a
        JOIN   stocks s ON s.ticker = a.ticker
        WHERE  a.date >= ?
        ORDER  BY a.date DESC
    """, (since,)).fetchall()
    conn.close()

    if not rows:
        return {"available": False, "days": days}

    df = pd.DataFrame([dict(r) for r in rows])

    def stats(subset):
        if subset.empty:
            return None
        n          = len(subset)
        avg_err    = subset["error_pct"].mean()
        within_1pct = (subset["error_pct"] < 1.0).sum() / n * 100
        within_3pct = (subset["error_pct"] < 3.0).sum() / n * 100
        dir_subset  = subset.dropna(subset=["signal_correct"])
        dir_acc     = dir_subset["signal_correct"].mean() * 100 if not dir_subset.empty else None
        return {
            "n":             n,
            "avg_error_pct": round(avg_err, 2),
            "within_1pct":   round(within_1pct, 1),
            "within_3pct":   round(within_3pct, 1),
            "direction_acc": round(dir_acc, 1) if dir_acc is not None else None,
        }

    overall       = stats(df)
    per_ticker    = {t: stats(df[df["ticker"] == t]) for t in df["ticker"].unique()}
    per_strategy  = {s: stats(df[df["strategy"] == s]) for s in df["strategy"].unique()}

    return {
        "available":    True,
        "days":         days,
        "total_scored": len(df),
        "overall":      overall,
        "per_ticker":   per_ticker,
        "per_strategy": per_strategy,
    }


def get_recent_log(ticker: str = None, limit: int = 30) -> list:
    """Return recent accuracy log entries, optionally filtered by ticker."""
    conn  = get_conn()
    if ticker:
        rows = conn.execute("""
            SELECT * FROM accuracy_log
            WHERE ticker = ?
            ORDER BY date DESC LIMIT ?
        """, (ticker.upper(), limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM accuracy_log
            ORDER BY date DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    results = score_predictions()
    for r in results:
        print(f"{r['ticker']}: predicted={r['predicted']} actual={r['actual_close']} "
              f"error={r['error_pct']}% direction={'✓' if r['signal_correct'] else '✗'}")
    print("\nSummary (last 30 days):")
    summary = get_accuracy_summary(30)
    if summary["available"]:
        print(f"  Overall: avg error={summary['overall']['avg_error_pct']}% "
              f"direction accuracy={summary['overall']['direction_acc']}%")
