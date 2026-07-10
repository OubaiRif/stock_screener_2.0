"""
pages/4_Gold_Dashboard.py — Gold trading dashboard.
Stock Screener 2.0 — uses core/ layer, no sidebar.
"""
import sys, os, time
from datetime import date, datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
try:
    import pandas_ta as ta
    PANDAS_TA_AVAILABLE = True
except ImportError:
    ta = None
    PANDAS_TA_AVAILABLE = False

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup     import setup_page, render_footer
from core.db_queries     import get_macro_series, log_journal_entry
from engine.db           import get_conn, init_db
from engine.fetcher      import fetch_daily_history
from engine.gold_signals import (get_position, log_trade, get_trade_history,
                                  get_current_price, get_live_price, compute_pnl,
                                  fetch_gold_history, get_gold_from_db,
                                  compute_swing_signal, compute_macro_hold_signal,
                                  get_action_recommendations,
                                  get_position_aware_recommendations)
from engine.etf_signals  import get_latest_macro, refresh_etf_signals
from engine.sentiment    import get_latest_sentiment, get_headlines
from utils               import score_color, get_et_time, is_market_hours, BULL, BEAR, NEUT

setup_page("Gold Dashboard", "🥇", active_page="4_Gold_Dashboard")

st.markdown("""<style>
.gold-card { background:#1a1f2e; border-radius:10px; padding:18px 22px; margin-bottom:10px; }
.gold-card.bull { border-left:4px solid #00c896; }
.gold-card.bear { border-left:4px solid #ff4b4b; }
.gold-card.neut { border-left:4px solid #ffd700; }
.pos-label  { font-size:0.78em; text-transform:uppercase; letter-spacing:0.08em;
              color:#333; margin-bottom:4px; }
.pos-val    { font-family:'IBM Plex Mono',monospace; font-size:1.1em; font-weight:600; }
.sig-big    { font-family:'IBM Plex Mono',monospace; font-size:1.4em; font-weight:700; }
.driver-bull { color:#00c896; font-size:0.83em; padding:3px 0; }
.driver-bear { color:#ff4b4b; font-size:0.83em; padding:3px 0; }
.ind-row    { display:flex; justify-content:space-between; padding:7px 0;
              border-bottom:1px solid #252b3b; font-size:0.85em; }
.ind-row:last-child { border-bottom:none; }
.ind-label  { color:#333; width:50%; }
.ind-val    { font-family:'IBM Plex Mono',monospace; font-weight:600; width:25%; text-align:right; }
.ind-note   { width:25%; text-align:right; font-size:0.8em; }
.hl-bull    { color:#00c896; font-weight:700; margin-right:6px; }
.hl-bear    { color:#ff4b4b; font-weight:700; margin-right:6px; }
.hl-neut    { color:#ffd700; font-weight:700; margin-right:6px; }
.hl-text    { font-size:0.88em; color:#ddd; }
.hl-meta    { font-size:0.75em; color:#444; margin-top:3px; }
.rec-item   { padding:6px 0; border-bottom:1px solid #1e2130; font-size:0.85em; }
.rec-item:last-child { border-bottom:none; }
</style>""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _signal_color(signal):
    if signal in ("STRONG ADD", "ADD", "HOLD — Thesis Intact"): return BULL
    if signal in ("EXIT", "AVOID / SELL"):                       return BEAR
    if signal == "REDUCE":                                        return NEUT
    if "BUY" in signal or "HOLD — Thesis" in signal:             return BULL
    if "AVOID" in signal or "EXIT" in signal:                    return BEAR
    return NEUT

def _signal_class(signal):
    if signal in ("STRONG ADD", "ADD"): return "bull"
    if signal in ("EXIT", "REDUCE"):    return "bear"
    if "BUY" in signal or "HOLD — Thesis" in signal: return "bull"
    if "AVOID" in signal or "EXIT" in signal:         return "bear"
    return "neut"

def _trend_arrow(trend):
    return {"Rising": ("▲ Rising", BULL), "Falling": ("▼ Falling", BEAR)}.get(trend, ("── Stable", NEUT))

def _hl_icon(score):
    if score is None:  return '<span class="hl-neut">—</span>'
    if score > 0.2:    return '<span class="hl-bull">▲</span>'
    if score < -0.2:   return '<span class="hl-bear">▼</span>'
    return '<span class="hl-neut">—</span>'

def _time_ago(pub_str):
    if not pub_str: return ""
    try:
        delta = datetime.utcnow() - datetime.fromisoformat(pub_str.replace("Z",""))
        if delta.days > 0: return f"{delta.days}d ago"
        hrs = delta.seconds // 3600
        if hrs > 0:        return f"{hrs}h ago"
        return f"{delta.seconds//60}m ago"
    except: return ""

# ── Trade modal ───────────────────────────────────────────────────────────────

@st.dialog("Log a Trade — IAU", width="small")
def trade_modal(position):
    ticker   = "IAU"
    cur_pos  = position
    cur_price = get_live_price(ticker) or get_current_price(ticker) or 0

    action = st.radio("Action", ["Buy", "Sell"], horizontal=True)
    shares = st.number_input("Number of shares", min_value=0.01, step=1.0, value=1.0)
    price  = st.number_input("Price per share ($)", min_value=0.01,
                              step=0.01, value=float(cur_price) if cur_price else 0.0)
    notes  = st.text_input("Notes (optional)", placeholder="e.g. Adding on RSI dip")

    # Preview
    if shares > 0 and price > 0:
        cur_shares  = cur_pos.get("shares", 0)
        cur_avg     = cur_pos.get("avg_cost", 0)
        if action == "Buy":
            new_shares   = cur_shares + shares
            new_avg      = ((cur_shares * cur_avg) + (shares * price)) / new_shares
            total_cost   = shares * price
            st.markdown(f"""
            <div style='background:#0e1117;border-radius:8px;padding:12px;margin-top:10px'>
            <div style='font-size:.82em;color:#333'>After this trade:</div>
            <div style='font-family:IBM Plex Mono,monospace;margin-top:6px'>
            Total shares: <b>{new_shares:.0f}</b><br>
            New avg cost: <b>${new_avg:.2f}</b><br>
            Total spent: <b>${total_cost:.2f}</b>
            </div></div>""", unsafe_allow_html=True)
        else:
            new_shares = max(0, cur_shares - shares)
            realized   = (price - cur_avg) * shares
            pnl_c      = BULL if realized >= 0 else BEAR
            st.markdown(f"""
            <div style='background:#0e1117;border-radius:8px;padding:12px;margin-top:10px'>
            <div style='font-size:.82em;color:#333'>After this trade:</div>
            <div style='font-family:IBM Plex Mono,monospace;margin-top:6px'>
            Remaining shares: <b>{new_shares:.0f}</b><br>
            Realized P&L: <span style='color:{pnl_c}'><b>${realized:.2f}</b></span><br>
            Avg cost unchanged: <b>${cur_avg:.2f}</b>
            </div></div>""", unsafe_allow_html=True)

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("✓ Confirm Trade", use_container_width=True):
            if shares > 0 and price > 0:
                try:
                    log_trade(action.upper(), shares, price, ticker, notes)
                    st.success(f"✓ {action} {shares:.0f} shares @ ${price:.2f} logged")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
            else:
                st.warning("Enter shares and price.")
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()

# ── Header ────────────────────────────────────────────────────────────────────
et = get_et_time()
h1, h2, h3 = st.columns([3, 1.2, 1.5])
with h1:
    st.markdown("# 🥇 Gold Dashboard")
    st.markdown(f"<span style='color:#333;font-size:0.9em'>"
                f"{date.today().strftime('%A, %B %d, %Y')}</span>",
                unsafe_allow_html=True)
with h2:
    st.markdown("<div style='margin-top:18px'>", unsafe_allow_html=True)
    if st.button("🔄 Refresh", use_container_width=True):
        with st.spinner("Fetching gold data…"):
            try:
                fetch_daily_history("IAU", days=365)
                fetch_daily_history("GLD", days=365)
                df_gold = fetch_gold_history(days=365)
                if not df_gold.empty:
                    conn = get_conn()
                    for ts, row in df_gold.iterrows():
                        conn.execute("""
                            INSERT OR REPLACE INTO macro_data (series_id, date, value, source)
                            VALUES (?,?,?,'yfinance')
                        """, ("GC=F", ts.strftime("%Y-%m-%d"), round(float(row["Close"]),4)))
                    conn.commit(); conn.close()
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

# ── Load data ─────────────────────────────────────────────────────────────────
position  = get_position("IAU")
cur_price = get_live_price("IAU") or get_current_price("IAU")
macro     = get_latest_macro()

# Load gold price history
df_gold = get_gold_from_db(days=365)
if df_gold.empty:
    df_gold = fetch_gold_history(days=365)

swing  = compute_swing_signal(df_gold) if not df_gold.empty else {}
macro_sig = compute_macro_hold_signal(macro) if macro else {}
pnl    = compute_pnl(position, cur_price) if cur_price else {}
recs   = get_position_aware_recommendations(swing, macro_sig, pnl) if swing and macro_sig else {}

# ── SECTION 1: Position + Swing Trade side by side ───────────────────────────
pos_col, swing_col = st.columns([1, 1.6])

with pos_col:
    st.markdown('<div class="gold-card neut">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Your IAU Position</div>', unsafe_allow_html=True)

    if position["shares"] > 0 and pnl:
        pnl_color = BULL if pnl["pnl"] >= 0 else BEAR
        pnl_arrow = "▲" if pnl["pnl"] >= 0 else "▼"

        st.markdown(f"""
        <div class="ind-row">
          <span class="ind-label">Shares Held</span>
          <span class="ind-val">{pnl['shares']:.0f}</span>
          <span class="ind-note" style="color:#333"></span>
        </div>
        <div class="ind-row">
          <span class="ind-label">Average Cost</span>
          <span class="ind-val">${pnl['avg_cost']:.2f}</span>
          <span class="ind-note" style="color:#333">per share</span>
        </div>
        <div class="ind-row">
          <span class="ind-label">Cost Basis</span>
          <span class="ind-val">${pnl['cost_basis']:,.2f}</span>
          <span class="ind-note" style="color:#333">total invested</span>
        </div>
        <div class="ind-row">
          <span class="ind-label">Current Price</span>
          <span class="ind-val">${cur_price:.2f}</span>
          <span class="ind-note" style="color:#333">per share</span>
        </div>
        <div class="ind-row">
          <span class="ind-label">Current Value</span>
          <span class="ind-val">${pnl['current_value']:,.2f}</span>
          <span class="ind-note" style="color:#333">total value</span>
        </div>
        <div class="ind-row">
          <span class="ind-label">Unrealized P&L</span>
          <span class="ind-val" style="color:{pnl_color}">
            {pnl_arrow} ${abs(pnl['pnl']):,.2f}
          </span>
          <span class="ind-note" style="color:{pnl_color}">
            {pnl['pnl_pct']:+.2f}%
          </span>
        </div>
        <div class="ind-row">
          <span class="ind-label">Break-Even Price</span>
          <span class="ind-val">${pnl['break_even']:.2f}</span>
          <span class="ind-note" style="color:#333">
            {"▼ " + str(abs(round(pnl['avg_cost'] - cur_price, 2))) + " away"
             if cur_price < pnl['avg_cost']
             else "✓ Above break-even"}
          </span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown('<span style="color:#333">No position. Log a trade to start tracking.</span>',
                    unsafe_allow_html=True)

    st.markdown("<div style='margin-top:14px'>", unsafe_allow_html=True)
    if st.button("📝 Log a Trade", use_container_width=True):
        trade_modal(position)
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with swing_col:
    if swing:
        sig_color = _signal_color(swing["signal"])
        sig_class = _signal_class(swing["signal"])
        sig_arrow = "▲" if "BUY" in swing["signal"] else ("▼" if "AVOID" in swing["signal"] else "──")

        st.markdown(f'<div class="gold-card {sig_class}">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">🎯 Swing Trade Signal — Next 1–4 Weeks</div>',
                    unsafe_allow_html=True)

        st.markdown(
            f'<div class="sig-big" style="color:{sig_color}">'
            f'{sig_arrow} {swing["signal"]}</div>'
            f'<span style="color:#333;font-size:.85em">'
            f'{swing["score"]}/100 · {swing["confidence"]:.0f}% confidence</span>',
            unsafe_allow_html=True)

        if swing.get("entry_low"):
            st.markdown(f"""
            <div style='margin-top:14px;font-family:IBM Plex Mono,monospace;font-size:.85em'>
            <span style='color:#333'>Entry Zone: </span>
            <span style='color:#e0e0e0'>${swing['entry_low']:.2f} – ${swing['entry_high']:.2f}</span>
            &nbsp;&nbsp;
            <span style='color:#333'>Target: </span>
            <span style='color:{BULL}'>${swing['target']:.2f}</span>
            &nbsp;&nbsp;
            <span style='color:#333'>Stop: </span>
            <span style='color:{BEAR}'>${swing['stop_loss']:.2f}</span>
            &nbsp;&nbsp;
            <span style='color:#333'>Risk/Reward: </span>
            <span style='color:#e0e0e0'>1 : {swing['rr_ratio']}</span>
            </div>""", unsafe_allow_html=True)

        # Indicators
        ind_rows = ""
        if swing.get("rsi"):
            rsi_c = BULL if swing["rsi"] < 40 else (BEAR if swing["rsi"] > 60 else NEUT)
            rsi_n = "Oversold" if swing["rsi"] < 35 else ("Overbought" if swing["rsi"] > 65 else "Neutral")
            ind_rows += (f'<div class="ind-row"><span class="ind-label">RSI (14-day)</span>'
                         f'<span class="ind-val" style="color:{rsi_c}">{swing["rsi"]:.1f} / 100</span>'
                         f'<span class="ind-note" style="color:{rsi_c}">{rsi_n}</span></div>')
        if swing.get("macd_bull") is not None:
            mc = BULL if swing["macd_bull"] else BEAR
            mn = "Bullish crossover" if swing["macd_bull"] else "Bearish crossover"
            ind_rows += (f'<div class="ind-row"><span class="ind-label">MACD</span>'
                         f'<span class="ind-val" style="color:{mc}">{"▲" if swing["macd_bull"] else "▼"}</span>'
                         f'<span class="ind-note" style="color:{mc}">{mn}</span></div>')
        if swing.get("bb_pct_b") is not None:
            bc = BULL if swing["bb_pct_b"] < 0.2 else (BEAR if swing["bb_pct_b"] > 0.8 else NEUT)
            bn = "Near lower band" if swing["bb_pct_b"] < 0.2 else ("Near upper band" if swing["bb_pct_b"] > 0.8 else "Mid-band")
            ind_rows += (f'<div class="ind-row"><span class="ind-label">Bollinger Band Position</span>'
                         f'<span class="ind-val" style="color:{bc}">{swing["bb_pct_b"]:.2f} / 1.0</span>'
                         f'<span class="ind-note" style="color:{bc}">{bn}</span></div>')
        if swing.get("ema_200"):
            cp = swing.get("current_price", 0)
            ec = BULL if cp > swing["ema_200"] else BEAR
            en = f"Above 200-day EMA" if cp > swing["ema_200"] else "Below 200-day EMA"
            ind_rows += (f'<div class="ind-row"><span class="ind-label">200-Day EMA</span>'
                         f'<span class="ind-val" style="color:#777">${swing["ema_200"]:.2f}</span>'
                         f'<span class="ind-note" style="color:{ec}">{en}</span></div>')
        if swing.get("atr"):
            ind_rows += (f'<div class="ind-row"><span class="ind-label">ATR (Daily Range)</span>'
                         f'<span class="ind-val" style="color:#777">${swing["atr"]:.2f}</span>'
                         f'<span class="ind-note" style="color:#333">14-day avg range</span></div>')

        if ind_rows:
            st.markdown(f'<div style="margin-top:12px">{ind_rows}</div>', unsafe_allow_html=True)

        # Bull/Bear drivers
        if swing.get("bull") or swing.get("bear"):
            st.markdown("<div style='margin-top:12px'>", unsafe_allow_html=True)
            for d in (swing.get("bull") or [])[:3]:
                st.markdown(f'<div class="driver-bull">✓ {d}</div>', unsafe_allow_html=True)
            for d in (swing.get("bear") or [])[:3]:
                st.markdown(f'<div class="driver-bear">✗ {d}</div>', unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("No swing signal data. Click 🔄 Refresh to fetch gold price data.")

st.markdown("---")

# ── SECTION 2: Gold Price Chart ───────────────────────────────────────────────
st.markdown("### Spot Gold Price (GC=F)")

range_opt = st.selectbox("Chart Range", ["1 Month","3 Months","6 Months","1 Year"], index=2,
                          key="gold_range")
days_map  = {"1 Month":30,"3 Months":90,"6 Months":180,"1 Year":365}
chart_days = days_map[range_opt]

if not df_gold.empty:
    df_chart = df_gold.tail(chart_days).copy()
    if "Close" not in df_chart.columns and "close" in df_chart.columns:
        df_chart.rename(columns={"close":"Close"}, inplace=True)

    if len(df_chart) >= 20:
        if PANDAS_TA_AVAILABLE:
            df_chart["EMA_20"]  = ta.ema(df_chart["Close"], length=20)
            df_chart["EMA_50"]  = ta.ema(df_chart["Close"], length=50)
            df_chart["EMA_200"] = ta.ema(df_chart["Close"], length=200)
            bb = ta.bbands(df_chart["Close"], length=20, std=2.0)
        else:
            df_chart["EMA_20"]  = None
            df_chart["EMA_50"]  = None
            df_chart["EMA_200"] = None
            bb = None
        if bb is not None:
            df_chart["BB_Upper"] = bb.iloc[:,2]
            df_chart["BB_Lower"] = bb.iloc[:,0]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["Close"],
        line=dict(color="#f0c040", width=2), name="Spot Gold", fill="tozeroy",
        fillcolor="rgba(240,192,64,0.05)"))
    if "EMA_20" in df_chart.columns:
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["EMA_20"],
            line=dict(color=BULL, width=1.2, dash="solid"), name="EMA 20", opacity=0.8))
    if "EMA_50" in df_chart.columns:
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["EMA_50"],
            line=dict(color="#7eb8f7", width=1.2), name="EMA 50", opacity=0.8))
    if "EMA_200" in df_chart.columns:
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["EMA_200"],
            line=dict(color="#e07aff", width=1.5), name="EMA 200", opacity=0.9))
    if "BB_Upper" in df_chart.columns:
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["BB_Upper"],
            line=dict(color="#555", width=1, dash="dot"), name="BB Upper", opacity=0.6))
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["BB_Lower"],
            line=dict(color="#555", width=1, dash="dot"), name="BB Lower", opacity=0.6,
            fill="tonexty", fillcolor="rgba(100,100,100,0.05)"))

    # Break-even line
    if pnl.get("avg_cost"):
        iau_to_gold_ratio = 0.0966   # 1 IAU share ≈ 0.0966 oz gold
        be_spot = pnl["avg_cost"] / iau_to_gold_ratio
        fig.add_hline(y=be_spot, line_dash="dash", line_color=NEUT,
                      annotation_text=f"Break-even ~${be_spot:.0f}", annotation_position="left")

    fig.update_layout(height=400, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(color="#1a1a2e", size=11, family="IBM Plex Mono"),
        legend=dict(bgcolor="#f4f6fb", orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10,r=10,t=30,b=10), xaxis_rangeslider_visible=False)
    fig.update_xaxes(gridcolor="#e8eaf0")
    fig.update_yaxes(gridcolor="#e8eaf0")

    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, key=f"gold_chart_{chart_days}")
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.info("No gold price data. Click 🔄 Refresh.")

st.markdown("---")

# ── SECTION 3: Macro Hold Signal ─────────────────────────────────────────────
st.markdown("### 📊 Macro Hold Signal")

if macro_sig:
    sig_color = _signal_color(macro_sig["signal"])
    sig_class = _signal_class(macro_sig["signal"])
    sig_arrow = "✓" if "HOLD" in macro_sig["signal"] else ("▼" if "EXIT" in macro_sig["signal"] else "⚠")

    st.markdown(f'<div class="gold-card {sig_class}">', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sig-big" style="color:{sig_color}">'
        f'{sig_arrow} {macro_sig["signal"]}</div>'
        f'<div style="color:#333;font-size:.85em;margin-top:4px">'
        f'{macro_sig.get("signal_note","")}</div>'
        f'<span style="color:#333;font-size:.8em">'
        f'{macro_sig["score"]}/100 · {macro_sig["confidence"]:.0f}% confidence</span>',
        unsafe_allow_html=True)

    # Factor rows
    from engine.etf_signals import _val
    factors = [
        ("Real 10-Year Rate",    macro_sig.get("real_rate"),    "%",   macro_sig.get("rate_trend"),
         "Key driver — falling = bullish, rising = bearish"),
        ("Breakeven Inflation",  macro_sig.get("inflation"),    "%",   None,
         "Above 2% supports gold as inflation hedge"),
        ("USD Trade Index",      macro_sig.get("usd_val"),      "pts", macro_sig.get("usd_trend"),
         "Falling USD = bullish for gold (inverse relationship)"),
        ("High Yield Spread",    macro_sig.get("hy_spread"),    "%",   None,
         "High spread = risk-off = gold demand increases"),
        ("GDX Miners 10-Day",    macro_sig.get("gdx_mom"),      "%",   None,
         "Miners tend to lead physical gold by 2–4 weeks"),
        ("Consumer Sentiment",   macro_sig.get("consumer_sent"), "", None,
         "Below 60 = economic stress = gold safe haven demand"),
    ]

    html = '<div style="margin-top:14px">'
    for label, val, unit, trend, note in factors:
        if val is None: continue
        if trend:
            t_text, t_color = _trend_arrow(trend)
        else:
            t_text, t_color = "──", "#555"
        v_color = "#e0e0e0"
        html += (f'<div class="ind-row">'
                 f'<span class="ind-label" title="{note}">{label}</span>'
                 f'<span class="ind-val">{val:.2f}{unit}</span>'
                 f'<span class="ind-note" style="color:{t_color}">{t_text}</span>'
                 f'</div>')
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

    # Bull/bear drivers
    mc1, mc2 = st.columns(2)
    with mc1:
        if macro_sig.get("bull"):
            for d in macro_sig["bull"]:
                st.markdown(f'<div class="driver-bull">✓ {d}</div>', unsafe_allow_html=True)
    with mc2:
        if macro_sig.get("bear"):
            for d in macro_sig["bear"]:
                st.markdown(f'<div class="driver-bear">✗ {d}</div>', unsafe_allow_html=True)

    # Exit conditions
    if macro_sig.get("exit_conditions"):
        st.markdown("<div style='margin-top:12px;font-size:.82em;color:#333'>"
                    "<b>Exit thesis if:</b></div>", unsafe_allow_html=True)
        for ec in macro_sig["exit_conditions"]:
            st.markdown(f'<div class="driver-bear" style="margin-top:4px">⚠ {ec}</div>',
                        unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.info("No macro data. Click 🔄 Refresh and also refresh the ETF Screener page.")

st.markdown("---")

# ── SECTION 4: Action Recommendations ────────────────────────────────────────
st.markdown("### 💡 Action Recommendations")

if recs:
    # Context note — position-aware guidance
    if recs.get("context_note"):
        note_color = BEAR if recs.get("in_loss") else BULL
        st.markdown(
            f'<div style="background:#1a1f2e;border-radius:8px;padding:14px 18px;'
            f'margin-bottom:14px;border-left:4px solid {note_color}">'
            f'<div class="section-title">Position Context</div>'
            f'<span style="font-size:.88em;color:#ddd">{recs["context_note"]}</span>'
            f'</div>',
            unsafe_allow_html=True)

    r1, r2, r3 = st.columns(3)
    with r1:
        st.markdown('<div class="gold-card bull">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">When to Add to Position</div>',
                    unsafe_allow_html=True)
        if recs.get("add_triggers"):
            for item in recs["add_triggers"]:
                st.markdown(f'<div class="rec-item driver-bull">✓ {item}</div>',
                            unsafe_allow_html=True)
        else:
            st.markdown('<span style="color:#333">No add signals currently</span>',
                        unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with r2:
        st.markdown('<div class="gold-card neut">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">When to Reduce Position</div>',
                    unsafe_allow_html=True)
        if recs.get("reduce_triggers"):
            for item in recs["reduce_triggers"]:
                st.markdown(f'<div class="rec-item" style="color:#ffd700;padding:4px 0;'
                            f'border-bottom:1px solid #1e2130;font-size:.85em">◈ {item}</div>',
                            unsafe_allow_html=True)
        else:
            st.markdown('<span style="color:#333">No reduce signals currently</span>',
                        unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with r3:
        st.markdown('<div class="gold-card bear">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">When to Close Position</div>',
                    unsafe_allow_html=True)
        if recs.get("close_triggers"):
            for item in recs["close_triggers"]:
                st.markdown(f'<div class="rec-item driver-bear">✗ {item}</div>',
                            unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # Break-even note
    if recs.get("distance_to_be") and recs["distance_to_be"] > 0:
        st.markdown(
            f'<div style="margin-top:8px;font-size:.85em;color:#333">'
            f'Your break-even: '
            f'<span style="font-family:IBM Plex Mono,monospace;color:#ffd700">'
            f'${recs["break_even_iau"]:.2f} per IAU share</span> · '
            f'Currently <span style="color:{BEAR}">'
            f'${recs["distance_to_be"]:.2f} below break-even</span>'
            f'</div>', unsafe_allow_html=True)

st.markdown("---")

# ── SECTION 5: Gold vs Market Performance ────────────────────────────────────
st.markdown("### 📈 Gold vs Market — 12 Month Performance")

comparisons = {
    "GC=F":     ("Spot Gold",       "#f0c040"),
    "IAU":      ("IAU",             "#ffd700"),
    "GLD":      ("GLD",             "#e0c060"),
    "GDX":      ("GDX Miners",      "#a0a0ff"),
    "^GSPC":    ("S&P 500",         "#7eb8f7"),
    "DX-Y.NYB": ("US Dollar Index", "#ff9500"),
}

fig2 = go.Figure()
has_data = False
for ticker, (name, color) in comparisons.items():
    conn = get_conn()
    rows = conn.execute("""
        SELECT date, value FROM macro_data WHERE series_id=?
        ORDER BY date DESC LIMIT 252
    """, (ticker,)).fetchall()
    conn.close()
    if not rows or len(rows) < 10: continue
    df_c = pd.DataFrame([dict(r) for r in rows]).sort_values("date")
    df_c["date"] = pd.to_datetime(df_c["date"])
    # Normalize to 100
    base = df_c["value"].iloc[0]
    if base > 0:
        df_c["normalized"] = (df_c["value"] / base) * 100
        fig2.add_trace(go.Scatter(x=df_c["date"], y=df_c["normalized"],
            line=dict(color=color, width=1.5), name=name))
        has_data = True

if has_data:
    fig2.add_hline(y=100, line_dash="dot", line_color="#333")
    fig2.update_layout(height=320, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(color="#1a1a2e", size=11, family="IBM Plex Mono"),
        legend=dict(bgcolor="#f4f6fb", orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10,r=10,t=30,b=10),
        yaxis_title="Indexed to 100")
    fig2.update_xaxes(gridcolor="#e8eaf0")
    fig2.update_yaxes(gridcolor="#e8eaf0")
    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
    st.plotly_chart(fig2, use_container_width=True, key="gold_vs_market")
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.info("No comparison data yet. Click 🔄 Refresh and also refresh the ETF Screener page first.")

st.markdown("---")

# ── SECTION 6: Headlines ──────────────────────────────────────────────────────
st.markdown("### 📰 Gold News")
headlines = get_headlines("IAU", limit=5)
if not headlines:
    headlines = get_headlines("GLD", limit=5)

if headlines:
    html = '<div class="gold-card neut">'
    for h in headlines:
        icon = _hl_icon(h.get("sentiment_score"))
        url  = h.get("url","")
        link = f'<a href="{url}" target="_blank" style="color:#0066cc;font-size:.85em;font-weight:500;text-decoration:none;margin-left:6px">↗ Read</a>' if url else ""
        html += (f'<div class="hl-row" style="padding:10px 0;border-bottom:1px solid #1e2130">'
                 f'<div>{icon}<span class="hl-text">{h.get("headline","")}</span> {link}</div>'
                 f'<div class="hl-meta">{h.get("source","").title()} · {_time_ago(h.get("published_at",""))}</div>'
                 f'</div>')
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)
else:
    st.info("No headlines yet. Run: python run.py sentiment IAU")

st.markdown("---")

# ── SECTION 7: Trade History ──────────────────────────────────────────────────
st.markdown("### 📋 Trade History")
trades = get_trade_history("IAU", limit=20)
if trades:
    rows = []
    for t in trades:
        pnl_realized = None
        if t["action"] == "SELL":
            # Need original avg cost — approximate from DB
            pos_at_time = t.get("avg_cost_after", 0)
            pnl_realized = (t["price"] - pos_at_time) * t["shares"] if pos_at_time else None
        rows.append({
            "Date":          t["traded_at"][:10],
            "Action":        t["action"],
            "Shares":        f"{t['shares']:.0f}",
            "Price":         f"${t['price']:.2f}",
            "Total Value":   f"${t['total_value']:.2f}",
            "Shares After":  f"{t['shares_after']:.0f}",
            "Avg Cost After":f"${t['avg_cost_after']:.2f}",
            "Notes":         t.get("notes",""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("No trades logged yet. Click 📝 Log a Trade to add your first position.")

render_footer()
