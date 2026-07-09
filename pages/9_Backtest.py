"""
pages/9_Backtest.py — Backtesting dashboard.
Stock Screener 2.0 — uses core/ layer, no sidebar.
"""
import sys, os
from datetime import date, datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup   import setup_page, render_footer
from engine.db         import get_watchlist, get_conn
from engine.backtester import run_backtest, run_backtest_all
from utils             import score_color, BULL, BEAR, NEUT

setup_page("Backtesting", "📉", active_page="9_Backtest")

st.markdown("""<style>
.bt-card  { background:#1a1f2e; border-radius:10px; padding:18px 22px; margin-bottom:12px; }
.bt-card.positive { border-left:4px solid #00c896; }
.bt-card.negative { border-left:4px solid #ff4b4b; }
.bt-card.neutral  { border-left:4px solid #ffd700; }
.metric-big { font-family:'IBM Plex Mono',monospace; font-size:2em;
              font-weight:700; line-height:1.1; }
.metric-label { font-size:0.78em; text-transform:uppercase;
                letter-spacing:0.08em; color:#333; margin-top:4px; }
.trade-row { display:flex; justify-content:space-between; padding:8px 0;
             border-bottom:1px solid #252b3b; font-size:0.83em; }
.trade-row:last-child { border-bottom:none; }
.warning-box { background:#1a1200; border:1px solid #ffd700; border-radius:8px;
               padding:12px 18px; color:#ffd700; margin-bottom:1rem; font-size:0.85em; }
</style>""", unsafe_allow_html=True)

# ── Metric definitions for tooltips ──────────────────────────────────────────
METRIC_TIPS = {
    "Total Return":
        "The total percentage gain or loss from all trades combined over the backtest period.",
    "Buy and Hold Return":
        "What you would have earned by simply buying on day one and holding until the end. "
        "The benchmark the strategy must beat to be worth using.",
    "Alpha Generated":
        "Total Return minus Buy and Hold Return. Positive alpha means the strategy beat "
        "passive investing. Negative alpha means you were better off just holding.",
    "Win Rate":
        "Percentage of trades that were profitable. A 50% win rate is random chance. "
        "Above 55% with positive expectancy is a meaningful edge.",
    "Profit Factor":
        "Total gross profit divided by total gross loss. Above 1.0 means you made more "
        "than you lost. Above 1.5 is considered a solid trading edge. Above 2.0 is excellent.",
    "Sharpe Ratio":
        "Annualized return divided by annualized volatility, minus a 4% risk-free rate. "
        "Above 1.0 = good risk-adjusted return. Above 2.0 = excellent. Below 0 = not worth the risk.",
    "Maximum Drawdown":
        "The largest peak-to-trough decline in portfolio value during the backtest. "
        "If you cannot stomach a drawdown of this size, the strategy is too risky for you.",
    "Expectancy":
        "The average expected return per trade: (Win Rate × Average Win) + (Loss Rate × Average Loss). "
        "Positive expectancy means the strategy has a mathematical edge over time.",
    "Average Win":
        "The average percentage gain on winning trades.",
    "Average Loss":
        "The average percentage loss on losing trades. "
        "A good system has average wins larger than average losses.",
    "Average Holding Days":
        "How many calendar days the average position was held between entry and exit. "
        "Shorter = more active trading. Longer = more of a swing/position approach.",
    "Best Trade":
        "The single most profitable trade as a percentage return.",
    "Worst Trade":
        "The single worst trade as a percentage return. "
        "A good system limits worst-case losses through its exit signals.",
    "Total Profit":
        "Total dollar profit or loss across all trades, starting from the position size you set.",
}

def _tip(metric_name):
    return METRIC_TIPS.get(metric_name, "")

def _color(val, good_positive=True):
    if val is None: return "#555"
    if good_positive: return BULL if val > 0 else (BEAR if val < 0 else NEUT)
    return BEAR if val > 0 else (BULL if val < 0 else NEUT)

def _metric_card(col, label, value, suffix="", good_positive=True, extra_tip=""):
    tip  = _tip(label) + (" " + extra_tip if extra_tip else "")
    c    = _color(value if isinstance(value, (int, float)) else 0, good_positive)
    disp = f"{value:+.2f}{suffix}" if isinstance(value, float) else f"{value}{suffix}"
    col.markdown(
        f'<div class="metric-big" style="color:{c}">{disp}</div>'
        f'<div class="metric-label">{label}</div>',
        unsafe_allow_html=True,
        help=tip if tip else None
    )

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# 📉 Backtest")
st.markdown(
    '<div class="warning-box">⚠️ <strong>Important:</strong> Backtest results reflect '
    'historical performance only and do not guarantee future results. Indicator parameters '
    '(RSI 14, EMA 20/50/200 etc.) were chosen with some knowledge of what works historically, '
    'so results may be slightly optimistic compared to live trading. Use results to '
    '<strong>compare strategies against each other</strong> and against buy-and-hold, '
    'not as precise predictions of future returns.</div>',
    unsafe_allow_html=True)

st.markdown("---")

# ── Controls ──────────────────────────────────────────────────────────────────
st.markdown("### Backtest Settings")

watchlist = get_watchlist()
tickers   = [s["ticker"] for s in watchlist]
strat_map = {s["ticker"]: s.get("strategy","unassigned") for s in watchlist}

c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
with c1:
    mode = st.radio("Mode", ["Single Ticker", "All Tickers"], horizontal=True)
with c2:
    if mode == "Single Ticker":
        selected_ticker = st.selectbox("Ticker", tickers)
        selected_strategy = strat_map.get(selected_ticker, "unassigned")
        st.caption(f"Strategy: {selected_strategy}")
    else:
        st.markdown("<span style='color:#333;font-size:.85em'>Runs backtest for all tickers in watchlist</span>",
                    unsafe_allow_html=True)
        selected_ticker = None
with c3:
    start_date = st.date_input("Start Date",
                                value=date.today() - timedelta(days=730),
                                max_value=date.today() - timedelta(days=60))
    end_date   = st.date_input("End Date",
                                value=date.today(),
                                max_value=date.today())
with c4:
    position_size = st.number_input(
        "Position Size per Trade ($)",
        min_value=100, max_value=100000, value=1000, step=100,
        help="Dollar amount invested in each trade. Used to calculate total profit in dollars.")
    buy_thresh  = st.slider("Buy Signal Threshold (score above this = buy)",
                             min_value=50, max_value=80, value=60, step=5,
                             help="Composite score above which the system enters a position. "
                                  "Higher = fewer but higher-conviction trades.")
    sell_thresh = st.slider("Sell Signal Threshold (score below this = sell)",
                             min_value=20, max_value=50, value=40, step=5,
                             help="Composite score below which the system exits a position. "
                                  "Lower = holds longer before exiting.")
    st.markdown("**Strategy Improvements**")
    use_trend_filter = st.checkbox(
        "Trend Filter",
        value=True,
        help="Only enter new positions when S&P 500 is above its 200-day moving average. "
             "Avoids buying into a broad market downtrend. "
             "Recommended: ON for most tickers.")
    use_asymmetric_hold = st.checkbox(
        "Asymmetric Hold",
        value=True,
        help="When a position is profitable, require a stronger sell signal before exiting "
             "(threshold lowered by 5 points). Lets winners run longer. "
             "Recommended: ON for trend and momentum strategies.")

st.markdown("---")

if st.button("▶ Run Backtest", use_container_width=False, type="primary"):
    start_str = start_date.strftime("%Y-%m-%d")
    end_str   = end_date.strftime("%Y-%m-%d")

    if mode == "Single Ticker":
        with st.spinner(f"Running backtest for {selected_ticker}…"):
            result = run_backtest(
                selected_ticker,
                strategy=selected_strategy,
                start=start_str, end=end_str,
                position_size_usd=float(position_size),
                buy_threshold=float(buy_thresh),
                sell_threshold=float(sell_thresh),
                use_trend_filter=use_trend_filter,
                use_asymmetric_hold=use_asymmetric_hold,
            )
        st.session_state["bt_result"]  = result
        st.session_state["bt_results"] = None
        st.session_state["bt_mode"]    = "single"
    else:
        items = [{"ticker": s["ticker"], "strategy": s.get("strategy","unassigned")}
                 for s in watchlist]
        with st.spinner(f"Running backtest for {len(items)} tickers…"):
            results = run_backtest_all(
                items,
                start=start_str, end=end_str,
                position_size_usd=float(position_size),
                buy_threshold=float(buy_thresh),
                sell_threshold=float(sell_thresh),
                use_trend_filter=use_trend_filter,
                use_asymmetric_hold=use_asymmetric_hold,
            )
        st.session_state["bt_results"] = results
        st.session_state["bt_result"]  = None
        st.session_state["bt_mode"]    = "all"

# ══════════════════════════════════════════════════════════════════════════════
# SINGLE TICKER RESULTS
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.get("bt_mode") == "single":
    result = st.session_state.get("bt_result", {})

    if "error" in result:
        st.error(f"Backtest failed: {result['error']}")
        st.stop()

    ticker   = result["ticker"]
    strategy = result["strategy"]
    trades   = result.get("trades", [])
    n_days   = result.get("trading_days", 0)

    st.markdown(f"## Results — {ticker}")
    st.markdown(
        f'<span style="color:#333;font-size:.85em">'
        f'Strategy: <b>{strategy.replace("_"," ").title()}</b> · '
        f'{result["start_date"]} to {result["end_date"]} · '
        f'{n_days} trading days · '
        f'${position_size:,} per trade · '
        f'Buy above {buy_thresh} · Sell below {sell_thresh}'
        f'</span>', unsafe_allow_html=True)

    st.markdown("---")

    # ── Top metrics ───────────────────────────────────────────────────────────
    st.markdown("### Performance Summary")
    card_class = "positive" if result.get("alpha_pct", 0) > 0 else "negative"
    st.markdown(f'<div class="bt-card {card_class}">', unsafe_allow_html=True)

    m1, m2, m3, m4, m5 = st.columns(5)
    _metric_card(m1, "Total Return",       result.get("total_return_pct",0),      "%")
    _metric_card(m2, "Buy and Hold Return",result.get("buy_hold_return_pct",0),   "%")
    _metric_card(m3, "Alpha Generated",    result.get("alpha_pct",0),             "%",
                 extra_tip="Positive = strategy beat buy-and-hold.")
    _metric_card(m4, "Sharpe Ratio",       result.get("sharpe_ratio",0),          "",
                 extra_tip=">1.0 good · >2.0 excellent · <0 not worth the risk.")
    _metric_card(m5, "Maximum Drawdown",   result.get("max_drawdown_pct",0),      "%",
                 good_positive=False)

    st.markdown("<br>", unsafe_allow_html=True)
    m6, m7, m8, m9, m10 = st.columns(5)
    _metric_card(m6,  "Win Rate",             result.get("win_rate_pct",0),       "%",
                 extra_tip="Above 55% with positive expectancy = meaningful edge.")
    _metric_card(m7,  "Profit Factor",        result.get("profit_factor",0),      "×",
                 extra_tip=">1.5 solid · >2.0 excellent.")
    _metric_card(m8,  "Expectancy",           result.get("expectancy_pct",0),     "%",
                 extra_tip="Average expected return per trade.")
    _metric_card(m9,  "Total Profit",         result.get("total_profit_usd",0),   " USD",
                 extra_tip=f"Starting from ${position_size:,} per trade.")
    _metric_card(m10, "Average Holding Days", result.get("average_holding_days",0),"d",
                 good_positive=False)

    st.markdown("<br>", unsafe_allow_html=True)
    m11, m12, m13, m14, _ = st.columns(5)
    _metric_card(m11, "Average Win",  result.get("average_win_pct",0),   "%")
    _metric_card(m12, "Average Loss", result.get("average_loss_pct",0),  "%", good_positive=False)
    _metric_card(m13, "Best Trade",   result.get("best_trade_pct",0),    "%")
    _metric_card(m14, "Worst Trade",  result.get("worst_trade_pct",0),   "%", good_positive=False)

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Equity curve ──────────────────────────────────────────────────────────
    st.markdown("### Equity Curve")
    st.caption("Shows how $" + f"{position_size:,} would have grown using this strategy "
               "versus simply buying and holding the same amount.")

    portfolio   = result.get("portfolio", pd.Series(dtype=float))
    bah_port    = result.get("buy_hold_portfolio", pd.Series(dtype=float))

    if not portfolio.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=portfolio.index, y=portfolio.values,
            name="Strategy", line=dict(color=BULL, width=2),
            fill="tozeroy", fillcolor="rgba(0,200,150,0.05)"))
        if not bah_port.empty:
            # Normalize buy-and-hold to same starting value
            bah_start = position_size
            bah_norm  = bah_port / bah_port.iloc[0] * bah_start
            fig.add_trace(go.Scatter(
                x=bah_norm.index, y=bah_norm.values,
                name="Buy and Hold", line=dict(color="#555", width=1.5, dash="dot")))
        # Mark trades
        for t in trades:
            entry_dt = pd.to_datetime(t["entry_date"])
            exit_dt  = pd.to_datetime(t["exit_date"].replace(" (open)",""))
            color    = BULL if t["outcome"].startswith("Win") else BEAR
            fig.add_vline(x=entry_dt, line_color=color, opacity=0.2, line_width=1)
        fig.add_hline(y=position_size, line_dash="dot", line_color="#333")
        fig.update_layout(
            height=380, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
            font=dict(color="#1a1a2e", size=11, family="IBM Plex Mono"),
            legend=dict(bgcolor="#f4f6fb", orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=10,r=10,t=30,b=10),
            yaxis_title="Portfolio Value ($)")
        fig.update_xaxes(gridcolor="#e8eaf0")
        fig.update_yaxes(gridcolor="#e8eaf0")
        st.markdown('<div class="chart-container">', unsafe_allow_html=True)
        st.plotly_chart(fig, use_container_width=True, key="equity_curve")
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Trade log ─────────────────────────────────────────────────────────────
    st.markdown(f"### Trade Log — {len(trades)} Trades")
    st.caption("Each row is one complete trade (entry and exit). "
               "Vertical colored lines on the chart correspond to trade entries.")

    if trades:
        df_trades = pd.DataFrame(trades)
        df_trades.rename(columns={
            "entry_date":        "Entry Date",
            "exit_date":         "Exit Date",
            "entry_price":       "Entry Price ($)",
            "exit_price":        "Exit Price ($)",
            "shares":            "Shares",
            "gross_return_pct":  "Return (%)",
            "profit_usd":        "Profit ($)",
            "holding_days":      "Holding Days",
            "outcome":           "Outcome",
        }, inplace=True)
        st.dataframe(df_trades, use_container_width=True, hide_index=True)
    else:
        st.info("No completed trades in this period. "
                "Try lowering the Buy Threshold or extending the date range.")

# ══════════════════════════════════════════════════════════════════════════════
# ALL TICKERS SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.get("bt_mode") == "all":
    results = st.session_state.get("bt_results", {})
    if not results:
        st.info("No results yet. Click Run Backtest.")
        st.stop()

    st.markdown("## Results — All Tickers")
    st.markdown("---")
    st.markdown("### Summary Comparison")
    st.caption("Compare how each ticker performs under the system's signals vs buy-and-hold. "
               "Hover over column headers for metric definitions.")

    summary_rows = []
    for ticker, result in results.items():
        if "error" in result:
            summary_rows.append({
                "Ticker":                   ticker,
                "Strategy":                 "—",
                "Total Return (%)":         "Error",
                "Buy and Hold Return (%)":  "—",
                "Alpha Generated (%)":      "—",
                "Win Rate (%)":             "—",
                "Profit Factor":            "—",
                "Sharpe Ratio":             "—",
                "Maximum Drawdown (%)":     "—",
                "Total Trades":             "—",
                "Verdict":                  f"❌ {result['error'][:40]}",
            })
            continue

        alpha    = result.get("alpha_pct", 0)
        sharpe   = result.get("sharpe_ratio", 0)
        pf       = result.get("profit_factor", 0)
        win_rate = result.get("win_rate_pct", 0)

        if alpha > 2 and sharpe > 1 and pf > 1.5:
            verdict = "✅ Strong Edge"
        elif alpha > 0 and pf > 1:
            verdict = "✓ Modest Edge"
        elif alpha > 0:
            verdict = "~ Marginal"
        else:
            verdict = "❌ Underperforms Buy-and-Hold"

        summary_rows.append({
            "Ticker":                   ticker,
            "Strategy":                 result.get("strategy","—").replace("_"," ").title(),
            "Total Return (%)":         f"{result.get('total_return_pct',0):+.1f}%",
            "Buy and Hold Return (%)":  f"{result.get('buy_hold_return_pct',0):+.1f}%",
            "Alpha Generated (%)":      f"{alpha:+.1f}%",
            "Win Rate (%)":             f"{win_rate:.1f}%",
            "Profit Factor":            f"{pf:.2f}×",
            "Sharpe Ratio":             f"{sharpe:.2f}",
            "Maximum Drawdown (%)":     f"{result.get('max_drawdown_pct',0):.1f}%",
            "Total Trades":             result.get("total_trades",0),
            "Verdict":                  verdict,
        })

    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    st.markdown("---")

    # Combined equity curves
    st.markdown("### Equity Curves — All Tickers vs Buy and Hold")
    st.caption("Each line shows how $" + f"{position_size:,} would have grown per ticker. "
               "Dotted lines = buy and hold baseline for each ticker.")

    fig2 = go.Figure()
    colors = ["#00c896","#7eb8f7","#f0c040","#e07aff","#ff9500","#ff4b4b","#00d4ff"]
    for i, (ticker, result) in enumerate(results.items()):
        if "error" in result or result.get("portfolio", pd.Series()).empty:
            continue
        color = colors[i % len(colors)]
        port  = result["portfolio"]
        fig2.add_trace(go.Scatter(
            x=port.index, y=port.values,
            name=f"{ticker} Strategy",
            line=dict(color=color, width=2)))
        bah = result.get("buy_hold_portfolio", pd.Series())
        if not bah.empty:
            bah_norm = bah / bah.iloc[0] * position_size
            fig2.add_trace(go.Scatter(
                x=bah_norm.index, y=bah_norm.values,
                name=f"{ticker} Buy and Hold",
                line=dict(color=color, width=1, dash="dot"),
                opacity=0.5))

    fig2.add_hline(y=position_size, line_dash="dot", line_color="#333")
    fig2.update_layout(
        height=420, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(color="#1a1a2e", size=11, family="IBM Plex Mono"),
        legend=dict(bgcolor="#f4f6fb", orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10,r=10,t=30,b=10),
        yaxis_title="Portfolio Value ($)")
    fig2.update_xaxes(gridcolor="#e8eaf0")
    fig2.update_yaxes(gridcolor="#e8eaf0")
    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
    st.plotly_chart(fig2, use_container_width=True, key="all_equity")
    st.markdown('</div>', unsafe_allow_html=True)

    # Individual ticker drill-down
    st.markdown("---")
    st.markdown("### Individual Ticker Detail")
    drill_ticker = st.selectbox("Select ticker for detailed view",
                                 [t for t in results if "error" not in results[t]])
    if drill_ticker:
        r = results[drill_ticker]
        st.markdown(f"**{drill_ticker}** — {r.get('trading_days',0)} trading days · "
                    f"Strategy: {r.get('strategy','—').replace('_',' ').title()}")
        d1, d2, d3, d4, d5 = st.columns(5)
        _metric_card(d1, "Total Return",    r.get("total_return_pct",0),    "%")
        _metric_card(d2, "Alpha Generated", r.get("alpha_pct",0),           "%")
        _metric_card(d3, "Win Rate",        r.get("win_rate_pct",0),        "%")
        _metric_card(d4, "Sharpe Ratio",    r.get("sharpe_ratio",0),        "")
        _metric_card(d5, "Maximum Drawdown",r.get("max_drawdown_pct",0),    "%", good_positive=False)

        if r.get("trades"):
            with st.expander(f"Show all {len(r['trades'])} trades for {drill_ticker}"):
                df_t = pd.DataFrame(r["trades"])
                df_t.rename(columns={
                    "entry_date":"Entry Date","exit_date":"Exit Date",
                    "entry_price":"Entry Price ($)","exit_price":"Exit Price ($)",
                    "gross_return_pct":"Return (%)","profit_usd":"Profit ($)",
                    "holding_days":"Holding Days","outcome":"Outcome"
                }, inplace=True)
                st.dataframe(df_t, use_container_width=True, hide_index=True)

render_footer()
