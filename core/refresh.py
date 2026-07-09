"""
core/refresh.py — Shared data refresh pipeline for Stock Screener 2.0.
Import and call these instead of duplicating pipeline logic in each page.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

from engine.fetcher    import fetch_daily_history, fetch_fundamentals
from engine.indicators import refresh_indicators
from engine.sentiment  import fetch_sentiment_batch
from engine.predictor  import predict


def _refresh_one(ticker: str):
    fetch_daily_history(ticker)
    fetch_fundamentals(ticker)
    refresh_indicators(ticker)
    return ticker


def run_full_refresh(tickers: list, include_sentiment: bool = True,
                     include_predictions: bool = True):
    """
    Full pipeline refresh with a Streamlit progress bar.
    Safe to call from any page.
    """
    if not tickers:
        st.warning("No tickers to refresh.")
        return

    prog = st.progress(0, text="Starting…")
    n, done = len(tickers), 0

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_refresh_one, t): t for t in tickers}
        for f in as_completed(futures):
            done += 1
            prog.progress(done / n * 0.65,
                          text=f"Fetched {futures[f]} ({done}/{n})")
            try:
                f.result()
            except Exception as e:
                st.warning(f"{futures[f]}: {e}")

    if include_sentiment:
        prog.progress(0.75, text="Sentiment…")
        try:
            fetch_sentiment_batch(tickers)
        except Exception as e:
            st.warning(f"Sentiment: {e}")

    if include_predictions:
        prog.progress(0.9, text="Predictions…")
        for t in tickers:
            try:
                predict(t)
            except Exception as e:
                st.warning(f"{t}: {e}")

    prog.progress(1.0, text="Done.")
    time.sleep(0.4)
    prog.empty()
    st.session_state["last_refresh"] = time.time()


def run_ml_train(tickers: list):
    """
    Retrain XGBoost models for a list of tickers with progress bar.
    Returns list of (ticker, result) tuples.
    """
    from engine.ml_predictor import train as ml_train

    results = []
    prog = st.progress(0, text="Starting ML training…")
    for i, t in enumerate(tickers):
        prog.progress((i + 1) / len(tickers),
                      text=f"Training {t} ({i+1}/{len(tickers)})…")
        try:
            r = ml_train(t)
            results.append((t, r))
        except Exception as e:
            results.append((t, {"error": str(e)}))
    prog.empty()

    ok  = [(t, r) for t, r in results if "error" not in r]
    err = [(t, r) for t, r in results if "error" in r]
    if ok:
        st.success(
            f"✓ Trained {len(ok)} models: "
            + ", ".join(f"{t} ({r['val_accuracy']:.1f}%)" for t, r in ok)
        )
    if err:
        st.warning(f"Failed: {', '.join(t for t, _ in err)}")
    return results
