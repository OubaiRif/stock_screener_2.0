"""
engine/prices.py — Unified price fetching for Stock Screener 2.0.
Provides regular, pre-market, and post-market prices via yfinance.
All pages should import from here rather than duplicating yf calls.
"""
from datetime import datetime
from typing import Optional


def get_extended_hours_price(ticker: str) -> dict:
    """
    Fetch the most relevant price for a ticker depending on market session.
    Returns a dict with:
        price         — best available price right now
        price_type    — 'pre_market' | 'post_market' | 'regular' | 'previous_close'
        regular       — last regular market close
        pre_market    — pre-market price (or None)
        post_market   — post-market price (or None)
        pre_change    — pre-market $ change (or None)
        pre_change_pct — pre-market % change (or None)
        post_change   — post-market $ change (or None)
        post_change_pct — post-market % change (or None)
        pre_market_time — datetime of pre-market quote (or None)
        post_market_time — datetime of post-market quote (or None)
        error         — error message if fetch failed (or None)
    """
    result = {
        "price": None, "price_type": None,
        "regular": None,
        "pre_market": None, "post_market": None,
        "pre_change": None, "pre_change_pct": None,
        "post_change": None, "post_change_pct": None,
        "pre_market_time": None, "post_market_time": None,
        "error": None,
    }

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info

        result["regular"]          = info.get("regularMarketPrice") or info.get("previousClose")
        result["pre_market"]       = info.get("preMarketPrice")
        result["post_market"]      = info.get("postMarketPrice")
        result["pre_change"]       = info.get("preMarketChange")
        result["pre_change_pct"]   = info.get("preMarketChangePercent")
        result["post_change"]      = info.get("postMarketChange")
        result["post_change_pct"]  = info.get("postMarketChangePercent")

        # Convert Unix timestamps to datetime
        pre_ts  = info.get("preMarketTime")
        post_ts = info.get("postMarketTime")
        if pre_ts:
            result["pre_market_time"]  = datetime.fromtimestamp(pre_ts)
        if post_ts:
            result["post_market_time"] = datetime.fromtimestamp(post_ts)

        # Determine best current price and label
        if result["pre_market"]:
            result["price"]      = result["pre_market"]
            result["price_type"] = "pre_market"
        elif result["post_market"]:
            result["price"]      = result["post_market"]
            result["price_type"] = "post_market"
        elif result["regular"]:
            result["price"]      = result["regular"]
            result["price_type"] = "regular"
        else:
            result["price_type"] = "previous_close"

    except Exception as e:
        result["error"] = str(e)

    return result


def get_best_price(ticker: str) -> Optional[float]:
    """
    Returns the single best available price:
    pre-market → post-market → regular → None.
    Fast helper for places that just need a number.
    """
    d = get_extended_hours_price(ticker)
    return d.get("price")


def format_price_label(data: dict) -> str:
    """
    Returns a short human-readable label for the price type.
    e.g. 'Pre-Market' | 'After Hours' | 'Regular' | 'Prev Close'
    """
    mapping = {
        "pre_market":     "Pre-Market",
        "post_market":    "After Hours",
        "regular":        "Regular",
        "previous_close": "Prev Close",
    }
    return mapping.get(data.get("price_type"), "")


def format_change_html(change: Optional[float], change_pct: Optional[float],
                       bull: str = "#006040", bear: str = "#aa1800") -> str:
    """Returns colored HTML for a price change + percent."""
    if change is None or change_pct is None:
        return ""
    c = bull if change >= 0 else bear
    a = "▲" if change >= 0 else "▼"
    return (f'<span style="color:{c};font-weight:600;font-family:IBM Plex Mono,monospace">'
            f'{a} ${abs(change):.2f} ({abs(change_pct):.2f}%)</span>')
