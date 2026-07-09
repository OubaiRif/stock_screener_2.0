"""
core/page_setup.py — Shared page bootstrap for Stock Screener 2.0.
"""
import streamlit as st
from datetime import datetime
from utils import inject_css

NAV_ITEMS = [
    ("🏠 Home",        "dashboard.py"),
    ("📈 Stock",       "pages/1_Stock_Detail.py"),
    ("📊 ETF",         "pages/2_ETF_Screener.py"),
    ("⚡ Swing",       "pages/3_Swing_Trades.py"),
    ("🥇 Gold",        "pages/4_Gold_Dashboard.py"),
    ("🎯 Assistant",   "pages/5_Trading_Assistant.py"),
    ("💼 Portfolio",   "pages/6_Portfolio.py"),
    ("📓 Journal",     "pages/7_Journal.py"),
    ("🎲 Accuracy",    "pages/8_Accuracy.py"),
    ("📉 Backtest",    "pages/9_Backtest.py"),
]

# Injected into <head> via st.set_page_config to hide sidebar BEFORE render
_HIDE_SIDEBAR_STYLE = """
<style>
[data-testid="stSidebar"]        { display: none !important; }
[data-testid="collapsedControl"] { display: none !important; }
[data-testid="stToolbar"]        { display: none !important; }
[data-testid="stDecoration"]     { display: none !important; }
[data-testid="stStatusWidget"]   { display: none !important; }
header[data-testid="stHeader"]   { display: none !important; }
#MainMenu                        { display: none !important; }
footer                           { display: none !important; }
.block-container                 { padding-top: 0 !important; margin-top: 0 !important; }
</style>
"""

_NAV_CSS = """
<style>
/* Nav row */
.nav-row {
    background: #f4f6fb;
    border-bottom: 1px solid #dde1ea;
    padding: 3px 6px 1px 6px;
    margin-bottom: 1rem;
}
/* Shrink page_link text and padding */
.nav-row [data-testid="stPageLink"] p {
    font-size: 0.70em !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    padding: 0 !important;
    margin: 0 !important;
}
.nav-row [data-testid="stPageLink"] a {
    padding: 2px 3px !important;
    border-radius: 4px !important;
    color: #333 !important;
    text-decoration: none !important;
}
.nav-row [data-testid="stPageLink"] a:hover {
    background: #e0e4ee !important;
}
.nav-row [data-testid="stPageLink-NavLink"][aria-current="page"] a {
    background: #1a1f2e !important;
    color: #fff !important;
    font-weight: 600 !important;
}
/* Zero out column padding */
.nav-row [data-testid="column"] {
    padding: 0 1px !important;
    min-width: 0 !important;
}
</style>
"""


def render_nav(active_page: str = ""):
    st.html(_NAV_CSS)
    with st.container():
        st.markdown('<div class="nav-row">', unsafe_allow_html=True)
        # Use proportional widths based on label length to avoid truncation
        widths = [0.8, 0.8, 0.7, 0.8, 0.7, 1.1, 1.0, 0.9, 0.9, 1.0]
        cols = st.columns(widths)
        for col, (label, path) in zip(cols, NAV_ITEMS):
            with col:
                st.page_link(path, label=label)
        st.markdown('</div>', unsafe_allow_html=True)


def setup_page(title: str, icon: str = "📈", active_page: str = ""):
    st.set_page_config(
        page_title=title,
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.html(_HIDE_SIDEBAR_STYLE)
    inject_css()
    render_nav(active_page)

    # Demo mode banner
    try:
        from config import DEMO_MODE
        if DEMO_MODE:
            st.markdown(
                '<div style="background:#1a1f2e;color:#fff;padding:8px 16px;font-size:.82em;'
                'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                '<span>🎯 <b>Demo Mode</b> — Data is sample only. Changes reset when you close the tab.</span>'
                '<a href="https://github.com/OubaiRif/stock-screener-2.0" target="_blank" '
                'style="color:#7eb8f7;text-decoration:none;font-weight:600">'
                '⬇ Download Full App on GitHub →</a>'
                '</div>',
                unsafe_allow_html=True)
    except Exception:
        pass


def render_footer(note: str = ""):
    extra = f" · {note}" if note else ""
    st.markdown(
        f'<div class="footer">For informational purposes only — not financial advice.'
        f'{extra} {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>',
        unsafe_allow_html=True,
    )
