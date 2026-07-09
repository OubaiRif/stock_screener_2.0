"""
pages/8_Accuracy.py — Prediction accuracy tracker.
Stock Screener 2.0 — uses core/ layer, no sidebar.
"""
import sys, os
from datetime import date, timedelta

import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup import setup_page, render_footer
from engine.db       import get_conn, get_watchlist
from engine.accuracy import get_accuracy_summary, get_recent_log, score_predictions
from utils           import score_color, BULL, BEAR, NEUT

setup_page("Accuracy", "🎯", active_page="8_Accuracy")

st.markdown("""<style>
.acc-card  { background:#1a1f2e; border-radius:10px; padding:18px 22px; margin-bottom:12px; }
.acc-row   { display:flex; justify-content:space-between; align-items:center;
             padding:10px 0; border-bottom:1px solid #252b3b; }
.acc-row:last-child { border-bottom:none; }
.acc-ticker { font-weight:700; font-size:1.0em; width:12%; }
.acc-label  { color:#333; font-size:0.82em; }
.acc-val    { font-family:'IBM Plex Mono',monospace; font-weight:600; }
.big-metric { font-family:'IBM Plex Mono',monospace; font-size:2.2em;
              font-weight:700; line-height:1.1; }
.big-label  { font-size:0.78em; text-transform:uppercase;
              letter-spacing:0.08em; color:#333; margin-top:4px; }
.warning-box { background:#2a1f00; border:1px solid #ffd700; border-radius:8px;
               padding:12px 18px; color:#ffd700; margin-bottom:1rem; font-size:0.88em; }
.strat-row  { display:flex; align-items:center; padding:12px 0;
              border-bottom:1px solid #252b3b; gap:16px; }
.strat-row:last-child { border-bottom:none; }
</style>""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _color_error(pct):
    if pct is None: return "#555"
    if pct < 0.5:   return BULL
    if pct < 2.0:   return NEUT
    return BEAR

def _color_dir(acc):
    if acc is None: return "#555"
    if acc >= 55:   return BULL
    if acc >= 50:   return NEUT
    return BEAR

def _dir_label(acc):
    if acc is None: return "No data"
    if acc >= 55:   return f"{acc:.1f}% ✓ Above random"
    if acc >= 50:   return f"{acc:.1f}% ── Near random"
    return f"{acc:.1f}% ✗ Below random"

def _model_label(ticker, stats):
    ml_acc = stats.get("ml_accuracy")
    if ml_acc and ml_acc >= 50: return f"XGBoost + Rules (accuracy {ml_acc:.1f}%)"
    if ml_acc:                  return f"Rules only (XGBoost {ml_acc:.1f}% — below threshold)"
    return "Rules only (not yet trained)"

def _within_band(log_rows):
    """Check how many times actual close was within predicted low-high range."""
    conn  = get_conn()
    today = date.today().isoformat()
    rows  = conn.execute("""
        SELECT p.ticker, p.price_low, p.price_high, p.actual_close, p.actual_high, p.actual_low
        FROM predictions p
        WHERE p.price_low IS NOT NULL AND p.actual_close IS NOT NULL
    """).fetchall()
    conn.close()
    if not rows: return None, None, None
    total    = len(rows)
    in_band  = sum(1 for r in rows if r["price_low"] <= r["actual_close"] <= r["price_high"])
    above    = sum(1 for r in rows if r["actual_close"] > r["price_high"])
    below    = sum(1 for r in rows if r["actual_close"] < r["price_low"])
    return in_band, above, below

def get_finbert_comparison():
    """Compare FinBERT vs keyword sentiment scores from DB."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT ticker, date, score FROM sentiment
        WHERE source IN ('newsapi','newsapi_finbert')
        ORDER BY date DESC LIMIT 100
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# 🎯 Prediction Accuracy")

# Controls
col_days, col_score, col_space = st.columns([2, 2, 4])
with col_days:
    days = st.selectbox("Lookback Period", [7, 14, 30, 60, 90], index=2,
                        format_func=lambda d: f"Last {d} days")
with col_score:
    if st.button("📊 Score Today's Predictions", use_container_width=True):
        with st.spinner("Fetching actual prices and scoring…"):
            results = score_predictions()
        if results:
            correct = sum(1 for r in results if r.get("signal_correct") == 1)
            st.success(f"Scored {len(results)} predictions — direction correct: {correct}/{len(results)}")
        else:
            st.info("No predictions to score, or market has not closed yet.")

st.markdown("---")

# ── Data ──────────────────────────────────────────────────────────────────────
summary  = get_accuracy_summary(days=days)
log      = get_recent_log(limit=100)
watchlist = get_watchlist()

# Minimum data warning
MIN_DAYS = 10
days_tracked = 0
if log:
    dates = set(r["date"] for r in log)
    days_tracked = len(dates)

if days_tracked < MIN_DAYS:
    st.markdown(
        f'<div class="warning-box">⚠️ <strong>Minimum {MIN_DAYS} trading days needed for meaningful statistics.</strong> '
        f'Currently tracking {days_tracked} day{"s" if days_tracked != 1 else ""}. '
        f'Results below are early indicators only — do not draw conclusions yet.</div>',
        unsafe_allow_html=True)

if not summary.get("available"):
    st.info(f"No accuracy data for the last {days} days. Run a nightly pipeline or score today's predictions above.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — OVERALL SYSTEM
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## Overall System Performance")
ov = summary["overall"]
in_band, above_band, below_band = _within_band(log)

st.markdown('<div class="acc-card">', unsafe_allow_html=True)

# Big metrics row
m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    c = _color_error(ov.get("avg_error_pct"))
    st.markdown(f'<div class="big-metric" style="color:{c}">{ov.get("avg_error_pct","—"):.2f}%</div>'
                f'<div class="big-label">Average Price Error</div>', unsafe_allow_html=True)
with m2:
    c = _color_dir(ov.get("direction_acc"))
    da = ov.get("direction_acc")
    st.markdown(f'<div class="big-metric" style="color:{c}">'
                f'{da:.1f}%</div>' if da else '<div class="big-metric" style="color:#333">—</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="big-label">Direction Accuracy</div>', unsafe_allow_html=True)
with m3:
    w1 = ov.get("within_1pct")
    c  = BULL if (w1 or 0) > 80 else NEUT
    st.markdown(f'<div class="big-metric" style="color:{c}">{w1:.1f}%</div>'
                f'<div class="big-label">Predictions Within 1%</div>'
                if w1 else '<div class="big-metric" style="color:#333">—</div>'
                           '<div class="big-label">Predictions Within 1%</div>',
                unsafe_allow_html=True)
with m4:
    w3 = ov.get("within_3pct")
    c  = BULL if (w3 or 0) > 90 else NEUT
    st.markdown(f'<div class="big-metric" style="color:{c}">{w3:.1f}%</div>'
                f'<div class="big-label">Predictions Within 3%</div>'
                if w3 else '<div class="big-metric" style="color:#333">—</div>'
                           '<div class="big-label">Predictions Within 3%</div>',
                unsafe_allow_html=True)
with m5:
    n = summary.get("total_scored", 0)
    st.markdown(f'<div class="big-metric" style="color:#e0e0e0">{n}</div>'
                f'<div class="big-label">Total Predictions Scored</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# Range band accuracy
if in_band is not None:
    total_band = (in_band or 0) + (above_band or 0) + (below_band or 0)
    if total_band > 0:
        pct_in = in_band / total_band * 100
        c = BULL if pct_in >= 70 else NEUT
        st.markdown(
            f'**Predicted Range Accuracy** — '
            f'<span style="color:{c};font-family:IBM Plex Mono,monospace">'
            f'{in_band} of {total_band} closes ({pct_in:.0f}%) fell within predicted band</span> · '
            f'<span style="color:{BEAR}">Broke above high: {above_band}</span> · '
            f'<span style="color:{NEUT}">Broke below low: {below_band}</span>',
            unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — INDIVIDUAL STOCKS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## Individual Stocks")

stocks = [s for s in watchlist if not s.get("is_etf")]
if not stocks:
    st.info("No individual stocks in watchlist.")
else:
    for s in stocks:
        ticker = s["ticker"]
        stats  = summary.get("per_ticker", {}).get(ticker)
        if not stats: continue

        st.markdown('<div class="acc-card">', unsafe_allow_html=True)

        # Get latest prediction row
        conn  = get_conn()
        pred  = conn.execute("""
            SELECT p.price_low, p.price_mid, p.price_high,
                   p.actual_close, p.actual_high, p.actual_low,
                   p.signal, p.date,
                   m.val_accuracy as ml_acc
            FROM predictions p
            LEFT JOIN ml_models m ON m.ticker = p.ticker
            WHERE p.ticker=? AND p.actual_close IS NOT NULL
            ORDER BY p.date DESC LIMIT 1
        """, (ticker,)).fetchone()
        conn.close()

        c1, c2, c3, c4 = st.columns([1.5, 2.5, 2.5, 2.5])
        with c1:
            st.markdown(f"**{ticker}**")
            st.markdown(f'<span class="acc-label">{s.get("name","")[:22]}</span>',
                        unsafe_allow_html=True)
            ml_acc = stats.get("ml_accuracy")
            model  = "XGBoost + Rules" if (ml_acc and ml_acc >= 50) else "Rules Only"
            st.markdown(f'<span style="font-size:.75em;color:#333">{model}</span>',
                        unsafe_allow_html=True)
        with c2:
            st.markdown("**Price Prediction**")
            if pred:
                err_c = _color_error(stats.get("avg_error_pct"))
                st.markdown(
                    f'<div class="acc-row">'
                    f'<span class="acc-label">Predicted Range</span>'
                    f'<span class="acc-val">${pred["price_low"]:.2f} – ${pred["price_mid"]:.2f} – ${pred["price_high"]:.2f}</span>'
                    f'</div>'
                    f'<div class="acc-row">'
                    f'<span class="acc-label">Actual Close</span>'
                    f'<span class="acc-val">${pred["actual_close"]:.2f}</span>'
                    f'</div>'
                    f'<div class="acc-row">'
                    f'<span class="acc-label">Average Error</span>'
                    f'<span class="acc-val" style="color:{err_c}">{stats.get("avg_error_pct","—"):.2f}%</span>'
                    f'</div>'
                    f'<div class="acc-row">'
                    f'<span class="acc-label">Within 1% — Within 3%</span>'
                    f'<span class="acc-val">{stats.get("within_1pct","—"):.1f}% — {stats.get("within_3pct","—"):.1f}%</span>'
                    f'</div>',
                    unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#333">No scored predictions yet</span>',
                            unsafe_allow_html=True)
        with c3:
            st.markdown("**Direction Accuracy**")
            da = stats.get("direction_acc")
            dc = _color_dir(da)
            st.markdown(
                f'<div style="font-family:IBM Plex Mono,monospace;font-size:1.6em;'
                f'font-weight:700;color:{dc}">'
                f'{da:.1f}%</div>' if da else
                '<div style="color:#333">No data</div>',
                unsafe_allow_html=True)
            st.markdown(f'<span style="font-size:.8em;color:{dc}">{_dir_label(da)}</span>',
                        unsafe_allow_html=True)
            st.markdown(f'<span style="font-size:.75em;color:#333">'
                        f'Based on {stats.get("n","—")} predictions</span>',
                        unsafe_allow_html=True)
        with c4:
            st.markdown("**XGBoost Model**")
            conn   = get_conn()
            ml_row = conn.execute(
                "SELECT val_mae, val_accuracy, trained_at, n_samples FROM ml_models WHERE ticker=?",
                (ticker,)
            ).fetchone()
            conn.close()
            if ml_row:
                acc_c = BULL if (ml_row["val_accuracy"] or 0) >= 55 else \
                        (NEUT if (ml_row["val_accuracy"] or 0) >= 50 else BEAR)
                st.markdown(
                    f'<div class="acc-row"><span class="acc-label">Validation Accuracy</span>'
                    f'<span class="acc-val" style="color:{acc_c}">{ml_row["val_accuracy"]:.1f}%</span></div>'
                    f'<div class="acc-row"><span class="acc-label">Validation MAE</span>'
                    f'<span class="acc-val">${ml_row["val_mae"]:.2f}</span></div>'
                    f'<div class="acc-row"><span class="acc-label">Trained on</span>'
                    f'<span class="acc-val">{ml_row["n_samples"]} samples</span></div>'
                    f'<div class="acc-row"><span class="acc-label">Last trained</span>'
                    f'<span class="acc-val">{ml_row["trained_at"][:10]}</span></div>',
                    unsafe_allow_html=True)
                status = "Active" if (ml_row["val_accuracy"] or 0) >= 50 else "Disabled (accuracy below 50%)"
                status_c = BULL if "Active" in status else BEAR
                st.markdown(f'<span style="font-size:.78em;color:{status_c}">{status}</span>',
                            unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#333">Not yet trained — run: python run.py train</span>',
                            unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — ETFs
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## ETFs")

etfs = [s for s in watchlist if s.get("is_etf")]
if not etfs:
    st.info("No ETFs detected in watchlist. Add ETFs and run a refresh to populate the ETF Screener.")
else:
    for s in etfs:
        ticker = s["ticker"]
        stats  = summary.get("per_ticker", {}).get(ticker)
        if not stats: continue

        st.markdown('<div class="acc-card">', unsafe_allow_html=True)
        conn = get_conn()
        pred = conn.execute("""
            SELECT price_low, price_mid, price_high, actual_close, signal, date
            FROM predictions WHERE ticker=? AND actual_close IS NOT NULL
            ORDER BY date DESC LIMIT 1
        """, (ticker,)).fetchone()
        conn.close()

        c1, c2, c3 = st.columns([1.5, 3, 3])
        with c1:
            st.markdown(f"**{ticker}**")
            st.markdown(f'<span class="acc-label">{s.get("name","")[:22]}</span>',
                        unsafe_allow_html=True)
            cat = s.get("etf_category","").replace("_"," ").title()
            st.markdown(f'<span style="font-size:.75em;color:#333">{cat}</span>',
                        unsafe_allow_html=True)
            st.markdown('<span style="font-size:.75em;color:#333">Rules Only — '
                        'XGBoost not used for ETFs with low accuracy</span>',
                        unsafe_allow_html=True)
        with c2:
            st.markdown("**Price Prediction**")
            if pred:
                err_c = _color_error(stats.get("avg_error_pct"))
                st.markdown(
                    f'<div class="acc-row"><span class="acc-label">Predicted Range</span>'
                    f'<span class="acc-val">${pred["price_low"]:.2f} – ${pred["price_mid"]:.2f} – ${pred["price_high"]:.2f}</span></div>'
                    f'<div class="acc-row"><span class="acc-label">Actual Close</span>'
                    f'<span class="acc-val">${pred["actual_close"]:.2f}</span></div>'
                    f'<div class="acc-row"><span class="acc-label">Average Error</span>'
                    f'<span class="acc-val" style="color:{err_c}">{stats.get("avg_error_pct","—"):.2f}%</span></div>'
                    f'<div class="acc-row"><span class="acc-label">Within 1% — Within 3%</span>'
                    f'<span class="acc-val">{stats.get("within_1pct","—"):.1f}% — {stats.get("within_3pct","—"):.1f}%</span></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#333">No scored predictions yet</span>',
                            unsafe_allow_html=True)
        with c3:
            st.markdown("**Direction Accuracy**")
            da = stats.get("direction_acc")
            dc = _color_dir(da)
            st.markdown(
                f'<div style="font-family:IBM Plex Mono,monospace;font-size:1.6em;'
                f'font-weight:700;color:{dc}">{da:.1f}%</div>' if da else
                '<div style="color:#333">No data</div>', unsafe_allow_html=True)
            st.markdown(f'<span style="font-size:.8em;color:{dc}">{_dir_label(da)}</span>',
                        unsafe_allow_html=True)
            if da and da < 50:
                st.markdown('<span style="font-size:.78em;color:#ff4b4b">'
                            'Note: ETF direction is driven by macro environment signals, '
                            'not XGBoost. Consider checking the ETF Screener page for '
                            'macro-based signals.</span>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — STRATEGY PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## Strategy Performance")

strategy_data = summary.get("per_strategy", {})
if not strategy_data:
    st.info("No strategy data yet.")
else:
    STRAT_META = {
        "trend":           ("📈 Trend Following",    "Weights EMA stack and MACD higher"),
        "mean_reversion":  ("↩ Mean Reversion",      "Weights RSI and Z-score higher"),
        "rubber_band":     ("🔴 Rubber Band",         "Weights Williams %R, BB %B, ATR higher"),
        "breakout_volume": ("💥 Breakout Volume",     "Weights Relative Volume and Resistance break higher"),
        "unassigned":      ("— Unassigned",           "Equal weights across all indicators"),
    }

    for strat_key, stats in strategy_data.items():
        if not stats: continue
        meta    = STRAT_META.get(strat_key, (strat_key, ""))
        tickers = [s["ticker"] for s in watchlist if (s.get("strategy") or "unassigned") == strat_key]
        da      = stats.get("direction_acc")
        dc      = _color_dir(da)
        err     = stats.get("avg_error_pct")
        err_c   = _color_error(err)

        st.markdown('<div class="acc-card">', unsafe_allow_html=True)
        sc1, sc2, sc3, sc4 = st.columns([2.5, 2, 2, 2])
        with sc1:
            st.markdown(f"**{meta[0]}**")
            st.markdown(f'<span class="acc-label">{meta[1]}</span>', unsafe_allow_html=True)
            st.markdown(f'<span style="font-size:.8em;color:#333">Tickers: {", ".join(tickers) or "None"}</span>',
                        unsafe_allow_html=True)
        with sc2:
            st.markdown("**Direction Accuracy**")
            st.markdown(
                f'<div style="font-family:IBM Plex Mono,monospace;font-size:1.6em;'
                f'font-weight:700;color:{dc}">{da:.1f}%</div>' if da else
                '<div style="color:#333">No data</div>', unsafe_allow_html=True)
            st.markdown(f'<span style="font-size:.8em;color:{dc}">{_dir_label(da)}</span>',
                        unsafe_allow_html=True)
        with sc3:
            st.markdown("**Average Price Error**")
            st.markdown(
                f'<div style="font-family:IBM Plex Mono,monospace;font-size:1.6em;'
                f'font-weight:700;color:{err_c}">{err:.2f}%</div>' if err else
                '<div style="color:#333">No data</div>', unsafe_allow_html=True)
            w1 = stats.get("within_1pct")
            w3 = stats.get("within_3pct")
            if w1:
                st.markdown(f'<span style="font-size:.8em;color:#333">'
                            f'Within 1%: {w1:.1f}% · Within 3%: {w3:.1f}%</span>',
                            unsafe_allow_html=True)
        with sc4:
            st.markdown("**Signal Alignment**")
            conn   = get_conn()
            aligned = conn.execute("""
                SELECT COUNT(*) as n FROM predictions
                WHERE strategy_signal='ALIGNED'
                AND ticker IN ({})
            """.format(",".join(f"'{t}'" for t in tickers) if tickers else "'__none__'")
            ).fetchone()
            mixed = conn.execute("""
                SELECT COUNT(*) as n FROM predictions
                WHERE strategy_signal='MIXED'
                AND ticker IN ({})
            """.format(",".join(f"'{t}'" for t in tickers) if tickers else "'__none__'")
            ).fetchone()
            conn.close()
            al = aligned["n"] if aligned else 0
            mx = mixed["n"] if mixed else 0
            total_sig = al + mx
            if total_sig > 0:
                pct = al / total_sig * 100
                c   = BULL if pct >= 50 else NEUT
                st.markdown(
                    f'<div style="font-family:IBM Plex Mono,monospace;font-size:1.4em;'
                    f'font-weight:700;color:{c}">{pct:.0f}%</div>'
                    f'<span style="font-size:.8em;color:#333">Aligned predictions · '
                    f'{al} aligned, {mx} mixed</span>',
                    unsafe_allow_html=True)
                if pct < 40:
                    st.markdown('<span style="font-size:.78em;color:#ffd700">'
                                'Low alignment — consider reassigning strategy</span>',
                                unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#333">No alignment data</span>',
                            unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

# ── Recent log ────────────────────────────────────────────────────────────────
st.markdown("## Recent Predictions Log")
all_tickers = ["All"] + sorted(set(r["ticker"] for r in log))
filter_t    = st.selectbox("Filter by ticker", all_tickers)
filtered_log = [r for r in log if filter_t == "All" or r["ticker"] == filter_t][:30]

if filtered_log:
    rows = []
    for r in filtered_log:
        correct = r.get("signal_correct")
        rows.append({
            "Date":           r["date"],
            "Ticker":         r["ticker"],
            "Type":           r["prediction_type"],
            "Predicted Price":f"${r['predicted_mid']:.2f}" if r.get("predicted_mid") else "—",
            "Actual Close":   f"${r['actual_close']:.2f}"  if r.get("actual_close") else "—",
            "Error Percent":  f"{r['error_pct']:.2f}%"     if r.get("error_pct") is not None else "—",
            "Signal":         r.get("signal","—"),
            "Direction":      "Correct ✓" if correct==1 else ("Wrong ✗" if correct==0 else "—"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("No log entries yet.")

render_footer(note=f"Accuracy covers last {days} days. Scores update nightly.")
