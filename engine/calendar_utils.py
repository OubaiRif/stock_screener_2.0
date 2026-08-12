"""
engine/calendar_utils.py — NYSE trading-day calendar helpers.
Wraps pandas_market_calendars. The calendar object is cached at module
level since constructing it is expensive and this is called in a loop
over the watchlist.
"""
import pandas as pd
import pandas_market_calendars as mcal

_NYSE = None


def _nyse():
    global _NYSE
    if _NYSE is None:
        _NYSE = mcal.get_calendar("NYSE")
    return _NYSE


def _to_date_str(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def is_trading_day(date_str: str) -> bool:
    """True if date_str (YYYY-MM-DD) is an NYSE trading day."""
    valid = _nyse().valid_days(start_date=date_str, end_date=date_str)
    return len(valid) > 0


def next_trading_day(date_str: str) -> str:
    """Return the next NYSE trading day strictly after date_str, as YYYY-MM-DD."""
    start = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    end   = (pd.Timestamp(date_str) + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    valid = _nyse().valid_days(start_date=start, end_date=end)
    if len(valid) == 0:
        raise ValueError(f"No trading day found within 21 days after {date_str}")
    return _to_date_str(valid[0])


def add_trading_days(date_str: str, n: int) -> str:
    """
    Return the date that is n NYSE trading sessions after date_str.
    date_str itself is not counted, even if it is a trading day.
    """
    start = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    end   = (pd.Timestamp(date_str) + pd.Timedelta(days=n * 3 + 21)).strftime("%Y-%m-%d")
    valid = _nyse().valid_days(start_date=start, end_date=end)
    if len(valid) < n:
        raise ValueError(f"Fewer than {n} trading days found after {date_str}")
    return _to_date_str(valid[n - 1])
