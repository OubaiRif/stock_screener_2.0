"""
pages/5_Trading_Assistant.py — Trading Assistant
Stock Screener 2.0 — uses core/ layer, no sidebar.
"""
import sys, os
from datetime import date, datetime
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.page_setup       import setup_page, render_footer
from core.db_queries       import get_current_price
from engine.db             import get_conn, get_watchlist
from engine.indicators     import get_latest_indicators, refresh_indicators
from engine.fetcher        import fetch_daily_history, fetch_fundamentals
from engine.sentiment      import get_latest_sentiment
from engine.predictor      import predict
from utils import BULL, BEAR, NEUT, demo_banner
from engine.prices import get_extended_hours_price, format_price_label, format_change_html

setup_page("Trading Assistant", "🎯", active_page="5_Trading_Assistant")

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_sp500_daily_change():
    """Get today's S&P 500 % change from macro_data."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT value FROM macro_data WHERE series_id='^GSPC'
        ORDER BY date DESC LIMIT 2
    """).fetchall()
    conn.close()
    if len(rows) < 2:
        return None
    today_val = rows[0]["value"]
    prev_val  = rows[1]["value"]
    return ((today_val - prev_val) / prev_val) * 100

def get_market_pulse(pct_change):
    """Return verdict, color, emoji and R/R suggestion based on S&P change."""
    if pct_change is None:
        return ("⚪ No market data — refresh ETF Screener to load macro data",
                "#888", "⚪", None)
    if pct_change < -3.0:
        return ("⚠️ Market down sharply — high risk day, potential trend reversal. Reduce size or stay out.",
                "#b07800", "⚠️", "1:1")
    elif pct_change < -1.5:
        return ("🟢 Good day to hunt — broad selloff creating oversold opportunities. Potential reward 2:1",
                BULL, "🟢", "2:1")
    elif pct_change < -0.5:
        return ("🟡 Moderate day — selective opportunities, be choosy. Potential reward 1.5:1",
                "#b07800", "🟡", "1.5:1")
    else:
        return ("🔴 Weak hunting day — market too strong, few oversold setups. Consider staying in cash.",
                BEAR, "🔴", None)

def check_entry(ind, sent, pred):
    """Return entry checklist with pass/fail for each condition."""
    checks = []

    # RSI
    rsi = ind.get("rsi")
    checks.append({
        "label": "RSI < 40 (oversold)",
        "pass":  rsi is not None and rsi < 40,
        "value": f"{rsi:.1f}" if rsi else "—",
        "note":  "Oversold" if rsi and rsi < 40 else ("Neutral" if rsi and rsi < 60 else "Overbought")
    })

    # BB %B
    bb = ind.get("bb_pct_b")
    checks.append({
        "label": "BB %B < 0.2 (near lower band)",
        "pass":  bb is not None and bb < 0.2,
        "value": f"{bb:.2f}" if bb is not None else "—",
        "note":  "Near lower band" if bb and bb < 0.2 else ("Mid-band" if bb and bb < 0.8 else "Near upper band")
    })

    # Z-Score
    zs = ind.get("zscore")
    checks.append({
        "label": "Z-Score < -1.5 (extended down)",
        "pass":  zs is not None and zs < -1.5,
        "value": f"{zs:.2f}σ" if zs is not None else "—",
        "note":  f"{'Below' if zs and zs < 0 else 'Above'} mean" if zs else "—"
    })

    # Relative Volume
    rv = ind.get("rel_volume")
    checks.append({
        "label": "Rel Volume > 1.2 (unusual activity)",
        "pass":  rv is not None and rv > 1.2,
        "value": f"{rv:.2f}x" if rv is not None else "—",
        "note":  "High" if rv and rv > 1.5 else ("Normal" if rv and rv > 0.8 else "Low")
    })

    # Sentiment
    s_pass = sent.get("available") and sent.get("avg_score", 0) > 0.2
    s_val  = f"+{sent['avg_score']:.2f}" if sent.get("available") else "—"
    checks.append({
        "label": "Sentiment Bullish",
        "pass":  s_pass,
        "value": s_val,
        "note":  sent.get("overall_label", "No data")
    })

    # XGBoost
    ml = pred.get("ml")
    if ml:
        bull_prob = ml.get("bullish_prob", 0)
        ml_pass   = bull_prob > 60
        checks.append({
            "label": "XGBoost Bull > 60%",
            "pass":  ml_pass,
            "value": f"{bull_prob:.0f}%",
            "note":  f"Val acc: {ml.get('val_accuracy', 0):.1f}%"
        })
    else:
        checks.append({
            "label": "XGBoost Bull > 60%",
            "pass":  False,
            "value": "—",
            "note":  "No ML model — retrain first"
        })

    return checks

def setup_quality(checks):
    """Score the setup based on how many checks pass."""
    passed = sum(1 for c in checks if c["pass"])
    total  = len(checks)
    if passed >= 5:
        return "Strong", BULL
    elif passed >= 3:
        return "Moderate", "#b07800"
    elif passed >= 2:
        return "Weak", "#b07800"
    else:
        return "No Setup", BEAR

def check_exit(ind, pred, entry_price, target_price, stop_price, entry_date_str):
    """Return exit checklist."""
    checks = []

    # Method 1 — RSI
    rsi = ind.get("rsi")
    rsi_exit = rsi is not None and rsi >= 55
    checks.append({
        "method": "Method 1 — RSI Target",
        "label":  f"RSI reached 55–60 (currently {rsi:.1f})" if rsi else "RSI not available",
        "pass":   rsi_exit,
        "action": "✅ RSI target reached — consider exiting" if rsi_exit else f"⏳ Wait — RSI needs to reach 55 (currently {rsi:.1f})" if rsi else "—"
    })

    # Method 2 — Price target
    current = ind.get("close")
    price_exit = current is not None and target_price and current >= target_price
    checks.append({
        "method": "Method 2 — Price Target",
        "label":  f"Price reached target ${target_price:.2f} (currently ${current:.2f})" if current and target_price else "Enter target price in calculator",
        "pass":   price_exit,
        "action": "✅ Price target reached — take profit" if price_exit else f"⏳ Wait — ${target_price - current:.2f} away from target" if current and target_price else "—"
    })

    # Method 2b — Stop loss
    stop_hit = current is not None and stop_price and current <= stop_price
    checks.append({
        "method": "Stop Loss",
        "label":  f"Price hit stop ${stop_price:.2f} (currently ${current:.2f})" if current and stop_price else "Enter stop price in calculator",
        "pass":   stop_hit,
        "action": "🔴 STOP LOSS HIT — EXIT IMMEDIATELY" if stop_hit else f"✅ Safe — ${current - stop_price:.2f} above stop" if current and stop_price else "—"
    })

    # Method 3 — App signal
    signal = pred.get("signal", "NEUTRAL")
    sig_exit = signal == "BEARISH"
    checks.append({
        "method": "Method 3 — App Signal",
        "label":  f"Signal turned Bearish (currently {signal})",
        "pass":   sig_exit,
        "action": "✅ Signal is Bearish — exit now" if sig_exit else f"⏳ Hold — signal is {signal}" if signal != "BEARISH" else "—"
    })

    # Holding period
    if entry_date_str:
        try:
            entry_dt  = date.fromisoformat(entry_date_str)
            days_held = (date.today() - entry_dt).days
            # Count only weekdays roughly
            weekdays  = sum(1 for i in range(days_held)
                           if (entry_dt.toordinal() + i) % 7 not in (6, 0))
            too_long  = weekdays >= 5
            checks.append({
                "method": "Holding Period",
                "label":  f"Held {weekdays} trading days (max 5)",
                "pass":   too_long,
                "action": "⚠️ Held 5+ days with no clear move — free your capital" if too_long else f"⏳ Day {weekdays} of 5 — still within window"
            })
        except Exception:
            pass

    return checks

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown("# 🎯 Trading Assistant")
demo_banner("📋", "Demo limitations on this page",
            "• <strong>Exit checklist</strong>: requires a live position with entry price in the journal — shows defaults if journal is empty. "
            "• <strong>Price &amp; ATR</strong>: fetched via yfinance; may show N/A on cloud due to rate limits — run locally for real-time data.")
st.markdown("---")

# ── Section 1: Market Pulse ───────────────────────────────────────────────────
st.markdown("### 📡 Market Pulse")

pct = get_sp500_daily_change()
verdict, v_color, v_emoji, rr = get_market_pulse(pct)

pct_str = f" (S&P 500: {pct:+.2f}% today)" if pct is not None else ""

st.markdown(
    f'<div style="background:#f4f6fb;border-radius:10px;padding:16px 22px;'
    f'border-left:4px solid {v_color};margin-bottom:1.5rem">'
    f'<span style="font-size:1.1em;font-weight:600;color:{v_color}">{verdict}</span>'
    f'<span style="font-size:0.85em;color:#666;margin-left:10px">{pct_str}</span>'
    f'</div>',
    unsafe_allow_html=True
)

st.markdown("---")

# ── Section 2: Entry Analyzer ─────────────────────────────────────────────────
st.markdown("### 🔍 Entry Analyzer")

# Pre-load ticker if coming from Swing page
_preload = st.session_state.pop("ta_ticker", None)
col_search, col_btn = st.columns([3, 1])
with col_search:
    ticker_input = st.text_input("", placeholder="Type ticker e.g. PDYN and press Enter",
                                  label_visibility="collapsed", key="ta_search",
                                  value=_preload or "").upper().strip()
with col_btn:
    st.markdown("<div style='margin-top:4px'>", unsafe_allow_html=True)
    analyze = st.button("Analyze", use_container_width=True, key="ta_analyze")
    st.markdown("</div>", unsafe_allow_html=True)

# Auto-trigger analysis if pre-loaded
if _preload and not ticker_input:
    ticker_input = _preload

if ticker_input:
    with st.spinner(f"Analyzing {ticker_input}…"):
        # Fetch data if not in DB
        conn = get_conn()
        row  = conn.execute("SELECT ticker FROM stocks WHERE ticker=?",
                            (ticker_input,)).fetchone()
        conn.close()
        if not row:
            from engine.db import upsert_stock
            upsert_stock(ticker_input)
            try:
                fetch_daily_history(ticker_input)
                fetch_fundamentals(ticker_input)
                refresh_indicators(ticker_input)
            except Exception as e:
                st.error(f"Could not fetch data for {ticker_input}: {e}")
                st.stop()

        ind  = get_latest_indicators(ticker_input)
        sent = get_latest_sentiment(ticker_input)
        pred = predict(ticker_input)

        # Get current price from price_history
        conn2 = get_conn()
        ph = conn2.execute(
            "SELECT close FROM price_history WHERE ticker=? ORDER BY date DESC LIMIT 1",
            (ticker_input,)
        ).fetchone()
        conn2.close()
        if ph:
            ind["close"] = ph["close"]

        if not ind:
            st.warning(f"No indicator data for {ticker_input}. Try refreshing from Stock Detail.")
            st.stop()

        # ── Extended hours price banner ────────────────────────────────────
        _px = get_extended_hours_price(ticker_input)
        if not _px.get("error"):
            _label    = format_price_label(_px)
            _price    = _px.get("price")
            _regular  = _px.get("regular")
            _chg_html = ""
            if _px.get("price_type") == "pre_market" and _px.get("pre_change") is not None:
                _chg_html = format_change_html(_px["pre_change"], _px["pre_change_pct"])
            elif _px.get("price_type") == "post_market" and _px.get("post_change") is not None:
                _chg_html = format_change_html(_px["post_change"], _px["post_change_pct"])
            if _px.get("price_type") in ("pre_market", "post_market"):
                _bg   = "#fffbe6" if _px["price_type"] == "pre_market" else "#f0f4ff"
                _bd   = "#c8a000" if _px["price_type"] == "pre_market" else "#0066cc"
                _time = (_px.get("pre_market_time") or _px.get("post_market_time"))
                _ts   = _time.strftime("%H:%M") if _time else ""
                st.markdown(
                    f'<div style="background:{_bg};border-left:4px solid {_bd};'
                    f'border-radius:8px;padding:10px 16px;margin-bottom:12px">'
                    f'<span style="font-size:.78em;text-transform:uppercase;letter-spacing:.06em;color:{_bd}">'
                    f'{_label}{" · " + _ts if _ts else ""}</span><br>'
                    f'<span style="font-family:IBM Plex Mono,monospace;font-size:1.2em;font-weight:700">'
                    f'${_price:.2f}</span>'
                    + (f' {_chg_html}' if _chg_html else '')
                    + (f'<span style="font-size:.8em;color:#555;margin-left:10px">Regular close: ${_regular:.2f}</span>' if _regular else '')
                    + '</div>',
                    unsafe_allow_html=True)
            # Use extended hours price as current for calculator if available
            if _price and _px.get("price_type") in ("pre_market", "post_market"):
                ind["close"] = _price

        checks  = check_entry(ind, sent, pred)
        quality, q_color = setup_quality(checks)
        passed  = sum(1 for c in checks if c["pass"])
        current = ind.get("close") or 0
        atr     = ind.get("atr") or 0

        # Setup verdict
        st.markdown(
            f'<div style="background:#f4f6fb;border-radius:10px;padding:16px 22px;'
            f'border-left:4px solid {q_color};margin-bottom:1rem">'
            f'<div style="font-size:1.2em;font-weight:700;color:{q_color}">'
            f'{quality + " Setup" if quality != "No Setup" else "No Setup"} — {passed}/{len(checks)} conditions met</div>'
            f'<div style="font-size:0.85em;color:#666;margin-top:4px">'
            f'{ticker_input} · Current Price: ${current:.2f} · ATR: ${atr:.2f}</div>'
            f'</div>',
            unsafe_allow_html=True
        )

        # ── Entry checklist | Exit checklist side by side ────────────────────
        cl_left, cl_right = st.columns(2)

        with cl_left:
            st.markdown("**🔍 Entry Checklist**")
            for c in checks:
                icon  = "✅" if c["pass"] else "❌"
                color = BULL if c["pass"] else BEAR
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;'
                    f'padding:6px 0;border-bottom:1px solid #e0e4ee">'
                    f'<span style="font-size:0.88em">{icon} {c["label"]}</span>'
                    f'<span style="font-family:IBM Plex Mono,monospace;font-size:0.85em;color:{color};font-weight:600">{c["value"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

        with cl_right:
            st.markdown("**🚪 Exit Checklist**")
            # Need entry_date and prices for exit — use defaults until calc runs
            _entry_date_tmp = date.today().isoformat()
            _stop_tmp  = round(current - atr, 2) if atr else 0
            _target_tmp= round(current + atr * 1.5, 2) if atr else 0
            exit_checks_preview = check_exit(ind, pred, current, _target_tmp,
                                             _stop_tmp, _entry_date_tmp)
            for ec in exit_checks_preview:
                is_stop  = ec["method"] == "Stop Loss"
                icon     = "🔴" if (is_stop and ec["pass"]) else ("✅" if (ec["pass"] and not is_stop) else "⏳")
                bg_color = "#fff0f0" if (is_stop and ec["pass"]) else ("#f0fff8" if ec["pass"] else "#f4f6fb")
                bd_color = BEAR if (is_stop and ec["pass"]) else (BULL if ec["pass"] else "#dde1ea")
                st.markdown(
                    f'<div style="background:{bg_color};border-radius:6px;padding:8px 12px;'
                    f'border-left:3px solid {bd_color};margin-bottom:6px">'
                    f'<div style="font-size:0.72em;text-transform:uppercase;letter-spacing:0.06em;color:#888">{ec["method"]}</div>'
                    f'<div style="font-size:0.85em;font-weight:600">{ec["action"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

        st.markdown("---")

        # ── Trade Calculator with Entry/Exit toggle ────────────────────────────
        st.markdown("### 🧮 Trade Calculator")
        calc_mode = st.radio("Mode", ["📥 Entry", "📤 Exit"],
                             horizontal=True, key="ta_calc_mode")

        calc_left, calc_right = st.columns(2)

        with calc_left:
            st.markdown("**Inputs**")
            if calc_mode == "📥 Entry":
                # Use extended hours price as default if available
                _default_entry = float(_px.get("price") or current) if "_px" in dir() else float(current)
                dollar_amount = st.number_input("Dollar amount ($)", min_value=0.0,
                                                value=500.0, step=50.0, key="ta_dollars")
                stop_mult   = st.selectbox("Stop loss multiplier (× ATR)",
                                           [0.75, 1.0, 1.25, 1.5], index=1, key="ta_stop_mult")
                target_mult = st.selectbox("Target multiplier (× ATR)",
                                           [1.0, 1.5, 2.0, 2.5, 3.0], index=1, key="ta_target_mult")
                entry_date  = st.date_input("Entry date", value=date.today(), key="ta_entry_date")
            else:
                shares_held  = st.number_input("Shares held", min_value=0, value=100, step=1, key="ta_exit_shares")
                entry_price  = st.number_input("Entry price ($)", min_value=0.0,
                                               value=float(current) if current else 0.0,
                                               step=0.01, key="ta_exit_entry")
                stop_mult   = st.selectbox("Stop loss multiplier (× ATR)",
                                           [0.75, 1.0, 1.25, 1.5], index=1, key="ta_stop_mult_ex")
                target_mult = st.selectbox("Target multiplier (× ATR)",
                                           [1.0, 1.5, 2.0, 2.5, 3.0], index=1, key="ta_target_mult_ex")
                entry_date  = st.date_input("Entry date", value=date.today(), key="ta_entry_date_ex")

        with calc_right:
            st.markdown("**Results**")
            if current > 0 and atr > 0:
                stop_price   = round(current - atr * stop_mult, 2)
                target_price = round(current + atr * target_mult, 2)

                if calc_mode == "📥 Entry":
                    shares      = int(dollar_amount / current) if dollar_amount > 0 else 0
                    actual_cost = shares * current
                    max_loss    = round(shares * (current - stop_price), 2)
                    max_gain    = round(shares * (target_price - current), 2)
                    rr_ratio    = round(max_gain / max_loss, 2) if max_loss > 0 else 0
                    rr_color    = BULL if rr_ratio >= 1.5 else ("#b07800" if rr_ratio >= 1.0 else BEAR)
                    st.markdown(
                        f'<div style="background:#f4f6fb;border-radius:10px;padding:16px 18px">'
                        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">'
                        f'<div><div style="font-size:0.72em;color:#666;text-transform:uppercase">Entry Price</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace">${current:.2f}</div></div>'
                        f'<div><div style="font-size:0.72em;color:#666;text-transform:uppercase">Shares</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace">{shares}</div></div>'
                        f'<div><div style="font-size:0.72em;color:#666;text-transform:uppercase">Total Cost</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace">${actual_cost:.2f}</div></div>'
                        f'<div><div style="font-size:0.72em;color:#666;text-transform:uppercase">Risk / Reward</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace;color:{rr_color}">1 : {rr_ratio}</div>'
                        f'<div style="font-size:0.78em;color:{rr_color}">{"Good" if rr_ratio >= 1.5 else ("Acceptable" if rr_ratio >= 1.0 else "Poor")}</div></div>'
                        f'<div><div style="font-size:0.72em;color:#cc2200;text-transform:uppercase">Stop Loss</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace;color:{BEAR}">${stop_price:.2f}</div>'
                        f'<div style="font-size:0.78em;color:{BEAR}">Max loss: ${max_loss:.2f}</div></div>'
                        f'<div><div style="font-size:0.72em;color:#007a4d;text-transform:uppercase">Target</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace;color:{BULL}">${target_price:.2f}</div>'
                        f'<div style="font-size:0.78em;color:{BULL}">Max gain: ${max_gain:.2f}</div></div>'
                        f'</div></div>', unsafe_allow_html=True)
                else:
                    # Exit mode
                    cost_basis   = shares_held * entry_price
                    current_val  = shares_held * current
                    unrealized   = current_val - cost_basis
                    unreal_pct   = (unrealized / cost_basis * 100) if cost_basis else 0
                    pnl_c        = BULL if unrealized >= 0 else BEAR
                    pnl_a        = "▲" if unrealized >= 0 else "▼"
                    dist_stop    = current - stop_price
                    dist_target  = target_price - current
                    exit_checks  = check_exit(ind, pred, current, target_price,
                                              stop_price, entry_date.isoformat())
                    stop_hit     = any(ec["method"] == "Stop Loss" and ec["pass"] for ec in exit_checks)
                    st.markdown(
                        f'<div style="background:#f4f6fb;border-radius:10px;padding:16px 18px">'
                        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">'
                        f'<div><div style="font-size:0.72em;color:#666;text-transform:uppercase">Current Price</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace">${current:.2f}</div></div>'
                        f'<div><div style="font-size:0.72em;color:#666;text-transform:uppercase">Unrealized P&L</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace;color:{pnl_c}">{pnl_a} ${abs(unrealized):.2f} ({unreal_pct:+.1f}%)</div></div>'
                        f'<div><div style="font-size:0.72em;color:#cc2200;text-transform:uppercase">Stop Loss</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace;color:{BEAR}">${stop_price:.2f}</div>'
                        f'<div style="font-size:0.78em;color:{BEAR}">${dist_stop:.2f} away</div></div>'
                        f'<div><div style="font-size:0.72em;color:#007a4d;text-transform:uppercase">Target</div>'
                        f'<div style="font-size:1.2em;font-weight:700;font-family:IBM Plex Mono,monospace;color:{BULL}">${target_price:.2f}</div>'
                        f'<div style="font-size:0.78em;color:{BULL}">${dist_target:.2f} away</div></div>'
                        f'<div><div style="font-size:0.72em;text-transform:uppercase;color:#666">Recommendation</div>'
                        f'<div style="font-size:1.0em;font-weight:700;color:{"#cc2200" if stop_hit else BULL}">{"🔴 EXIT — Stop Hit" if stop_hit else "⏳ Hold Position"}</div></div>'
                        f'</div></div>', unsafe_allow_html=True)
            else:
                demo_banner("⚠️", "Price / ATR not available",
                            "yfinance rate-limits on Streamlit Cloud. Refresh the page or run the app locally for live price and ATR data.")



render_footer()
