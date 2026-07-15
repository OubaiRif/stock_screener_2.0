"""
engine/predictor.py — Composite scoring and price range prediction.
Rules-based: indicators vote +1/0/-1, weighted by strategy, normalized to 0-100.
"""
import logging, sys, os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import SCORE_WEIGHT_TECHNICAL, SCORE_WEIGHT_FUNDAMENTAL, SCORE_WEIGHT_SENTIMENT
from engine.db import get_conn
from engine.indicators import get_latest_indicators
from engine.fetcher import load_fundamentals

logger = logging.getLogger(__name__)

# ── Strategy weight multipliers ───────────────────────────────────────────────
_W = {
    "trend":           {"ema":2.0,"macd":1.5,"adx":1.0,"rsi":1.0,"bb":1.0,"z":0.5,"wr":0.8},
    "mean_reversion":  {"ema":1.0,"macd":1.0,"adx":0.0,"rsi":2.0,"bb":2.0,"z":1.5,"wr":0.8},
    "rubber_band":     {"ema":1.0,"macd":1.0,"adx":0.0,"rsi":2.0,"bb":2.0,"z":0.5,"wr":1.5},
    "breakout_volume": {"ema":1.0,"macd":1.0,"adx":0.0,"rsi":1.0,"bb":1.0,"z":0.5,"wr":0.8},
    "unassigned":      {"ema":1.0,"macd":1.0,"adx":0.0,"rsi":1.0,"bb":1.0,"z":0.5,"wr":0.8},
}

def _w(strategy, key):
    return _W.get(strategy, _W["unassigned"]).get(key, 1.0)

def _add(signals, ws, wt, name, vote, weight, value="", note=""):
    if vote != 0 or True:   # always record
        signals.append({"indicator": name, "vote": vote, "value": value, "note": note})
    ws += vote * weight
    wt += weight
    return ws, wt

# ── Technical scoring ─────────────────────────────────────────────────────────

def _score_technical(ind, strategy):
    signals, ws, wt = [], 0.0, 0.0
    g = ind.get
    close, e20, e50, e200 = g("close"), g("ema_20"), g("ema_50"), g("ema_200")
    rsi, macd, msig       = g("rsi"), g("macd"), g("macd_signal")
    adx, bb, zs, stk      = g("adx"), g("bb_pct_b"), g("zscore"), g("stoch_k")
    wr, rv, obv, obv_e    = g("williams_r"), g("rel_volume"), g("obv"), g("obv_ema")

    # EMA stack
    w = _w(strategy, "ema")
    if close and e20 and e50 and e200:
        v = 1 if close>e20>e50>e200 else (-1 if close<e20<e50<e200 else 0)
        note = "Bull stack" if v==1 else "Bear stack" if v==-1 else "Mixed"
        ws, wt = _add(signals, ws, wt, "EMA Stack", v, w, f"{close:.2f}", note)

    # MACD
    w = _w(strategy, "macd")
    if macd is not None and msig is not None:
        v = 1 if macd>msig else (-1 if macd<msig else 0)
        ws, wt = _add(signals, ws, wt, "MACD", v, w, f"{macd:.4f}")

    # ADX (trend only)
    if adx is not None and strategy == "trend":
        v = 1 if adx > 25 else 0
        ws, wt = _add(signals, ws, wt, "ADX", v, 1.0, round(adx,2),
                      "Strong trend" if adx>25 else "Weak/ranging")

    # RSI
    w = _w(strategy, "rsi")
    if rsi is not None:
        v = 1 if rsi<35 else (-1 if rsi>65 else 0)
        note = "Oversold" if rsi<35 else "Overbought" if rsi>65 else "Neutral"
        ws, wt = _add(signals, ws, wt, "RSI", v, w, round(rsi,2), note)

    # BB %B
    w = _w(strategy, "bb")
    if bb is not None:
        v = 1 if bb<0.1 else (-1 if bb>0.9 else 0)
        note = "Near lower band" if bb<0.1 else "Near upper band" if bb>0.9 else "Mid-band"
        ws, wt = _add(signals, ws, wt, "BB %B", v, w, round(bb,3), note)

    # Z-score
    w = _w(strategy, "z")
    if zs is not None:
        v = 1 if zs<-1.5 else (-1 if zs>1.5 else 0)
        ws, wt = _add(signals, ws, wt, "Z-Score", v, w, round(zs,3),
                      f"{'Below' if zs<0 else 'Above'} mean {abs(zs):.1f}σ")

    # Stochastic
    if stk is not None:
        v = 1 if stk<20 else (-1 if stk>80 else 0)
        ws, wt = _add(signals, ws, wt, "Stoch %K", v, 1.0, round(stk,2))

    # Williams %R
    w = _w(strategy, "wr")
    if wr is not None:
        v = 1 if wr<-80 else (-1 if wr>-20 else 0)
        ws, wt = _add(signals, ws, wt, "Williams %R", v, w, round(wr,2))

    # Relative volume
    if rv is not None:
        v = 1 if rv>1.5 else (-1 if rv<0.5 else 0)
        note = "High vol" if rv>1.5 else "Low vol" if rv<0.5 else "Normal"
        ws, wt = _add(signals, ws, wt, "Rel Volume", v, 0.8, round(rv,2), note)

    # OBV
    if obv is not None and obv_e is not None:
        v = 1 if obv>obv_e else -1
        ws, wt = _add(signals, ws, wt, "OBV vs EMA", v, 1.0)

    # Breakout volume
    if strategy == "breakout_volume":
        res = g("resistance_20d")
        if close and res and rv is not None:
            pvr = (close-res)/res
            if pvr>=0 and rv>=1.5:   v,n = 1, f"Breaking res ${res:.2f} on {rv:.1f}x vol"
            elif pvr>=-0.02 and rv>=1.2: v,n = 1, "Approaching resistance"
            elif rv<0.8:              v,n = -1, "Low volume — no conviction"
            else:                     v,n = 0,  "Watching"
            ws, wt = _add(signals, ws, wt, "Breakout", v, 2.5, f"{pvr:.2%}", n)
        if rv is not None and rv>=2.0:
            ws, wt = _add(signals, ws, wt, "Vol Spike", 1, 1.0, f"{rv:.1f}x", "SPIKE ALERT")

    if wt == 0: return 50.0, signals
    return round((ws/wt+1)/2*100, 1), signals

# ── Fundamental scoring ───────────────────────────────────────────────────────

def _score_fundamental(fund):
    score, signals = 50.0, []
    checks = [
        ("pe_trailing",   lambda v: (10 if v<15 else 5 if v<25 else -5 if v<40 else -10) if 0<v<100 else 0),
        ("peg_ratio",     lambda v: (8 if v<1 else -5 if v>2 else 0) if v>0 else 0),
        ("profit_margin", lambda v: (8 if v>0.20 else 3 if v>0.05 else -8 if v<0 else 0)),
        ("debt_to_equity",lambda v: (5 if v<0.5 else -5 if v>2 else 0)),
        ("short_ratio",   lambda v: (5 if v>5 else 0)),
    ]
    for key, fn in checks:
        v = fund.get(key)
        if v is not None:
            adj = fn(v)
            score += adj
            signals.append({"indicator": key, "value": v, "adj": adj})
    return round(max(0, min(100, score)), 1), signals

# ── Sentiment scoring ─────────────────────────────────────────────────────────

def _score_sentiment(ticker):
    today = date.today().isoformat()
    conn  = get_conn()
    rows  = conn.execute(
        "SELECT source,score FROM sentiment WHERE ticker=? AND date=?",
        (ticker.upper(), today)
    ).fetchall()
    conn.close()
    if not rows: return 50.0, [{"source":"none","note":"No sentiment data"}]
    scores  = [(r["score"]+1)/2*100 for r in rows if r["score"] is not None]
    signals = [dict(r) for r in rows]
    return round(sum(scores)/len(scores), 1) if scores else 50.0, signals

# ── Price range ───────────────────────────────────────────────────────────────

def _price_range(ticker, ind, composite):
    conn = get_conn()
    row  = conn.execute(
        "SELECT close FROM price_history WHERE ticker=? ORDER BY date DESC LIMIT 1",
        (ticker.upper(),)
    ).fetchone()
    conn.close()
    if not row: return {}
    last  = row["close"]
    atr   = ind.get("atr") or last*0.015
    bias  = (composite-50)/50
    mid   = last*(1+bias*0.003)
    return {"price_low":round(mid-atr*0.8,4),"price_mid":round(mid,4),
            "price_high":round(mid+atr*0.8,4),"last_close":round(last,4)}

# ── Strategy alignment ────────────────────────────────────────────────────────

def _strategy_alignment(strategy, signals):
    sv = {s["indicator"]: s["vote"] for s in signals}
    if strategy == "trend":
        e,m = sv.get("EMA Stack",0), sv.get("MACD",0)
        return "ALIGNED" if e==m and e!=0 else "MIXED"
    if strategy == "mean_reversion":
        r,b = sv.get("RSI",0), sv.get("BB %B",0)
        return "ALIGNED" if r==b and r!=0 else "MIXED"
    if strategy == "rubber_band":
        w,b = sv.get("Williams %R",0), sv.get("BB %B",0)
        return "ALIGNED" if w==b and w!=0 else "MIXED"
    if strategy == "breakout_volume":
        bo,rv = sv.get("Breakout",0), sv.get("Rel Volume",0)
        return "ALIGNED" if bo==1 and rv>=0 else "MIXED"
    return "N/A"

# ── Save prediction ───────────────────────────────────────────────────────────

def _save(result):
    conn = get_conn()
    conn.execute("""
        INSERT INTO predictions
            (ticker,date,prediction_type,price_low,price_mid,price_high,
             signal,confidence,technical_score,fundamental_score,
             sentiment_score,composite_score,strategy_signal)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker,date,prediction_type) DO UPDATE SET
            price_low=excluded.price_low,price_mid=excluded.price_mid,
            price_high=excluded.price_high,signal=excluded.signal,
            confidence=excluded.confidence,technical_score=excluded.technical_score,
            fundamental_score=excluded.fundamental_score,
            sentiment_score=excluded.sentiment_score,
            composite_score=excluded.composite_score,
            strategy_signal=excluded.strategy_signal,
            generated_at=datetime('now')
    """, (result["ticker"],result["date"],result["prediction_type"],
          result.get("price_low"),result.get("price_mid"),result.get("price_high"),
          result["signal"],result["confidence"],result["technical_score"],
          result["fundamental_score"],result["sentiment_score"],
          result["composite_score"],result["strategy_signal"]))
    conn.commit(); conn.close()

# ── Main predict function ─────────────────────────────────────────────────────

def predict(ticker, prediction_type="next_day"):
    ticker = ticker.upper()
    ind    = get_latest_indicators(ticker)
    conn   = get_conn()
    ph     = conn.execute("SELECT close FROM price_history WHERE ticker=? ORDER BY date DESC LIMIT 1",(ticker,)).fetchone()
    st_row = conn.execute("SELECT strategy FROM stocks WHERE ticker=?",(ticker,)).fetchone()
    conn.close()
    if ph: ind["close"] = ph["close"]
    ind["ticker"] = ticker
    strategy = (st_row["strategy"] if st_row else "unassigned") or "unassigned"
    fund     = load_fundamentals(ticker)

    # ── Rules-based scores ────────────────────────────────────────────────────
    ts, tsig = _score_technical(ind, strategy)
    fs, fsig = _score_fundamental(fund)
    ss, ssig = _score_sentiment(ticker)
    composite = round(ts*SCORE_WEIGHT_TECHNICAL + fs*SCORE_WEIGHT_FUNDAMENTAL + ss*SCORE_WEIGHT_SENTIMENT, 1)

    # ── XGBoost ML prediction ─────────────────────────────────────────────────
    ml = None
    try:
        from engine.ml_predictor import predict_ml
        ml_result = predict_ml(ticker)
        # Only use ML if direction accuracy is above 50% (otherwise it hurts)
        if ml_result and float(ml_result.get("val_accuracy") or 0) >= 50:
            ml = ml_result
        elif ml_result:
            logger.info("ML model for %s has low accuracy (%.1f%%) — using rules only",
                        ticker, ml_result.get("val_accuracy", 0))
    except Exception as e:
        logger.debug("ML prediction skipped for %s: %s", ticker, e)

    # ── Blend signals ─────────────────────────────────────────────────────────
    if ml:
        # Convert bullish_prob (0-100) to score (0-100) and blend 50/50
        ml_score      = ml["bullish_prob"]
        final_score   = round(composite * 0.5 + ml_score * 0.5, 1)
    else:
        final_score   = composite

    signal   = "BULLISH" if final_score>=60 else ("BEARISH" if final_score<=40 else "NEUTRAL")
    conf     = round(abs(final_score-50)*2, 1)
    strat_sig= _strategy_alignment(strategy, tsig)

    # ── Price range: blend ATR-based + ML price ───────────────────────────────
    pr = _price_range(ticker, ind, final_score)
    if ml and pr.get("price_mid"):
        blended_mid    = round(pr["price_mid"]*0.4 + ml["predicted_price"]*0.6, 4)
        atr            = ind.get("atr") or (pr["last_close"]*0.015)
        pr["price_mid"]  = blended_mid
        pr["price_low"]  = round(blended_mid - atr*0.8, 4)
        pr["price_high"] = round(blended_mid + atr*0.8, 4)

    result = {
        "ticker":ticker,"date":date.today().isoformat(),"prediction_type":prediction_type,
        "signal":signal,"confidence":conf,
        "composite_score":final_score,
        "rules_score":composite,
        "technical_score":ts,"fundamental_score":fs,"sentiment_score":ss,
        "strategy":strategy,"strategy_signal":strat_sig,
        "tech_signals":tsig,"fund_signals":fsig,"sent_signals":ssig,
        "ml": ml,
        **pr
    }
    _save(result)
    return result
