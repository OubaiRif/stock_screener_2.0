"""
engine/strategy_advisor.py — Auto-suggests a trading strategy for any ticker.
Analyzes historical price behavior, volatility, and fundamental profile.
"""
import logging, sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.db import get_conn
from engine.fetcher import load_daily_history, load_fundamentals

logger = logging.getLogger(__name__)

STRATEGIES = ["trend", "mean_reversion", "rubber_band", "breakout_volume", "unassigned"]


def suggest_strategy(ticker: str) -> dict:
    """
    Analyze a ticker and suggest the best strategy.
    Returns a dict with: strategy, confidence, reasons, scores per strategy.
    """
    ticker = ticker.upper()
    df     = load_daily_history(ticker)
    fund   = load_fundamentals(ticker)

    if df.empty or len(df) < 60:
        return {
            "ticker":     ticker,
            "strategy":   "unassigned",
            "confidence": 0,
            "reason":     "Not enough price history to analyze.",
            "scores":     {}
        }

    scores  = {}
    reasons = {}

    # ── Compute behavioral metrics ────────────────────────────────────────────
    close      = df["Close"]
    volume     = df["Volume"]
    returns    = close.pct_change().dropna()

    # Trend strength: R² of price vs time over last 90 days
    trend_r2 = _trend_r2(close.tail(90))

    # Mean reversion: autocorrelation of returns (negative = mean-reverting)
    autocorr = returns.tail(60).autocorr(lag=1)

    # Volatility: annualized std of daily returns
    volatility = returns.tail(60).std() * np.sqrt(252)

    # Average True Range as % of price (normalized ATR)
    atr_pct = _atr_pct(df.tail(60))

    # Volume consistency: std of relative volume (low = consistent, high = spiky)
    avg_vol    = volume.rolling(20).mean()
    rel_vol    = (volume / avg_vol).dropna()
    vol_spikes = (rel_vol > 2.0).sum()           # days with >2x average volume
    vol_spike_pct = vol_spikes / len(rel_vol)    # fraction of days with spikes

    # Resistance break frequency
    rolling_high = close.rolling(20).max().shift(1)
    breakouts    = (close > rolling_high).sum()
    breakout_pct = breakouts / len(close)

    # Beta proxy (correlation with broad market — uses return std ratio)
    beta = fund.get("beta") or _estimate_beta(returns)

    # ── Score each strategy ───────────────────────────────────────────────────

    # TREND: wants high R², consistent direction, moderate volatility
    trend_score = 0
    trend_reasons = []
    if trend_r2 > 0.7:
        trend_score += 35
        trend_reasons.append(f"Strong price trend (R²={trend_r2:.2f})")
    elif trend_r2 > 0.4:
        trend_score += 15
        trend_reasons.append(f"Moderate trend (R²={trend_r2:.2f})")
    if autocorr > 0.05:
        trend_score += 20
        trend_reasons.append("Returns show momentum (positive autocorrelation)")
    if 0.10 < volatility < 0.40:
        trend_score += 20
        trend_reasons.append(f"Volatility suitable for trend ({volatility:.0%} ann.)")
    if beta and 0.5 < beta < 1.5:
        trend_score += 15
        trend_reasons.append(f"Beta {beta:.2f} — moves with market")
    if _is_etf(ticker, fund):
        trend_score += 10
        trend_reasons.append("ETF — typically trend-follows macro")
    scores["trend"]   = min(100, trend_score)
    reasons["trend"]  = trend_reasons

    # MEAN REVERSION: wants negative autocorr, low-moderate volatility, range-bound
    mr_score = 0
    mr_reasons = []
    if autocorr < -0.05:
        mr_score += 35
        mr_reasons.append(f"Returns mean-revert (autocorr={autocorr:.2f})")
    if trend_r2 < 0.3:
        mr_score += 25
        mr_reasons.append("Price is range-bound, not trending")
    if volatility < 0.30:
        mr_score += 20
        mr_reasons.append(f"Low-moderate volatility ({volatility:.0%} ann.)")
    if beta and beta < 0.8:
        mr_score += 10
        mr_reasons.append(f"Low beta {beta:.2f} — relatively stable")
    scores["mean_reversion"]  = min(100, mr_score)
    reasons["mean_reversion"] = mr_reasons

    # RUBBER BAND: wants high volatility, sharp moves, weak/no trend
    rb_score = 0
    rb_reasons = []
    if volatility > 0.50:
        rb_score += 35
        rb_reasons.append(f"High volatility ({volatility:.0%} ann.) — snap moves likely")
    elif volatility > 0.35:
        rb_score += 20
        rb_reasons.append(f"Above-average volatility ({volatility:.0%} ann.)")
    if atr_pct > 0.03:
        rb_score += 25
        rb_reasons.append(f"Large daily range (ATR={atr_pct:.1%} of price)")
    if trend_r2 < 0.4:
        rb_score += 15
        rb_reasons.append("Choppy price action — no clean trend")
    if beta and beta > 1.5:
        rb_score += 15
        rb_reasons.append(f"High beta {beta:.2f} — amplified moves")
    fund_score = _fundamental_weakness(fund)
    if fund_score > 0:
        rb_score += fund_score
        rb_reasons.append("Weak fundamentals — sentiment-driven price action")
    scores["rubber_band"]  = min(100, rb_score)
    reasons["rubber_band"] = rb_reasons

    # BREAKOUT VOLUME: wants volume spikes, frequent resistance breaks, momentum
    bv_score = 0
    bv_reasons = []
    if vol_spike_pct > 0.10:
        bv_score += 30
        bv_reasons.append(f"Frequent volume spikes ({vol_spike_pct:.0%} of days >2x avg)")
    if breakout_pct > 0.08:
        bv_score += 30
        bv_reasons.append(f"Breaks resistance often ({breakout_pct:.0%} of days)")
    if volatility > 0.25:
        bv_score += 20
        bv_reasons.append(f"Enough volatility for meaningful breakouts")
    if autocorr > 0.0:
        bv_score += 10
        bv_reasons.append("Momentum tendency after moves")
    if vol_spikes > 5:
        bv_score += 10
        bv_reasons.append(f"{int(vol_spikes)} high-volume days in last 60 sessions")
    scores["breakout_volume"]  = min(100, bv_score)
    reasons["breakout_volume"] = bv_reasons

    # ── Pick winner ───────────────────────────────────────────────────────────
    best_strategy = max(scores, key=scores.get)
    best_score    = scores[best_strategy]

    if best_score < 25:
        best_strategy = "unassigned"
        confidence    = 0
        top_reasons   = ["Insufficient signal to recommend a strategy confidently."]
    else:
        # Confidence = gap between top and second score
        sorted_scores = sorted(scores.values(), reverse=True)
        gap        = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
        confidence = min(100, int(best_score * 0.6 + gap * 0.4))
        top_reasons = reasons[best_strategy]

    result = {
        "ticker":     ticker,
        "strategy":   best_strategy,
        "confidence": confidence,
        "reason":     " | ".join(top_reasons),
        "scores":     scores,
        "metrics": {
            "trend_r2":      round(trend_r2, 3),
            "autocorr":      round(autocorr, 3),
            "volatility_ann": round(volatility, 3),
            "atr_pct":       round(atr_pct, 3),
            "vol_spike_pct": round(vol_spike_pct, 3),
            "breakout_pct":  round(breakout_pct, 3),
            "beta":          round(beta, 2) if beta else None,
        }
    }

    logger.info("Strategy suggestion for %s: %s (confidence=%d)",
                ticker, best_strategy, confidence)
    return result


# ── Helper functions ──────────────────────────────────────────────────────────

def _trend_r2(series: pd.Series) -> float:
    """R² of price vs time — 1.0 = perfect trend, 0.0 = random."""
    if len(series) < 10:
        return 0.0
    x = np.arange(len(series))
    y = series.values
    try:
        coeffs = np.polyfit(x, y, 1)
        y_hat  = np.polyval(coeffs, x)
        ss_res = np.sum((y - y_hat) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        return float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    except Exception:
        return 0.0


def _atr_pct(df: pd.DataFrame) -> float:
    """Average True Range as a fraction of closing price."""
    try:
        high  = df["High"]
        low   = df["Low"]
        close = df["Close"]
        prev  = close.shift(1)
        tr    = pd.concat([
            high - low,
            (high - prev).abs(),
            (low  - prev).abs()
        ], axis=1).max(axis=1)
        atr   = tr.rolling(14).mean().iloc[-1]
        return float(atr / close.iloc[-1])
    except Exception:
        return 0.0


def _estimate_beta(returns: pd.Series) -> float:
    """Rough beta estimate from return volatility vs typical market (15% ann)."""
    ann_vol = returns.std() * np.sqrt(252)
    return round(ann_vol / 0.15, 2)


def _is_etf(ticker: str, fund: dict) -> bool:
    """Heuristic ETF detection."""
    etf_tickers = {"VOO","SPY","QQQ","IWM","IAU","GLD","SLV","TLT","HYG",
                   "XLK","XLF","XLE","XLV","ARKK","VTI","VEA","VWO"}
    if ticker in etf_tickers:
        return True
    mc = fund.get("market_cap") or 0
    pe = fund.get("pe_trailing")
    return mc > 1e10 and pe is None   # large cap, no PE = likely ETF


def _fundamental_weakness(fund: dict) -> int:
    """Returns bonus score for rubber_band if fundamentals are weak."""
    score = 0
    pe = fund.get("pe_trailing")
    pm = fund.get("profit_margin")
    de = fund.get("debt_to_equity")
    if pe and pe > 100:
        score += 5
    if pm is not None and pm < 0:
        score += 10
    if de is not None and de > 3:
        score += 5
    return score


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    for ticker in ["VOO", "IAU", "PLUG", "TPET"]:
        r = suggest_strategy(ticker)
        print(f"\n{r['ticker']} → {r['strategy'].upper()} (confidence: {r['confidence']}%)")
        print(f"  Reason: {r['reason']}")
        print(f"  Scores: {r['scores']}")
