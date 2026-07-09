"""
engine/fetcher.py — Pulls OHLCV (daily + intraday) and fundamentals from yfinance.
"""
import logging, sys, os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import HISTORY_DAYS_EXTENDED, INTRADAY_INTERVAL, INTRADAY_PERIOD
from engine.db import get_conn, upsert_stock

logger = logging.getLogger(__name__)

# ── Daily OHLCV ───────────────────────────────────────────────────────────────

def fetch_daily_history(ticker, days=HISTORY_DAYS_EXTENDED):
    ticker = ticker.upper()
    start  = (datetime.today()-timedelta(days=days)).strftime("%Y-%m-%d")
    logger.info("Fetching daily history for %s since %s", ticker, start)
    df = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    if df.empty:
        logger.warning("No data returned for %s", ticker); return df
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df["date"] = df.index.strftime("%Y-%m-%d")
    conn = get_conn()
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT OR REPLACE INTO price_history
                    (ticker,date,open,high,low,close,adj_close,volume)
                VALUES (?,?,?,?,?,?,?,?)
            """, (ticker, row["date"],
                  round(row["Open"],4), round(row["High"],4),
                  round(row["Low"],4),  round(row["Close"],4),
                  round(row["Close"],4),
                  int(row["Volume"]) if pd.notna(row["Volume"]) else None))
        except Exception as e:
            logger.debug("Skip %s %s: %s", ticker, row["date"], e)
    conn.commit(); conn.close()
    logger.info("Stored %d rows for %s", len(df), ticker)
    return df


def load_daily_history(ticker, days=HISTORY_DAYS_EXTENDED):
    """Load from DB; auto-refresh if data is stale (>3 days old)."""
    ticker = ticker.upper()
    conn   = get_conn()
    meta   = conn.execute(
        "SELECT COUNT(*) as n, MAX(date) as latest FROM price_history WHERE ticker=?",
        (ticker,)
    ).fetchone()
    conn.close()
    n, latest = meta["n"], meta["latest"]
    stale = False
    if latest:
        age   = (datetime.today().date() - datetime.strptime(latest,"%Y-%m-%d").date()).days
        stale = age > 3
    if n < 100 or stale:
        return fetch_daily_history(ticker, days=days)
    conn = get_conn()
    rows = conn.execute("""
        SELECT date,open,high,low,close,adj_close,volume
        FROM   price_history WHERE ticker=?
        ORDER  BY date DESC LIMIT ?
    """, (ticker, days)).fetchall()
    conn.close()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    df.rename(columns={"open":"Open","high":"High","low":"Low",
                        "close":"Close","adj_close":"Adj Close","volume":"Volume"}, inplace=True)
    return df

# ── Intraday ──────────────────────────────────────────────────────────────────

def fetch_intraday(ticker):
    ticker = ticker.upper()
    df = yf.Ticker(ticker).history(period=INTRADAY_PERIOD, interval=INTRADAY_INTERVAL, auto_adjust=True)
    if df.empty: return df
    df.index = pd.to_datetime(df.index).tz_localize(None)
    today = datetime.today().strftime("%Y-%m-%d")
    conn  = get_conn()
    conn.execute("DELETE FROM price_intraday WHERE ticker=? AND datetime LIKE ?", (ticker,f"{today}%"))
    for ts, row in df.iterrows():
        conn.execute("""
            INSERT OR REPLACE INTO price_intraday (ticker,datetime,open,high,low,close,volume)
            VALUES (?,?,?,?,?,?,?)
        """, (ticker, ts.strftime("%Y-%m-%d %H:%M:%S"),
              round(row["Open"],4), round(row["High"],4),
              round(row["Low"],4),  round(row["Close"],4),
              int(row["Volume"]) if pd.notna(row["Volume"]) else None))
    conn.commit(); conn.close()
    logger.info("Stored %d intraday bars for %s", len(df), ticker)
    return df


def load_intraday(ticker):
    ticker = ticker.upper()
    today  = datetime.today().strftime("%Y-%m-%d")
    conn   = get_conn()
    rows   = conn.execute("""
        SELECT datetime,open,high,low,close,volume FROM price_intraday
        WHERE ticker=? AND datetime LIKE ? ORDER BY datetime ASC
    """, (ticker,f"{today}%")).fetchall()
    conn.close()
    if not rows: return fetch_intraday(ticker)
    df = pd.DataFrame([dict(r) for r in rows])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)
    df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"}, inplace=True)
    return df

# ── Fundamentals ──────────────────────────────────────────────────────────────

def fetch_fundamentals(ticker):
    ticker = ticker.upper()
    info   = yf.Ticker(ticker).info
    g      = lambda k, d=None: info.get(k) or d
    is_etf = g("quoteType","") == "ETF"
    etf_cat = _etf_category(ticker, info) if is_etf else None
    data   = {
        "market_cap":g("marketCap"),"pe_trailing":g("trailingPE"),"pe_forward":g("forwardPE"),
        "peg_ratio":g("pegRatio"),"eps_trailing":g("trailingEps"),"eps_forward":g("forwardEps"),
        "revenue_ttm":g("totalRevenue"),"gross_margin":g("grossMargins"),
        "profit_margin":g("profitMargins"),"debt_to_equity":g("debtToEquity"),
        "current_ratio":g("currentRatio"),"beta":g("beta"),"dividend_yield":g("dividendYield"),
        "float_shares":g("floatShares"),"short_ratio":g("shortRatio"),
        "price_to_book":g("priceToBook"),"ebitda":g("ebitda"),
        "next_earnings_date": _next_earnings(yf.Ticker(ticker)),
    }
    upsert_stock(ticker, name=g("longName") or g("shortName",ticker),
                 sector=g("sector",""), industry=g("industry",""),
                 is_etf=int(is_etf), etf_category=etf_cat)
    conn = get_conn()
    cols = ["ticker"]+list(data.keys())
    vals = [ticker]+list(data.values())
    upd  = ", ".join(f"{c}=excluded.{c}" for c in data)+", fetched_at=datetime('now')"
    conn.execute(f"""
        INSERT INTO fundamentals ({",".join(cols)}) VALUES ({",".join(["?"]*len(vals))})
        ON CONFLICT(ticker) DO UPDATE SET {upd}
    """, vals)
    conn.commit(); conn.close()
    logger.info("Fundamentals stored for %s", ticker)
    return data


def load_fundamentals(ticker):
    ticker = ticker.upper()
    conn   = get_conn()
    row    = conn.execute("SELECT *,fetched_at FROM fundamentals WHERE ticker=?",(ticker,)).fetchone()
    conn.close()
    if row is None: return fetch_fundamentals(ticker)
    if (datetime.utcnow()-datetime.fromisoformat(row["fetched_at"])).days >= 7:
        return fetch_fundamentals(ticker)
    return dict(row)


def _etf_category(ticker: str, info: dict) -> str:
    """Categorize ETF by ticker name and category description."""
    name  = (info.get("longName") or info.get("shortName") or "").lower()
    cat   = (info.get("category") or "").lower()
    t     = ticker.upper()
    if t in {"IAU","GLD","SGOL","GLDM","BAR","OUNZ"} or "gold" in name or "gold" in cat:
        return "gold"
    if t in {"SLV","SIVR"} or "silver" in name:
        return "precious_metals"
    if t in {"USO","UCO","BNO"} or "oil" in name or "energy" in cat:
        return "commodities"
    if t in {"VOO","SPY","IVV","VTI","SCHB"} or "s&p 500" in name or "total market" in name:
        return "index_us_broad"
    if t in {"QQQ","TQQQ","ONEQ"} or "nasdaq" in name:
        return "index_us_tech"
    if t in {"IWM","VB","VTWO"} or "small cap" in name or "russell 2000" in name:
        return "index_us_smallcap"
    if t in {"VEA","EFA","IEFA"} or "international" in name or "developed" in name:
        return "index_international"
    if t in {"VWO","EEM"} or "emerging" in name:
        return "index_emerging"
    if t in {"TLT","IEF","BND","AGG"} or "bond" in name or "treasury" in name:
        return "bonds"
    if t in {"XLK","VGT"} or "technology" in cat:
        return "sector_tech"
    if t in {"XLF","VFH"} or "financial" in cat:
        return "sector_financial"
    if t in {"XLE","VDE"} or "energy" in cat:
        return "sector_energy"
    if t in {"XLV","VHT"} or "health" in cat:
        return "sector_health"
    return "other"


def _next_earnings(tk):
    try:
        cal = tk.calendar
        if cal is not None and not cal.empty:
            d = cal.iloc[0].get("Earnings Date")
            if d is not None: return pd.to_datetime(d).strftime("%Y-%m-%d")
    except Exception: pass
    return None


def refresh_all(tickers):
    for t in tickers:
        try: fetch_daily_history(t); fetch_fundamentals(t)
        except Exception as e: logger.error("Error refreshing %s: %s", t, e)
