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
from engine.db        import get_watchlist, get_conn
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

        # Fetch today's swing prediction if available
        _today = date.today().isoformat()
        _conn_sw = get_conn()
        _sw_pred = _conn_sw.execute("""
            SELECT signal, confidence, composite_score, technical_score,
                   fundamental_score, sentiment_score, horizon_days,
                   price_low, price_mid, price_high
            FROM predictions
            WHERE ticker=? AND date=? AND prediction_type LIKE 'swing_%'
            ORDER BY generated_at DESC LIMIT 1
        """, (ticker, _today)).fetchone()
        _conn_sw.close()

        # Use swing prediction if available, else fall back to next_day
        if _sw_pred:
            sw_signal = _sw_pred["signal"] or "NEUTRAL"
            sw_conf   = _sw_pred["confidence"] or 0
            sw_score  = _sw_pred["composite_score"] or 50
            sw_ts     = _sw_pred["technical_score"] or 0
            sw_fs     = _sw_pred["fundamental_score"] or 0
            sw_ss     = _sw_pred["sentiment_score"] or 0
            sw_h      = _sw_pred["horizon_days"] or 5
            sw_mid    = _sw_pred["price_mid"]
            sw_low    = _sw_pred["price_low"]
            sw_high   = _sw_pred["price_high"]
            sw_c      = BULL if sw_signal=="BULLISH" else (BEAR if sw_signal=="BEARISH" else NEUT)
            sw_a      = "▲" if sw_signal=="BULLISH" else ("▼" if sw_signal=="BEARISH" else "—")
        else:
            sw_signal, sw_conf, sw_score = signal, r["confidence"] or 0, score
            sw_ts = r["technical_score"] or 0
            sw_fs = r["fundamental_score"] or 0
            sw_ss = r["sentiment_score"] or 0
            sw_h, sw_mid, sw_low, sw_high = 5, None, None, None
            sw_c, sw_a = sig_c, sig_a

        with col1:
            st.markdown("**Signal**")
            st.markdown(
                f'<span style="color:{sw_c};font-size:1.3em;font-weight:700">{sw_a} {sw_signal}</span>'
                f'<br><span class="conf-label">{sw_conf:.0f}% confidence</span>'
                + (f'<br><span style="font-size:.75em;color:#555">Swing {sw_h}d · direction focus</span>'
                   if _sw_pred else '<br><span style="font-size:.75em;color:#777">next_day signal</span>'),
                unsafe_allow_html=True)
            st.markdown(score_bar_html(sw_score), unsafe_allow_html=True)
            st.markdown(f'<span style="font-size:.8em;color:#444">'
                        f'T:{sw_ts:.0f} · F:{sw_fs:.0f} · S:{sw_ss:.0f}</span>',
                        unsafe_allow_html=True)
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
            if (sw_mid or p_mid) and current:
                _disp_mid  = sw_mid or p_mid
                _disp_low  = sw_low or r.get("price_low")
                _disp_high = sw_high or r.get("price_high")
                _disp_mv   = ((_disp_mid - current) / current * 100) if _disp_mid and current else None
                mv_c = BULL if (_disp_mv or 0) >= 0 else BEAR
                mv_a = "▲" if (_disp_mv or 0) >= 0 else "▼"
                _label = f"Swing {sw_h}d →" if _sw_pred and sw_mid else "Predicted →"
                st.markdown(f'<span style="color:#444;font-size:.85em">{_label} </span>'
                            f'<span style="font-family:IBM Plex Mono,monospace;color:{score_color(sw_score)};font-weight:600">'
                            f'${_disp_mid:.2f}</span> '
                            f'<span style="color:{mv_c};font-size:.85em">{mv_a}{abs(_disp_mv):.2f}%</span>',
                            unsafe_allow_html=True)
                if _disp_low and _disp_high:
                    st.markdown(f'<span style="font-size:.78em;color:#555">'
                                f'Range: ${_disp_low:.2f} – ${_disp_high:.2f}</span>',
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
