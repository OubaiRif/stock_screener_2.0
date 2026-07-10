"""
utils.py — Shared helpers, constants, and CSS for Stock Screener 2.0.
Design system defined once here — all pages reference these tokens.
"""
from datetime import datetime
import requests

# ── Color tokens ──────────────────────────────────────────────────────────────
BULL  = "#006040"   # green  — bullish, profit, positive
BEAR  = "#aa1800"   # red    — bearish, loss, negative
NEUT  = "#8a5e00"   # gold   — neutral, caution
BLUE  = "#0055cc"   # blue   — links, info, ETF
MUTED = "#666666"   # muted text
BG    = "#f4f6fb"   # card background
BORDER= "#dde1ea"   # standard border

def score_color(score):
    if score is None: return MUTED
    return BULL if score >= 60 else (BEAR if score <= 40 else NEUT)

STRATEGY_LABELS = {
    "trend":           "📈 Trend",
    "mean_reversion":  "↩ Mean Rev",
    "rubber_band":     "🔴 Rubber Band",
    "breakout_volume": "💥 Breakout Vol",
    "unassigned":      "— Unassigned",
}

def strategy_label(s):
    return STRATEGY_LABELS.get(s or "unassigned", s or "—")

def signal_badge(signal):
    cfg = {"BULLISH": ("badge-bull","▲ BULLISH"), "BEARISH": ("badge-bear","▼ BEARISH")}
    cls, txt = cfg.get(signal, ("badge-neut","— NEUTRAL"))
    return f'<span class="badge {cls}">{txt}</span>'

def move_html(pct):
    if pct is None: return ""
    c = BULL if pct > 0 else (BEAR if pct < 0 else NEUT)
    a = "▲" if pct > 0 else ("▼" if pct < 0 else "")
    return f'<span style="color:{c}">{a}{abs(pct):.2f}%</span>'

def score_bar_html(score, color=None):
    score = score or 0
    color = color or score_color(score)
    pct   = max(0, min(100, score))
    return (f'<div class="score-track">'
            f'<div class="score-fill" style="width:{pct:.0f}%;background:{color}"></div>'
            f'</div>')

# ── Market time ───────────────────────────────────────────────────────────────
def get_et_time():
    try:
        r = requests.get(
            "https://timeapi.io/api/time/current/zone?timeZone=America/New_York",
            timeout=4
        )
        if r.status_code == 200:
            d = r.json()
            return datetime(d["year"],d["month"],d["day"],d["hour"],d["minute"],d["seconds"])
    except Exception:
        pass
    return None

def is_market_hours(et=None):
    if et is None: et = get_et_time()
    if et is None: return False
    if et.weekday() >= 5: return False
    return (9, 30) <= (et.hour, et.minute) <= (16, 0)

# ── Design system CSS ─────────────────────────────────────────────────────────
# Spacing scale: 4 · 8 · 12 · 16 · 24 · 32px
# Border radius: sm=6px  md=10px  lg=14px
# Font scale: xs=.72em  sm=.80em  md=.88em  base=1em  lg=1.15em  xl=1.3em
# Fonts: Inter (UI) · IBM Plex Mono (numbers/code)

_CSS = """
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>

/* ── Base typography ── */
html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
    font-size: 15px !important;
    color: #1a1f2e;
    line-height: 1.5;
}
.mono { font-family: 'IBM Plex Mono', monospace !important; }
h1, h2, h3 { font-family: 'Inter', sans-serif !important; font-weight: 700; }
.block-container { padding-top: 0 !important; }

/* ── Color tokens as CSS vars ── */
:root {
    --bull:   #006040;
    --bear:   #aa1800;
    --neut:   #8a5e00;
    --blue:   #0055cc;
    --muted:  #666666;
    --bg:     #f4f6fb;
    --bg2:    #eef0f7;
    --border: #dde1ea;
    --text:   #1a1f2e;
    --radius-sm: 6px;
    --radius-md: 10px;
    --radius-lg: 14px;
    --sp-xs: 4px;
    --sp-sm: 8px;
    --sp-md: 12px;
    --sp-lg: 16px;
    --sp-xl: 24px;
}

/* ── Badges ── */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.80em;
    font-weight: 600;
    letter-spacing: 0.02em;
}
.badge-bull { background: rgba(0,96,64,0.10);  color: var(--bull); }
.badge-bear { background: rgba(170,24,0,0.10);  color: var(--bear); }
.badge-neut { background: rgba(138,94,0,0.10); color: var(--neut); }

/* ── Score bar ── */
.score-track {
    background: #e0e4ee;
    border-radius: 4px;
    height: 5px;
    width: 100%;
    margin-top: 4px;
}
.score-fill { height: 5px; border-radius: 4px; }

/* ── Cards ── */
.card {
    background: var(--bg);
    border-radius: var(--radius-md);
    padding: var(--sp-lg) 18px;
    margin-bottom: var(--sp-sm);
}
.card-border-bull { border-left: 3px solid var(--bull); }
.card-border-bear { border-left: 3px solid var(--bear); }
.card-border-neut { border-left: 3px solid var(--neut); }
.card-border-blue { border-left: 3px solid var(--blue); }
.card-border-muted{ border-left: 3px solid var(--border); }

/* ── Table rows ── */
.tbl-row {
    display: flex;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.88em;
}
.tbl-row:last-child { border-bottom: none; }
hr.divider {
    margin: 4px 0;
    border: none;
    border-top: 1px solid var(--border);
}

/* ── Pills ── */
.strat-pill {
    font-size: 0.80em;
    color: #333;
    background: #e8eaf0;
    padding: 2px 8px;
    border-radius: 20px;
    display: inline-block;
    font-weight: 500;
}
.conf-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.80em;
    color: var(--muted);
}

/* ── Alerts ── */
.spike-alert {
    background: #fffbe6;
    border: 1px solid var(--neut);
    border-radius: var(--radius-sm);
    padding: 6px 12px;
    color: var(--neut);
    font-size: 0.85em;
    margin-bottom: 4px;
}
.pre-market-banner {
    border-radius: var(--radius-sm);
    padding: 8px 14px;
    margin-bottom: 10px;
    font-size: 0.88em;
}

/* ── Section titles ── */
.section-title {
    font-size: 0.75em;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    margin-bottom: 8px;
    font-weight: 600;
}

/* ── Footer ── */
.footer {
    color: var(--muted);
    font-size: 0.78em;
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
}

/* ── Charts ── */
.chart-container {
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 8px;
    background: #ffffff;
    margin-bottom: 1rem;
}

/* ── Prediction banner ── */
.prediction-banner {
    background: var(--bg);
    border-radius: var(--radius-md);
    padding: 16px 20px;
    border-left: 4px solid var(--bull);
    margin-bottom: 1rem;
}
.prediction-banner.bear { border-left-color: var(--bear); }
.prediction-banner.neut { border-left-color: var(--neut); }

/* ── Indicator rows ── */
.ind-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 7px 0;
    border-bottom: 1px solid var(--border);
}
.ind-row:last-child { border-bottom: none; }
.ind-name { font-size: 0.88em; color: #444; width: 38%; }
.ind-val  { font-family: 'IBM Plex Mono', monospace; font-size: 0.92em;
            font-weight: 600; width: 28%; text-align: right; }
.ind-note { font-size: 0.80em; width: 34%; text-align: right; padding-left: 6px; color: var(--muted); }
.ind-bar-track { background: #e0e4ee; border-radius: 3px; height: 4px; margin-top: 3px; width: 100%; }
.ind-bar-fill  { height: 4px; border-radius: 3px; }

/* ── Headlines ── */
.headline-row  { padding: 9px 0; border-bottom: 1px solid #e8eaf0; }
.headline-row:last-child { border-bottom: none; }
.hl-bull { color: #00a85a; font-weight: 700; margin-right: 5px; }
.hl-bear { color: #cc3300; font-weight: 700; margin-right: 5px; }
.hl-neut { color: #c8a000; font-weight: 700; margin-right: 5px; }
.hl-text { font-size: 0.92em; color: #e0e4ef; }
.hl-meta { font-size: 0.78em; color: #9aa0b0; margin-top: 2px; }

/* ── Stock cards ── */
.stock-card {
    background: var(--bg);
    border-radius: var(--radius-md);
    padding: 12px 16px;
    margin-bottom: 8px;
    border-left: 3px solid var(--border);
    transition: border-left-color 0.15s;
}
.stock-card:hover { border-left-color: var(--blue); }

/* ── Sentiment / indicator cards ── */
.sentiment-card, .indicator-card {
    background: var(--bg);
    border-radius: var(--radius-md);
    padding: 14px 16px;
}

/* ── Streamlit metric override — tighter ── */
[data-testid="stMetric"] {
    background: var(--bg);
    border-radius: var(--radius-sm);
    padding: 10px 14px !important;
    border: 1px solid var(--border);
}
[data-testid="stMetricLabel"] p { font-size: 0.75em !important; color: var(--muted) !important; }
[data-testid="stMetricValue"]   { font-size: 1.2em !important; font-weight: 700 !important; }

/* ── Streamlit buttons — consistent sizing ── */
[data-testid="stButton"] button {
    border-radius: var(--radius-sm) !important;
    font-size: 0.82em !important;
    font-weight: 500 !important;
    padding: 5px 12px !important;
    height: 34px !important;
    font-family: 'Inter', sans-serif !important;
}

/* ── Expander ── */
[data-testid="stExpander"] {
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    margin-bottom: 8px !important;
}

/* ── Selectbox / input ── */
[data-testid="stSelectbox"] > div,
[data-testid="stNumberInput"] > div,
[data-testid="stTextInput"] > div {
    border-radius: var(--radius-sm) !important;
    font-size: 0.88em !important;
}

/* ── Dataframe ── */
[data-testid="stDataFrame"] {
    border-radius: var(--radius-md) !important;
    border: 1px solid var(--border) !important;
    font-size: 0.85em !important;
}

</style>
"""

def inject_css():
    """Inject shared CSS using st.html() which bypasses Streamlit's style stripping."""
    import streamlit as st
    st.html(_CSS)

# Keep for backwards compat
SHARED_CSS = _CSS


# ── Demo limitation banners (Fixes 5-9) ──────────────────────────────────────

def demo_banner(icon: str, title: str, body: str):
    """Render a styled info banner for demo limitations."""
    import streamlit as st
    st.html(f"""
    <div style="
        background:rgba(59,130,246,0.07);
        border:1px solid rgba(59,130,246,0.3);
        border-radius:8px;
        padding:0.65rem 1rem;
        margin-bottom:1rem;
        display:flex;gap:0.75rem;align-items:flex-start;">
      <span style="font-size:1.15rem;line-height:1.4">{icon}</span>
      <div>
        <strong style="color:#60a5fa">{title}</strong>
        <div style="color:#94a3b8;font-size:0.83rem;margin-top:0.15rem">{body}</div>
      </div>
    </div>""")
