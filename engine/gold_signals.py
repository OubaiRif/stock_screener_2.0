"""
engine/gold_signals.py — Gold-specific swing trade and macro hold signals.
Combines technical indicators on spot gold + macro environment.
"""
import logging, sys, os
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.db import get_conn
from engine.etf_signals import get_latest_macro, compute_gold_signal, _momentum

logger = logging.getLogger(__name__)

SPOT_TICKER = "GC=F"   # Spot gold futures
IAU_TICKER  = "IAU"

# ── Position management ───────────────────────────────────────────────────────

def get_position(ticker="IAU") -> dict:
    """Load current position from gold_position table."""
    conn = get_conn()
    row  = conn.execute(
        "SELECT * FROM gold_position WHERE ticker=?", (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {"ticker": ticker, "shares": 0, "avg_cost": 0}


def log_trade(action: str, shares: float, price: float,
              ticker: str = "IAU", notes: str = "") -> dict:
    """
    Log a buy or sell trade and update position.
    Returns updated position dict.
    """
    action = action.upper()
    pos    = get_position(ticker)
    cur_shares   = pos["shares"]
    cur_avg_cost = pos["avg_cost"]

    if action == "BUY":
        new_shares   = cur_shares + shares
        # Weighted average cost
        new_avg_cost = ((cur_shares * cur_avg_cost) + (shares * price)) / new_shares \
                       if new_shares > 0 else price
    elif action == "SELL":
        new_shares   = max(0, cur_shares - shares)
        new_avg_cost = cur_avg_cost   # avg cost stays the same on sell
    else:
        raise ValueError(f"Invalid action: {action}. Use BUY or SELL.")

    total_value = shares * price

    conn = get_conn()
    # Log trade
    conn.execute("""
        INSERT INTO gold_trades
            (ticker, action, shares, price, total_value, shares_after, avg_cost_after, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, action, shares, price, total_value,
          new_shares, round(new_avg_cost, 4), notes))
    # Update position
    conn.execute("""
        INSERT INTO gold_position (ticker, shares, avg_cost)
        VALUES (?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            shares=excluded.shares,
            avg_cost=excluded.avg_cost,
            updated_at=datetime('now')
    """, (ticker, new_shares, round(new_avg_cost, 4)))
    conn.commit()
    conn.close()

    logger.info("Trade logged: %s %s shares @ $%.2f | New position: %s shares @ $%.2f avg",
                action, shares, price, new_shares, new_avg_cost)
    return {"ticker": ticker, "shares": new_shares, "avg_cost": round(new_avg_cost, 4)}


def get_trade_history(ticker: str = "IAU", limit: int = 20) -> list:
    """Return recent trade history."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM gold_trades WHERE ticker=?
        ORDER BY traded_at DESC LIMIT ?
    """, (ticker, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_current_price(ticker: str) -> float | None:
    """Get latest close price from price_history DB."""
    conn = get_conn()
    row  = conn.execute(
        "SELECT close FROM price_history WHERE ticker=? ORDER BY date DESC LIMIT 1",
        (ticker,)
    ).fetchone()
    conn.close()
    return row["close"] if row else None
    
def get_live_price(ticker: str) -> float | None:
    """Fetch live/delayed price directly from yfinance, works outside US hours."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        price = t.fast_info.get("last_price") or t.fast_info.get("previous_close")
        return round(float(price), 2) if price else None
    except Exception:
        return None


def compute_pnl(position: dict, current_price: float) -> dict:
    """Compute unrealized P&L for a position."""
    shares    = position.get("shares", 0)
    avg_cost  = position.get("avg_cost", 0)
    cost_basis = shares * avg_cost
    cur_value  = shares * current_price
    pnl        = cur_value - cost_basis
    pnl_pct    = (pnl / cost_basis * 100) if cost_basis > 0 else 0
    return {
        "shares":      shares,
        "avg_cost":    avg_cost,
        "cost_basis":  round(cost_basis, 2),
        "current_value": round(cur_value, 2),
        "pnl":         round(pnl, 2),
        "pnl_pct":     round(pnl_pct, 2),
        "break_even":  avg_cost,
    }


# ── Gold price data ───────────────────────────────────────────────────────────

def fetch_gold_history(days: int = 365) -> pd.DataFrame:
    """Fetch spot gold price history."""
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    df    = yf.Ticker(SPOT_TICKER).history(start=start, auto_adjust=True)
    if df.empty: return df
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def get_gold_from_db(days: int = 365) -> pd.DataFrame:
    """Load spot gold from macro_data table."""
    conn  = get_conn()
    rows  = conn.execute("""
        SELECT date, value as close FROM macro_data
        WHERE series_id=? ORDER BY date DESC LIMIT ?
    """, (SPOT_TICKER, days)).fetchall()
    conn.close()
    if not rows: return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    return df


# ── Swing trade signal ────────────────────────────────────────────────────────

def compute_swing_signal(df: pd.DataFrame) -> dict:
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"signal": "NO DATA", "score": 50}
    if ta is None:
        return {"signal": "HOLD", "score": 50, "note": "indicators unavailable in demo mode"}
    """
    Compute swing trade signal from spot gold technicals.
    df: DataFrame with Close column indexed by date.
    Returns signal dict with score, entry/target/stop zones.
    """
    if df.empty or len(df) < 30:
        return {"signal": "INSUFFICIENT DATA", "score": 50}

    try:
        import pandas_ta as ta
    except ImportError:
        ta = None

    close  = df["Close"] if "Close" in df.columns else df["close"]
    high   = df["High"]  if "High"  in df.columns else close
    low    = df["Low"]   if "Low"   in df.columns else close
    volume = df["Volume"] if "Volume" in df.columns else None

    score  = 50
    bull   = []
    bear   = []

    # RSI
    rsi = ta.rsi(close, length=14)
    rsi_val = float(rsi.iloc[-1]) if rsi is not None and not rsi.empty else None
    if rsi_val:
        if rsi_val < 35:
            score += 15; bull.append(f"RSI oversold ({rsi_val:.1f}/100) — bounce candidate")
        elif rsi_val < 45:
            score += 8;  bull.append(f"RSI approaching oversold ({rsi_val:.1f}/100)")
        elif rsi_val > 65:
            score -= 12; bear.append(f"RSI overbought ({rsi_val:.1f}/100) — caution")
        elif rsi_val > 55:
            score -= 5;  bear.append(f"RSI elevated ({rsi_val:.1f}/100)")

    # MACD
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    macd_val, macd_sig = None, None
    if macd_df is not None and not macd_df.empty:
        cols     = list(macd_df.columns)
        macd_col = next((c for c in cols if "MACD_" in c and "MACDh" not in c and "MACDs" not in c), cols[0])
        sig_col  = next((c for c in cols if "MACDs" in c), cols[2])
        macd_val = float(macd_df[macd_col].iloc[-1])
        macd_sig = float(macd_df[sig_col].iloc[-1])
        if macd_val > macd_sig:
            score += 10; bull.append("MACD bullish crossover — upward momentum")
        else:
            score -= 10; bear.append("MACD bearish — downward momentum")

    # Bollinger Bands
    bb = ta.bbands(close, length=20, std=2.0)
    bb_pct_b = None
    if bb is not None and not bb.empty:
        bb_upper = float(bb.iloc[:, 2].iloc[-1])
        bb_lower = float(bb.iloc[:, 0].iloc[-1])
        bb_mid   = float(bb.iloc[:, 1].iloc[-1])
        cur      = float(close.iloc[-1])
        if bb_upper != bb_lower:
            bb_pct_b = (cur - bb_lower) / (bb_upper - bb_lower)
            if bb_pct_b < 0.1:
                score += 12; bull.append(f"Price near lower Bollinger Band ({bb_pct_b:.2f}) — oversold zone")
            elif bb_pct_b > 0.9:
                score -= 12; bear.append(f"Price near upper Bollinger Band ({bb_pct_b:.2f}) — overbought zone")

    # EMA alignment
    ema_20  = float(ta.ema(close, length=20).iloc[-1])  if len(close) >= 20  else None
    ema_50  = float(ta.ema(close, length=50).iloc[-1])  if len(close) >= 50  else None
    ema_200 = float(ta.ema(close, length=200).iloc[-1]) if len(close) >= 200 else None
    cur_price = get_live_price(SPOT_TICKER) or float(close.iloc[-1])
    if ema_20 and ema_50:
        if cur_price > ema_20 > ema_50:
            score += 8; bull.append("Price above EMA 20 and 50 — bullish trend structure")
        elif cur_price < ema_20 < ema_50:
            score -= 8; bear.append("Price below EMA 20 and 50 — bearish trend structure")
    if ema_200:
        if cur_price > ema_200:
            score += 5; bull.append(f"Price above 200-day EMA (${ema_200:.2f}) — long-term uptrend intact")
        else:
            score -= 10; bear.append(f"Price below 200-day EMA (${ema_200:.2f}) — long-term trend broken")

    # ATR for entry/stop zone
    atr = ta.atr(high, low, close, length=14)
    atr_val = float(atr.iloc[-1]) if atr is not None and not atr.empty else cur_price * 0.015

    # Support/Resistance
    support_20d    = float(low.rolling(20).min().iloc[-1])
    resistance_20d = float(high.rolling(20).max().iloc[-1])
    support_50d    = float(low.rolling(50).min().iloc[-1])  if len(low) >= 50 else support_20d

    # Entry/Target/Stop calculation
    entry_low  = round(support_20d, 2)
    entry_high = round(support_20d + atr_val, 2)
    target     = round(resistance_20d, 2)
    stop_loss  = round(support_20d - atr_val * 1.5, 2)
    risk       = cur_price - stop_loss
    reward     = target - cur_price
    rr_ratio   = round(reward / risk, 1) if risk > 0 else 0

    score = max(0, min(100, score))
    if score >= 60:   signal = "BUY"
    elif score >= 45: signal = "HOLD / WAIT"
    else:             signal = "AVOID / SELL"

    return {
        "signal":        signal,
        "score":         score,
        "confidence":    round(abs(score - 50) * 2, 1),
        "current_price": round(cur_price, 2),
        "entry_low":     entry_low,
        "entry_high":    entry_high,
        "target":        target,
        "stop_loss":     stop_loss,
        "rr_ratio":      rr_ratio,
        "rsi":           round(rsi_val, 1) if rsi_val else None,
        "macd_bull":     macd_val > macd_sig if macd_val and macd_sig else None,
        "bb_pct_b":      round(bb_pct_b, 3) if bb_pct_b else None,
        "ema_20":        round(ema_20, 2) if ema_20 else None,
        "ema_50":        round(ema_50, 2) if ema_50 else None,
        "ema_200":       round(ema_200, 2) if ema_200 else None,
        "atr":           round(atr_val, 2),
        "support_20d":   round(support_20d, 2),
        "resistance_20d":round(resistance_20d, 2),
        "bull":          bull,
        "bear":          bear,
    }


# ── Macro hold signal ─────────────────────────────────────────────────────────

def compute_macro_hold_signal(macro: dict) -> dict:
    """
    Compute macro hold signal — is the gold thesis (dollar/inflation hedge) intact?
    Same scoring style as swing signal for consistency.
    """
    from engine.etf_signals import _val, _trend

    score = 50
    bull  = []
    bear  = []

    # Real rates (most important)
    real_rate  = _val(macro, "DFII10")
    rate_trend = _trend(macro, "DFII10")
    if real_rate is not None:
        if real_rate < 0:
            score += 15; bull.append(f"Real rates negative ({real_rate:.2f}%) — strongest gold environment")
        elif real_rate < 1.0:
            score += 8;  bull.append(f"Real rates low ({real_rate:.2f}%) — supportive for gold")
        elif real_rate < 2.0:
            score += 2;  bull.append(f"Real rates moderate ({real_rate:.2f}%) — neutral for gold")
        elif real_rate < 3.0:
            score -= 8;  bear.append(f"Real rates elevated ({real_rate:.2f}%) — headwind for gold")
        else:
            score -= 15; bear.append(f"Real rates very high ({real_rate:.2f}%) — strong headwind for gold")
        if rate_trend == "Falling":
            score += 10; bull.append("Real rates falling — increasing gold attractiveness over time")
        elif rate_trend == "Rising":
            score -= 10; bear.append("Real rates rising — increasing pressure on gold thesis")

    # Inflation
    inflation = _val(macro, "T10YIE")
    inf_trend = _trend(macro, "T10YIE")
    if inflation:
        if inflation > 3.0:
            score += 12; bull.append(f"Inflation elevated ({inflation:.2f}%) — strong case for gold hedge")
        elif inflation > 2.0:
            score += 6;  bull.append(f"Inflation above Fed target ({inflation:.2f}%) — supports gold hedge thesis")
        elif inflation < 1.5:
            score -= 8;  bear.append(f"Inflation below target ({inflation:.2f}%) — weakens gold hedge case")
        if inf_trend == "Rising":
            score += 6;  bull.append("Inflation trending up — strengthens gold thesis")
        elif inf_trend == "Falling":
            score -= 6;  bear.append("Inflation falling — weakens gold thesis")

    # USD
    usd_trend = _trend(macro, "DTWEXBGS")
    usd_val   = _val(macro, "DTWEXBGS")
    if usd_trend == "Falling":
        score += 8;  bull.append(f"USD weakening ({usd_val:.1f}) — dollar losing value supports gold")
    elif usd_trend == "Rising":
        score -= 8;  bear.append(f"USD strengthening ({usd_val:.1f}) — dollar strength pressures gold")

    # Credit spreads (risk-off = gold demand)
    hy = _val(macro, "BAMLH0A0HYM2")
    if hy:
        if hy > 5:
            score += 8;  bull.append(f"High credit spreads ({hy:.2f}%) — risk-off favors gold")
        elif hy < 3:
            score -= 4;  bear.append(f"Tight credit spreads ({hy:.2f}%) — risk-on reduces gold demand")

    # GDX lead
    gdx_mom = _momentum("GDX", 10)
    if gdx_mom is not None:
        if gdx_mom > 3:
            score += 8;  bull.append(f"Gold miners up {gdx_mom:.1f}% — positive lead for gold")
        elif gdx_mom < -3:
            score -= 8;  bear.append(f"Gold miners down {gdx_mom:.1f}% — negative lead for gold")

    # Consumer sentiment (low = stress = gold demand)
    sent = _val(macro, "UMCSENT")
    if sent:
        if sent < 60:
            score += 6;  bull.append(f"Consumer sentiment very low ({sent:.0f}) — economic stress supports gold")
        elif sent > 85:
            score -= 4;  bear.append(f"Consumer sentiment high ({sent:.0f}) — confidence reduces gold demand")

    # Fix USD exit threshold — 120+ is normal range, use 128 as meaningful threshold
    exit_conditions = []
    if real_rate and real_rate > 3.0:
        exit_conditions.append(f"Real rates at {real_rate:.2f}% — above 3% exit threshold")
    if usd_val and usd_val > 128:
        exit_conditions.append(f"USD at {usd_val:.1f} — above 128 exit threshold (significant strengthening)")
    if inflation and inflation < 1.5:
        exit_conditions.append(f"Inflation at {inflation:.2f}% — below 1.5% exit threshold")

    score = max(0, min(100, score))

    # Granular signal with 5 levels
    if score >= 75:
        signal     = "STRONG ADD"
        signal_note = "Multiple macro factors aligned — add aggressively"
    elif score >= 60:
        signal     = "ADD"
        signal_note = "Macro supports gold — consider adding to position"
    elif score >= 45:
        signal     = "HOLD"
        signal_note = "Thesis intact but mixed — maintain current position"
    elif score >= 30:
        signal     = "REDUCE"
        signal_note = "Thesis weakening — consider taking partial profit if in profit"
    else:
        signal     = "EXIT"
        signal_note = "Thesis broken — close or significantly reduce position"

    return {
        "signal":           signal,
        "signal_note":      signal_note,
        "score":            score,
        "confidence":       round(abs(score - 50) * 2, 1),
        "real_rate":        real_rate,
        "rate_trend":       rate_trend,
        "inflation":        inflation,
        "usd_trend":        usd_trend,
        "usd_val":          usd_val,
        "hy_spread":        hy,
        "gdx_mom":          gdx_mom,
        "consumer_sent":    sent,
        "bull":             bull,
        "bear":             bear,
        "exit_conditions":  exit_conditions,
    }


# ── Action recommendations ────────────────────────────────────────────────────

def get_action_recommendations(swing: dict, macro: dict, pnl: dict) -> dict:
    """
    Generate specific add/take profit/close recommendations
    based on swing signal, macro signal, and current position.
    """
    add_triggers    = []
    profit_triggers = []
    close_triggers  = []

    # Add triggers
    if swing.get("rsi") and swing["rsi"] < 35:
        add_triggers.append(f"RSI oversold at {swing['rsi']:.1f} — strong entry signal")
    if swing.get("entry_low") and swing.get("entry_high"):
        add_triggers.append(f"Price in entry zone ${swing['entry_low']:.2f}–${swing['entry_high']:.2f}")
    if macro.get("gdx_mom") and macro["gdx_mom"] > 3:
        add_triggers.append(f"GDX miners recovering +{macro['gdx_mom']:.1f}% — positive lead")
    add_triggers.append("Real rates start falling (watch DFII10 trend)")

    # Profit triggers
    if swing.get("rsi") and swing["rsi"] > 65:
        profit_triggers.append(f"RSI overbought at {swing['rsi']:.1f} — consider partial profit")
    if swing.get("resistance_20d"):
        profit_triggers.append(f"Price approaching 20-day resistance ${swing['resistance_20d']:.2f}")
    if swing.get("target"):
        profit_triggers.append(f"Price reaches swing target ${swing['target']:.2f}")
    profit_triggers.append("Gold/SPY ratio spikes (gold outperforming strongly)")

    # Close triggers (thesis-based)
    if macro.get("exit_conditions"):
        close_triggers.extend(macro["exit_conditions"])
    if swing.get("ema_200"):
        close_triggers.append(
            f"Gold breaks below 200-day EMA (${swing['ema_200']:.2f}) with high volume")
    close_triggers.append("Fed signals aggressive rate hike cycle restart")

    # Break-even analysis
    break_even_iau   = pnl.get("avg_cost", 0)
    cur_price_iau    = pnl.get("current_value", 0) / pnl.get("shares", 1) if pnl.get("shares") else 0
    distance_to_be   = break_even_iau - cur_price_iau if cur_price_iau else None

    return {
        "add_triggers":    add_triggers,
        "profit_triggers": profit_triggers,
        "close_triggers":  close_triggers,
        "break_even_iau":  break_even_iau,
        "distance_to_be":  round(distance_to_be, 2) if distance_to_be else None,
    }

def get_position_aware_recommendations(swing: dict, macro: dict, pnl: dict) -> dict:
    """
    Position-aware recommendations that consider both macro signal AND current P&L.
    Replaces the old get_action_recommendations.
    """
    add_triggers    = []
    reduce_triggers = []
    close_triggers  = []

    macro_score  = macro.get("score", 50)
    macro_signal = macro.get("signal", "HOLD")
    pnl_pct      = pnl.get("pnl_pct", 0)
    avg_cost     = pnl.get("avg_cost", 0)
    shares       = pnl.get("shares", 0)
    cur_price_iau = pnl.get("current_value", 0) / shares if shares else 0
    in_loss      = pnl_pct < 0

    # ── Position context note ─────────────────────────────────────────────────
    if in_loss and macro_signal == "REDUCE":
        context_note = (f"Macro score suggests reducing, but you are at "
                        f"{pnl_pct:.1f}% unrealized loss. Selling now locks in that loss. "
                        f"Only reduce if the thesis is truly breaking down (score below 30). "
                        f"Consider holding until break-even or a technical bounce first.")
    elif in_loss and macro_signal in ("STRONG ADD", "ADD"):
        context_note = (f"You are at {pnl_pct:.1f}% unrealized loss. "
                        f"Macro supports gold — adding here would lower your average cost "
                        f"from ${avg_cost:.2f}. Only add what you can hold long-term.")
    elif in_loss and macro_signal == "HOLD":
        context_note = (f"You are at {pnl_pct:.1f}% unrealized loss and macro is neutral. "
                        f"Do not sell at this level — you would lock in a ${abs(pnl.get('pnl',0)):,.0f} loss. "
                        f"Wait for either macro to improve (score above 60) to add, "
                        f"or a technical bounce toward break-even (${avg_cost:.2f}) to reduce.")
    elif not in_loss and macro_signal in ("REDUCE", "EXIT"):
        context_note = (f"You are in profit (+{pnl_pct:.1f}%). "
                        f"Macro is weakening — good time to take partial profit and protect gains.")
    elif not in_loss and macro_signal in ("STRONG ADD", "ADD"):
        context_note = (f"You are in profit (+{pnl_pct:.1f}%) and macro supports gold. "
                        f"You can hold or add — consider your total exposure carefully.")
    elif not in_loss and macro_signal == "HOLD":
        context_note = (f"You are in profit (+{pnl_pct:.1f}%) and macro is neutral. "
                        f"Hold your position. Consider taking partial profit if spot gold "
                        f"reaches the swing target or RSI goes above 65.")
    else:
        context_note = (f"Current P&L: {pnl_pct:+.1f}%. "
                        f"Macro signal: {macro_signal}. Hold and monitor conditions.")

    # ── Add triggers ──────────────────────────────────────────────────────────
    if macro_score >= 60:
        add_triggers.append(f"Macro score {macro_score}/100 — environment supports gold")
    if swing.get("rsi") and swing["rsi"] < 35:
        add_triggers.append(f"RSI oversold at {swing['rsi']:.1f}/100 — strong technical entry")
    if swing.get("entry_low") and swing.get("entry_high"):
        add_triggers.append(
            f"Spot gold in entry zone ${swing['entry_low']:.0f}–${swing['entry_high']:.0f}")
    if macro.get("gdx_mom") and macro["gdx_mom"] > 3:
        add_triggers.append(f"GDX miners recovering +{macro['gdx_mom']:.1f}% — positive lead signal")
    if macro.get("rate_trend") == "Falling":
        add_triggers.append("Real rates falling — gold environment improving")
    if in_loss and avg_cost > 0 and cur_price_iau > 0:
        target_avg = avg_cost * 0.9
        add_triggers.append(
            f"Adding at current price ${cur_price_iau:.2f} would lower avg cost toward ${target_avg:.2f}")

    # ── Reduce triggers ───────────────────────────────────────────────────────
    if macro_score <= 45 and not in_loss:
        reduce_triggers.append(f"Macro score declining ({macro_score}/100) — consider protecting gains")
    if swing.get("rsi") and swing["rsi"] > 65:
        reduce_triggers.append(f"RSI overbought at {swing['rsi']:.1f}/100 — technical sell signal")
    if swing.get("resistance_20d"):
        reduce_triggers.append(
            f"Spot gold approaching 20-day resistance ${swing['resistance_20d']:.0f}")
    if swing.get("target"):
        reduce_triggers.append(
            f"Spot gold reaches swing target ${swing['target']:.0f} — take partial profit")
    reduce_triggers.append("Gold/SPY ratio spikes — gold outperforming at extremes")
    if not in_loss and shares > 0:
        recover_shares = int(pnl.get("cost_basis", 0) / cur_price_iau) if cur_price_iau else 0
        if recover_shares > 0 and recover_shares < shares:
            reduce_triggers.append(
                f"Sell {shares - recover_shares:.0f} shares to recover original investment, "
                f"keep {recover_shares:.0f} shares as free position")

    # ── Close triggers ────────────────────────────────────────────────────────
    if macro.get("exit_conditions"):
        close_triggers.extend(macro["exit_conditions"])
    if swing.get("ema_200"):
        close_triggers.append(
            f"Spot gold breaks below 200-day EMA (${swing['ema_200']:.0f}) with high volume")
    close_triggers.append("Federal Reserve signals aggressive new rate hike cycle")
    close_triggers.append("Real rates rise above 3% — gold loses appeal vs bonds")

    break_even_iau = avg_cost
    distance_to_be = round(break_even_iau - cur_price_iau, 2) if cur_price_iau else None

    return {
        "add_triggers":    add_triggers,
        "reduce_triggers": reduce_triggers,
        "close_triggers":  close_triggers,
        "context_note":    context_note,
        "break_even_iau":  break_even_iau,
        "distance_to_be":  distance_to_be,
        "in_loss":         in_loss,
        "pnl_pct":         pnl_pct,
    }
