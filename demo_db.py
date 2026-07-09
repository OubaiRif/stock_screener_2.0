"""
demo_db.py — Generate a sanitized demo database for Stock Screener 2.0.
Run from: ~/Desktop/stock_screener_2.0/
Usage:    python3 demo_db.py

What it does:
  1. Copies screener.db → demo_screener.db
  2. Removes all personal data (positions, trades, journal, account balance)
  3. Cleans up orphaned/empty tickers
  4. Assigns proper watchlist_types to all tickers
  5. Populates fictional but realistic portfolio positions
  6. Adds sample journal entries
  7. Adds sample gold position
"""

import sys, os, sqlite3, shutil
from datetime import datetime, date, timedelta
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SOURCE_DB = "screener.db"
DEMO_DB   = "demo_screener.db"

def get_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def main():
    print("=" * 60)
    print("  DEMO DB GENERATOR — Stock Screener 2.0")
    print("=" * 60)

    # 1. Copy source DB
    if not os.path.exists(SOURCE_DB):
        print(f"❌ {SOURCE_DB} not found")
        sys.exit(1)

    shutil.copy2(SOURCE_DB, DEMO_DB)
    print(f"✓ Copied {SOURCE_DB} → {DEMO_DB}")

    conn = get_conn(DEMO_DB)

    # 2. Remove all personal data
    print("\n  Removing personal data...")

    # Clear portfolio positions
    conn.execute("""
        UPDATE stocks SET
            in_portfolio = 0,
            shares_held  = NULL,
            avg_cost     = NULL,
            notes        = NULL
    """)

    # Clear trading journal
    conn.execute("DELETE FROM trading_journal")

    # Clear gold position and trades
    conn.execute("DELETE FROM gold_position")
    conn.execute("DELETE FROM gold_trades")

    # Clear account balance
    conn.execute("DELETE FROM macro_data WHERE series_id = 'account_balance'")

    print("  ✓ Portfolio positions cleared")
    print("  ✓ Trading journal cleared")
    print("  ✓ Gold position cleared")
    print("  ✓ Account balance cleared")

    # 3. Remove orphaned/empty tickers
    empty_tickers = ["OTLK", "PYDN"]
    for t in empty_tickers:
        conn.execute("DELETE FROM stocks WHERE ticker=?", (t,))
        conn.execute("DELETE FROM price_history WHERE ticker=?", (t,))
        conn.execute("DELETE FROM indicators WHERE ticker=?", (t,))
        conn.execute("DELETE FROM predictions WHERE ticker=?", (t,))
        conn.execute("DELETE FROM accuracy_log WHERE ticker=?", (t,))
        conn.execute("DELETE FROM sentiment WHERE ticker=?", (t,))
        conn.execute("DELETE FROM headlines WHERE ticker=?", (t,))
        conn.execute("DELETE FROM fundamentals WHERE ticker=?", (t,))
    print(f"  ✓ Removed empty tickers: {empty_tickers}")

    # 4. Fix watchlist_types
    watchlist_assignments = {
        "AAPL": ("stock", 0),
        "META": ("stock", 0),
        "GLD":  ("etf",   1),
        "IAU":  ("etf",   1),
        "QQQ":  ("etf",   1),
        "VOO":  ("etf",   1),
        "ADIL": ("swing", 0),
        "CHPT": ("swing", 0),
        "MARA": ("swing", 0),
        "PDYN": ("swing", 0),
        "PLUG": ("swing", 0),
        "SOUN": ("swing", 0),
        "TPET": ("swing", 0),
    }
    for ticker, (wl_type, is_etf) in watchlist_assignments.items():
        conn.execute("""
            UPDATE stocks SET watchlist_type=?, is_etf=?
            WHERE ticker=?
        """, (wl_type, is_etf, ticker))
    print("  ✓ Watchlist types fixed")

    # 5. Add fictional but realistic portfolio positions
    # Using real tickers with made-up cost basis
    demo_positions = [
        # ticker, shares, avg_cost, watchlist_type
        ("AAPL",  15,   178.50, "stock"),
        ("IAU",   40,    38.20, "etf"),
        ("PLUG",  200,    3.15, "swing"),
        ("MARA",  100,   12.40, "swing"),
        ("SOUN",  250,    4.80, "swing"),
    ]
    for ticker, shares, avg_cost, wl_type in demo_positions:
        conn.execute("""
            UPDATE stocks SET
                in_portfolio = 1,
                shares_held  = ?,
                avg_cost     = ?,
                watchlist_type = ?
            WHERE ticker = ?
        """, (shares, avg_cost, wl_type, ticker))
    print("  ✓ Demo portfolio positions added")

    # 6. Add demo gold position
    conn.execute("""
        INSERT OR REPLACE INTO gold_position (ticker, shares, avg_cost, updated_at)
        VALUES ('IAU', 40, 38.20, datetime('now'))
    """)

    # Add a few demo gold trades
    demo_gold_trades = [
        ("2024-06-15", "BUY",  20, 36.10, 722.00,   20, 36.10),
        ("2024-09-03", "BUY",  10, 37.50, 375.00,   30, 36.57),
        ("2025-01-20", "BUY",  10, 40.50, 405.00,   40, 37.32),
    ]
    for dt, action, shares, price, total, shares_after, avg_after in demo_gold_trades:
        conn.execute("""
            INSERT INTO gold_trades
                (ticker, action, shares, price, total_value,
                 shares_after, avg_cost_after, traded_at)
            VALUES ('IAU',?,?,?,?,?,?,?)
        """, (action, shares, price, total, shares_after, avg_after, dt + "T09:30:00"))
    print("  ✓ Demo gold position added")

    # 7. Add demo journal entries
    random.seed(42)
    demo_journal = [
        ("AAPL", "BUY",  15,  178.50, "Initial position — strong fundamentals"),
        ("PLUG", "BUY",  100,  3.40,  "Rubber band setup — oversold entry"),
        ("PLUG", "BUY",  100,  2.90,  "Adding to position on dip"),
        ("MARA", "BUY",  100, 12.40,  "Breakout volume signal"),
        ("SOUN", "BUY",  250,  4.80,  "Swing entry — RSI oversold"),
        ("PLUG", "SELL",  50,  3.80,  "Partial exit — took profit on bounce"),
        ("IAU",  "BUY",   20, 36.10,  "Gold DCA — macro hedge"),
        ("IAU",  "BUY",   10, 37.50,  "Gold DCA — adding on strength"),
        ("IAU",  "BUY",   10, 40.50,  "Gold DCA — Fed pivot thesis"),
    ]

    signals = ["BULLISH", "NEUTRAL", "BEARISH", "BULLISH", "BULLISH",
               "NEUTRAL", "BULLISH", "BULLISH", "BULLISH"]
    scores  = [72, 55, 48, 68, 63, 51, 70, 74, 71]

    base_date = date(2024, 6, 15)
    for i, (ticker, action, shares, price, notes) in enumerate(demo_journal):
        traded_at = (base_date + timedelta(days=i*20)).strftime("%Y-%m-%dT09:30:00")
        conn.execute("""
            INSERT INTO trading_journal
                (ticker, action, shares, price, total_value,
                 signal_at_trade, score_at_trade, notes, traded_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (ticker, action, shares, price, round(shares*price, 2),
              signals[i], scores[i], notes, traded_at))
    print("  ✓ Demo journal entries added")

    # 8. Add demo account balance
    conn.execute("""
        INSERT OR REPLACE INTO macro_data (series_id, date, value, source)
        VALUES ('account_balance', date('now'), 12500.00, 'demo')
    """)
    print("  ✓ Demo account balance set ($12,500)")

    conn.commit()
    conn.close()

    # Final size check
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

  Use this DB for Streamlit Cloud deployment.
  Set DEMO_DB_PATH=demo_screener.db in .streamlit/secrets.toml
""")

if __name__ == "__main__":
    main()
