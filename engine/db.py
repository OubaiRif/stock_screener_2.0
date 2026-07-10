"""
engine/db.py — SQLite schema and low-level helpers.
"""
import sqlite3, logging, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """Create all tables if they don't exist. Safe to run on every startup."""
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
        added_at        TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker      TEXT NOT NULL,
        date        TEXT NOT NULL,
        open        REAL, high REAL, low REAL, close REAL, adj_close REAL, volume INTEGER,
        UNIQUE(ticker, date),
        FOREIGN KEY(ticker) REFERENCES stocks(ticker)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_intraday (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker      TEXT NOT NULL,
        datetime    TEXT NOT NULL,
        open        REAL, high REAL, low REAL, close REAL, volume INTEGER,
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
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker      TEXT NOT NULL DEFAULT 'IAU',
        action      TEXT NOT NULL,
        shares      REAL NOT NULL,
        price       REAL NOT NULL,
        total_value REAL NOT NULL,
        shares_after    REAL,
        avg_cost_after  REAL,
        traded_at   TEXT DEFAULT (datetime('now')),
        notes       TEXT
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
        logged_at       TEXT DEFAULT (datetime('now'))
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

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


# ── Convenience helpers ───────────────────────────────────────────────────────

def get_watchlist() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM stocks ORDER BY ticker").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_stock(ticker: str, **kwargs) -> None:
    conn = get_conn()
    ticker = ticker.upper()
    cols = ["ticker"] + list(kwargs.keys())
    vals = [ticker] + list(kwargs.values())
    placeholders = ", ".join(["?"] * len(vals))
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in kwargs.keys())
    update_clause = (update_clause + ", " if update_clause else "") + "updated_at = datetime('now')"
    sql = f"""
        INSERT INTO stocks ({", ".join(cols)}) VALUES ({placeholders})
        ON CONFLICT(ticker) DO UPDATE SET {update_clause}
    """
    conn.execute(sql, vals)
    conn.commit()
    conn.close()


def remove_stock(ticker: str) -> None:
    conn = get_conn()
    t = ticker.upper()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM price_history   WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM price_intraday  WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM indicators      WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM fundamentals    WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM sentiment       WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM headlines       WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM predictions     WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM ml_models       WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM etf_signals     WHERE ticker = ?", (t,))
    conn.execute("DELETE FROM stocks          WHERE ticker = ?", (t,))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()


def set_strategy(ticker: str, strategy: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE stocks SET strategy=?, updated_at=datetime('now') WHERE ticker=?",
        (strategy, ticker.upper())
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level="INFO")
    init_db()
    print("DB ready.")
