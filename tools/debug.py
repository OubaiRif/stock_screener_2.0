"""
debug.py — Run this from your stock_screener folder to diagnose chart issues.
Usage: python debug.py NVDA
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "NVDA"
print(f"\n{'='*60}")
print(f"  DEBUG REPORT FOR {ticker}")
print(f"{'='*60}\n")

# 1. Check DB price history
from engine.db import get_conn
conn = get_conn()

rows = conn.execute(
    "SELECT COUNT(*) as n, MIN(date) as earliest, MAX(date) as latest "
    "FROM price_history WHERE ticker=?", (ticker,)
).fetchone()
print(f"[1] Price history in DB:")
print(f"    Rows: {rows['n']}  |  {rows['earliest']} → {rows['latest']}")

# 2. Check indicators table
irows = conn.execute(
    "SELECT COUNT(*) as n, MIN(date) as earliest, MAX(date) as latest "
    "FROM indicators WHERE ticker=?", (ticker,)
).fetchone()
print(f"\n[2] Indicators in DB:")
print(f"    Rows: {irows['n']}  |  {irows['earliest']} → {irows['latest']}")

# Sample latest indicator row
if irows['n'] > 0:
    latest = conn.execute(
        "SELECT * FROM indicators WHERE ticker=? ORDER BY date DESC LIMIT 1",
        (ticker,)
    ).fetchone()
    print(f"    Latest row sample: RSI={latest['rsi']}  MACD={latest['macd']}  EMA20={latest['ema_20']}")

conn.close()

# 3. Load via fetcher and check DataFrame
print(f"\n[3] load_daily_history output:")
from engine.fetcher import load_daily_history
df = load_daily_history(ticker, days=365)
print(f"    Shape: {df.shape}")
print(f"    Columns: {list(df.columns)}")
if not df.empty:
    print(f"    Index type: {type(df.index[0])}")
    print(f"    First date: {df.index[0]}  Last date: {df.index[-1]}")
    print(f"    Sample Close: {df['Close'].tail(3).values}")

# 4. Compute indicators and check
print(f"\n[4] compute_indicators output:")
from engine.indicators import compute_indicators
import pandas as pd
if not df.empty:
    df2 = compute_indicators(ticker, df)
    print(f"    Shape after indicators: {df2.shape}")
    print(f"    Has 'macd': {'macd' in df2.columns}, null count: {df2['macd'].isna().sum() if 'macd' in df2.columns else 'N/A'}")
    print(f"    Has 'rsi':  {'rsi' in df2.columns},  null count: {df2['rsi'].isna().sum() if 'rsi' in df2.columns else 'N/A'}")
    print(f"    Last 3 MACD: {df2['macd'].dropna().tail(3).values if 'macd' in df2.columns else 'N/A'}")
    print(f"    Last 3 RSI:  {df2['rsi'].dropna().tail(3).values if 'rsi' in df2.columns else 'N/A'}")

    # 5. Simulate the display trim
    print(f"\n[5] Display trim (30 days):")
    cutoff = (pd.Timestamp.now() - pd.Timedelta(days=30)).normalize()
    df3 = df2[df2.index >= cutoff]
    print(f"    Cutoff: {cutoff}")
    print(f"    Rows after trim: {len(df3)}")
    print(f"    Index tz-aware: {df2.index.tz}")
    if len(df3) == 0:
        print("    *** PROBLEM: trim is removing all rows ***")
        print(f"    Index sample: {df2.index[-3:]}")
        print(f"    Cutoff vs index: cutoff={cutoff}, last_date={df2.index[-1]}")
else:
    print("    *** PROBLEM: DataFrame is empty after load ***")

print(f"\n{'='*60}\n")
