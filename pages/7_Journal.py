"""
pages/7_Journal.py — Auto Trading Journal.
All trades logged via Portfolio page or Gold Dashboard appear here.
Manual entry also supported.
"""
import sys, os
from datetime import date, datetime

import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.page_setup  import setup_page, render_footer
from core.db_queries  import (get_all_journal_entries, get_journal_entries_for_ticker,
                               log_journal_entry, ensure_journal_table, get_current_price)
from engine.db        import get_conn
from utils import BULL, BEAR, NEUT

setup_page("Trading Journal", "📓", active_page="7_Journal")

ensure_journal_table()

# ── Manual entry modal ────────────────────────────────────────────────────────
@st.dialog("Log Manual Trade", width="small")
def manual_entry_modal():
    st.markdown("### 📓 Log a Trade")
    ticker = st.text_input("Ticker", placeholder="e.g. AAPL").upper().strip()
    action = st.radio("Action", ["BUY", "SELL"], horizontal=True)
    shares = st.number_input("Shares", min_value=0.01, step=1.0, value=1.0)
    price  = st.number_input("Price per share ($)", min_value=0.01, step=0.01, value=1.0)
    notes  = st.text_input("Notes (optional)")

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("✓ Save", use_container_width=True):
            if ticker and shares > 0 and price > 0:
                cur  = get_current_price(ticker)
                log_journal_entry(ticker, action, shares, price, notes=notes)
                st.success(f"✓ {action} {shares:.0f} {ticker} @ ${price:.2f} logged")
                st.rerun()
            else:
                st.warning("Fill in ticker, shares and price.")
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()



# ── Fidelity CSV Import Modal ─────────────────────────────────────────────────
@st.dialog("📥 Import Fidelity CSV", width="large")
def fidelity_import_modal():
    import csv, io
    from datetime import datetime as _dt
    from core.db_queries import ensure_journal_table
    from engine.db import get_conn as _gc

    st.markdown("### Import Fidelity Trade History")
    st.info("Fidelity: Accounts & Trade → Activity & Orders → Export CSV. Upload one or more files below.")

    uploaded = st.file_uploader("Upload Fidelity CSV file(s)", type=["csv"],
                                 accept_multiple_files=True, key="fidelity_csv_upload")

    TRADE_PREFIXES = ("YOU BOUGHT", "YOU SOLD")
    SKIP_SYMBOLS   = {"SPAXX", ""}

    def _parse(fileobj):
        raw   = fileobj.read().decode("utf-8-sig")
        lines = [l for l in raw.split("\n")
                 if l.strip() and not l.startswith('"') and "," in l]
        trades = []
        reader = csv.DictReader(lines)
        for row in reader:
            if not row: continue
            action_raw = (row.get("Action") or "").strip()
            symbol     = (row.get("Symbol") or "").strip()
            qty_raw    = (row.get("Quantity") or "").strip()
            price_raw  = (row.get("Price ($)") or "").strip()
            amount_raw = (row.get("Amount ($)") or "").strip()
            date_raw   = (row.get("Run Date") or "").strip()
            desc       = (row.get("Description") or "").strip()
            if not any(action_raw.startswith(p) for p in TRADE_PREFIXES): continue
            if symbol in SKIP_SYMBOLS: continue
            if not qty_raw or not price_raw: continue
            try:
                qty   = float(qty_raw)
                price = float(price_raw)
                amt   = float(amount_raw) if amount_raw else abs(qty * price)
            except Exception:
                continue
            try:
                dt       = _dt.strptime(date_raw, "%m-%d-%Y")
                iso_date = dt.strftime("%Y-%m-%d") + "T09:30:00"
            except Exception:
                iso_date = date_raw
            trades.append({
                "ticker":    symbol,
                "action":    "BUY" if qty > 0 else "SELL",
                "shares":    abs(qty),
                "price":     price,
                "total":     abs(amt),
                "traded_at": iso_date,
                "notes":     "Fidelity import · " + desc[:50],
            })
        return trades

    if not uploaded:
        return

    all_trades = []
    for f in uploaded:
        try:
            trades = _parse(f)
            all_trades.extend(trades)
            st.success(f"✓ {f.name} — {len(trades)} trades parsed")
        except Exception as e:
            st.error(f"❌ {f.name}: {e}")

    # Deduplicate
    seen, deduped = set(), []
    for t in all_trades:
        key = (t["traded_at"], t["ticker"], t["action"], t["shares"])
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    deduped.sort(key=lambda x: x["traded_at"])

    if not deduped:
        st.warning("No BUY/SELL trades found in the uploaded files.")
        return

    st.markdown(f"**{len(deduped)} unique trades found:**")
    preview_df = pd.DataFrame([{
        "Date":   t["traded_at"][:10],
        "Ticker": t["ticker"],
        "Action": t["action"],
        "Shares": t["shares"],
        "Price":  "$" + f"{t['price']:.2f}",
        "Total":  "$" + f"{t['total']:.2f}",
    } for t in deduped])
    st.dataframe(preview_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    clear_existing = st.checkbox("Clear existing journal before import", value=True,
                                  help="Recommended to avoid duplicates")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ Confirm Import", use_container_width=True, key="fid_confirm"):
            ensure_journal_table()
            conn = _gc()
            if clear_existing:
                conn.execute("DELETE FROM trading_journal")
            for t in deduped:
                conn.execute(
                    "INSERT INTO trading_journal "
                    "(ticker, action, shares, price, total_value, notes, traded_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (t["ticker"], t["action"], t["shares"], t["price"],
                     t["total"], t["notes"], t["traded_at"]))
            conn.commit()
            conn.close()
            st.success(f"✓ {len(deduped)} trades imported!")
            st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True, key="fid_cancel"):
            st.rerun()

# ── Header ────────────────────────────────────────────────────────────────────
h1, h2, h3 = st.columns([3, 1.5, 1.5])
with h1:
    st.markdown("## 📓 Trading Journal")
    st.markdown(f"<span style='color:#444;font-size:.9em'>{date.today().strftime('%A, %B %d, %Y')}</span>",
                unsafe_allow_html=True)
with h2:
    if st.button("📥 Import CSV", use_container_width=True):
        fidelity_import_modal()
with h3:
    if st.button("➕ Log Trade", use_container_width=True):
        manual_entry_modal()

st.markdown("---")

# ── Load entries ──────────────────────────────────────────────────────────────
entries = get_all_journal_entries(limit=500)

if not entries:
    st.info("No journal entries yet. Trades logged from the Portfolio page appear here automatically. "
            "You can also log manually with the ➕ button above.")
    st.stop()

# ── Summary metrics ───────────────────────────────────────────────────────────
buys  = [e for e in entries if e["action"] == "BUY"]
sells = [e for e in entries if e["action"] == "SELL"]

realized_pnl = 0.0
for e in sells:
    # approximate realized P&L using current avg cost for the ticker
    conn = get_conn()
    row  = conn.execute("SELECT avg_cost FROM stocks WHERE ticker=?", (e["ticker"],)).fetchone()
    conn.close()
    if row and row["avg_cost"]:
        realized_pnl += (e["price"] - row["avg_cost"]) * e["shares"]

total_bought = sum(e["total_value"] for e in buys)
total_sold   = sum(e["total_value"] for e in sells)
rpnl_c       = BULL if realized_pnl >= 0 else BEAR

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total Trades",    len(entries))
m2.metric("Buys",            len(buys))
m3.metric("Sells",           len(sells))
m4.metric("Total Bought",    f"${total_bought:,.2f}")
m5.metric("Total Sold",      f"${total_sold:,.2f}")

st.markdown("---")

# ── Filters ───────────────────────────────────────────────────────────────────
f1, f2, f3 = st.columns([2, 2, 2])
with f1:
    all_tickers = ["All"] + sorted({e["ticker"] for e in entries})
    ticker_f    = st.selectbox("Ticker", all_tickers)
with f2:
    action_f    = st.selectbox("Action", ["All", "BUY", "SELL"])
with f3:
    sort_f      = st.selectbox("Sort", ["Newest first", "Oldest first"])

filtered = entries
if ticker_f != "All":
    filtered = [e for e in filtered if e["ticker"] == ticker_f]
if action_f != "All":
    filtered = [e for e in filtered if e["action"] == action_f]
if sort_f == "Oldest first":
    filtered = list(reversed(filtered))

# ── Journal table ─────────────────────────────────────────────────────────────
for col, hdr in zip(
    st.columns([1.2, 1.2, 1.5, 1.5, 1.8, 1.8, 2, 2.5]),
    ["Date", "Ticker", "Action", "Shares", "Price", "Total Value", "Signal at Trade", "Notes"]
):
    col.markdown(f"**{hdr}**")
st.markdown('<hr style="margin:4px 0;border-color:#dde1ea">', unsafe_allow_html=True)

for e in filtered[:100]:
    action_c = BULL if e["action"] == "BUY" else BEAR
    sig      = e.get("signal_at_trade") or "—"
    sig_c    = BULL if sig == "BULLISH" else (BEAR if sig == "BEARISH" else NEUT)
    score    = e.get("score_at_trade")

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.2, 1.2, 1.5, 1.5, 1.8, 1.8, 2, 2.5])
    c1.markdown(f'<span style="font-size:.88em;color:#444">{e["traded_at"][:10]}</span>',
                unsafe_allow_html=True)
    c2.markdown(f"**{e['ticker']}**")
    c3.markdown(f'<span style="color:{action_c};font-weight:700">{e["action"]}</span>',
                unsafe_allow_html=True)
    c4.markdown(f'<span style="font-family:IBM Plex Mono,monospace">{e["shares"]:.3f}</span>',
                unsafe_allow_html=True)
    c5.markdown(f'<span style="font-family:IBM Plex Mono,monospace">${e["price"]:.2f}</span>',
                unsafe_allow_html=True)
    c6.markdown(f'<span style="font-family:IBM Plex Mono,monospace">${e["total_value"]:.2f}</span>',
                unsafe_allow_html=True)
    c7.markdown(f'<span style="color:{sig_c}">{sig}</span>'
                + (f'<span style="font-size:.75em;color:#555"> · {score:.0f}/100</span>' if score else ""),
                unsafe_allow_html=True)
    c8.markdown(f'<span style="font-size:.82em;color:#555">{e.get("notes") or "—"}</span>',
                unsafe_allow_html=True)
    st.markdown('<hr style="margin:3px 0;border-color:#eee">', unsafe_allow_html=True)

if len(filtered) > 100:
    st.caption(f"Showing 100 of {len(filtered)} entries.")

# ── CSV export ────────────────────────────────────────────────────────────────
st.markdown("---")
if st.button("📥 Export to CSV"):
    df = pd.DataFrame(filtered)
    csv = df.to_csv(index=False)
    st.download_button("Download journal.csv", csv, "trading_journal.csv", "text/csv")

render_footer()
