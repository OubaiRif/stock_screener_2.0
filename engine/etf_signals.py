"""
engine/etf_signals.py — Macro data fetching and ETF-specific signal computation.

Data sources:
  - FRED API (free, no key needed for most series)
  - yfinance (VIX, spot gold, GDX, DXY, TNX etc.)

Gold ETF signals driven by:
  Real rates, USD, inflation, GDX lead, gold momentum, seasonal

Index ETF signals driven by:
  VIX, yield curve, market breadth (approximated), sector rotation,
  Fed policy direction, momentum
"""
import logging, sys, os, json
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.db import get_conn

logger = logging.getLogger(__name__)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

# ── FRED series definitions ───────────────────────────────────────────────────
FRED_SERIES = {
    # Gold drivers
    "DFII10":      ("Real 10-Year Treasury Rate",          "percent"),
    "DTWEXBGS":    ("USD Broad Trade-Weighted Index",      "index"),
    "T10YIE":      ("10-Year Breakeven Inflation Rate",    "percent"),
    "BAMLH0A0HYM2":("High Yield Credit Spread",           "percent"),
    # Index / macro drivers
    "FEDFUNDS":    ("Federal Funds Rate",                  "percent"),
    "T10Y2Y":      ("10Y-2Y Yield Curve Spread",           "percent"),
    "UMCSENT":     ("University of Michigan Consumer Sentiment", "index"),
    "INDPRO":      ("Industrial Production Index",         "index"),
    "UNRATE":      ("Unemployment Rate",                   "percent"),
}

# ── yfinance macro tickers ────────────────────────────────────────────────────
YF_MACRO = {
    "^VIX":    "VIX Fear Index",
    "GC=F":    "Spot Gold ($/oz)",
    "GDX":     "Gold Miners ETF (lead indicator)",
    "DX-Y.NYB":"US Dollar Index",
    "^TNX":    "10-Year Treasury Yield",
    "^GSPC":   "S&P 500 Index",
    "^IXIC":   "Nasdaq Composite",
    "^RUT":    "Russell 2000 Small Cap",
    "XLK":     "Technology Sector",
    "XLF":     "Financial Sector",
    "XLE":     "Energy Sector",
    "XLV":     "Healthcare Sector",
    "XLU":     "Utilities Sector (defensive)",
    "XLP":     "Consumer Staples (defensive)",
    "XLY":     "Consumer Discretionary (growth)",
}


# ── FRED data fetching ────────────────────────────────────────────────────────

def fetch_fred_series(series_id: str, days: int = 90) -> pd.Series | None:
    """Fetch a FRED time series. Returns a pandas Series indexed by date."""
    try:
        url  = FRED_BASE + series_id
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            logger.warning("FRED %s returned HTTP %s", series_id, resp.status_code)
            return None
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text), parse_dates=["observation_date"], index_col="observation_date")
        df = df[df.iloc[:,0] != "."]     # remove missing value markers
        df.iloc[:,0] = pd.to_numeric(df.iloc[:,0], errors="coerce")
        df.dropna(inplace=True)
        series = df.iloc[:,0]
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
        return series[series.index >= cutoff]
    except Exception as e:
        logger.error("FRED fetch failed for %s: %s", series_id, e)
        return None


def fetch_all_fred(days: int = 90) -> dict:
    """Fetch all FRED series and store latest values in macro_data table."""
    results = {}
    conn    = get_conn()
    for series_id in FRED_SERIES:
        s = fetch_fred_series(series_id, days=days)
        if s is None or s.empty:
            continue
        latest_date  = s.index[-1].strftime("%Y-%m-%d")
        latest_value = round(float(s.iloc[-1]), 4)
        prev_value   = round(float(s.iloc[-2]), 4) if len(s) > 1 else latest_value
        results[series_id] = {
            "value":  latest_value,
            "prev":   prev_value,
            "change": round(latest_value - prev_value, 4),
            "date":   latest_date,
        }
        # Store in DB
        for dt, val in s.items():
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO macro_data (series_id, date, value, source)
                    VALUES (?, ?, ?, 'fred')
                """, (series_id, dt.strftime("%Y-%m-%d"), round(float(val), 4)))
            except Exception:
                pass
        logger.info("FRED %s: %.3f (change: %.3f)", series_id, latest_value, latest_value-prev_value)
    conn.commit()
    conn.close()
    return results


def fetch_yf_macro(days: int = 90) -> dict:
    """Fetch macro yfinance tickers and store latest values."""
    results = {}
    conn    = get_conn()
    start   = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    for ticker, name in YF_MACRO.items():
        try:
            df = yf.Ticker(ticker).history(start=start, auto_adjust=True)
            if df.empty: continue
            latest = df.iloc[-1]
            prev   = df.iloc[-2] if len(df) > 1 else latest
            close  = float(latest["Close"])
            prev_c = float(prev["Close"])
            chg    = round((close - prev_c) / prev_c * 100, 3)
            results[ticker] = {
                "name":   name,
                "close":  round(close, 4),
                "prev":   round(prev_c, 4),
                "chg_pct": chg,
                "date":   df.index[-1].strftime("%Y-%m-%d"),
            }
            # Store in macro_data
            for dt, row in df.iterrows():
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO macro_data (series_id, date, value, source)
                        VALUES (?, ?, ?, 'yfinance')
                    """, (ticker, dt.strftime("%Y-%m-%d"), round(float(row["Close"]), 4)))
                except Exception:
                    pass
        except Exception as e:
            logger.warning("YF macro fetch failed for %s: %s", ticker, e)
    conn.commit()
    conn.close()
    return results


def get_latest_macro() -> dict:
    """Load most recent macro data values from DB."""
    conn    = get_conn()
    series  = list(FRED_SERIES.keys()) + list(YF_MACRO.keys())
    results = {}
    for s in series:
        rows = conn.execute("""
            SELECT date, value FROM macro_data WHERE series_id=?
            ORDER BY date DESC LIMIT 2
        """, (s,)).fetchall()
        if rows:
            results[s] = {
                "value":  rows[0]["value"],
                "prev":   rows[1]["value"] if len(rows) > 1 else rows[0]["value"],
                "date":   rows[0]["date"],
            }
            results[s]["change"] = round(results[s]["value"] - results[s]["prev"], 4)
    conn.close()
    return results


# ── Signal computation ────────────────────────────────────────────────────────

def _trend(macro: dict, key: str) -> str:
    """Return Rising / Falling / Stable based on recent change."""
    d = macro.get(key, {})
    if not d: return "Unknown"
    chg = d.get("change", 0)
    if chg > 0.05:   return "Rising"
    if chg < -0.05:  return "Falling"
    return "Stable"

def _val(macro: dict, key: str) -> float | None:
    d = macro.get(key)
    return d["value"] if d else None

def _momentum(ticker: str, days: int) -> float | None:
    """Return % return over last N days from macro_data."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT value FROM macro_data WHERE series_id=?
        ORDER BY date DESC LIMIT ?
    """, (ticker, days)).fetchall()
    conn.close()
    if len(rows) < 2: return None
    latest = rows[0]["value"]
    oldest = rows[-1]["value"]
    return round((latest - oldest) / oldest * 100, 2) if oldest else None


# ── Gold ETF signal ───────────────────────────────────────────────────────────

def compute_gold_signal(macro: dict) -> dict:
    """
    Compute gold ETF buy/hold/sell signal from macro environment.
    Returns dict with signal, score 0-100, bull/bear drivers.
    """
    score  = 50
    bull   = []
    bear   = []

    # Real rates (most important — falling real rates = bullish gold)
    real_rate = _val(macro, "DFII10")
    rate_trend = _trend(macro, "DFII10")
    if real_rate is not None:
        if real_rate < 0:
            score += 15; bull.append(f"Negative real rates ({real_rate:.2f}%) — strong gold tailwind")
        elif real_rate < 0.5:
            score += 8;  bull.append(f"Low real rates ({real_rate:.2f}%) — mild gold tailwind")
        elif real_rate > 2:
            score -= 12; bear.append(f"High real rates ({real_rate:.2f}%) — headwind for gold")
        if rate_trend == "Falling":
            score += 8;  bull.append("Real rates falling — increasing gold attractiveness")
        elif rate_trend == "Rising":
            score -= 8;  bear.append("Real rates rising — pressure on gold")

    # USD (weaker dollar = bullish gold — inverse relationship)
    usd = _val(macro, "DTWEXBGS")
    usd_trend = _trend(macro, "DTWEXBGS")
    if usd_trend == "Falling":
        score += 8; bull.append("USD weakening — gold tends to rise inversely")
    elif usd_trend == "Rising":
        score -= 8; bear.append("USD strengthening — headwind for gold")

    # Breakeven inflation (higher inflation = bullish gold as hedge)
    inflation = _val(macro, "T10YIE")
    if inflation is not None:
        if inflation > 2.5:
            score += 8;  bull.append(f"Breakeven inflation elevated ({inflation:.2f}%) — gold as inflation hedge")
        elif inflation < 1.5:
            score -= 5;  bear.append(f"Low inflation expectations ({inflation:.2f}%) — weaker gold case")

    # High yield spread (risk-off = bullish gold)
    hy_spread = _val(macro, "BAMLH0A0HYM2")
    if hy_spread is not None:
        if hy_spread > 5:
            score += 8;  bull.append(f"High yield spread elevated ({hy_spread:.2f}%) — risk-off favors gold")
        elif hy_spread < 3:
            score -= 3;  bear.append("Tight credit spreads — risk-on environment, gold less needed")

    # GDX lead (gold miners tend to lead physical gold by 2-4 weeks)
    gdx_mom = _momentum("GDX", 10)
    if gdx_mom is not None:
        if gdx_mom > 3:
            score += 8;  bull.append(f"Gold miners up {gdx_mom:.1f}% — lead indicator for gold")
        elif gdx_mom < -3:
            score -= 8;  bear.append(f"Gold miners down {gdx_mom:.1f}% — negative lead for gold")

    # VIX (fear = gold demand)
    vix = _val(macro, "^VIX")
    if vix is not None:
        if vix > 25:
            score += 6;  bull.append(f"Elevated VIX ({vix:.1f}) — fear drives gold demand")
        elif vix < 15:
            score -= 3;  bear.append(f"Low VIX ({vix:.1f}) — calm markets reduce gold demand")

    # Spot gold momentum
    gold_1m  = _momentum("GC=F", 21)
    gold_3m  = _momentum("GC=F", 63)
    if gold_1m is not None and gold_1m > 2:
        score += 5; bull.append(f"Gold momentum positive ({gold_1m:.1f}% past month)")
    elif gold_1m is not None and gold_1m < -2:
        score -= 5; bear.append(f"Gold momentum negative ({gold_1m:.1f}% past month)")

    # Seasonal factor (gold historically strong Aug-Sep, Jan; weak Mar, Oct)
    month = date.today().month
    if month in [1, 8, 9]:
        score += 4; bull.append(f"Seasonally favorable month for gold (month {month})")
    elif month in [3, 10]:
        score -= 3; bear.append(f"Seasonally weak month for gold (month {month})")

    score = max(0, min(100, score))
    if score >= 65:   signal = "BUY"
    elif score >= 45: signal = "HOLD"
    else:             signal = "SELL"

    return {
        "signal":     signal,
        "score":      score,
        "confidence": round(abs(score - 50) * 2, 1),
        "bull":       bull,
        "bear":       bear,
        "real_rate":  real_rate,
        "usd_trend":  usd_trend,
        "vix":        vix,
        "gdx_mom":    gdx_mom,
        "gold_1m":    gold_1m,
        "gold_3m":    gold_3m,
    }


# ── Index ETF signal ──────────────────────────────────────────────────────────

def compute_index_signal(macro: dict) -> dict:
    """
    Compute index ETF buy/hold/sell signal from macro environment.
    """
    score = 50
    bull  = []
    bear  = []

    # VIX (fear gauge)
    vix = _val(macro, "^VIX")
    vix_trend = _trend(macro, "^VIX")
    if vix is not None:
        if vix < 15:
            score += 8;  bull.append(f"Low VIX ({vix:.1f}) — calm, low-fear market")
        elif vix < 20:
            score += 4;  bull.append(f"VIX moderate ({vix:.1f}) — manageable volatility")
        elif vix > 30:
            score -= 12; bear.append(f"High VIX ({vix:.1f}) — elevated fear, market stress")
        elif vix > 20:
            score -= 5;  bear.append(f"VIX rising ({vix:.1f}) — increasing uncertainty")
        if vix_trend == "Falling" and vix > 25:
            score += 6;  bull.append("VIX falling from elevated level — fear receding, often buy signal")
        elif vix_trend == "Rising":
            score -= 4;  bear.append("VIX rising — increasing market stress")

    # Yield curve (inversion = recession warning)
    yield_curve = _val(macro, "T10Y2Y")
    if yield_curve is not None:
        if yield_curve > 0.5:
            score += 8;  bull.append(f"Yield curve positive ({yield_curve:.2f}%) — healthy economy signal")
        elif yield_curve > 0:
            score += 3;  bull.append(f"Yield curve slightly positive ({yield_curve:.2f}%)")
        elif yield_curve > -0.5:
            score -= 5;  bear.append(f"Yield curve slightly inverted ({yield_curve:.2f}%) — caution")
        else:
            score -= 12; bear.append(f"Yield curve inverted ({yield_curve:.2f}%) — recession warning")

    # Fed funds rate direction
    fed = _val(macro, "FEDFUNDS")
    fed_trend = _trend(macro, "FEDFUNDS")
    if fed_trend == "Falling":
        score += 8;  bull.append(f"Fed cutting rates ({fed:.2f}%) — historically bullish for equities")
    elif fed_trend == "Rising":
        score -= 5;  bear.append(f"Fed raising rates ({fed:.2f}%) — headwind for equities")

    # Consumer sentiment
    sentiment = _val(macro, "UMCSENT")
    sent_trend = _trend(macro, "UMCSENT")
    if sentiment is not None:
        if sentiment > 80:
            score += 5;  bull.append(f"Consumer sentiment strong ({sentiment:.0f})")
        elif sentiment < 65:
            score -= 5;  bear.append(f"Consumer sentiment weak ({sentiment:.0f})")
        if sent_trend == "Rising":
            score += 3;  bull.append("Consumer sentiment improving")
        elif sent_trend == "Falling":
            score -= 3;  bear.append("Consumer sentiment deteriorating")

    # S&P 500 vs 200-day moving average (trend health)
    sp500_200d = _momentum("^GSPC", 200)
    sp500_1m   = _momentum("^GSPC", 21)
    if sp500_1m is not None:
        if sp500_1m > 3:
            score += 6;  bull.append(f"S&P 500 up {sp500_1m:.1f}% past month — positive momentum")
        elif sp500_1m < -3:
            score -= 6;  bear.append(f"S&P 500 down {sp500_1m:.1f}% past month — negative momentum")

    # Sector rotation (defensive vs growth — risk-off signal)
    xlu = _momentum("XLU", 21)   # utilities (defensive)
    xly = _momentum("XLY", 21)   # discretionary (growth)
    xlp = _momentum("XLP", 21)   # staples (defensive)
    xlk = _momentum("XLK", 21)   # tech (growth)
    if xlu is not None and xly is not None:
        if xlu > xly + 2:
            score -= 6;  bear.append("Defensive sectors outperforming growth — risk-off rotation")
        elif xly > xlu + 2:
            score += 6;  bull.append("Growth sectors outperforming defensives — risk-on rotation")
    if xlk is not None and xlp is not None:
        if xlk > xlp + 3:
            score += 4;  bull.append("Tech outperforming staples — growth-oriented market")
        elif xlp > xlk + 3:
            score -= 4;  bear.append("Staples outperforming tech — defensive positioning")

    # Industrial production (economic health)
    indpro_trend = _trend(macro, "INDPRO")
    if indpro_trend == "Rising":
        score += 4;  bull.append("Industrial production rising — economic expansion")
    elif indpro_trend == "Falling":
        score -= 4;  bear.append("Industrial production falling — economic contraction signal")

    score = max(0, min(100, score))
    if score >= 65:   signal = "BUY"
    elif score >= 45: signal = "HOLD"
    else:             signal = "SELL"

    return {
        "signal":      signal,
        "score":       score,
        "confidence":  round(abs(score - 50) * 2, 1),
        "bull":        bull,
        "bear":        bear,
        "vix":         vix,
        "yield_curve": yield_curve,
        "fed_rate":    fed,
        "consumer_sent": sentiment,
        "sp500_1m":    sp500_1m,
        "sp500_200d":  sp500_200d,
    }


# ── Per-ETF signal with sentiment + technical ─────────────────────────────────

def compute_etf_signal(ticker: str, category: str, macro_signal: dict,
                       sentiment_score: float = 0.0) -> dict:
    """
    Blend macro signal with ETF-specific technical momentum + sentiment.
    Returns final score, signal, and price momentum data.
    """
    # Momentum
    mom_1m  = _momentum(ticker, 21)
    mom_3m  = _momentum(ticker, 63)
    mom_6m  = _momentum(ticker, 126)
    mom_12m = _momentum(ticker, 252)

    # Start from macro signal score
    score = macro_signal["score"]

    # Adjust for individual ETF momentum
    if mom_1m is not None:
        score += min(8, max(-8, mom_1m * 0.5))
    if mom_3m is not None:
        score += min(6, max(-6, mom_3m * 0.2))

    # Add sentiment (convert -1..1 to -10..+10 adjustment)
    score += sentiment_score * 10

    score = max(0, min(100, round(score, 1)))
    if score >= 65:   signal = "BUY"
    elif score >= 45: signal = "HOLD"
    else:             signal = "SELL"

    result = {
        "ticker":      ticker,
        "category":    category,
        "signal":      signal,
        "score":       score,
        "confidence":  round(abs(score - 50) * 2, 1),
        "macro_score": macro_signal["score"],
        "sentiment":   round(sentiment_score, 3),
        "momentum_1m": mom_1m,
        "momentum_3m": mom_3m,
        "momentum_6m": mom_6m,
        "momentum_12m":mom_12m,
        "bull_drivers": macro_signal.get("bull", []),
        "bear_drivers": macro_signal.get("bear", []),
    }

    # Save to DB
    today = date.today().isoformat()
    conn  = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO etf_signals
            (ticker, date, category, signal, score, confidence,
             drivers_bull, drivers_bear, momentum_1m, momentum_3m, momentum_6m, momentum_12m)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (ticker, today, category, signal, score, result["confidence"],
          json.dumps(result["bull_drivers"]), json.dumps(result["bear_drivers"]),
          mom_1m, mom_3m, mom_6m, mom_12m))
    conn.commit(); conn.close()
    return result


# ── Main refresh function ─────────────────────────────────────────────────────

def store_etf_prices_in_macro(etf_list: list) -> None:
    """
    Copy ETF price history into macro_data so _momentum() can read it.
    Called during refresh_etf_signals.
    """
    conn = get_conn()
    for etf in etf_list:
        ticker = etf["ticker"]
        rows   = conn.execute("""
            SELECT date, close FROM price_history WHERE ticker=?
            ORDER BY date DESC LIMIT 365
        """, (ticker,)).fetchall()
        for r in rows:
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO macro_data (series_id, date, value, source)
                    VALUES (?, ?, ?, 'yfinance')
                """, (ticker, r["date"], r["close"]))
            except Exception:
                pass
        logger.info("Stored %d price rows for %s in macro_data", len(rows), ticker)
    conn.commit()
    conn.close()


def refresh_etf_signals(etf_list: list) -> dict:
    """
    Full ETF signal refresh: fetch macro data, compute signals for all ETFs.
    etf_list: list of dicts with keys ticker, etf_category
    Returns dict of results keyed by ticker.
    """
    logger.info("Refreshing macro data…")
    fred_data = fetch_all_fred(days=90)
    yf_data   = fetch_yf_macro(days=365)
    store_etf_prices_in_macro(etf_list)
    macro     = get_latest_macro()

    gold_sig  = compute_gold_signal(macro)
    index_sig = compute_index_signal(macro)

    results   = {"macro": macro, "gold_signal": gold_sig, "index_signal": index_sig}

    from engine.sentiment import get_latest_sentiment

    for etf in etf_list:
        ticker = etf["ticker"]
        cat    = etf.get("etf_category") or "other"
        sent   = get_latest_sentiment(ticker)
        sent_score = sent.get("avg_score", 0) if sent.get("available") else 0.0

        if "gold" in cat or "precious" in cat:
            macro_sig = gold_sig
        elif "index" in cat or "bond" in cat or "sector" in cat:
            macro_sig = index_sig
        else:
            macro_sig = index_sig   # default

        result = compute_etf_signal(ticker, cat, macro_sig, sent_score)
        results[ticker] = result
        logger.info("ETF %s: %s (%d/100)", ticker, result["signal"], result["score"])

    return results
