"""
pages/6_Portfolio.py — Portfolio P&L tracker.
Reads in_portfolio=1 tickers. Shows cost basis, market value, unrealized P&L,
position weights, and today's prediction for each holding.
"""
import sys, os
from datetime import date, datetime

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup  import setup_page, render_footer
from core.db_queries  import get_portfolio_stocks, get_current_price, log_journal_entry
from engine.prices import get_extended_hours_price
from core.refresh     import run_full_refresh
from engine.db        import upsert_stock, get_conn, get_watchlist
from utils import (score_color, score_bar_html, strategy_label,
                   get_et_time, is_market_hours, BULL, BEAR, NEUT)

setup_page("Portfolio", "💼", active_page="6_Portfolio")

# Fix icon-button overflow on cloud
st.html("""
<style>
/* Constrain emoji action buttons so icons don't overflow */
section[data-testid="stButton"] > button {
    overflow: hidden;
    line-height: 1.15;
    padding: 0.3rem 0.4rem;
    min-width: 0;
}
section[data-testid="stButton"] > button p {
    margin: 0;
    font-size: 1rem;
    line-height: 1.15;
    overflow: hidden;
    text-overflow: clip;
}
</style>
""")

def _position_suggestion(signal, score, pnl_pct, shares, avg_cost):
    """Rule-based suggestion with icon prefix."""
    no_position = not shares or not avg_cost
    if no_position:
        if signal == "BULLISH" and (score or 0) >= 60:
            return ("🟢 Signal active — consider entry", BULL)
        return ("⚪ Add position data to track", "#888")
    s = score or 0
    p = pnl_pct or 0
    # Stop loss territory
    if p <= -15 and signal == "BEARISH":
        return ("🔴 Cut loss — signal bearish", BEAR)
    if p <= -25:
        return ("🔴 Large loss — reassess thesis", BEAR)
    # Take profit territory
    if p >= 30 and signal == "BEARISH":
        return ("🟡 Take profit — signal turned bearish", NEUT)
    if p >= 50:
        return ("🟢 Strong gain — consider partial exit", BULL)
    if p >= 20 and signal == "BULLISH":
        return ("🟢 Hold — gaining + signal bullish", BULL)
    # Normal hold/watch
    if signal == "BULLISH" and s >= 65:
        return ("🟢 Hold — strong bullish signal", BULL)
    if signal == "BULLISH" and s >= 50:
        return ("🟡 Hold — moderate signal", NEUT)
    if signal == "BEARISH" and s <= 40:
        return ("🔴 Watch — bearish signal", BEAR)
    if signal == "BEARISH":
        return ("🟡 Caution — signal weakening", NEUT)
    return ("⚪ Hold and monitor", NEUT)



# ── Edit position modal ───────────────────────────────────────────────────────
@st.dialog("Edit Position", width="small")
def edit_position_modal(row):
    ticker = row["ticker"]
    st.markdown(f"### {ticker}")
    in_pf    = st.checkbox("Currently holding", value=bool(row["in_portfolio"]), key=f"pf_{ticker}")
    avg_cost = st.number_input("Avg cost $", value=float(row["avg_cost"] or 0),
                               step=0.01, key=f"pf_cost_{ticker}")
    shares   = st.number_input("Shares held", value=float(row["shares_held"] or 0),
                               step=1.0, key=f"pf_sh_{ticker}")
    notes    = st.text_area("Notes", value=row.get("notes") or "", height=60, key=f"pf_n_{ticker}")

    cur = get_current_price(ticker)
    if in_pf and avg_cost > 0 and shares > 0 and cur:
        pnl  = (cur - avg_cost) * shares
        ppct = (cur - avg_cost) / avg_cost * 100
        c    = BULL if pnl >= 0 else BEAR
        a    = "▲" if pnl >= 0 else "▼"
        st.markdown(f'<span style="color:{c};font-family:IBM Plex Mono,monospace">'
                    f'{a} ${abs(pnl):.2f} ({ppct:+.2f}%)</span>', unsafe_allow_html=True)

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Save", use_container_width=True, key=f"pf_save_{ticker}"):
            upsert_stock(ticker, in_portfolio=int(in_pf), avg_cost=avg_cost,
                         shares_held=shares, notes=notes)
            st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True, key=f"pf_cancel_{ticker}"):
            st.rerun()


# ── Log trade modal ───────────────────────────────────────────────────────────
@st.dialog("Log Trade", width="small")
def log_trade_modal(row):
    ticker    = row["ticker"]
    cur_price = get_current_price(ticker) or 0.0
    st.markdown(f"### Log Trade — {ticker}")
    action = st.radio("Action", ["BUY", "SELL"], horizontal=True, key=f"lt_action_{ticker}")
    shares = st.number_input("Shares", min_value=0.01, step=1.0, value=1.0, key=f"lt_sh_{ticker}")
    price  = st.number_input("Price per share ($)", min_value=0.01, step=0.01,
                             value=float(cur_price), key=f"lt_pr_{ticker}")
    notes  = st.text_input("Notes (optional)", key=f"lt_notes_{ticker}")

    cur_shares = row.get("shares_held") or 0
    cur_avg    = row.get("avg_cost") or 0
    if shares > 0 and price > 0:
        if action == "BUY":
            new_shares = cur_shares + shares
            new_avg    = ((cur_shares * cur_avg) + (shares * price)) / new_shares if new_shares else price
            st.markdown(f'<div style="background:#f4f6fb;border-radius:8px;padding:10px;margin-top:8px;'
                        f'font-family:IBM Plex Mono,monospace;font-size:.85em">'
                        f'After: <b>{new_shares:.0f} shares</b> · New avg: <b>${new_avg:.2f}</b> · '
                        f'Cost: <b>${shares * price:.2f}</b></div>', unsafe_allow_html=True)
        else:
            realized = (price - cur_avg) * shares if cur_avg else 0
            pc       = BULL if realized >= 0 else BEAR
            st.markdown(f'<div style="background:#f4f6fb;border-radius:8px;padding:10px;margin-top:8px;'
                        f'font-family:IBM Plex Mono,monospace;font-size:.85em">'
                        f'Realized P&L: <span style="color:{pc}"><b>${realized:+.2f}</b></span>'
                        f'</div>', unsafe_allow_html=True)

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("✓ Confirm", use_container_width=True, key=f"lt_confirm_{ticker}"):
            if shares > 0 and price > 0:
                # Update position
                if action == "BUY":
                    new_s = cur_shares + shares
                    new_a = ((cur_shares * cur_avg) + (shares * price)) / new_s if new_s else price
                    upsert_stock(ticker, in_portfolio=1, avg_cost=round(new_a, 4),
                                 shares_held=new_s)
                else:
                    new_s = max(0, cur_shares - shares)
                    in_pf = 1 if new_s > 0 else 0
                    upsert_stock(ticker, in_portfolio=in_pf, shares_held=new_s)
                # Auto-log to journal
                log_journal_entry(ticker, action, shares, price,
                                  signal_at_trade=row.get("signal"),
                                  score_at_trade=row.get("composite_score"),
                                  notes=notes)
                st.success(f"✓ {action} {shares:.0f} shares @ ${price:.2f} logged")
                st.rerun()
            else:
                st.warning("Enter shares and price.")
    with c2:
        if st.button("Cancel", use_container_width=True, key=f"lt_cancel_{ticker}"):
            st.rerun()



# ── Portfolio Management Modal ────────────────────────────────────────────────
@st.dialog("💼 Portfolio Management", width="large")
def portfolio_management_modal():
    st.markdown("### Manage Portfolio & Watchlist")

    # ── Account Balance ───────────────────────────────────────────────────────
    st.markdown("**Available Cash**")
    conn = get_conn()
    bal_row = conn.execute(
        "SELECT value FROM macro_data WHERE series_id='account_balance' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    current_balance = float(bal_row["value"]) if bal_row else 0.0
    new_balance = st.number_input("Available cash balance ($)", min_value=0.0,
                                   value=current_balance, step=100.0,
                                   key="mgmt_balance")
    if st.button("💾 Save Balance", key="save_balance"):
        conn = get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO macro_data (series_id, date, value, source)
            VALUES ('account_balance', date('now'), ?, 'manual')
        """, (new_balance,))
        conn.commit()
        conn.close()
        st.success(f"✓ Cash balance updated to ${new_balance:,.2f}")

    st.markdown("---")

    # ── Add New Ticker ────────────────────────────────────────────────────────
    st.markdown("**Open New Position**")
    a1, a2, a3 = st.columns([2, 2, 1.5])
    with a1:
        new_ticker = st.text_input("Ticker", placeholder="e.g. NVDA",
                                    key="mgmt_new_ticker").upper().strip()
    with a2:
        watchlist_type = st.selectbox("Watchlist",
                                       ["stock", "etf", "swing", "portfolio"],
                                       key="mgmt_wl_type")
    with a3:
        st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
        add_clicked = st.button("➕ Open Position", use_container_width=True, key="mgmt_add")
        st.markdown("</div>", unsafe_allow_html=True)

    if watchlist_type == "portfolio":
        p1, p2 = st.columns(2)
        with p1:
            new_shares   = st.number_input("Shares held", min_value=0.0, step=1.0, key="mgmt_new_shares")
        with p2:
            new_avg_cost = st.number_input("Avg cost ($)", min_value=0.0, step=0.01, key="mgmt_new_avg")
    else:
        new_shares   = 0.0
        new_avg_cost = 0.0

    if add_clicked and new_ticker:
        conn = get_conn()
        existing = conn.execute("SELECT ticker FROM stocks WHERE ticker=?",
                                (new_ticker,)).fetchone()
        conn.close()
        _in_pf   = 1 if watchlist_type == "portfolio" else 0
        _shares  = new_shares if watchlist_type == "portfolio" else 0
        _avg     = new_avg_cost if watchlist_type == "portfolio" else None
        _conn2   = get_conn()
        if existing:
            _conn2.execute("""
                UPDATE stocks SET watchlist_type=?, in_portfolio=?,
                    shares_held=?, avg_cost=?, updated_at=datetime('now')
                WHERE ticker=?
            """, (watchlist_type, _in_pf, _shares, _avg, new_ticker))
            _conn2.commit()
            _conn2.close()
            st.success(f"✓ {new_ticker} moved to {watchlist_type} watchlist")
        else:
            try:
                import yfinance as yf
                _info   = yf.Ticker(new_ticker).info
                _name   = _info.get("longName") or _info.get("shortName") or new_ticker
                _is_etf = 1 if _info.get("quoteType") == "ETF" else 0
            except Exception:
                _name   = new_ticker
                _is_etf = 1 if watchlist_type == "etf" else 0
            _conn2.execute("""
                INSERT OR IGNORE INTO stocks
                    (ticker, name, is_etf, watchlist_type, in_portfolio,
                     shares_held, avg_cost, strategy, added_at, updated_at)
                VALUES (?,?,?,?,?,?,?,'unassigned',datetime('now'),datetime('now'))
            """, (new_ticker, _name, _is_etf, watchlist_type, _in_pf, _shares, _avg))
            _conn2.commit()
            _conn2.close()
            st.success(f"✓ {new_ticker} added to {watchlist_type} watchlist")
        st.rerun()

    st.markdown("---")

    # ── Edit Existing Positions ───────────────────────────────────────────────
    st.markdown("**Edit Existing Positions**")
    conn      = get_conn()
    all_stocks = conn.execute(
        "SELECT ticker, name, watchlist_type, in_portfolio, shares_held, avg_cost, notes "
        "FROM stocks ORDER BY watchlist_type, ticker"
    ).fetchall()
    conn.close()

    # Filter
    wl_filter = st.selectbox("Filter by watchlist", ["portfolio", "all", "stock", "etf", "swing"],
                              key="mgmt_filter")

    for row in all_stocks:
        r = dict(row)
        if wl_filter != "all":
            if wl_filter == "portfolio" and not r["in_portfolio"]:
                continue
            elif wl_filter != "portfolio" and r.get("watchlist_type") != wl_filter:
                continue

        ticker = r["ticker"]
        cur    = get_current_price(ticker)
        pnl_html = ""
        if r["in_portfolio"] and r["avg_cost"] and r["shares_held"] and cur:
            pnl     = (cur - r["avg_cost"]) * r["shares_held"]
            pnl_pct = (cur - r["avg_cost"]) / r["avg_cost"] * 100
            pc      = BULL if pnl >= 0 else BEAR
            pa      = "▲" if pnl >= 0 else "▼"
            pnl_html = f'<span style="color:{pc};font-size:.8em"> {pa} ${abs(pnl):.2f} ({pnl_pct:+.1f}%)</span>'

        with st.expander(
            f"**{ticker}** · {r.get('watchlist_type') or '—'}"
            + (" 💼" if r["in_portfolio"] else "")
            + f" · ${cur:.2f}" if cur else f"**{ticker}**",
            expanded=False
        ):
            e1, e2, e3 = st.columns([2, 2, 2])
            with e1:
                new_wl = st.selectbox("Watchlist", ["stock", "etf", "swing", "none"],
                                       index=["stock","etf","swing","none"].index(
                                           r.get("watchlist_type") or "none"
                                       ) if (r.get("watchlist_type") or "none") in ["stock","etf","swing","none"] else 3,
                                       key=f"mgmt_wl_{ticker}")
                in_pf  = st.checkbox("In portfolio", value=bool(r["in_portfolio"]),
                                      key=f"mgmt_pf_{ticker}")
            with e2:
                new_sh  = st.number_input("Shares", value=float(r["shares_held"] or 0),
                                           step=1.0, key=f"mgmt_sh_{ticker}")
                new_avg = st.number_input("Avg cost ($)", value=float(r["avg_cost"] or 0),
                                           step=0.01, key=f"mgmt_avg_{ticker}")
            with e3:
                if cur:
                    st.markdown(f'<div style="margin-top:8px;font-family:IBM Plex Mono,monospace">'
                                f'<b>${cur:.2f}</b>{pnl_html}</div>', unsafe_allow_html=True)
                new_notes = st.text_input("Notes", value=r.get("notes") or "",
                                           key=f"mgmt_notes_{ticker}")

            sb1, sb2 = st.columns([1, 1])
            with sb1:
                if st.button("💾 Save", key=f"mgmt_save_{ticker}", use_container_width=True):
                    _wl_val = new_wl if new_wl != "none" else None
                    _sc = get_conn()
                    _sc.execute("""
                        UPDATE stocks SET watchlist_type=?, in_portfolio=?,
                            shares_held=?, avg_cost=?, notes=?, updated_at=datetime('now')
                        WHERE ticker=?
                    """, (_wl_val, int(in_pf), new_sh, new_avg, new_notes, ticker))
                    _sc.commit()
                    _sc.close()
                    st.success(f"✓ {ticker} updated")
                    st.rerun()
            with sb2:
                if st.button("🗑 Remove from watchlist", key=f"mgmt_del_{ticker}",
                              use_container_width=True):
                    conn = get_conn()
                    conn.execute("UPDATE stocks SET watchlist_type=NULL, in_portfolio=0, "
                                "shares_held=0, avg_cost=NULL WHERE ticker=?", (ticker,))
                    conn.commit()
                    conn.close()
                    st.success(f"✓ {ticker} removed from watchlist")
                    st.rerun()

# ── Header ────────────────────────────────────────────────────────────────────
et = get_et_time()
h1, h2, h3, h4 = st.columns([2.5, 1.2, 1.3, 1.5])
with h1:
    st.markdown("## 💼 Portfolio")
    st.markdown(f"<span style='color:#444;font-size:.9em'>{date.today().strftime('%A, %B %d, %Y')}</span>",
                unsafe_allow_html=True)
with h2:
    if st.button("⚙️ Manage", use_container_width=True):
        portfolio_management_modal()
with h3:
    if st.button("🔄 Refresh", use_container_width=True):
        holdings = get_portfolio_stocks()
        tickers  = [r["ticker"] for r in holdings]
        if tickers:
            run_full_refresh(tickers)
            st.rerun()
        else:
            st.warning("No portfolio positions found.")
with h4:
    if et:
        et_str = et.strftime("%H:%M ET")
        if is_market_hours(et): st.success(f"🟢 Open · {et_str}")
        else:                   st.error(f"🔴 Closed · {et_str}")
    else:
        st.warning("⚪ Offline?")

st.markdown("---")

# ── Load portfolio ────────────────────────────────────────────────────────────
holdings = get_portfolio_stocks()

if not holdings:
    st.info("No positions marked as in_portfolio. Edit a stock and check 'Currently holding'.")
    st.stop()

# ── Portfolio summary ─────────────────────────────────────────────────────────
total_cost   = 0.0
total_value  = 0.0
total_pnl    = 0.0
rows_display = []

for r in holdings:
    cur     = get_current_price(r["ticker"])
    shares  = r.get("shares_held") or 0
    avg     = r.get("avg_cost") or 0
    cost    = shares * avg
    value   = shares * cur if cur and shares else 0
    pnl     = value - cost if value else 0
    pnl_pct = (pnl / cost * 100) if cost else 0
    total_cost  += cost
    total_value += value
    total_pnl   += pnl
    rows_display.append({**r, "_cur": cur, "_cost": cost,
                         "_value": value, "_pnl": pnl, "_pnl_pct": pnl_pct})

total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
pnl_c         = BULL if total_pnl >= 0 else BEAR
pnl_a         = "▲" if total_pnl >= 0 else "▼"

# Load cash balance
_cb_conn = get_conn()
_cb_row  = _cb_conn.execute(
    "SELECT value FROM macro_data WHERE series_id='account_balance' ORDER BY date DESC LIMIT 1"
).fetchone()
_cb_conn.close()
cash_balance   = float(_cb_row["value"]) if _cb_row else 0.0
total_account  = total_value + cash_balance

s1, s2, s3, s4, s5 = st.columns(5)
s1.metric("Total Account Value", f"${total_account:,.2f}",
          help="Market value of positions + available cash")
s2.metric("Invested Value",      f"${total_value:,.2f}")
s3.metric("Available Cash",      f"${cash_balance:,.2f}")
s4.metric("Unrealized P&L",
          f"${total_pnl:+,.2f}",
          delta=f"{total_pnl_pct:+.2f}%",
          delta_color="normal" if total_pnl >= 0 else "inverse")
s5.metric("Positions", len(holdings))

st.markdown("---")

# ── Position table ────────────────────────────────────────────────────────────
for col, hdr in zip(
    st.columns([1.2, 1.5, 1.5, 1.8, 2, 2, 1.5, 1.8]),
    ["Ticker", "Shares", "Avg Cost", "Mkt Price", "Market Value", "P&L", "Signal", "Actions"]
):
    col.markdown(f"**{hdr}**")
st.markdown('<hr style="margin:4px 0;border-color:#dde1ea">', unsafe_allow_html=True)

for r in rows_display:
    ticker  = r["ticker"]
    cur     = r["_cur"]
    shares  = r.get("shares_held") or 0
    avg     = r.get("avg_cost") or 0
    pnl     = r["_pnl"]
    pnl_pct = r["_pnl_pct"]
    pnl_c   = BULL if pnl >= 0 else BEAR
    pnl_a   = "▲" if pnl >= 0 else "▼"
    signal  = r.get("signal") or "—"
    score   = r.get("composite_score") or 50
    sig_c   = BULL if signal == "BULLISH" else (BEAR if signal == "BEARISH" else NEUT)
    sig_a   = "▲" if signal == "BULLISH" else ("▼" if signal == "BEARISH" else "—")
    weight  = (r["_value"] / total_value * 100) if total_value else 0

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.5, 1.2, 1.2, 1.8, 2, 2, 2.5, 1.5])
    sug_txt, sug_c = _position_suggestion(signal, score, pnl_pct if r.get("avg_cost") else None,
                                           r.get("shares_held"), r.get("avg_cost"))

    # Pre-market price
    _ppx = get_extended_hours_price(ticker)
    _pm_html = ""
    if not _ppx.get("error") and _ppx.get("price_type") in ("pre_market", "post_market"):
        _plbl = "PM" if _ppx["price_type"] == "pre_market" else "AH"
        _pc   = "#c8a000" if _ppx["price_type"] == "pre_market" else "#0066cc"
        _ppct = _ppx.get("pre_change_pct") or _ppx.get("post_change_pct") or 0
        _pa   = "▲" if _ppct >= 0 else "▼"
        _pm_html = (f'<br><span style="color:{_pc};font-size:.75em;font-family:IBM Plex Mono,monospace">'
                    f'{_plbl} ${_ppx["price"]:.2f} {_pa}{abs(_ppct):.1f}%</span>')

    c1.markdown(f"**{ticker}**<br><span style='font-size:.75em;color:#555'>"
                f"{(r.get('name') or ticker)[:18]}</span>", unsafe_allow_html=True)
    c2.markdown(f'<span style="font-family:IBM Plex Mono,monospace">{shares:.0f}</span>',
                unsafe_allow_html=True)
    c3.markdown(f'<span style="font-family:IBM Plex Mono,monospace">'
                f'{"$" + f"{avg:.2f}" if avg else "—"}</span>', unsafe_allow_html=True)
    _cur_str = "$" + f"{cur:.2f}" if cur else "—"
    c4.markdown(f'<span style="font-family:IBM Plex Mono,monospace;font-weight:600">{_cur_str}</span>' + _pm_html,
                unsafe_allow_html=True)
    val_str = f'${r["_value"]:,.2f}' if r["_value"] else "—"
    c5.markdown(f'<span style="font-family:IBM Plex Mono,monospace">{val_str}</span>'
                f'<br><span style="font-size:.75em;color:#555">{weight:.1f}% of portfolio</span>',
                unsafe_allow_html=True)
    c6.markdown(f'<span style="color:{pnl_c};font-family:IBM Plex Mono,monospace;font-weight:600">'
                f'{pnl_a} ${abs(pnl):,.2f} ({pnl_pct:+.1f}%)</span>'
                if pnl != 0 else '<span style="color:#555">—</span>',
                unsafe_allow_html=True)
    c7.markdown(f'<span style="color:{sig_c};font-weight:600">{sig_a} {signal}</span>'
                f'<br><span style="font-size:.75em;color:#555">{score:.0f}/100</span>'
                f'<br><span style="color:{sug_c};font-size:.78em">{sug_txt}</span>',
                unsafe_allow_html=True)

    if c8.button("📊 Detail", key=f"pf_det_{ticker}", use_container_width=True):
        st.session_state["detail_ticker"] = ticker
        st.switch_page("pages/1_Stock_Detail.py")
    if c8.button("✏️ Edit", key=f"pf_edit_{ticker}", use_container_width=True):
        edit_position_modal(r)
    if c8.button("💱 Trade", key=f"pf_trade_{ticker}", use_container_width=True):
        log_trade_modal(r)

    st.markdown('<hr style="margin:4px 0;border-color:#eee">', unsafe_allow_html=True)

# ── Allocation chart ─────────────────────────────────────────────────────────
if total_value > 0:
    import plotly.graph_objects as go
    st.markdown("---")
    st.markdown("**Portfolio Allocation**")
    labels = [r["ticker"] for r in rows_display if r["_value"] > 0]
    values = [r["_value"] for r in rows_display if r["_value"] > 0]
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.4,
                           textinfo="label+percent",
                           marker_colors=["#006040","#7eb8f7","#f0c040","#e07aff",
                                          "#ff9500","#00c896","#aa1800","#555"]))
    fig.update_layout(height=320, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                      margin=dict(l=10, r=10, t=20, b=10),
                      showlegend=False,
                      font=dict(family="IBM Plex Mono", size=11, color="#1a1a2e"))
    st.plotly_chart(fig, use_container_width=True, key="portfolio_allocation")

render_footer()
