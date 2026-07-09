"""
core/db_queries.py — All shared database reads for Stock Screener 2.0.
Every page imports from here. Never duplicate a DB query in a page file.
"""
from datetime import date
from engine.db import get_conn

def _is_demo():
    try:
        from config import DEMO_MODE
        return DEMO_MODE
    except Exception:
        return False


# ── Price ─────────────────────────────────────────────────────────────────────

def get_current_price(ticker: str):
    """Latest close price for a ticker."""
    conn = get_conn()
    row  = conn.execute(
        "SELECT close FROM price_history WHERE ticker=? ORDER BY date DESC LIMIT 1",
        (ticker,)
    ).fetchone()
    conn.close()
    return row["close"] if row else None


def get_price_history(ticker: str, limit: int = 9999):
    """Raw price history rows, newest first."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT date,open,high,low,close,adj_close,volume FROM price_history "
        "WHERE ticker=? ORDER BY date DESC LIMIT ?",
        (ticker, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist_by_type(watchlist_type: str):
    """All tickers of a given watchlist_type ('stock', 'etf', 'swing')."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM stocks WHERE watchlist_type=? ORDER BY ticker",
        (watchlist_type,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portfolio_stocks():
    """All tickers with in_portfolio=1, with latest prediction."""
    today = date.today().isoformat()
    conn  = get_conn()
    rows  = conn.execute("""
        SELECT s.ticker, s.name, s.watchlist_type, s.in_portfolio,
               s.avg_cost, s.shares_held, s.notes, s.strategy,
               p.signal, p.confidence, p.composite_score,
               p.technical_score, p.fundamental_score, p.sentiment_score,
               p.price_low, p.price_mid, p.price_high, p.generated_at
        FROM   stocks s
        LEFT   JOIN predictions p
               ON s.ticker = p.ticker AND p.date=? AND p.prediction_type='next_day'
        WHERE  s.in_portfolio = 1
        ORDER  BY s.ticker
    """, (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_stocks_with_predictions():
    """All watchlisted stocks with today's predictions — used by Home page."""
    today = date.today().isoformat()
    conn  = get_conn()
    rows  = conn.execute("""
        SELECT s.ticker, s.name, s.strategy, s.in_portfolio, s.watchlist_type,
               s.avg_cost, s.shares_held, s.notes,
               p.signal, p.confidence, p.composite_score,
               p.technical_score, p.fundamental_score, p.sentiment_score,
               p.price_low, p.price_mid, p.price_high, p.generated_at
        FROM   stocks s
        LEFT   JOIN predictions p
               ON s.ticker = p.ticker AND p.date=? AND p.prediction_type='next_day'
        ORDER  BY p.composite_score DESC NULLS LAST
    """, (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_swing_stocks_with_predictions():
    """Swing watchlist tickers with today's predictions."""
    today = date.today().isoformat()
    conn  = get_conn()
    rows  = conn.execute("""
        SELECT s.ticker, s.name, s.strategy, s.in_portfolio, s.watchlist_type,
               s.avg_cost, s.shares_held, s.notes,
               p.signal, p.confidence, p.composite_score,
               p.technical_score, p.fundamental_score, p.sentiment_score,
               p.price_low, p.price_mid, p.price_high, p.generated_at
        FROM   stocks s
        LEFT   JOIN predictions p
               ON s.ticker = p.ticker AND p.date=? AND p.prediction_type='next_day'
        WHERE  s.watchlist_type = 'swing'
        ORDER  BY p.composite_score DESC NULLS LAST
    """, (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Volume spikes ─────────────────────────────────────────────────────────────

def get_volume_spikes(min_rel_volume: float = 2.0):
    """Tickers with relative volume above threshold."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT i.ticker, i.rel_volume, i.resistance_20d, ph.close
        FROM   indicators i
        JOIN   price_history ph ON ph.ticker = i.ticker
        WHERE  i.rel_volume >= ?
        AND    i.date  = (SELECT MAX(date) FROM indicators    WHERE ticker=i.ticker)
        AND    ph.date = (SELECT MAX(date) FROM price_history WHERE ticker=ph.ticker)
        ORDER  BY i.rel_volume DESC
    """, (min_rel_volume,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Journal ───────────────────────────────────────────────────────────────────

def get_all_journal_entries(limit: int = 200):
    """All journal entries newest first. In demo mode merges session state."""
    import streamlit as st
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM trading_journal ORDER BY traded_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    entries = [dict(r) for r in rows]
    if _is_demo():
        session_entries = st.session_state.get("demo_journal", [])
        entries = session_entries + entries
    return entries[:limit]


def get_journal_entries_for_ticker(ticker: str, limit: int = 50):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM trading_journal WHERE ticker=? ORDER BY traded_at DESC LIMIT ?
    """, (ticker, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ensure_journal_table():
    """Create trading_journal table if it doesn't exist yet."""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trading_journal (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT    NOT NULL,
            action       TEXT    NOT NULL,   -- BUY | SELL
            shares       REAL    NOT NULL,
            price        REAL    NOT NULL,
            total_value  REAL    NOT NULL,
            signal_at_trade TEXT,            -- BULLISH | BEARISH | NEUTRAL
            score_at_trade  REAL,
            notes        TEXT,
            traded_at    TEXT    NOT NULL    -- ISO datetime
        )
    """)
    conn.commit()
    conn.close()


def log_journal_entry(ticker, action, shares, price, signal_at_trade=None,
                      score_at_trade=None, notes=None):
    """Insert a row into trading_journal. In demo mode writes to session state."""
    from datetime import datetime
    import streamlit as st
    entry = {
        "id": None,
        "ticker": ticker.upper(),
        "action": action.upper(),
        "shares": shares,
        "price": price,
        "total_value": round(shares * price, 4),
        "signal_at_trade": signal_at_trade,
        "score_at_trade": score_at_trade,
        "notes": notes,
        "traded_at": datetime.now().isoformat(timespec="seconds"),
    }
    if _is_demo():
        if "demo_journal" not in st.session_state:
            st.session_state["demo_journal"] = []
        st.session_state["demo_journal"].insert(0, entry)
        return
    ensure_journal_table()
    conn = get_conn()
    conn.execute("""
        INSERT INTO trading_journal
            (ticker, action, shares, price, total_value,
             signal_at_trade, score_at_trade, notes, traded_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        entry["ticker"], entry["action"], entry["shares"], entry["price"],
        entry["total_value"], signal_at_trade, score_at_trade, notes,
        entry["traded_at"]
    ))
    conn.commit()
    conn.close()


# ── ETF helpers ───────────────────────────────────────────────────────────────

def get_etf_list():
    """All ETF tickers (is_etf=1 OR watchlist_type='etf')."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT ticker, name, etf_category, is_etf, watchlist_type
        FROM stocks
        WHERE is_etf = 1 OR watchlist_type = 'etf'
        ORDER BY etf_category, ticker
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_etf_signal_db(ticker: str):
    """Today's ETF signal from the etf_signals table."""
    today = date.today().isoformat()
    conn  = get_conn()
    row   = conn.execute(
        "SELECT * FROM etf_signals WHERE ticker=? AND date=?",
        (ticker, today)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Macro ─────────────────────────────────────────────────────────────────────

def get_macro_series(series_id: str, limit: int = 252):
    conn = get_conn()
    rows = conn.execute("""
        SELECT date, value FROM macro_data WHERE series_id=?
        ORDER BY date DESC LIMIT ?
    """, (series_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
