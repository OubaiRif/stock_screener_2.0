"""
engine/sentiment.py — Sentiment from StockTwits, NewsAPI, and FinBERT (HuggingFace API).

FinBERT is used when HF_API_KEY is set in environment.
Falls back to keyword sentiment if API is unavailable or rate-limited.
"""
import logging, sys, os, time
from datetime import date

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import NEWS_API_KEY
from engine.db import get_conn

logger  = logging.getLogger(__name__)
HEADERS = {"User-Agent": "stock-screener/0.1"}

HF_API_KEY  = os.getenv("HF_API_KEY", "")
FINBERT_MODEL = "ProsusAI/finbert"

def _get_finbert_headers():
    key = os.getenv("HF_API_KEY", "")
    return key if key else None

# ── FinBERT via HuggingFace API ───────────────────────────────────────────────

def _finbert_score(texts: list) -> list | None:
    """
    Send headlines to FinBERT via HuggingFace InferenceClient.
    Returns list of scores (-1 to +1) or None if unavailable.
    """
    key = os.getenv("HF_API_KEY", "")
    if not key:
        return None
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(provider="hf-inference", api_key=key)
        scores = []
        for text in texts:
            result = client.text_classification(text, model=FINBERT_MODEL)
            # result is a list of ClassificationOutputElement
            label_scores = {r.label.lower(): r.score for r in result}
            pos = label_scores.get("positive", 0)
            neg = label_scores.get("negative", 0)
            scores.append(round(pos - neg, 3))
        return scores
    except ImportError:
        logger.warning("huggingface_hub not installed — pip install huggingface_hub")
        return None
    except Exception as e:
        logger.warning("FinBERT failed: %s — falling back to keywords", e)
        return None


# ── Keyword sentiment fallback ────────────────────────────────────────────────

def _kw_sentiment(text: str) -> float:
    text = text.lower()
    bull = {"surge":1.0,"soar":1.0,"rally":0.8,"beat":0.8,"gain":0.7,"rise":0.6,
            "growth":0.7,"profit":0.7,"upgrade":0.9,"buy":0.8,"bullish":1.0,
            "strong":0.6,"record":0.7,"breakout":0.9,"outperform":0.8,"boost":0.7,
            "jump":0.8,"climb":0.6,"exceed":0.8,"recover":0.6,"positive":0.6}
    bear = {"plunge":-1.0,"crash":-1.0,"drop":-0.8,"fall":-0.7,"decline":-0.7,
            "loss":-0.8,"miss":-0.8,"downgrade":-0.9,"sell":-0.7,"bearish":-1.0,
            "weak":-0.6,"warning":-0.7,"disappoint":-0.8,"slump":-0.8,"layoff":-0.7,
            "lawsuit":-0.6,"investigation":-0.6,"recall":-0.5,"risk":-0.4,
            "concern":-0.5,"cut":-0.6,"tumble":-0.8}
    score, hits = 0.0, 0
    for w, v in {**bull, **bear}.items():
        if w in text: score += v; hits += 1
    return max(-1.0, min(1.0, score / hits)) if hits else 0.0


# ── Save helpers ──────────────────────────────────────────────────────────────

def _save_sentiment(ticker, source, score, mention_count,
                    bullish_pct=None, bearish_pct=None, sample=""):
    today = date.today().isoformat()
    conn  = get_conn()
    conn.execute("""
        INSERT INTO sentiment (ticker,date,source,score,mention_count,bullish_pct,bearish_pct,sample_headline)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker,date,source) DO UPDATE SET
            score=excluded.score, mention_count=excluded.mention_count,
            bullish_pct=excluded.bullish_pct, bearish_pct=excluded.bearish_pct,
            sample_headline=excluded.sample_headline, fetched_at=datetime('now')
    """, (ticker.upper(), today, source, score,
          mention_count, bullish_pct, bearish_pct, sample))
    conn.commit(); conn.close()


def _save_headlines(ticker, articles, scores):
    """Store up to 5 headlines with FinBERT or keyword sentiment scores."""
    today = date.today().isoformat()
    conn  = get_conn()
    conn.execute("DELETE FROM headlines WHERE ticker=? AND date=? AND source='newsapi'",
                 (ticker.upper(), today))
    for i, a in enumerate(articles[:5]):
        title = (a.get("title") or "")[:300]
        score = scores[i] if scores and i < len(scores) else _kw_sentiment(
            title + " " + (a.get("description") or ""))
        conn.execute("""
            INSERT INTO headlines (ticker,date,source,headline,sentiment_score,url,published_at)
            VALUES (?,?,'newsapi',?,?,?,?)
        """, (ticker.upper(), today, title, round(score, 3),
              a.get("url", ""), (a.get("publishedAt", ""))[:19]))
    conn.commit(); conn.close()


# ── StockTwits ────────────────────────────────────────────────────────────────

def fetch_stocktwits(ticker):
    ticker = ticker.upper()
    try:
        r = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json",
            headers=HEADERS, timeout=10)
        if r.status_code != 200:
            logger.warning("StockTwits HTTP %s for %s", r.status_code, ticker); return
        msgs   = [m for m in r.json().get("messages", []) if m is not None]
    except Exception as e:
        logger.error("StockTwits failed for %s: %s", ticker, e); return

    def _get_basic(m):
        ent = m.get("entities") if m else None
        if not ent: return None
        sent = ent.get("sentiment") if isinstance(ent, dict) else None
        if not sent: return None
        return sent.get("basic") if isinstance(sent, dict) else None

    bull   = sum(1 for m in msgs if _get_basic(m) == "Bullish")
    bear   = sum(1 for m in msgs if _get_basic(m) == "Bearish")
    tagged = bull + bear
    score  = round((bull - bear) / tagged, 3) if tagged else 0.0
    bpct   = round(bull / tagged, 3) if tagged else None
    bpct2  = round(bear / tagged, 3) if tagged else None
    sample = msgs[0].get("body", "")[:200] if msgs else ""

    # Try FinBERT on sample messages for richer scoring
    if HF_API_KEY and msgs:
        texts  = [m.get("body","")[:256] for m in msgs[:5] if m.get("body")]
        fb_scores = _finbert_score(texts)
        if fb_scores:
            score = round(sum(fb_scores) / len(fb_scores), 3)
            logger.info("StockTwits %s — FinBERT score=%.2f (was %.2f keyword)", ticker, score, round((bull-bear)/tagged,3) if tagged else 0)

    _save_sentiment(ticker, "stocktwits", score, len(msgs), bpct, bpct2, sample)
    logger.info("StockTwits %s score=%.2f (%d msgs)", ticker, score, len(msgs))


# ── NewsAPI + FinBERT ─────────────────────────────────────────────────────────

def fetch_newsapi(ticker):
    if not NEWS_API_KEY: return
    ticker = ticker.upper()
    try:
        r = requests.get("https://newsapi.org/v2/everything", headers=HEADERS, timeout=10,
                         params={"q": f'"{ticker}" stock', "language": "en",
                                 "sortBy": "publishedAt", "pageSize": 20,
                                 "apiKey": NEWS_API_KEY})
        data = r.json()
    except Exception as e:
        logger.error("NewsAPI failed for %s: %s", ticker, e); return
    if data.get("status") != "ok": return
    articles = [a for a in data.get("articles", []) if a is not None]
    if not articles: return

    headlines = [(a.get("title") or "") + " " + (a.get("description") or "")
                 for a in articles[:5]]

    # Try FinBERT first, fall back to keywords
    fb_scores = _finbert_score(headlines)
    if fb_scores:
        scores = fb_scores
        source_label = "newsapi_finbert"
        logger.info("NewsAPI %s — using FinBERT sentiment", ticker)
    else:
        scores = [_kw_sentiment(h) for h in headlines]
        source_label = "newsapi"
        logger.info("NewsAPI %s — using keyword sentiment", ticker)

    avg    = round(sum(scores) / len(scores), 3)
    sample = articles[0].get("title", "")[:200]

    _save_sentiment(ticker, "newsapi", avg, len(articles), None, None, sample)
    _save_headlines(ticker, articles, scores)
    logger.info("NewsAPI %s score=%.2f (%d articles, source=%s)",
                ticker, avg, len(articles), source_label)


# ── Batch fetch ───────────────────────────────────────────────────────────────

def fetch_all_sentiment(ticker):
    try:
        fetch_stocktwits(ticker)
    except Exception as e:
        logger.error("StockTwits error for %s: %s", ticker, e, exc_info=True)
    time.sleep(1)
    try:
        fetch_newsapi(ticker)
    except Exception as e:
        logger.error("NewsAPI error for %s: %s", ticker, e, exc_info=True)

def fetch_sentiment_batch(tickers):
    using_finbert = bool(os.getenv("HF_API_KEY",""))
    logger.info("Sentiment mode: %s", "FinBERT API" if using_finbert else "keyword")
    for t in tickers:
        try: fetch_all_sentiment(t); time.sleep(2)
        except Exception as e: logger.error("Sentiment failed %s: %s", t, e)

# ── Load ──────────────────────────────────────────────────────────────────────

def get_latest_sentiment(ticker):
    today = date.today().isoformat()
    conn  = get_conn()
    rows  = conn.execute("""
        SELECT source, score, mention_count, bullish_pct, bearish_pct, sample_headline
        FROM sentiment WHERE ticker=? AND date=?
    """, (ticker.upper(), today)).fetchall()
    conn.close()
    if not rows: return {"available": False}
    sources = {r["source"]: dict(r) for r in rows}
    scores  = [r["score"] for r in rows if r["score"] is not None]
    avg     = round(sum(scores) / len(scores), 3) if scores else 0.0
    label   = "Bullish" if avg > 0.2 else ("Bearish" if avg < -0.2 else "Neutral")
    return {"available": True, "sources": sources, "avg_score": avg,
            "overall_label": label, "using_finbert": bool(HF_API_KEY)}

def get_headlines(ticker, limit=5):
    conn = get_conn()
    rows = conn.execute("""
        SELECT headline, source, url, published_at, sentiment_score
        FROM headlines WHERE ticker=?
        ORDER BY date DESC, published_at DESC LIMIT ?
    """, (ticker.upper(), limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

