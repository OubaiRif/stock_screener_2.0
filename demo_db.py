"""
demo_db.py — Generate a sanitized demo database for Stock Screener 2.0.
Builds entirely from scratch — does NOT require screener.db to exist.
Safe to run on Streamlit Cloud.

Usage: python3 demo_db.py
"""

import sys, os, sqlite3, json
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEMO_DB = "demo_screener.db"

# ── Demo tickers ──────────────────────────────────────────────────────────────
DEMO_TICKERS = {
    "AAPL": {"watchlist_type": "stock",  "is_etf": 0, "in_portfolio": 1, "shares": 15,  "avg_cost": 178.50},
    "META": {"watchlist_type": "stock",  "is_etf": 0, "in_portfolio": 0, "shares": None,"avg_cost": None},
    "GLD":  {"watchlist_type": "etf",    "is_etf": 1, "in_portfolio": 0, "shares": None,"avg_cost": None},
    "IAU":  {"watchlist_type": "etf",    "is_etf": 1, "in_portfolio": 1, "shares": 40,  "avg_cost": 38.20},
    "QQQ":  {"watchlist_type": "etf",    "is_etf": 1, "in_portfolio": 0, "shares": None,"avg_cost": None},
    "VOO":  {"watchlist_type": "etf",    "is_etf": 1, "in_portfolio": 0, "shares": None,"avg_cost": None},
    "MARA": {"watchlist_type": "swing",  "is_etf": 0, "in_portfolio": 1, "shares": 100, "avg_cost": 12.40},
    "PLUG": {"watchlist_type": "swing",  "is_etf": 0, "in_portfolio": 1, "shares": 200, "avg_cost": 3.15},
    "SOUN": {"watchlist_type": "swing",  "is_etf": 0, "in_portfolio": 1, "shares": 250, "avg_cost": 4.80},
    "ADIL": {"watchlist_type": "swing",  "is_etf": 0, "in_portfolio": 0, "shares": None,"avg_cost": None},
    "CHPT": {"watchlist_type": "swing",  "is_etf": 0, "in_portfolio": 0, "shares": None,"avg_cost": None},
    "PDYN": {"watchlist_type": "swing",  "is_etf": 0, "in_portfolio": 0, "shares": None,"avg_cost": None},
    "TPET": {"watchlist_type": "swing",  "is_etf": 0, "in_portfolio": 0, "shares": None,"avg_cost": None},
}


def get_conn():
    conn = sqlite3.connect(DEMO_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema():
    """Create all tables from scratch — mirrors engine/db.py exactly."""
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stocks (
        ticker          TEXT PRIMARY KEY,
        name            TEXT,
        sector          TEXT,
        industry        TEXT,
        strategy        TEXT DEFAULT 'unassigned',
        is_etf          INTEGER DEFAULT 0,
        etf_category    TEXT,
        in_portfolio    INTEGER DEFAULT 0,
        avg_cost        REAL,
        shares_held     REAL,
        notes           TEXT,
        watchlist_type  TEXT DEFAULT 'stock',
        added_at        TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker    TEXT NOT NULL,
        date      TEXT NOT NULL,
        open      REAL, high REAL, low REAL, close REAL, adj_close REAL, volume INTEGER,
        UNIQUE(ticker, date),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_intraday (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker    TEXT NOT NULL,
        datetime  TEXT NOT NULL,
        open      REAL, high REAL, low REAL, close REAL, volume INTEGER,
        UNIQUE(ticker, datetime),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS indicators (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL,
        date            TEXT NOT NULL,
        ema_20          REAL, ema_50 REAL, ema_200 REAL,
        macd            REAL, macd_signal REAL, macd_hist REAL,
        adx             REAL, obv REAL, obv_ema REAL,
        rsi             REAL,
        bb_upper        REAL, bb_mid REAL, bb_lower REAL, bb_pct_b REAL,
        zscore          REAL, stoch_k REAL, stoch_d REAL,
        williams_r      REAL, atr REAL,
        rel_volume      REAL, vwap REAL,
        support_20d     REAL, resistance_20d REAL,
        UNIQUE(ticker, date),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fundamentals (
        ticker              TEXT PRIMARY KEY,
        market_cap          REAL, pe_trailing REAL, pe_forward REAL,
        peg_ratio           REAL, eps_trailing REAL, eps_forward REAL,
        revenue_ttm         REAL, gross_margin REAL, profit_margin REAL,
        debt_to_equity      REAL, current_ratio REAL, beta REAL,
        dividend_yield      REAL, float_shares REAL, short_ratio REAL,
        price_to_book       REAL, ebitda REAL,
        next_earnings_date  TEXT,
        fetched_at          TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sentiment (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL,
        date            TEXT NOT NULL,
        source          TEXT NOT NULL,
        score           REAL,
        mention_count   INTEGER,
        bullish_pct     REAL,
        bearish_pct     REAL,
        sample_headline TEXT,
        fetched_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(ticker, date, source),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker              TEXT NOT NULL,
        date                TEXT NOT NULL,
        generated_at        TEXT DEFAULT (datetime('now')),
        prediction_type     TEXT NOT NULL,
        price_low           REAL, price_mid REAL, price_high REAL,
        signal              TEXT,
        confidence          REAL,
        technical_score     REAL, fundamental_score REAL,
        sentiment_score     REAL, composite_score REAL,
        strategy_signal     TEXT,
        actual_close        REAL, actual_high REAL, actual_low REAL,
        UNIQUE(ticker, date, prediction_type),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS headlines (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL,
        date            TEXT NOT NULL,
        source          TEXT NOT NULL,
        headline        TEXT NOT NULL,
        sentiment_score REAL,
        url             TEXT,
        published_at    TEXT,
        fetched_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ml_models (
        ticker        TEXT PRIMARY KEY,
        model_blob    BLOB NOT NULL,
        feature_cols  TEXT NOT NULL,
        trained_on    TEXT NOT NULL,
        n_samples     INTEGER,
        val_mae       REAL,
        val_accuracy  REAL,
        trained_at    TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gold_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL DEFAULT 'IAU',
        action          TEXT NOT NULL,
        shares          REAL NOT NULL,
        price           REAL NOT NULL,
        total_value     REAL NOT NULL,
        shares_after    REAL,
        avg_cost_after  REAL,
        traded_at       TEXT DEFAULT (datetime('now')),
        notes           TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gold_position (
        ticker      TEXT PRIMARY KEY,
        shares      REAL NOT NULL DEFAULT 0,
        avg_cost    REAL NOT NULL DEFAULT 0,
        updated_at  TEXT DEFAULT (datetime('now'))
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS macro_data (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        series_id   TEXT NOT NULL,
        date        TEXT NOT NULL,
        value       REAL,
        source      TEXT NOT NULL,
        fetched_at  TEXT DEFAULT (datetime('now')),
        UNIQUE(series_id, date)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS etf_signals (
        ticker          TEXT NOT NULL,
        date            TEXT NOT NULL,
        category        TEXT,
        signal          TEXT,
        score           INTEGER,
        confidence      REAL,
        drivers_bull    TEXT,
        drivers_bear    TEXT,
        nav_premium     REAL,
        momentum_1m     REAL,
        momentum_3m     REAL,
        momentum_6m     REAL,
        momentum_12m    REAL,
        generated_at    TEXT DEFAULT (datetime('now')),
        UNIQUE(ticker, date),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS accuracy_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL,
        date            TEXT NOT NULL,
        prediction_type TEXT NOT NULL,
        predicted_mid   REAL, actual_close REAL, error_pct REAL,
        signal          TEXT, signal_correct INTEGER,
        logged_at       TEXT DEFAULT (datetime('now'))\
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gold_swing_cache (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        computed_at TEXT NOT NULL,
        signal      TEXT,
        score       REAL,
        confidence  REAL,
        payload     TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trading_journal (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL,
        action          TEXT NOT NULL,
        shares          REAL,
        price           REAL,
        total_value     REAL,
        signal_at_trade TEXT,
        score_at_trade  REAL,
        notes           TEXT,
        traded_at       TEXT DEFAULT (datetime('now'))
    )""")

    conn.commit()
    conn.close()
    print("  ✓ Schema created")


def populate_tickers():
    print("\n[1] Inserting demo tickers...")
    conn = get_conn()
    for ticker, meta in DEMO_TICKERS.items():
        conn.execute("""
            INSERT OR IGNORE INTO stocks
                (ticker, watchlist_type, is_etf, in_portfolio, shares_held, avg_cost)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ticker, meta["watchlist_type"], meta["is_etf"],
              1 if meta["in_portfolio"] else 0,
              meta["shares"], meta["avg_cost"]))
    conn.commit()
    conn.close()
    print(f"  ✓ {len(DEMO_TICKERS)} tickers inserted")


def fetch_price_data():
    """Fetch 1 year of daily price history + indicators for all tickers."""
    print("\n[2] Fetching price history and indicators...")

    # Temporarily override DB_PATH so engine modules write to demo DB
    import config as _cfg
    _orig_db = _cfg.DB_PATH
    _cfg.DB_PATH = os.path.abspath(DEMO_DB)

    try:
        from engine.fetcher    import fetch_daily_history, fetch_fundamentals
        from engine.indicators import refresh_indicators

        for ticker in DEMO_TICKERS:
            try:
                fetch_daily_history(ticker)
                fetch_fundamentals(ticker)
                refresh_indicators(ticker)
                print(f"  ✓ {ticker}")
            except Exception as e:
                print(f"  ⚠ {ticker}: {e}")
    finally:
        _cfg.DB_PATH = _orig_db


def add_portfolio_positions():
    print("\n[3] Setting portfolio positions...")
    conn = get_conn()
    for ticker, meta in DEMO_TICKERS.items():
        if meta["in_portfolio"]:
            conn.execute("""
                UPDATE stocks SET in_portfolio=1, shares_held=?, avg_cost=?
                WHERE ticker=?
            """, (meta["shares"], meta["avg_cost"], ticker))
    conn.commit()
    conn.close()
    print("  ✓ Portfolio positions set")


def add_gold_position():
    print("\n[4] Adding demo gold position...")
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO gold_position (ticker, shares, avg_cost, updated_at)
        VALUES ('IAU', 40, 38.20, datetime('now'))
    """)
    demo_trades = [
        ("2024-06-15", "BUY",  20, 36.10, 722.00,   20, 36.10),
        ("2024-09-03", "BUY",  10, 37.50, 375.00,   30, 36.57),
        ("2025-01-20", "BUY",  10, 40.50, 405.00,   40, 37.32),
    ]
    for dt, action, shares, price, total, shares_after, avg_after in demo_trades:
        conn.execute("""
            INSERT INTO gold_trades
                (ticker, action, shares, price, total_value,
                 shares_after, avg_cost_after, traded_at)
            VALUES ('IAU',?,?,?,?,?,?,?)
        """, (action, shares, price, total, shares_after, avg_after, dt + "T09:30:00"))
    conn.commit()
    conn.close()
    print("  ✓ Gold position and trades added")


def add_journal():
    print("\n[5] Adding demo journal entries...")
    entries = [
        ("AAPL", "BUY",  15,  178.50, "BULLISH", 72, "Initial position — strong fundamentals",  "2024-06-15T09:30:00"),
        ("PLUG", "BUY",  100,   3.40, "NEUTRAL",  55, "Rubber band setup — oversold entry",     "2024-07-05T09:30:00"),
        ("PLUG", "BUY",  100,   2.90, "BEARISH",  48, "Adding to position on dip",              "2024-07-25T09:30:00"),
        ("MARA", "BUY",  100,  12.40, "BULLISH",  68, "Breakout volume signal",                 "2024-08-14T09:30:00"),
        ("SOUN", "BUY",  250,   4.80, "BULLISH",  63, "Swing entry — RSI oversold",             "2024-09-03T09:30:00"),
        ("PLUG", "SELL",  50,   3.80, "NEUTRAL",  51, "Partial exit — took profit on bounce",   "2024-10-10T09:30:00"),
        ("IAU",  "BUY",   20,  36.10, "BULLISH",  70, "Gold DCA — macro hedge",                 "2024-06-15T09:30:00"),
        ("IAU",  "BUY",   10,  37.50, "BULLISH",  74, "Gold DCA — adding on strength",          "2024-09-03T09:30:00"),
        ("IAU",  "BUY",   10,  40.50, "BULLISH",  71, "Gold DCA — Fed pivot thesis",            "2025-01-20T09:30:00"),
    ]
    conn = get_conn()
    for ticker, action, shares, price, signal, score, notes, traded_at in entries:
        conn.execute("""
            INSERT INTO trading_journal
                (ticker, action, shares, price, total_value,
                 signal_at_trade, score_at_trade, notes, traded_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (ticker, action, shares, price, round(shares * price, 2),
              signal, score, notes, traded_at))
    conn.commit()
    conn.close()
    print(f"  ✓ {len(entries)} journal entries added")


def add_account_balance():
    print("\n[6] Setting demo account balance...")
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO macro_data (series_id, date, value, source)
        VALUES ('account_balance', date('now'), 12500.00, 'demo')
    """)
    conn.commit()
    conn.close()
    print("  ✓ Account balance set ($12,500)")


def seed_gold_swing_cache():
    print("\n[7] Seeding gold_swing_cache...")
    conn = get_conn()
    payload = {
        "signal": "HOLD", "score": 58, "confidence": 0.58,
        "bull": ["Price above 200-day EMA — long-term uptrend intact",
                 "RSI neutral (52.3) — no extreme reading"],
        "bear": ["MACD slightly bearish — mild downward pressure"],
        "note": "Seeded by demo_db.py — updates after first nightly run"
    }
    conn.execute(
        "INSERT INTO gold_swing_cache (computed_at, signal, score, confidence, payload) "
        "VALUES (datetime('now'), ?, ?, ?, ?)",
        (payload["signal"], payload["score"], payload["confidence"], json.dumps(payload))
    )
    conn.commit()
    conn.close()
    print("  ✓ gold_swing_cache seeded")


def main():
    print("=" * 60)
    print("  DEMO DB GENERATOR — Stock Screener 2.0")
    print("  Building from scratch (no screener.db required)")
    print("=" * 60)

    if os.path.exists(DEMO_DB):
        os.remove(DEMO_DB)
        print(f"\n  Removed existing {DEMO_DB}")

    print("\n[0] Initialising schema...")
    init_schema()

    populate_tickers()
    fetch_price_data()
    add_portfolio_positions()
    add_gold_position()
    add_journal()
    add_account_balance()
    seed_gold_swing_cache()

    size_mb = os.path.getsize(DEMO_DB) / 1024 / 1024
    print(f"\n{'='*60}")
    print(f"  DEMO DB READY: {DEMO_DB} ({size_mb:.1f} MB)")
    print(f"{'='*60}")
    print("""
  Demo portfolio:
    AAPL  — 15 shares @ $178.50  (Stock)
    IAU   — 40 shares @ $38.20   (ETF / Gold)
    PLUG  — 200 shares @ $3.15   (Swing)
    MARA  — 100 shares @ $12.40  (Swing)
    SOUN  — 250 shares @ $4.80   (Swing)

  Set DEMO_MODE=true in .streamlit/secrets.toml for Streamlit Cloud.
""")


if __name__ == "__main__":
    main()
