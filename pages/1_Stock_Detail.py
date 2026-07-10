"""
pages/1_Stock_Detail.py — Stock detail: chart, prediction, indicators, headlines.
Stock Screener 2.0 — uses core/ layer, no sidebar.
"""
import sys, os, time
from datetime import date, datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import holidays
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup   import setup_page, render_footer
from core.db_queries   import get_current_price
from engine.prices import get_extended_hours_price, format_price_label, format_change_html
from engine.db         import get_conn, get_watchlist, upsert_stock
from engine.fetcher    import load_daily_history, fetch_daily_history, fetch_fundamentals
from engine.indicators import compute_indicators, get_latest_indicators, refresh_indicators
from engine.predictor  import predict
from engine.sentiment  import get_latest_sentiment, get_headlines
from utils import (score_color, strategy_label, get_et_time, is_market_hours,
                   BULL, BEAR, NEUT, move_html, demo_banner)

setup_page("Stock Detail", "📊", active_page="1_Stock_Detail")




# ── Helpers ───────────────────────────────────────────────────────────────────

def next_trading_day():
    us_hols = holidays.US(years=[date.today().year, date.today().year+1])
    d = date.today() + timedelta(days=1)
    while d.weekday() >= 5 or d in us_hols:
        d += timedelta(days=1)
    return d.strftime("%A, %B %-d, %Y")

# get_current_price imported from core.db_queries

def note_color(note):
    pos = {"oversold","bullish","strong trend","low leverage","breaking","approaching","high"}
    neg = {"overbought","bearish","weak/ranging","high leverage","low volume"}
    nl  = note.lower()
    if any(p in nl for p in pos): return BULL
    if any(n in nl for n in neg): return BEAR
    return "#555"

def hl_icon(score):
    if score is None:  return '<span class="hl-neut">—</span>'
    if score >  0.2:   return '<span class="hl-bull">▲</span>'
    if score < -0.2:   return '<span class="hl-bear">▼</span>'
    return '<span class="hl-neut">—</span>'

def time_ago(pub_str):
    if not pub_str: return ""
    try:
        delta = datetime.utcnow() - datetime.fromisoformat(pub_str.replace("Z",""))
        if delta.days > 0:    return f"{delta.days}d ago"
        hrs = delta.seconds // 3600
        if hrs > 0:           return f"{hrs}h ago"
        return f"{delta.seconds//60}m ago"
    except: return ""

def ind_bar(val, lo, hi, color):
    pct = max(0, min(100, (val-lo)/(hi-lo)*100)) if hi > lo else 50
    return (f'<div class="ind-bar-track">'
            f'<div class="ind-bar-fill" style="width:{pct:.0f}%;background:{color}"></div>'
            f'</div>')

# ── Cached data loader ────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _load_chart_data(ticker: str, display_days: int):
    conn  = get_conn()
    limit = 9999 if display_days >= 9999 else max(display_days, 365)
    rows  = conn.execute("""
        SELECT date,open,high,low,close,adj_close,volume
        FROM   price_history WHERE ticker=?
        ORDER  BY date DESC LIMIT ?
    """, (ticker.upper(), limit)).fetchall()
    conn.close()
    if not rows: return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    df.rename(columns={"open":"Open","high":"High","low":"Low",
                        "close":"Close","adj_close":"Adj Close","volume":"Volume"}, inplace=True)
    df = compute_indicators(ticker, df)
    if display_days < 9999:
        cutoff  = (pd.Timestamp.now() - pd.Timedelta(days=display_days)).normalize()
        df_trim = df[df.index >= cutoff]
        if not df_trim.empty: return df_trim
    return df

# ── Chart builder ─────────────────────────────────────────────────────────────

def build_chart(df, ticker, show_ind):
    has = lambda x: x in show_ind
    has_macd  = has("MACD")
    has_rsi   = has("RSI")
    has_stoch = has("Stochastic")
    n_rows  = 2 + has_macd + has_rsi + has_stoch
    heights = [0.55, 0.15] + [0.13]*has_macd + [0.12]*has_rsi + [0.12]*has_stoch
    titles  = [ticker, "Volume"] + (["MACD"] if has_macd else []) + \
              (["RSI (14)  |  30──50──70"] if has_rsi else []) + \
              (["Stochastic  |  20──80"] if has_stoch else [])

    fig = make_subplots(rows=n_rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.04, subplot_titles=titles,
                        row_heights=heights)

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        increasing_line_color=BULL, decreasing_line_color=BEAR, name=ticker, showlegend=False
    ), row=1, col=1)

    # Overlays
    for col_name, label, color in [("ema_20","EMA 20","#f0c040"),("ema_50","EMA 50","#7eb8f7"),("ema_200","EMA 200","#e07aff")]:
        if has(label) and col_name in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df[col_name],
                line=dict(color=color, width=1.2), name=label), row=1, col=1)
    if has("Bollinger Bands") and "bb_upper" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_upper"],
            line=dict(color="#555",width=1,dash="dot"), name="BB Upper", opacity=0.7), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_lower"],
            line=dict(color="#555",width=1,dash="dot"), name="BB Lower", opacity=0.7,
            fill="tonexty", fillcolor="rgba(100,100,100,0.06)"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_mid"],
            line=dict(color="#444",width=1), name="BB Mid", opacity=0.5), row=1, col=1)
    if has("Support/Resistance"):
        for col_n, lbl, c in [("support_20d","Support",BULL),("resistance_20d","Resistance",BEAR)]:
            if col_n in df.columns:
                fig.add_trace(go.Scatter(x=df.index, y=df[col_n],
                    line=dict(color=c,width=1,dash="dash"), name=lbl, opacity=0.5), row=1, col=1)
    if has("VWAP") and "vwap" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["vwap"],
            line=dict(color="#ff9500",width=1.2), name="VWAP"), row=1, col=1)

    # Volume
    vcols = [BULL if c>=o else BEAR for c,o in zip(df["Close"],df["Open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vcols,
        showlegend=False, opacity=0.7), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["Volume"].rolling(20).mean(),
        line=dict(color="#555",width=1), showlegend=False, opacity=0.6), row=2, col=1)

    cur = 3
    if has_macd and "macd" in df.columns and not df["macd"].isna().all():
        hc = [BULL if (v or 0)>=0 else BEAR for v in df["macd_hist"].fillna(0)]
        fig.add_trace(go.Bar(x=df.index, y=df["macd_hist"], marker_color=hc,
            showlegend=False, opacity=0.5), row=cur, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["macd"],
            line=dict(color="#7eb8f7",width=1.5), name="MACD"), row=cur, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["macd_signal"],
            line=dict(color="#f0c040",width=1.5), name="Signal"), row=cur, col=1)
        cur += 1
    if has_rsi and "rsi" in df.columns and not df["rsi"].isna().all():
        fig.add_trace(go.Scatter(x=df.index, y=df["rsi"],
            line=dict(color="#e07aff",width=1.5), showlegend=False), row=cur, col=1)
        for lvl, clr in [(70,"rgba(255,75,75,.4)"),(50,"rgba(80,80,80,.4)"),(30,"rgba(0,200,150,.4)")]:
            fig.add_hline(y=lvl, line_dash="dot", line_color=clr, row=cur, col=1)
        cur += 1
    if has_stoch and "stoch_k" in df.columns and not df["stoch_k"].isna().all():
        fig.add_trace(go.Scatter(x=df.index, y=df["stoch_k"],
            line=dict(color="#7eb8f7",width=1.2), showlegend=False, name="%K"), row=cur, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["stoch_d"],
            line=dict(color="#f0c040",width=1.2), showlegend=False, name="%D"), row=cur, col=1)
        for lvl, clr in [(80,"rgba(255,75,75,.4)"),(20,"rgba(0,200,150,.4)")]:
            fig.add_hline(y=lvl, line_dash="dot", line_color=clr, row=cur, col=1)

    # Remove weekend gaps using actual trading dates
    all_dates  = pd.date_range(df.index.min(), df.index.max(), freq="D")
    trade_set  = set(df.index.normalize())
    gap_values = [str(d.date()) for d in all_dates if d not in trade_set]

    fig.update_layout(height=700, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(color="#1a1a2e", size=11, family="IBM Plex Mono"),
        legend=dict(bgcolor="#f4f6fb", bordercolor="#dde1ea",
                    orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=40,b=10))
    rb = [dict(values=gap_values)] if gap_values else []
    fig.update_xaxes(gridcolor="#e8eaf0", rangebreaks=rb)
    fig.update_yaxes(gridcolor="#e8eaf0")
    return fig

# ── Indicator rows ────────────────────────────────────────────────────────────

IND_CONFIG = [
    # (key, label, full_name, lo, hi, bar_color_fn, note_fn)
    ("rsi",        "RSI",     "Relative Strength Index — momentum oscillator 0–100. <35=oversold, >65=overbought",
     0, 100, lambda v: score_color(100-v if v>50 else 100),
     lambda v: "Oversold (<35)" if v<35 else "Overbought (>65)" if v>65 else "Neutral",
     lambda v: f"{v:.1f} / 100"),
    ("adx",        "ADX",     "Average Directional Index — trend strength 0–60+. >25=trending",
     0, 60, lambda v: "#7eb8f7",
     lambda v: "Strong trend (>25)" if v>25 else "Weak/ranging (<25)",
     lambda v: f"{v:.1f} / 60+"),
    ("atr",        "ATR",     "Average True Range — average daily price range $ over 14 days. Higher=more volatile",
     None, None, None,
     lambda v: "Daily range (14d avg)",
     lambda v: f"${v:.2f}"),
    ("rel_volume", "Rel Vol", "Relative Volume — today's volume vs 20-day avg. >1.5x=unusual activity",
     0, 3, lambda v: "#f0c040",
     lambda v: "High (>1.5x)" if v>1.5 else "Low (<0.8x)" if v<0.8 else "Normal",
     lambda v: f"{v:.2f}x avg"),
    ("bb_pct_b",   "BB %B",   "Bollinger Band %B — 0=lower band, 0.5=mid, 1=upper band",
     0, 1, lambda v: score_color(v*100),
     lambda v: "Near lower band" if v<0.2 else "Near upper band" if v>0.8 else "Mid-band",
     lambda v: f"{v:.2f} / 1.0"),
    ("zscore",     "Z-Score", "Z-Score — std deviations from 20-day mean. >1.5 or <-1.5=stretched",
     None, None, None,
     lambda v: f"Extended ({abs(v):.1f}σ)" if abs(v)>1.5 else "Normal range",
     lambda v: f"{v:.2f} σ"),
]

def indicator_rows_html(ind, side="left"):
    rows  = []
    items = IND_CONFIG[:3] if side=="left" else IND_CONFIG[3:]
    for key, lbl, full, lo, hi, color_fn, note_fn, val_fn in items:
        v = ind.get(key)
        if v is None:
            rows.append(f'<div class="ind-row"><div class="ind-name" title="{full}">{lbl}</div>'
                        f'<div class="ind-val">—</div><div class="ind-note" style="color:#333">No data</div></div>')
            continue
        note = note_fn(v)
        nc   = note_color(note)
        bar  = ind_bar(v, lo, hi, color_fn(v)) if lo is not None else ""
        rows.append(
            f'<div class="ind-row">'
            f'<div class="ind-name" title="{full}">{lbl}</div>'
            f'<div style="width:40%"><div class="ind-val" style="color:{nc}">{val_fn(v)}</div>{bar}</div>'
            f'<div class="ind-note" style="color:{nc}">{note}</div>'
            f'</div>'
        )
    return "".join(rows)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE
# ══════════════════════════════════════════════════════════════════════════════

# ── Header ────────────────────────────────────────────────────────────────────
watchlist   = get_watchlist()
wl_tickers  = [s["ticker"] for s in watchlist]
dt          = st.session_state.get("detail_ticker")
if dt and dt not in wl_tickers: wl_tickers = [dt] + wl_tickers
if not wl_tickers:
    st.info("Watchlist is empty."); st.stop()
default_idx = wl_tickers.index(dt) if dt and dt in wl_tickers else 0

h1, h2, h3 = st.columns([2, 2, 1.5])
with h1:
    selected = st.selectbox("Ticker", wl_tickers, index=default_idx,
                            label_visibility="collapsed", key="detail_select")
    if selected != st.session_state.get("detail_ticker"):
        st.session_state["detail_ticker"] = selected
        st.rerun()
with h2:
    search_q = st.text_input("", placeholder="🔍 Type any ticker and press Enter",
                              label_visibility="collapsed", key="detail_search").upper().strip()
    if search_q and search_q != st.session_state.get("detail_ticker"):
        st.session_state["detail_ticker"] = search_q
        st.rerun()
with h3:
    if st.button("🔄 Refresh", use_container_width=True):
        tickers_all = [s["ticker"] for s in get_watchlist()]
        if tickers_all:
            prog = st.progress(0, text="Refreshing…")
            for i, t in enumerate(tickers_all):
                prog.progress((i+1)/len(tickers_all)*0.8, text=f"Fetching {t}…")
                try: fetch_daily_history(t); fetch_fundamentals(t); refresh_indicators(t)
                except: pass
            prog.progress(1.0, text="Done."); time.sleep(0.3); prog.empty()
            st.cache_data.clear(); st.rerun()
st.markdown("---")

# ── Ticker + range controls ───────────────────────────────────────────────────
tickers = wl_tickers
ticker  = st.session_state.get("detail_ticker") or (wl_tickers[0] if wl_tickers else None)
if not ticker:
    st.info("Select a ticker above."); st.stop()
c1, c2 = st.columns([2, 4])
with c1:
    pass  # ticker set from header dropdown
with c2:
    range_opt = st.selectbox("Range", [
        "1 Day","5 Days","10 Days","1 Month","3 Months",
        "6 Months","1 Year","2 Years","3 Years","5 Years","Max"
    ], index=3, key="range_select")
    days = {"1 Day":1,"5 Days":5,"10 Days":10,"1 Month":30,"3 Months":90,
            "6 Months":180,"1 Year":365,"2 Years":730,"3 Years":1095,
            "5 Years":1825,"Max":9999}[range_opt]

info = next((s for s in watchlist if s["ticker"] == ticker), {})
in_watchlist = ticker in [s["ticker"] for s in get_watchlist()]
name_col, btn_col = st.columns([6, 1])
with name_col:
    st.markdown(
        f"<div style='margin:-8px 0 8px 0'>"
        f"<span style='font-size:1.2em;font-weight:700'>📊 {ticker}</span> "
        f"<span style='color:#333;font-size:.9em'>{info.get('name') or ticker}</span></div>",
        unsafe_allow_html=True)
with btn_col:
    if not in_watchlist:
        if st.button("＋ Add", key="add_watchlist"):
            upsert_stock(ticker)
            st.success(f"{ticker} added!")
            st.rerun()
    else:
        _pf_icon = "💼" if info.get("in_portfolio") else "✓"
        st.markdown(f"<div style='margin-top:6px;color:#333;font-size:.85em'>{_pf_icon} Saved</div>",
                    unsafe_allow_html=True)

show_ind = st.multiselect("Indicators",
    ["EMA 20","EMA 50","EMA 200","Bollinger Bands","VWAP","Pivot Points","MACD"],
    default=["EMA 20","EMA 50","EMA 200"], key="show_ind")
st.markdown("---")

# ── Chart ─────────────────────────────────────────────────────────────────────
with st.spinner(f"Loading {ticker} — {range_opt}…"):
    df = _load_chart_data(ticker, days)

if df.empty:
    st.markdown(f'<div style="background:#1a1f2e;border-radius:8px;padding:20px;text-align:center">'
                f'<p style="color:#333">No price history for <b>{ticker}</b>.</p></div>',
                unsafe_allow_html=True)
    if st.button(f"⬇️ Fetch {ticker} data now"):
        with st.spinner(f"Fetching {ticker}…"):
            try:
                fetch_daily_history(ticker, days=730)
                fetch_fundamentals(ticker)
                refresh_indicators(ticker)
                st.cache_data.clear(); st.rerun()
            except Exception as e: st.error(f"Fetch failed: {e}")
else:
    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
    st.plotly_chart(build_chart(df, ticker, show_ind),
                    use_container_width=True, key=f"chart_{ticker}_{days}")
    st.markdown('</div>', unsafe_allow_html=True)

# ── Prediction + Sentiment ────────────────────────────────────────────────────
st.markdown("---")
pred_col, sent_col = st.columns([3, 2])

with pred_col:
    try:
        pred    = predict(ticker)
        if not pred or not pred.get("signal"):
            st.info("No prediction yet. Click **Fetch data** or **Refresh** to generate one.")
            p_mid = None
            st.stop()
        signal  = pred["signal"]
        conf    = pred["confidence"]
        comp    = pred["composite_score"]
        rules   = pred.get("rules_score", comp)
        p_low, p_mid, p_high = pred.get("price_low"), pred.get("price_mid"), pred.get("price_high")
        current = get_current_price(ticker)

        # Extended hours price banner
        _epx = get_extended_hours_price(ticker)
        if not _epx.get("error") and _epx.get("price_type") in ("pre_market", "post_market"):
            _elabel   = format_price_label(_epx)
            _eprice   = _epx["price"]
            _etime    = _epx.get("pre_market_time") or _epx.get("post_market_time")
            _ets      = _etime.strftime("%H:%M") if _etime else ""
            _echg     = format_change_html(
                _epx.get("pre_change") or _epx.get("post_change"),
                _epx.get("pre_change_pct") or _epx.get("post_change_pct"))
            _ebg  = "#fffbe6" if _epx["price_type"] == "pre_market" else "#f0f4ff"
            _ebd  = "#c8a000" if _epx["price_type"] == "pre_market" else "#0066cc"
            st.markdown(
                f'<div style="background:{_ebg};border-left:4px solid {_ebd};'
                f'border-radius:8px;padding:8px 14px;margin-bottom:10px">'
                f'<span style="font-size:.75em;text-transform:uppercase;letter-spacing:.06em;color:{_ebd}">'
                f'{_elabel}{" · " + _ets if _ets else ""}</span><br>'
                f'<span style="font-family:IBM Plex Mono,monospace;font-size:1.15em;font-weight:700">'
                f'${_eprice:.2f}</span> {_echg}'
                + (f'<span style="font-size:.78em;color:#555;margin-left:8px">Close: ${current:.2f}</span>' if current else '')
                + '</div>',
                unsafe_allow_html=True)

        exp_mv  = ((p_mid - current) / current * 100) if p_mid and current else None
        next_day = next_trading_day()
        ml      = pred.get("ml")

        sig_c   = BULL if signal=="BULLISH" else (BEAR if signal=="BEARISH" else NEUT)
        sig_a   = {"BULLISH":"▲","BEARISH":"▼"}.get(signal,"—")
        ban_cls = {"BULLISH":"","BEARISH":"bear"}.get(signal,"neut")

        move_s = ""
        if exp_mv is not None:
            c = BULL if exp_mv>=0 else BEAR; a = "▲" if exp_mv>=0 else "▼"
            move_s = f'<span style="color:{c}"> {a}{abs(exp_mv):.2f}%</span>'

        range_s = (f'<div style="font-family:IBM Plex Mono,monospace;margin-top:8px;color:#777;font-size:.85em">'
                   f'${p_low:.2f} &nbsp;{"─"*6}&nbsp;'
                   f'<span style="color:#e0e0e0;font-weight:600">${p_mid:.2f}</span>'
                   f'&nbsp;{"─"*6}&nbsp; ${p_high:.2f}</div>') if p_mid else ""

        # ML row
        ml_row = ""
        if ml:
            ml_c = BULL if ml["direction"]=="BULLISH" else BEAR
            ml_a = "▲" if ml["direction"]=="BULLISH" else "▼"
            ml_row = (
                f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid #2e3550;'
                f'font-size:.82em;color:#333;font-family:IBM Plex Mono,monospace">'
                f'<span style="color:#333">XGBoost: </span>'
                f'<span style="color:{ml_c}">{ml_a} {ml["direction"]}</span>'
                f'&nbsp;·&nbsp;'
                f'<span style="color:{BULL}">▲ Bull {ml["bullish_prob"]:.0f}%</span>'
                f'&nbsp;·&nbsp;'
                f'<span style="color:{BEAR}">▼ Bear {ml["bearish_prob"]:.0f}%</span>'
                f'&nbsp;·&nbsp;Price: <span style="color:#e0e0e0">${ml["predicted_price"]:.2f}</span>'
                f'&nbsp;·&nbsp;Val acc: {ml["val_accuracy"]:.1f}%'
                f'</div>'
            )
            rules_row = (
                f'<div style="font-size:.82em;color:#333;font-family:IBM Plex Mono,monospace;margin-top:4px">'
                f'<span style="color:#333">Rules: </span>'
                f'<span style="color:{score_color(rules)}">{rules:.0f}/100</span>'
                f'&nbsp;·&nbsp;T:{pred["technical_score"]:.0f} F:{pred["fundamental_score"]:.0f} S:{pred["sentiment_score"]:.0f}'
                f'</div>'
            )
        else:
            rules_row = (
                f'<div style="margin-top:14px;font-size:.82em;color:#333;font-family:IBM Plex Mono,monospace">'
                f'Composite: <span style="color:{score_color(comp)}">{comp:.0f}/100</span>'
                f'&nbsp;·&nbsp;T:{pred["technical_score"]:.0f} F:{pred["fundamental_score"]:.0f} S:{pred["sentiment_score"]:.0f}'
                f'</div>'
            )

        st.markdown(f"""
        <div class="prediction-banner {ban_cls}">
          <div class="section-title">Prediction for {next_day}</div>
          <div style="font-size:1.5em;font-weight:700;color:{sig_c};margin-bottom:6px">
            {sig_a} {signal}
            <span style="font-size:.55em;color:#333;font-weight:400;margin-left:10px">{conf:.0f}% confidence</span>
            {"<span style='font-size:.45em;color:#333;margin-left:8px'>XGBoost + Rules blended</span>" if ml else ""}
          </div>
          <div style="font-family:IBM Plex Mono,monospace;margin-top:6px">
            Current: <span style="color:#e0e0e0;font-weight:600">${f"{current:.2f}" if current else "—"}</span>
            &nbsp;→&nbsp;
            Final: <span style="color:{sig_c};font-weight:600">${f"{p_mid:.2f}" if p_mid else "—"}</span>{move_s}
          </div>
          {range_s}
          {ml_row}
          {rules_row}
        </div>""", unsafe_allow_html=True)

        # Retrain button
        if st.button(f"🔄 Retrain XGBoost for {ticker}"):
            with st.spinner(f"Training model for {ticker}…"):
                try:
                    from engine.ml_predictor import train as ml_train
                    r = ml_train(ticker)
                    if "error" in r:
                        st.error(f"Training failed: {r['error']}")
                    else:
                        st.success(f"✓ Trained on {r['n_samples']} samples · "
                                   f"MAE={r['val_mae']:.2f} · Accuracy={r['val_accuracy']:.1f}%")
                        st.cache_data.clear(); st.rerun()
                except Exception as e:
                    st.error(f"Training error: {e}")

    except Exception as e:
        st.error(f"Prediction error: {e}")
        p_mid = None

with sent_col:
    sent = get_latest_sentiment(ticker)
    st.markdown('<div class="sentiment-card" style="padding-top:14px">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Sentiment</div>', unsafe_allow_html=True)
    if not sent["available"]:
        demo_banner("🤖", "Sentiment unavailable in demo",
                    "FinBERT requires the HuggingFace Inference API — rate-limited on the free tier. "
                    "Works locally with a paid HF token.")
    if sent["available"]:
        sc = sent["avg_score"]
        c  = BULL if sc>0.2 else (BEAR if sc<-0.2 else NEUT)
        st.markdown(f'<div style="font-size:1.3em;font-weight:700;color:{c};margin-bottom:10px">'
                    f'{sent["overall_label"]} <span style="font-family:IBM Plex Mono,monospace;'
                    f'font-size:.65em">{sc:+.2f} / 1.0</span></div>', unsafe_allow_html=True)
        for src, sd in sent["sources"].items():
            sc2 = sd.get("score") or 0
            c2  = BULL if sc2>0.2 else (BEAR if sc2<-0.2 else NEUT)
            st.markdown(
                f'<div style="margin-bottom:8px">'
                f'<span style="font-size:.8em;color:#333">{src.title()}</span> '
                f'<span style="font-family:IBM Plex Mono,monospace;font-size:.8em;color:{c2}">{sc2:+.2f}</span>'
                f'<span style="font-size:.75em;color:#444"> · {sd.get("mention_count",0)} mentions</span></div>',
                unsafe_allow_html=True)
            if sd.get("bullish_pct") is not None:
                b = int(sd["bullish_pct"]*100)
                st.markdown(f'<div style="font-size:.75em;color:#333">Bull {b}% / Bear {100-b}%</div>',
                            unsafe_allow_html=True)
                st.progress(b)
    else:
        st.info("No sentiment data. Run a refresh.")
    st.markdown('</div>', unsafe_allow_html=True)

# ── Indicators ────────────────────────────────────────────────────────────────
st.markdown("---")
ind = get_latest_indicators(ticker)
st.markdown('<div class="indicator-card">', unsafe_allow_html=True)
st.markdown(f'<div class="section-title">📍 Current Indicators ({date.today().strftime("%b %d, %Y")})</div>',
            unsafe_allow_html=True)
if ind:
    ic1, ic2 = st.columns(2)
    with ic1: st.markdown(indicator_rows_html(ind, "left"),  unsafe_allow_html=True)
    with ic2: st.markdown(indicator_rows_html(ind, "right"), unsafe_allow_html=True)
else:
    st.info("No indicator data. Run a refresh.")
st.markdown('</div>', unsafe_allow_html=True)

# ── Headlines ─────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown('<div class="section-title">📰 Latest Headlines</div>', unsafe_allow_html=True)
headlines = get_headlines(ticker, limit=5)
if headlines:
    html = '<div style="background:#1a1f2e;border-radius:10px;padding:14px 18px">'
    for h in headlines:
        icon = hl_icon(h.get("sentiment_score"))
        url  = h.get("url","")
        link = f'<a href="{url}" target="_blank" style="color:#0066cc;font-size:.85em;font-weight:500;text-decoration:none;margin-left:6px">↗ Read</a>' if url else ""
        html += (f'<div class="headline-row">'
                 f'<div>{icon}<span class="hl-text">{h.get("headline","")}</span> {link}</div>'
                 f'<div class="hl-meta">{h.get("source","").title()} · {time_ago(h.get("published_at",""))}</div>'
                 f'</div>')
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)
else:
    st.info("No headlines stored yet. Run a refresh to fetch news.")


# ── Watchlist ─────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📋 Watchlist")

for _col, _hdr in zip(
    st.columns([1.2, 2.5, 1.8, 2, 2, 1.5]),
    ["Ticker", "Name", "Price", "Signal", "Score", "Actions"]
):
    _col.markdown(f"**{_hdr}**")
st.markdown('<hr style="margin:4px 0;border-color:#dde1ea">', unsafe_allow_html=True)

_today = date.today().isoformat()
for _wl_s in watchlist:
    _t   = _wl_s["ticker"]
    _cur = get_current_price(_t)
    _active = _t == ticker
    _conn2 = get_conn()
    _pred2 = _conn2.execute(
        "SELECT signal, composite_score FROM predictions "
        "WHERE ticker=? AND date=? AND prediction_type='next_day' LIMIT 1",
        (_t, _today)
    ).fetchone()
    _conn2.close()
    _signal = _pred2["signal"] if _pred2 else "—"
    _score  = _pred2["composite_score"] if _pred2 else None
    _sig_c  = BULL if _signal == "BULLISH" else (BEAR if _signal == "BEARISH" else "#8a5e00")
    _sig_a  = "▲" if _signal == "BULLISH" else ("▼" if _signal == "BEARISH" else "—")
    _pf     = " 💼" if _wl_s.get("in_portfolio") else ""

    _c1, _c2, _c3, _c4, _c5, _c6 = st.columns([1.2, 2.5, 1.8, 2, 2, 1.5])
    _c1.markdown(f'<span style="font-weight:{"700" if _active else "400"};color:{"#0066cc" if _active else "#222"}">{_t}{_pf}</span>', unsafe_allow_html=True)
    _c2.markdown(f'<span style="font-size:.85em;color:#444">{(_wl_s.get("name") or _t)[:28]}</span>', unsafe_allow_html=True)
    _c3.markdown(f'<span style="font-family:IBM Plex Mono,monospace">{"$" + f"{_cur:.2f}" if _cur else "—"}</span>', unsafe_allow_html=True)
    _c4.markdown(f'<span style="color:{_sig_c};font-weight:600">{_sig_a} {_signal}</span>', unsafe_allow_html=True)
    _c5.markdown(f'<span style="font-family:IBM Plex Mono,monospace">{f"{_score:.0f}/100" if _score else "—"}</span>', unsafe_allow_html=True)
    if _active:
        _c6.markdown('<span style="font-size:.82em;color:#0066cc">← viewing</span>', unsafe_allow_html=True)
    else:
        if _c6.button("View", key=f"wl_view_{_t}"):
            st.session_state["detail_ticker"] = _t
            st.rerun()
    st.markdown('<hr style="margin:3px 0;border-color:#eee">', unsafe_allow_html=True)

render_footer()
