"""
pages/2_ETF_Screener.py — ETF Screener with macro environment, gold and index signals.
Stock Screener 2.0 — uses core/ layer, no sidebar.
"""
import sys, os, json
from datetime import date, datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup    import setup_page, render_footer
from core.db_queries    import get_etf_list, get_etf_signal_db, get_current_price
from engine.db          import get_conn, get_watchlist, upsert_stock
from engine.fetcher     import fetch_daily_history, fetch_fundamentals
from engine.indicators  import refresh_indicators
from engine.sentiment   import get_latest_sentiment, fetch_all_sentiment
from engine.etf_signals import (refresh_etf_signals, get_latest_macro,
                                 compute_gold_signal, compute_index_signal,
                                 compute_etf_signal, _momentum, YF_MACRO, FRED_SERIES)
from utils import (score_color, get_et_time, is_market_hours, BULL, BEAR, NEUT)

setup_page("ETF Screener", "📊", active_page="2_ETF_Screener")




# ── Additional CSS ────────────────────────────────────────────────────────────
st.markdown("""<style>
.macro-card { background:#1a1f2e; border-radius:10px; padding:16px 20px; margin-bottom:8px; }
.macro-row  { display:flex; justify-content:space-between; padding:6px 0;
              border-bottom:1px solid #252b3b; font-size:0.85em; }
.macro-row:last-child { border-bottom:none; }
.macro-label { color:#333; width:55%; }
.macro-value { font-family:'IBM Plex Mono',monospace; font-weight:600; width:25%; text-align:right; }
.macro-trend { width:20%; text-align:right; font-size:0.8em; }
.etf-card { background:#1a1f2e; border-radius:10px; padding:16px 20px;
            margin-bottom:10px; border-left:4px solid #2e3550; }
.etf-card.buy  { border-left-color:#00c896; }
.etf-card.hold { border-left-color:#ffd700; }
.etf-card.sell { border-left-color:#ff4b4b; }
.driver-bull { color:#00c896; font-size:0.82em; padding:2px 0; }
.driver-bear { color:#ff4b4b; font-size:0.82em; padding:2px 0; }
.mom-bar-pos { background:#00c896; height:6px; border-radius:3px; }
.mom-bar-neg { background:#ff4b4b; height:6px; border-radius:3px; }
.signal-buy  { color:#00c896; font-size:1.3em; font-weight:700; }
.signal-hold { color:#ffd700; font-size:1.3em; font-weight:700; }
.signal-sell { color:#ff4b4b; font-size:1.3em; font-weight:700; }
.env-badge   { display:inline-block; padding:4px 14px; border-radius:20px;
               font-size:0.82em; font-weight:600; margin-top:6px; }
</style>""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _trend_arrow(change):
    if change is None: return "—", "#555"
    if change > 0.05:  return "▲ Rising",  BULL
    if change < -0.05: return "▼ Falling", BEAR
    return "── Stable", NEUT

def _signal_class(signal):
    return {"BUY": "buy", "SELL": "sell"}.get(signal, "hold")

def _signal_html(signal, score, conf):
    cls   = {"BUY": "signal-buy", "SELL": "signal-sell"}.get(signal, "signal-hold")
    arrow = {"BUY": "▲", "SELL": "▼"}.get(signal, "──")
    return (f'<span class="{cls}">{arrow} {signal}</span> '
            f'<span style="color:#333;font-size:0.6em;font-weight:400">'
            f'{score}/100 · {conf:.0f}% confidence</span>')

def _mom_bar(pct, width=120):
    if pct is None: return "<span style='color:#333'>—</span>"
    color = BULL if pct >= 0 else BEAR
    bar_w = min(abs(pct) * 4, width)
    return (f'<span style="font-family:IBM Plex Mono,monospace;color:{color}">'
            f'{pct:+.1f}%</span> '
            f'<span style="display:inline-block;width:{bar_w:.0f}px;height:6px;'
            f'background:{color};border-radius:3px;vertical-align:middle"></span>')

# get_etf_list and get_etf_signal_db imported from core.db_queries

def macro_sparkline(series_id, days=60):
    """Mini sparkline chart for a macro series."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT date, value FROM macro_data WHERE series_id=?
        ORDER BY date DESC LIMIT ?
    """, (series_id, days)).fetchall()
    conn.close()
    if len(rows) < 5: return None
    df = pd.DataFrame([dict(r) for r in rows]).sort_values("date")
    fig = go.Figure(go.Scatter(x=df["date"], y=df["value"],
                               line=dict(color=BULL if df["value"].iloc[-1] >= df["value"].iloc[0] else BEAR,
                                         width=1.5),
                               fill="tozeroy", fillcolor="rgba(0,0,0,0)"))
    fig.update_layout(height=60, margin=dict(l=0,r=0,t=0,b=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      showlegend=False, xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


# ── Header ────────────────────────────────────────────────────────────────────
et = get_et_time()
h1, h2, h3 = st.columns([3, 1.2, 1.5])
with h1:
    st.markdown("# 📊 ETF Screener")
    st.markdown(f"<span style='color:#333;font-size:0.9em'>{date.today().strftime('%A, %B %d, %Y')}</span>",
                unsafe_allow_html=True)
with h2:
    st.markdown("<div style='margin-top:18px'>", unsafe_allow_html=True)
    if st.button("🔄 Refresh All", use_container_width=True):
        etfs = get_etf_list()
        if not etfs:
            st.warning("No ETFs in watchlist. Add ETFs from the dashboard first.")
        else:
            with st.spinner("Fetching macro data and computing signals…"):
                try:
                    results = refresh_etf_signals(etfs)
                    st.success(f"Refreshed macro data and {len(etfs)} ETF signals.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Refresh failed: {e}")
    st.markdown("</div>", unsafe_allow_html=True)
with h3:
    st.markdown("<div style='margin-top:22px'>", unsafe_allow_html=True)
    if et:
        et_str = et.strftime("%H:%M ET")
        if is_market_hours(et): st.success(f"🟢 Open · {et_str}")
        else:                   st.error(f"🔴 Closed · {et_str}")
    else:
        st.warning("⚪ Offline?")
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ── Load macro data ───────────────────────────────────────────────────────────
macro = get_latest_macro()

if not macro:
    st.info("No macro data yet. Click **🔄 Refresh All** to fetch macro environment data.")
    st.stop()

# ── Macro environment panel ───────────────────────────────────────────────────
st.markdown("### 🌍 Macro Environment")

gold_sig  = compute_gold_signal(macro)
index_sig = compute_index_signal(macro)

env_col1, env_col2 = st.columns(2)

with env_col1:
    st.markdown('<div class="macro-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Gold Environment</div>', unsafe_allow_html=True)

    gold_rows = [
        ("DFII10",      "Real 10-Year Rate",          "%"),
        ("T10YIE",      "Breakeven Inflation",         "%"),
        ("DTWEXBGS",    "USD Trade-Weighted Index",    "pts"),
        ("BAMLH0A0HYM2","High Yield Credit Spread",    "%"),
        ("^VIX",        "VIX Fear Index",              ""),
        ("GC=F",        "Spot Gold Price",             "$/oz"),
    ]
    html = ""
    for sid, label, unit in gold_rows:
        d = macro.get(sid, {})
        if not d: continue
        val  = d.get("value")
        chg  = d.get("change", 0)
        arrow, color = _trend_arrow(chg)
        html += (f'<div class="macro-row">'
                 f'<span class="macro-label">{label}</span>'
                 f'<span class="macro-value">{val:.2f}{unit}</span>'
                 f'<span class="macro-trend" style="color:{color}">{arrow}</span>'
                 f'</div>')
    st.markdown(html, unsafe_allow_html=True)
    # Overall gold environment
    env_color = BULL if gold_sig["score"] >= 55 else (BEAR if gold_sig["score"] <= 45 else NEUT)
    env_label = "Bullish" if gold_sig["score"] >= 55 else ("Bearish" if gold_sig["score"] <= 45 else "Neutral")
    st.markdown(
        f'<div style="margin-top:12px">Overall: '
        f'<span class="env-badge" style="background:{env_color}22;color:{env_color}">'
        f'{env_label} for Gold ({gold_sig["score"]}/100)</span></div>',
        unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with env_col2:
    st.markdown('<div class="macro-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Market / Index Environment</div>', unsafe_allow_html=True)

    idx_rows = [
        ("^VIX",    "VIX Fear Index",            ""),
        ("T10Y2Y",  "Yield Curve (10Y-2Y)",       "%"),
        ("FEDFUNDS","Federal Funds Rate",          "%"),
        ("UMCSENT", "Consumer Sentiment",          ""),
        ("^TNX",    "10-Year Treasury Yield",      "%"),
        ("^GSPC",   "S&P 500 Index",              ""),
    ]
    html = ""
    for sid, label, unit in idx_rows:
        d = macro.get(sid, {})
        if not d: continue
        val  = d.get("value")
        chg  = d.get("change", 0)
        arrow, color = _trend_arrow(chg)
        html += (f'<div class="macro-row">'
                 f'<span class="macro-label">{label}</span>'
                 f'<span class="macro-value">{val:.2f}{unit}</span>'
                 f'<span class="macro-trend" style="color:{color}">{arrow}</span>'
                 f'</div>')
    st.markdown(html, unsafe_allow_html=True)
    env_color = BULL if index_sig["score"] >= 55 else (BEAR if index_sig["score"] <= 45 else NEUT)
    env_label = "Bullish" if index_sig["score"] >= 55 else ("Bearish" if index_sig["score"] <= 45 else "Neutral")
    st.markdown(
        f'<div style="margin-top:12px">Overall: '
        f'<span class="env-badge" style="background:{env_color}22;color:{env_color}">'
        f'{env_label} for Indexes ({index_sig["score"]}/100)</span></div>',
        unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")

# ── ETF sections ──────────────────────────────────────────────────────────────
etfs = get_etf_list()

if not etfs:
    st.info(
        "No ETFs in your watchlist yet. "
        "Add ETFs from the Dashboard (they will be automatically detected). "
        "Suggested starters: **IAU, GLD, VOO, QQQ, IWM**"
    )
    with st.expander("➕ Quick Add ETFs"):
        suggestions = {
            "Gold":   ["IAU","GLD","SGOL"],
            "Index":  ["VOO","QQQ","IWM","SPY"],
            "Sector": ["XLK","XLF","XLE","XLV"],
            "Bonds":  ["TLT","BND"],
        }
        for cat, tickers in suggestions.items():
            st.markdown(f"**{cat}:** {', '.join(tickers)}")
        add_input = st.text_input("Add ticker", placeholder="e.g. GLD").upper().strip()
        if st.button("Add") and add_input:
            with st.spinner(f"Adding {add_input}…"):
                try:
                    upsert_stock(add_input)
                    fetch_daily_history(add_input)
                    fetch_fundamentals(add_input)
                    refresh_indicators(add_input)
                    st.success(f"✓ {add_input} added")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")
    st.stop()

# Group ETFs by category
CATEGORY_ORDER = [
    ("gold",             "🥇 Gold ETFs"),
    ("precious_metals",  "🥈 Precious Metals ETFs"),
    ("index_us_broad",   "📈 Broad Market Index ETFs"),
    ("index_us_tech",    "💻 Technology Index ETFs"),
    ("index_us_smallcap","🔹 Small Cap Index ETFs"),
    ("index_international","🌐 International ETFs"),
    ("index_emerging",   "🌏 Emerging Market ETFs"),
    ("sector_tech",      "💡 Technology Sector ETFs"),
    ("sector_financial", "🏦 Financial Sector ETFs"),
    ("sector_energy",    "⚡ Energy Sector ETFs"),
    ("sector_health",    "🏥 Healthcare Sector ETFs"),
    ("bonds",            "🏛️ Bond ETFs"),
    ("other",            "📦 Other ETFs"),
]

# Compute signals for all ETFs
all_results = {}
for etf in etfs:
    ticker = etf["ticker"]
    cat    = etf.get("etf_category") or "other"
    # Try loading from DB first
    sig    = get_etf_signal_db(ticker)
    if sig:
        all_results[ticker] = {
            "signal":      sig["signal"],
            "score":       sig["score"],
            "confidence":  sig["confidence"],
            "category":    sig["category"],
            "momentum_1m": sig.get("momentum_1m"),
            "momentum_3m": sig.get("momentum_3m"),
            "momentum_6m": sig.get("momentum_6m"),
            "momentum_12m":sig.get("momentum_12m"),
            "bull_drivers": json.loads(sig.get("drivers_bull") or "[]"),
            "bear_drivers": json.loads(sig.get("drivers_bear") or "[]"),
        }
    else:
        # Compute on the fly
        sent = get_latest_sentiment(ticker)
        sent_score = sent.get("avg_score", 0) if sent.get("available") else 0.0
        macro_sig  = gold_sig if "gold" in cat else index_sig
        all_results[ticker] = compute_etf_signal(ticker, cat, macro_sig, sent_score)

# Display by category
for cat_key, cat_label in CATEGORY_ORDER:
    cat_etfs = [e for e in etfs if (e.get("etf_category") or "other") == cat_key]
    if not cat_etfs: continue

    st.markdown(f"### {cat_label}")

    for etf in cat_etfs:
        ticker = etf["ticker"]
        name   = etf.get("name") or ticker
        sig    = all_results.get(ticker, {})
        signal = sig.get("signal", "HOLD")
        score  = sig.get("score", 50)
        conf   = sig.get("confidence", 0)
        bull_d = sig.get("bull_drivers", [])
        bear_d = sig.get("bear_drivers", [])
        sent   = get_latest_sentiment(ticker)
        sent_sc= sent.get("avg_score", 0) if sent.get("available") else 0.0
        sent_lb= sent.get("overall_label", "Neutral") if sent.get("available") else "No data"

        card_cls = _signal_class(signal)
        st.markdown(f'<div class="etf-card {card_cls}">', unsafe_allow_html=True)

        # Header row
        hcol1, hcol2, hcol3, hcol4 = st.columns([2, 2, 2, 2])
        with hcol1:
            st.markdown(f"**{ticker}** — {name}")
            st.markdown(_signal_html(signal, score, conf), unsafe_allow_html=True)
        with hcol2:
            st.markdown("**Momentum**")
            mom_items = [
                ("1 Month",  sig.get("momentum_1m")),
                ("3 Months", sig.get("momentum_3m")),
                ("6 Months", sig.get("momentum_6m")),
                ("12 Months",sig.get("momentum_12m")),
            ]
            for label, val in mom_items:
                if val is not None:
                    st.markdown(f"<span style='color:#333;font-size:.8em'>{label}: </span>"
                                + _mom_bar(val), unsafe_allow_html=True)
        with hcol3:
            st.markdown("**Signal Drivers**")
            if bull_d:
                for d in bull_d[:3]:
                    st.markdown(f'<div class="driver-bull">✓ {d}</div>', unsafe_allow_html=True)
            if bear_d:
                for d in bear_d[:3]:
                    st.markdown(f'<div class="driver-bear">✗ {d}</div>', unsafe_allow_html=True)
            if not bull_d and not bear_d:
                st.markdown("<span style='color:#333'>Run a refresh to compute drivers</span>",
                            unsafe_allow_html=True)
        with hcol4:
            st.markdown("**Sentiment**")
            sent_color = BULL if sent_sc > 0.2 else (BEAR if sent_sc < -0.2 else NEUT)
            st.markdown(
                f'<span style="color:{sent_color};font-weight:600">{sent_lb}</span>'
                f'<span style="font-family:IBM Plex Mono,monospace;color:{sent_color};font-size:.85em"> {sent_sc:+.2f}</span>',
                unsafe_allow_html=True)
            if sent.get("available"):
                for src, sd in sent["sources"].items():
                    mc = sd.get("mention_count", 0)
                    sc = sd.get("score") or 0
                    sc_c = BULL if sc > 0.2 else (BEAR if sc < -0.2 else NEUT)
                    st.markdown(
                        f'<span style="font-size:.78em;color:#444">{src.title()}: '
                        f'<span style="color:{sc_c}">{sc:+.2f}</span> · {mc} mentions</span>',
                        unsafe_allow_html=True)

        # Expand for full drivers
        with st.expander(f"Show all signal drivers for {ticker}"):
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Bullish Factors**")
                if bull_d:
                    for d in bull_d:
                        st.markdown(f'<div class="driver-bull">✓ {d}</div>', unsafe_allow_html=True)
                else:
                    st.markdown("<span style='color:#333'>None identified</span>",
                                unsafe_allow_html=True)
            with d2:
                st.markdown("**Bearish Factors**")
                if bear_d:
                    for d in bear_d:
                        st.markdown(f'<div class="driver-bear">✗ {d}</div>', unsafe_allow_html=True)
                else:
                    st.markdown("<span style='color:#333'>None identified</span>",
                                unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown("")


# ── ETF Watchlist ─────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📋 ETF Watchlist")

etf_list = get_etf_list()
if etf_list:
    for col, hdr in zip(
        st.columns([1.2, 2.5, 1.5, 1.8, 2, 2]),
        ["Ticker", "Name", "Category", "Price", "Signal", "Actions"]
    ):
        col.markdown(f"**{hdr}**")
    st.markdown('<hr style="margin:4px 0;border-color:#dde1ea">', unsafe_allow_html=True)

    for e in sorted(etf_list, key=lambda x: x.get("etf_category") or ""):
        ticker = e["ticker"]
        sig    = get_etf_signal_db(ticker)
        if not sig:
            try:
                mac  = get_latest_macro()
                if mac:
                    cat  = e.get("etf_category") or "other"
                    gs   = compute_gold_signal(mac)
                    ins  = compute_index_signal(mac)
                    ms   = gs if "gold" in cat else ins
                    sig  = compute_etf_signal(ticker, cat, ms, 0.0)
            except Exception:
                sig = {}
        signal  = sig.get("signal", "—") if sig else "—"
        score   = sig.get("score", 0) if sig else 0
        sig_c   = BULL if signal == "BUY" else (BEAR if signal == "SELL" else NEUT)
        sig_a   = "▲" if signal == "BUY" else ("▼" if signal == "SELL" else "——")
        cur     = get_current_price(ticker)

        c1, c2, c3, c4, c5, c6 = st.columns([1.2, 2.5, 1.5, 1.8, 2, 2])
        c1.markdown(f"**{ticker}**")
        c2.markdown(f'<span style="font-size:.88em;color:#444">{e.get("name","")[:30]}</span>',
                    unsafe_allow_html=True)
        c3.markdown(f'<span style="font-size:.82em;color:#555">{e.get("etf_category") or "—"}</span>',
                    unsafe_allow_html=True)
        c4.markdown(f'<span style="font-family:IBM Plex Mono,monospace">{"$" + f"{cur:.2f}" if cur else "—"}</span>',
                    unsafe_allow_html=True)
        c5.markdown(f'<span style="color:{sig_c};font-weight:700">{sig_a} {signal}</span>'
                    f'<br><span style="font-size:.75em;color:#555">{score}/100</span>',
                    unsafe_allow_html=True)
        if c6.button("📊 Detail", key=f"etf_wl_det_{ticker}", use_container_width=True):
            st.session_state["detail_ticker"] = ticker
            st.switch_page("pages/1_Stock_Detail.py")
        st.markdown('<hr style="margin:3px 0;border-color:#eee">', unsafe_allow_html=True)
else:
    st.info("No ETFs in watchlist. Add tickers with watchlist_type='etf'.")

render_footer(note="Macro data from FRED and yfinance.")
