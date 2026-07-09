"""
run.py — CLI entry point for the stock screener.
Usage: python run.py [command] [args]
Commands: init, add, remove, strategy, suggest, refresh, sentiment, predict, status, nightly
"""
import sys, os, logging, argparse
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from config import STRATEGIES, LOG_PATH, LOG_LEVEL
from engine.db import init_db, get_watchlist, upsert_stock, remove_stock, set_strategy, get_conn
from engine.fetcher import fetch_daily_history, fetch_fundamentals
from engine.indicators import refresh_indicators
from engine.sentiment import fetch_sentiment_batch, get_latest_sentiment
from engine.predictor import predict
from engine.strategy_advisor import suggest_strategy
from engine.accuracy import score_predictions
from engine.ml_predictor import train, train_all
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("run")

# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_init():
    init_db(); print("✓ Database initialized.")

def cmd_add(tickers):
    for t in tickers:
        upsert_stock(t.upper())
        try: fetch_fundamentals(t.upper()); print(f"✓ Added {t.upper()}")
        except Exception as e: print(f"  [warn] {t.upper()} added but fundamentals failed: {e}")
        try:
            fetch_daily_history(t.upper())
            cmd_suggest([t.upper()])
        except Exception as e: logger.debug("Strategy suggestion skipped for %s: %s", t.upper(), e)

def cmd_remove(tickers):
    for t in tickers: remove_stock(t.upper()); print(f"✓ Removed {t.upper()}")

def cmd_strategy(ticker, strategy):
    if strategy not in STRATEGIES:
        print(f"Invalid strategy. Choose: {STRATEGIES}"); sys.exit(1)
    set_strategy(ticker, strategy); print(f"✓ {ticker.upper()} → {strategy}")

def cmd_suggest(tickers=None):
    if not tickers: tickers = [s["ticker"] for s in get_watchlist()]
    print(f"\n{'─'*60}\n  STRATEGY SUGGESTIONS\n{'─'*60}")
    for t in tickers:
        try:
            r   = suggest_strategy(t)
            bar = _bar(r["confidence"])
            print(f"\n  {r['ticker']} → {r['strategy'].upper().replace('_',' ')}")
            print(f"  Confidence: {bar} {r['confidence']}%")
            print(f"  Reason: {r['reason']}")
            print(f"  Scores: " + "  ".join(f"{k.replace('_',' ')}={v}" for k,v in r["scores"].items()))
        except Exception as e: print(f"  {t}: {e}")
    print(f"{'─'*60}\n  Options: {' | '.join(STRATEGIES)}\n")

def cmd_refresh(tickers=None):
    if not tickers: tickers = [s["ticker"] for s in get_watchlist()]
    if not tickers: print("Watchlist empty."); return
    print(f"Refreshing {len(tickers)} tickers…")
    for t in tickers:
        print(f"  → {t}", end=" ", flush=True)
        try: fetch_daily_history(t); fetch_fundamentals(t); refresh_indicators(t); print("✓")
        except Exception as e: print(f"✗ {e}")

def cmd_sentiment(tickers=None):
    if not tickers: tickers = [s["ticker"] for s in get_watchlist()]
    if not tickers: print("Watchlist empty."); return
    print(f"Fetching sentiment for {len(tickers)} tickers…")
    fetch_sentiment_batch(tickers)
    for t in tickers:
        s  = get_latest_sentiment(t)
        st = s.get("sources",{}).get("stocktwits",{})
        if s["available"]:
            print(f"  {t:<8} {s['overall_label']:<10} score={s['avg_score']:+.2f}  "
                  f"StockTwits: {st.get('mention_count',0)} msgs")

def cmd_predict(tickers=None):
    if not tickers: tickers = [s["ticker"] for s in get_watchlist()]
    if not tickers: print("Watchlist empty."); return
    print(f"\n{'='*60}\n  PREDICTIONS — {date.today().isoformat()}\n{'='*60}")
    for t in tickers:
        try:
            r = predict(t)
            print(f"\n  {r['ticker']} | {r['signal']} | {r['confidence']:.0f}% conf | "
                  f"Strategy: {r['strategy']} ({r['strategy_signal']})")
            print(f"  Score: {_bar(r['composite_score'])} {r['composite_score']:.1f}/100  "
                  f"[T:{r['technical_score']:.0f} F:{r['fundamental_score']:.0f} S:{r['sentiment_score']:.0f}]")
            if r.get("price_mid"):
                print(f"  Range: ${r['price_low']:.2f} ← ${r['price_mid']:.2f} → ${r['price_high']:.2f}")
        except Exception as e: logger.error("Prediction failed for %s: %s", t, e)

def cmd_status():
    today = date.today().isoformat()
    conn  = get_conn()
    rows  = conn.execute("""
        SELECT s.ticker,s.strategy,s.in_portfolio,
               p.signal,p.confidence,p.composite_score,p.price_low,p.price_mid,p.price_high
        FROM stocks s LEFT JOIN predictions p
             ON s.ticker=p.ticker AND p.date=? AND p.prediction_type='next_day'
        ORDER BY s.ticker
    """, (today,)).fetchall()
    conn.close()
    print(f"\n{'='*70}\n  STATUS — {today}\n{'='*70}")
    for r in rows:
        pf = " [P]" if r["in_portfolio"] else ""
        print(f"  {r['ticker']:<8}{pf:<4} {r['strategy'] or 'unassigned':<16} "
              f"{r['signal'] or '—':<10} {r['confidence'] or 0:>5.1f}% "
              f"{r['composite_score'] or 0:>6.1f} "
              f"{r['price_mid'] or 0:>8.2f}")

def cmd_train(tickers=None):
    if not tickers: tickers = [s["ticker"] for s in get_watchlist()]
    if not tickers: print("Watchlist empty."); return
    print(f"Training ML models for {len(tickers)} tickers…")
    for t in tickers:
        print(f"  → {t}", end=" ", flush=True)
        try:
            r = train(t)
            if "error" in r:
                print(f"✗ {r['error']}")
            else:
                print(f"✓ MAE={r['val_mae']:.2f}  Acc={r['val_accuracy']:.1f}%  n={r['n_samples']}")
        except Exception as e:
            print(f"✗ {e}")


def cmd_nightly():
    print("Starting nightly pipeline…")
    tickers = [s["ticker"] for s in get_watchlist()]
    if not tickers: print("Watchlist empty."); return
    cmd_refresh(tickers)
    cmd_sentiment(tickers)
    cmd_train(tickers)
    cmd_predict(tickers)
    # Refresh ETF signals
    etfs = [s for s in get_watchlist() if s.get("is_etf")]
    if etfs:
        print(f"Refreshing ETF signals for {len(etfs)} ETFs…")
        try:
            from engine.etf_signals import refresh_etf_signals
            results = refresh_etf_signals(etfs)
            for t in etfs:
                r = results.get(t["ticker"], {})
                print(f"  {t['ticker']}: {r.get('signal','—')} ({r.get('score','—')}/100)")
        except Exception as e:
            print(f"  ETF signals failed: {e}")
    try:
        results = score_predictions()
        if results:
            correct = sum(1 for r in results if r.get("signal_correct")==1)
            print(f"  Scored {len(results)} predictions — direction: {correct}/{len(results)}")
    except Exception as e: print(f"  Accuracy scoring failed: {e}")
    print("Nightly pipeline complete.")

def _bar(score):
    f = int((score or 0)/10)
    return "[" + "█"*f + "░"*(10-f) + "]"

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["init","add","remove","strategy","suggest",
                                        "refresh","sentiment","train","predict","status","nightly"])
    p.add_argument("args", nargs="*")
    a = p.parse_args()
    dispatch = {
        "init":      lambda: cmd_init(),
        "add":       lambda: cmd_add(a.args),
        "remove":    lambda: cmd_remove(a.args),
        "strategy":  lambda: cmd_strategy(a.args[0], a.args[1]) if len(a.args)>=2 else print("Usage: strategy TICKER name"),
        "suggest":   lambda: cmd_suggest(a.args or None),
        "refresh":   lambda: cmd_refresh(a.args or None),
        "sentiment": lambda: cmd_sentiment(a.args or None),
        "train":     lambda: cmd_train(a.args or None),
        "predict":   lambda: cmd_predict(a.args or None),
        "status":    lambda: cmd_status(),
        "nightly":   lambda: cmd_nightly(),
    }
    dispatch[a.command]()
