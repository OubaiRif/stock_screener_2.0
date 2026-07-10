"""
pages/3_Swing_Trades.py — Swing Trades watchlist.
Reads watchlist_type='swing' from DB. Shows signals, entry checklist, P&L if held.
"""
import sys, os
from datetime import date, datetime

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup  import setup_page, render_footer
from core.db_queries  import (get_swing_stocks_with_predictions, get_current_price,
                               get_volume_spikes)
from core.refresh     import run_full_refresh
from engine.db        import get_watchlist
from engine.indicators import get_latest_indicators
from engine.sentiment  import get_latest_sentiment
from engine.prices import get_extended_hours_price, format_change_html
from utils import (score_color, score_bar_html, strategy_label,
                   get_et_time, is_market_hours, BULL, BEAR, NEUT, demo_banner)

from config import DEMO_MODE

setup_page("Swing Trades", "⚡", active_page="3_Swing_Trades")





# ── Helpers ───────────────────────────────────────────────────────────────────

def entry_score(ind):
    """Quick entry score 0–5 based on oversold indicators."""
    if not ind: return 0, []
    checks, passed = [], []
    rsi = ind.get("rsi")
    if rsi is not None:
        ok = rsi < 40
        checks.append(("RSI < 40", ok, f"{rsi:.1f}"))
        if ok: passed.append("RSI oversold")
    bb = ind.get("bb_pct_b")
    if bb is not None:
        ok = bb < 0.2
        checks.append(("BB %B < 0.2", ok, f"{bb:.2f}"))
        if ok: passed.append("Near BB lower")
    zs = ind.get("zscore")
    if zs is not None:
        ok = zs < -1.5
        checks.append(("Z-Score < -1.5", ok, f"{zs:.2f}"))
        if ok: passed.append("Extended down")
    wr = ind.get("williams_r")
    if wr is not None:
        ok = wr < -80
        checks.append(("Williams %R < -80", ok, f"{wr:.1f}"))
        if ok: passed.append("Williams oversold")
    rv = ind.get("rel_volume")
    if rv is not None:
        ok = rv >= 1.5
        checks.append(("Rel Volume ≥ 1.5x", ok, f"{rv:.1f}x"))
        if ok: passed.append("Volume spike")
    return len(passed), checks, passed


# ── Header ────────────────────────────────────────────────────────────────────
et = get_et_time()
h1, h2, h3 = st.columns([3, 1.2, 1.5])
with h1:
    st.markdown("## ⚡ Swing Trades")
    st.markdown(f"<span style='color:#444;font-size:.9em'>{date.today().strftime('%A, %B %d, %Y')}</span>",
                unsafe_allow_html=True)
with h2:
    if st.button("🔄 Refresh", use_container_width=True):
        tickers = [r["ticker"] for r in get_swing_stocks_with_predictions()]
        if tickers:
            run_full_refresh(tickers)
            st.rerun()
        else:
            st.warning("No swing tickers found.")
with h3:
    if et:
        et_str = et.strftime("%H:%M ET")
        if is_market_hours(et): st.success(f"🟢 Open · {et_str}")
        else:                   st.error(f"🔴 Closed · {et_str}")
    else:
        st.warning("⚪ Offline?")

st.markdown("---")

# ── Load data ─────────────────────────────────────────────────────────────────
rows = get_swing_stocks_with_predictions()

if not rows:
    st.info("No swing trades in watchlist. Add tickers with watchlist_type='swing'.")
    st.stop()

# ── Volume spikes for swing tickers ──────────────────────────────────────────
swing_tickers = {r["ticker"] for r in rows}
spikes = [s for s in get_volume_spikes(min_rel_volume=1.5) if s["ticker"] in swing_tickers]
if spikes:
    cols = st.columns(min(len(spikes), 4))
    for i, s in enumerate(spikes):
        with cols[i % 4]:
            st.markdown(
                f'<div class="spike-alert">⚡ <b>{s["ticker"]}</b> '
                f'<span style="font-family:IBM Plex Mono,monospace">{s["rel_volume"]:.1f}x</span>'
                f' avg volume</div>', unsafe_allow_html=True)
    st.markdown("---")

# ── Summary ───────────────────────────────────────────────────────────────────
total   = len(rows)
bullish = sum(1 for r in rows if r["signal"] == "BULLISH")
bearish = sum(1 for r in rows if r["signal"] == "BEARISH")
held    = sum(1 for r in rows if r["in_portfolio"])

for col, lbl, val in zip(st.columns(4),
    ["Swing Tickers", "▲ Bullish", "▼ Bearish", "💼 Held"],
    [total, bullish, bearish, held]):
    col.metric(lbl, val)

st.markdown("---")

# ── Per-ticker cards ──────────────────────────────────────────────────────────
for r in rows:
    ticker  = r["ticker"]
    signal  = r["signal"] or "NEUTRAL"
    score   = r["composite_score"] or 50
    current = get_current_price(ticker)
    p_mid   = r["price_mid"]
    exp_mv  = ((p_mid - current) / current * 100) if p_mid and current else None
    sig_c   = BULL if signal == "BULLISH" else (BEAR if signal == "BEARISH" else NEUT)
    sig_a   = "▲" if signal == "BULLISH" else ("▼" if signal == "BEARISH" else "—")

    ind      = get_latest_indicators(ticker)
    n_passed, checks, passed_labels = entry_score(ind) if ind else (0, [], [])
    entry_c  = BULL if n_passed >= 4 else (NEUT if n_passed >= 2 else BEAR)

    # P&L if held
    pnl_html = ""
    if r["in_portfolio"] and r["avg_cost"] and r["shares_held"] and current:
        pnl     = (current - r["avg_cost"]) * r["shares_held"]
        pnl_pct = (current - r["avg_cost"]) / r["avg_cost"] * 100
        pc      = BULL if pnl >= 0 else BEAR
        pa      = "▲" if pnl >= 0 else "▼"
        pnl_html = (f'<span style="color:{pc};font-family:IBM Plex Mono,monospace;font-size:.88em">'
                    f'{pa} ${abs(pnl):.2f} ({pnl_pct:+.1f}%) · '
                    f'{r["shares_held"]:.0f} sh @ ${r["avg_cost"]:.2f}</span>')

    with st.expander(f"**{ticker}** — {(r['name'] or ticker)[:30]}  ·  "
                     f"{sig_a} {signal}  ·  {score:.0f}/100"
                     + ("  💼" if r["in_portfolio"] else ""), expanded=True):

        col1, col2, col3 = st.columns([2, 2, 2])

        with col1:
            st.markdown("**Signal**")
            st.markdown(
                f'<span style="color:{sig_c};font-size:1.3em;font-weight:700">{sig_a} {signal}</span>'
                f'<br><span class="conf-label">{r["confidence"] or 0:.0f}% confidence</span>',
                unsafe_allow_html=True)
            st.markdown(score_bar_html(score), unsafe_allow_html=True)
            st.markdown(f'<span style="font-size:.8em;color:#444">'
                        f'T:{r["technical_score"] or 0:.0f} · F:{r["fundamental_score"] or 0:.0f} '
                        f'· S:{r["sentiment_score"] or 0:.0f}</span>', unsafe_allow_html=True)
            if pnl_html:
                st.markdown("---")
                st.markdown("**Position**")
                st.markdown(pnl_html, unsafe_allow_html=True)

        with col2:
            st.markdown("**Price**")
            if current:
                st.markdown(f'<span style="font-family:IBM Plex Mono,monospace;font-size:1.2em;font-weight:600">'
                            f'${current:.2f}</span>', unsafe_allow_html=True)
            # Pre-market price
            _spx = get_extended_hours_price(ticker)
            if not _spx.get("error") and _spx.get("price_type") in ("pre_market", "post_market"):
                _slbl = "PM" if _spx["price_type"] == "pre_market" else "AH"
                _sc   = "#c8a000" if _spx["price_type"] == "pre_market" else "#0066cc"
                _spct = _spx.get("pre_change_pct") or _spx.get("post_change_pct") or 0
                _sa   = "▲" if _spct >= 0 else "▼"
                st.markdown(
                    f'<span style="color:{_sc};font-size:.82em;font-family:IBM Plex Mono,monospace">'
                    f'{_slbl} ${_spx["price"]:.2f} {_sa}{abs(_spct):.1f}%</span>',
                    unsafe_allow_html=True)
            if p_mid and current:
                mv_c = BULL if (exp_mv or 0) >= 0 else BEAR
                mv_a = "▲" if (exp_mv or 0) >= 0 else "▼"
                st.markdown(f'<span style="color:#444;font-size:.85em">Predicted → </span>'
                            f'<span style="font-family:IBM Plex Mono,monospace;color:{score_color(score)};font-weight:600">'
                            f'${p_mid:.2f}</span> '
                            f'<span style="color:{mv_c};font-size:.85em">{mv_a}{abs(exp_mv):.2f}%</span>',
                            unsafe_allow_html=True)
                st.markdown(f'<span style="font-size:.78em;color:#555">'
                            f'Range: ${r["price_low"]:.2f} – ${r["price_high"]:.2f}</span>',
                            unsafe_allow_html=True)
            st.markdown(f'<span class="strat-pill" style="margin-top:8px;display:inline-block">'
                        f'{strategy_label(r["strategy"] or "unassigned")}</span>',
                        unsafe_allow_html=True)

        with col3:
            st.markdown(f'**Entry Checklist** — <span style="color:{entry_c}">{n_passed}/5 passed</span>',
                        unsafe_allow_html=True)
            if DEMO_MODE:
                demo_banner("🔄", "Resets on refresh",
                        "Checklist state is session-only — ticked boxes clear on browser refresh.")
            if checks:
                for label, ok, val in checks:
                    icon  = "✅" if ok else "❌"
                    color = BULL if ok else "#999"
                    st.markdown(f'<div style="font-size:.83em;color:{color};padding:2px 0">'
                                f'{icon} {label} <span style="font-family:IBM Plex Mono,monospace">({val})</span></div>',
                                unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#555;font-size:.85em">Run a refresh to load indicators.</span>',
                            unsafe_allow_html=True)

        # Action buttons
        btn1, btn2 = st.columns(2)
        with btn1:
            if st.button(f"📊 Detail", key=f"swing_det_{ticker}",
                         use_container_width=True):
                st.session_state["detail_ticker"] = ticker
                st.switch_page("pages/1_Stock_Detail.py")
        with btn2:
            if st.button(f"🎯 Analyze", key=f"swing_analyze_{ticker}",
                         use_container_width=True):
                st.session_state["ta_ticker"] = ticker
                st.switch_page("pages/5_Trading_Assistant.py")

render_footer()
