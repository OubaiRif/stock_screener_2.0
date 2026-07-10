"""
dashboard.py — Stock Screener 2.0 Home Page.
6 tabs: Market | Stocks | ETFs | Gold | Accuracy | Analyzer
No sidebar — top nav via core/page_setup.py.
"""
import sys, os, time
from datetime import date, datetime

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.page_setup  import setup_page, render_footer
from engine.prices import get_extended_hours_price, format_price_label, format_change_html
from core.db_queries  import (get_all_stocks_with_predictions, get_volume_spikes,
                               get_current_price, get_etf_list, get_etf_signal_db,
                               get_portfolio_stocks)
from core.refresh     import run_full_refresh, run_ml_train
from engine.db        import get_conn, get_watchlist, set_strategy, upsert_stock, remove_stock
from engine.fetcher   import fetch_daily_history, fetch_fundamentals
from engine.predictor import predict
from engine.strategy_advisor import suggest_strategy
from engine.accuracy  import get_accuracy_summary
from engine.etf_signals import (get_latest_macro, compute_gold_signal,
                                 compute_index_signal, compute_etf_signal)
from engine.sentiment import get_latest_sentiment
from utils import (score_color, score_bar_html, strategy_label, signal_badge,
                   move_html, get_et_time, is_market_hours, BULL, BEAR, NEUT)
from config import STRATEGIES, DEMO_MODE, DB_PATH

# ── Auto-generate demo DB on Streamlit Cloud if missing ───────────────────────
if DEMO_MODE and not os.path.exists(DB_PATH):
    with st.spinner("Setting up demo environment…"):
        try:
            import subprocess
            _script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_db.py")
            result  = subprocess.run(
                ["python3", _script],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                st.error(f"Demo setup failed: {result.stderr[-500:]}")
                st.stop()
            st.rerun()
        except Exception as e:
            st.error(f"Could not initialise demo database: {e}")
            st.stop()

setup_page("Stock Screener 2.0", "📈", active_page="dashboard")

if "last_refresh" not in st.session_state:
    st.session_state["last_refresh"] = None

# ── Modals ────────────────────────────────────────────────────────────────────

@st.dialog("Edit Position", width="small")
def edit_modal(row):
    ticker = row["ticker"]
    st.markdown(f"### {ticker} — {row['name'] or ticker}")
    strat_idx = STRATEGIES.index(row["strategy"] or "unassigned") \
                if (row["strategy"] or "unassigned") in STRATEGIES else 0
    new_strat = st.selectbox("Strategy", STRATEGIES, index=strat_idx,
                             format_func=strategy_label, key=f"ms_{ticker}")
    if st.button("💡 Suggest", key=f"sug_{ticker}"):
        sug = suggest_strategy(ticker)
        st.info(f"**{sug['strategy'].replace('_',' ').title()}** ({sug['confidence']}%)\n\n_{sug['reason']}_")
    st.markdown("---")
    in_pf    = st.checkbox("Currently holding", value=bool(row["in_portfolio"]), key=f"mpf_{ticker}")
    avg_cost = st.number_input("Avg cost $", value=float(row["avg_cost"] or 0), step=0.01, key=f"mc_{ticker}")
    shares   = st.number_input("Shares", value=float(row["shares_held"] or 0), step=0.01, key=f"ms2_{ticker}")
    if in_pf and avg_cost > 0 and shares > 0:
        cur = get_current_price(ticker)
        if cur:
            pnl  = (cur - avg_cost) * shares
            ppct = (cur - avg_cost) / avg_cost * 100
            c    = BULL if pnl >= 0 else BEAR
            a    = "▲" if pnl >= 0 else "▼"
            st.markdown(f"<span style='color:{c};font-family:IBM Plex Mono,monospace'>"
                        f"{a} ${abs(pnl):.2f} ({ppct:+.2f}%)</span>", unsafe_allow_html=True)
    notes = st.text_area("Notes", value=row.get("notes") or "", height=60, key=f"mn_{ticker}")
    st.markdown("---")
    c1, c2, c3 = st.columns([2, 1.5, 1.5])
    with c1:
        if st.button("💾 Save", use_container_width=True, key=f"save_{ticker}"):
            set_strategy(ticker, new_strat)
            upsert_stock(ticker, in_portfolio=int(in_pf), avg_cost=avg_cost,
                         shares_held=shares, notes=notes)
            st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True, key=f"cancel_{ticker}"):
            st.rerun()
    with c3:
        if st.button("🗑 Delete", use_container_width=True, key=f"del_{ticker}"):
            st.session_state[f"confirm_{ticker}"] = True
    if st.session_state.get(f"confirm_{ticker}"):
        st.warning(f"Remove **{ticker}**?")
        y, n = st.columns(2)
        if y.button("Yes", key=f"dy_{ticker}"):
            remove_stock(ticker)
            st.session_state.pop(f"confirm_{ticker}", None)
            st.rerun()
        if n.button("No", key=f"dn_{ticker}"):
            st.session_state.pop(f"confirm_{ticker}", None)
            st.rerun()


@st.dialog("Search Ticker", width="small")
def search_modal():
    st.markdown("### 🔍 Search any ticker")
    query = st.text_input("Ticker symbol", placeholder="e.g. NVDA",
                          key="modal_search").upper().strip()
    if query:
        with st.spinner(f"Looking up {query}…"):
            try:
                import yfinance as yf
                info  = yf.Ticker(query).info
                name  = info.get("longName") or info.get("shortName") or query
                price = info.get("regularMarketPrice") or info.get("previousClose")
                mc    = info.get("marketCap")
                in_wl = any(s["ticker"] == query for s in get_watchlist())
                st.markdown(f"**{query}** — {name}")
                if info.get("sector"): st.caption(info.get("sector"))
                c1, c2 = st.columns(2)
                if price: c1.metric("Price", f"${price:.2f}")
                if mc:    c2.metric("Mkt Cap", f"${mc/1e9:.1f}B")
                if in_wl:
                    st.info("Already in watchlist.")
                else:
                    if st.button(f"➕ Add {query} to watchlist"):
                        upsert_stock(query)
                        fetch_daily_history(query)
                        fetch_fundamentals(query)
                        st.success(f"✓ {query} added")
                        st.rerun()
            except Exception as e:
                st.error(f"Lookup failed: {e}")


# ── Header row ────────────────────────────────────────────────────────────────
et = get_et_time()
h1, h2, h3, h4, h5, h6 = st.columns([2.5, 1.2, 1.2, 1.2, 1.2, 1.5])

with h1:
    st.markdown("## 📈 Stock Screener 2.0")
    st.markdown(f"<span style='color:#444;font-size:.9em'>{date.today().strftime('%A, %B %d, %Y')}</span>",
                unsafe_allow_html=True)
with h2:
    if st.button("🔍 Search", use_container_width=True):
        search_modal()
with h3:
    if st.button("🔄 Refresh", use_container_width=True):
        tickers = [s["ticker"] for s in get_watchlist()]
        if tickers:
            run_full_refresh(tickers)
            st.rerun()
        else:
            st.warning("Watchlist is empty.")
with h4:
    if st.button("🧠 Retrain", use_container_width=True):
        tickers = [s["ticker"] for s in get_watchlist()]
        if tickers:
            run_ml_train(tickers)
with h5:
    if st.session_state.last_refresh:
        st.caption(f"Last refresh: {datetime.fromtimestamp(st.session_state.last_refresh).strftime('%H:%M')}")
with h6:
    if et:
        et_str = et.strftime("%H:%M ET")
        if is_market_hours(et): st.success(f"🟢 Open · {et_str}")
        else:                   st.error(f"🔴 Closed · {et_str}")
    else:
        st.warning("⚪ Offline?")

st.markdown("---")


# ── Load all data once ────────────────────────────────────────────────────────
predictions  = get_all_stocks_with_predictions()
portfolio    = get_portfolio_stocks()
spikes       = get_volume_spikes()

total    = len(predictions)
bullish  = [r for r in predictions if r["signal"] == "BULLISH"]
bearish  = [r for r in predictions if r["signal"] == "BEARISH"]
avg_c    = sum(r["composite_score"] or 50 for r in predictions) / total if total else 50
held     = [r for r in portfolio if r.get("in_portfolio")]

# ── Summary metrics ───────────────────────────────────────────────────────────
for col, lbl, val, tip in zip(
    st.columns(5),
    ["Watching", "▲ Bullish", "▼ Bearish", "— Neutral", "Avg Score"],
    [total, len(bullish), len(bearish), total - len(bullish) - len(bearish), f"{avg_c:.1f}"],
    ["Total tickers", "Composite ≥ 60", "Composite ≤ 40", "Score 40–60",
     "Tech 50% · Fund 25% · Sent 25%"]
):
    col.metric(lbl, val, help=tip)

if spikes:
    st.markdown("---")
    cols = st.columns(min(len(spikes), 4))
    for i, s in enumerate(spikes):
        with cols[i % 4]:
            st.markdown(
                f'<div class="spike-alert">⚡ <b>{s["ticker"]}</b> '
                f'<span style="font-family:IBM Plex Mono,monospace">{s["rel_volume"]:.1f}x</span>'
                f' avg volume</div>', unsafe_allow_html=True)

st.markdown("---")

# ── Helper: render one page card ──────────────────────────────────────────────
def _card_start(col, label, desc, color):
    col.markdown(
        f'<div style="background:#f4f6fb;border-radius:10px;padding:14px 16px;'
        f'border-left:4px solid {color};margin-bottom:4px;min-height:120px">'
        f'<div style="font-size:1.0em;font-weight:700;margin-bottom:3px">{label}</div>'
        f'<div style="font-size:.80em;color:#555;margin-bottom:8px">{desc}</div>',
        unsafe_allow_html=True)

def _card_line(col, html):
    col.markdown(html, unsafe_allow_html=True)

def _card_end(col):
    col.markdown('</div>', unsafe_allow_html=True)

def _sig_span(signal):
    c = BULL if signal == "BULLISH" else (BEAR if signal == "BEARISH" else NEUT)
    a = "▲" if signal == "BULLISH" else ("▼" if signal == "BEARISH" else "—")
    return f'<span style="color:{c};font-weight:700;font-size:.85em">{a} {signal}</span>'

# ── Row 1: Stocks | ETF ───────────────────────────────────────────────────────
r1l, r1r = st.columns(2)

# STOCKS card — top 3 bullish signals
with r1l:
    _card_start(r1l, "📈 Stocks", "Watchlist signals & predictions", BULL)
    top3 = bullish[:3] if bullish else predictions[:3]
    for r in top3:
        cur = get_current_price(r["ticker"])
        p   = r.get("price_mid")
        mv  = f' → <b>${p:.2f}</b>' if p and cur else ""
        # Show pre/post market price if available
        _ep = get_extended_hours_price(r["ticker"])
        _ep_html = ""
        if not _ep.get("error") and _ep.get("price_type") in ("pre_market", "post_market"):
            _ep_lbl = "PM" if _ep["price_type"] == "pre_market" else "AH"
            _ep_c   = "#c8a000" if _ep["price_type"] == "pre_market" else "#0066cc"
            _ep_pct = _ep.get("pre_change_pct") or _ep.get("post_change_pct") or 0
            _ep_a   = "▲" if _ep_pct >= 0 else "▼"
            _ep_html = (f' <span style="color:{_ep_c};font-size:.78em">'
                        f'{_ep_lbl} ${_ep["price"]:.2f} {_ep_a}{abs(_ep_pct):.1f}%</span>')
        r1l.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:3px 0;border-bottom:1px solid #e8eaf0;font-size:.82em">'
            f'<span><b>{r["ticker"]}</b> <span style="color:#555">${cur:.2f}</span>{_ep_html}{mv}</span>'
            f'{_sig_span(r["signal"] or "NEUTRAL")}</div>',
            unsafe_allow_html=True)
    if not top3:
        r1l.markdown('<span style="color:#555;font-size:.82em">No signals yet — run Refresh.</span>',
                     unsafe_allow_html=True)
    _card_end(r1l)
    st.page_link("pages/1_Stock_Detail.py", label="Open Stocks →", use_container_width=True)

# ETF card — ETF signal summary
with r1r:
    try:
        etfs  = get_etf_list()
        macro = get_latest_macro()
        buys  = 0; holds = 0; sells = 0
        etf_rows = []
        if etfs and macro:
            gold_sig  = compute_gold_signal(macro)
            index_sig = compute_index_signal(macro)
            for e in etfs[:4]:
                sig  = get_etf_signal_db(e["ticker"])
                if not sig:
                    sent = get_latest_sentiment(e["ticker"])
                    ss   = sent.get("avg_score", 0) if sent.get("available") else 0.0
                    cat  = e.get("etf_category") or "other"
                    ms   = gold_sig if "gold" in cat else index_sig
                    sig  = compute_etf_signal(e["ticker"], cat, ms, ss)
                s = sig.get("signal", "HOLD")
                if s == "BUY": buys += 1
                elif s == "SELL": sells += 1
                else: holds += 1
                etf_rows.append((e["ticker"], s, sig.get("score", 50)))
        _card_start(r1r, "📊 ETF Screener", "Macro-driven ETF signals", "#0066cc")
        if etf_rows:
            for ticker, sig, score in etf_rows:
                sc = BULL if sig == "BUY" else (BEAR if sig == "SELL" else NEUT)
                sa = "▲" if sig == "BUY" else ("▼" if sig == "SELL" else "——")
                r1r.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:3px 0;border-bottom:1px solid #e8eaf0;font-size:.82em">'
                    f'<b>{ticker}</b>'
                    f'<span style="color:{sc};font-weight:700">{sa} {sig} · {score}/100</span></div>',
                    unsafe_allow_html=True)
        else:
            r1r.markdown('<span style="color:#555;font-size:.82em">No ETF data — go to ETF page and refresh.</span>',
                         unsafe_allow_html=True)
        _card_end(r1r)
    except Exception as e:
        _card_start(r1r, "📊 ETF Screener", "Macro-driven ETF signals", "#0066cc")
        r1r.markdown(f'<span style="color:#888;font-size:.82em">ETF data unavailable: {e}</span>', unsafe_allow_html=True)
        _card_end(r1r)
    st.page_link("pages/2_ETF_Screener.py", label="Open ETF Screener →", use_container_width=True)

# ── Row 2: Swing | Gold ───────────────────────────────────────────────────────
r2l, r2r = st.columns(2)

# SWING card
with r2l:
    from core.db_queries import get_swing_stocks_with_predictions
    swing_rows = get_swing_stocks_with_predictions()
    _card_start(r2l, "⚡ Swing Trades", "Entry checklists & signals", NEUT)
    shown = [r for r in swing_rows if r.get("signal")] or swing_rows
    for r in shown[:3]:
        cur   = get_current_price(r["ticker"])
        pf_badge = ' 💼' if r["in_portfolio"] else ''
        r2l.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:3px 0;border-bottom:1px solid #e8eaf0;font-size:.82em">'
            f'<span><b>{r["ticker"]}</b>{pf_badge} <span style="color:#555">${cur:.2f}</span></span>'
            f'{_sig_span(r["signal"] or "NEUTRAL")}</div>',
            unsafe_allow_html=True)
    if not shown:
        r2l.markdown('<span style="color:#555;font-size:.82em">No swing data — run Refresh.</span>',
                     unsafe_allow_html=True)
    _card_end(r2l)
    st.page_link("pages/3_Swing_Trades.py", label="Open Swing Trades →", use_container_width=True)

# GOLD card
with r2r:
    try:
        from engine.gold_signals import (get_position, compute_pnl,
                                          get_current_price as gold_price,
                                          compute_swing_signal)
        pos       = get_position("IAU")
        iau_price = gold_price("IAU") or 0
        shares    = pos.get("shares", 0)
        avg_cost  = pos.get("avg_cost", 0)
        pnl       = compute_pnl(pos, iau_price) if shares > 0 else {}
        pnl_val   = pnl.get("unrealized_pnl", 0)
        pnl_pct   = pnl.get("unrealized_pct", 0)
        pnl_c     = BULL if pnl_val >= 0 else BEAR
        pnl_a     = "▲" if pnl_val >= 0 else "▼"
        # compute_swing_signal needs a DataFrame, not the macro dict
        # Load IAU price history as DataFrame instead
        try:
            import pandas as pd
            from engine.fetcher import load_daily_history
            df_gold   = load_daily_history("IAU")
            swing_sig = compute_swing_signal(df_gold).get("signal", "—") if df_gold is not None and len(df_gold) > 0 else "—"
        except Exception:
            swing_sig = "—"
        swing_c   = BULL if swing_sig in ("BUY","STRONG BUY") else (BEAR if swing_sig == "SELL" else NEUT)
        _card_start(r2r, "🥇 Gold Dashboard", "IAU position & macro signals", "#c8a000")
        r2r.markdown(
            f'<div style="font-size:.82em;display:flex;flex-direction:column;gap:4px">'
            f'<div style="display:flex;justify-content:space-between">'
            f'<span>IAU · {shares:.0f} sh @ ${avg_cost:.2f}</span>'
            f'<span style="font-family:IBM Plex Mono,monospace">${iau_price:.2f}</span></div>'
            f'<div style="display:flex;justify-content:space-between">'
            f'<span>Unrealized P&L</span>'
            f'<span style="color:{pnl_c};font-weight:700">{pnl_a} ${abs(pnl_val):.2f} ({pnl_pct:+.1f}%)</span></div>'
            f'<div style="display:flex;justify-content:space-between">'
            f'<span>Swing Signal</span>'
            f'<span style="color:{swing_c};font-weight:700">{swing_sig}</span></div>'
            f'</div>', unsafe_allow_html=True)
        _card_end(r2r)
    except Exception as e:
        _card_start(r2r, "🥇 Gold Dashboard", "IAU position & macro signals", "#c8a000")
        r2r.markdown(f'<span style="color:#888;font-size:.82em">Gold data unavailable: {e}</span>', unsafe_allow_html=True)
        _card_end(r2r)
    st.page_link("pages/4_Gold_Dashboard.py", label="Open Gold Dashboard →", use_container_width=True)

# ── Row 3: Trading Assistant | Portfolio ─────────────────────────────────────
r3l, r3r = st.columns(2)

# TRADING ASSISTANT card
with r3l:
    _card_start(r3l, "🎯 Trading Assistant", "Market pulse, entry analyzer, calculator", "#6a3fbf")
    et = get_et_time()
    market_status = "🟢 Market Open" if (et and is_market_hours(et)) else "🔴 Market Closed"
    et_str = et.strftime("%H:%M ET") if et else "—"
    top_bull = bullish[0] if bullish else None
    r3l.markdown(
        f'<div style="font-size:.82em;display:flex;flex-direction:column;gap:4px">'
        f'<div style="display:flex;justify-content:space-between">'
        f'<span>Market Status</span><span style="font-weight:700">{market_status} · {et_str}</span></div>'
        f'<div style="display:flex;justify-content:space-between">'
        f'<span>Bullish signals</span><span style="color:{BULL};font-weight:700">{len(bullish)}</span></div>'
        f'<div style="display:flex;justify-content:space-between">'
        f'<span>Top pick</span>'
        f'<span style="font-weight:700">{top_bull["ticker"] if top_bull else "—"}'
        f'{" · " + str(round(top_bull["composite_score"] or 0)) + "/100" if top_bull else ""}</span></div>'
        f'</div>', unsafe_allow_html=True)
    _card_end(r3l)
    st.page_link("pages/5_Trading_Assistant.py", label="Open Trading Assistant →", use_container_width=True)

# PORTFOLIO card
with r3r:
    try:
        total_cost  = sum((r.get("shares_held") or 0) * (r.get("avg_cost") or 0) for r in held)
        total_value = 0.0
        for r in held:
            cur = get_current_price(r["ticker"])
            if cur:
                total_value += (r.get("shares_held") or 0) * cur
        total_pnl     = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
        pnl_c = BULL if total_pnl >= 0 else BEAR
        pnl_a = "▲" if total_pnl >= 0 else "▼"
        _card_start(r3r, "💼 Portfolio", f"{len(held)} positions", BULL)
        r3r.markdown(
            f'<div style="font-size:.82em;display:flex;flex-direction:column;gap:4px">'
            f'<div style="display:flex;justify-content:space-between">'
            f'<span>Cost Basis</span><span style="font-family:IBM Plex Mono,monospace">${total_cost:,.2f}</span></div>'
            f'<div style="display:flex;justify-content:space-between">'
            f'<span>Market Value</span><span style="font-family:IBM Plex Mono,monospace">${total_value:,.2f}</span></div>'
            f'<div style="display:flex;justify-content:space-between">'
            f'<span>Unrealized P&L</span>'
            f'<span style="color:{pnl_c};font-weight:700">{pnl_a} ${abs(total_pnl):,.2f} ({total_pnl_pct:+.1f}%)</span></div>'
            f'</div>', unsafe_allow_html=True)
        _card_end(r3r)
    except Exception as e:
        _card_start(r3r, "💼 Portfolio", "Holdings & P&L", BULL)
        r3r.markdown(f'<span style="color:#888;font-size:.82em">Portfolio data unavailable: {e}</span>', unsafe_allow_html=True)
        _card_end(r3r)
    st.page_link("pages/6_Portfolio.py", label="Open Portfolio →", use_container_width=True)

# ── Row 4: Journal | Accuracy ─────────────────────────────────────────────────
r4l, r4r = st.columns(2)

# JOURNAL card
with r4l:
    try:
        from core.db_queries import get_all_journal_entries, ensure_journal_table
        ensure_journal_table()
        entries = get_all_journal_entries(limit=3)
        _card_start(r4l, "📓 Journal", "Recent trades logged", "#444")
        if entries:
            for e in entries:
                ac = BULL if e["action"] == "BUY" else BEAR
                r4l.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:3px 0;border-bottom:1px solid #e8eaf0;font-size:.82em">'
                    f'<span><b>{e["ticker"]}</b> · <span style="color:{ac}">{e["action"]}</span>'
                    f' {e["shares"]:.0f} sh @ ${e["price"]:.2f}</span>'
                    f'<span style="color:#555">{e["traded_at"][:10]}</span></div>',
                    unsafe_allow_html=True)
        else:
            r4l.markdown('<span style="color:#555;font-size:.82em">No trades logged yet.</span>',
                         unsafe_allow_html=True)
        _card_end(r4l)
    except Exception as e:
        _card_start(r4l, "📓 Journal", "Recent trades logged", "#444")
        r4l.markdown(f'<span style="color:#888;font-size:.82em">Journal unavailable: {e}</span>', unsafe_allow_html=True)
        _card_end(r4l)
    st.page_link("pages/7_Journal.py", label="Open Journal →", use_container_width=True)

# ACCURACY card
with r4r:
    try:
        summary = get_accuracy_summary(days=30)
        _card_start(r4r, "🎲 Accuracy", "Signal accuracy & prediction scoring", "#0088aa")
        if summary.get("available"):
            ov  = summary["overall"]
            da  = ov.get("direction_acc")
            err = ov.get("avg_error_pct")
            w1  = ov.get("within_1pct")
            w3  = ov.get("within_3pct")
            da_c = BULL if (da or 0) >= 55 else (NEUT if (da or 0) >= 50 else BEAR)
            r4r.markdown(
                f'<div style="font-size:.82em;display:flex;flex-direction:column;gap:4px">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span>Direction Accuracy</span>'
                f'<span style="color:{da_c};font-weight:700">{da:.1f}%</span></div>'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span>Avg Price Error</span>'
                f'<span style="font-family:IBM Plex Mono,monospace">{err:.2f}%</span></div>'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span>Within 1% / 3%</span>'
                f'<span style="font-family:IBM Plex Mono,monospace">{w1:.0f}% / {w3:.0f}%</span></div>'
                f'</div>', unsafe_allow_html=True)
        else:
            r4r.markdown('<span style="color:#555;font-size:.82em">No accuracy data yet — run nightly pipeline for a few days.</span>',
                         unsafe_allow_html=True)
        _card_end(r4r)
    except Exception as e:
        _card_start(r4r, "🎲 Accuracy", "Signal accuracy & prediction scoring", "#0088aa")
        r4r.markdown(f'<span style="color:#888;font-size:.82em">Accuracy unavailable: {e}</span>', unsafe_allow_html=True)
        _card_end(r4r)
    st.page_link("pages/8_Accuracy.py", label="Open Accuracy →", use_container_width=True)

# ── Row 5: Backtesting (solo) ─────────────────────────────────────────────────
r5l, r5r = st.columns(2)
with r5l:
    _card_start(r5l, "📉 Backtesting", "Historical strategy performance vs buy-and-hold", BEAR)
    r5l.markdown(
        f'<div style="font-size:.82em;color:#555">Run backtests on any watchlist ticker '
        f'to compare your strategy returns against benchmark.</div>',
        unsafe_allow_html=True)
    _card_end(r5l)
    st.page_link("pages/9_Backtest.py", label="Open Backtesting →", use_container_width=True)


render_footer()
