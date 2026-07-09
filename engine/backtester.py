"""
engine/backtester.py — Historical signal backtesting engine.

Simulates trading based on the system's composite signals over historical data.
Entry: next day open after signal fires.
Exit: next day open after signal reverses.
No lookahead bias on price data — each day only uses data available up to that point.

Note on indicator parameters: RSI 14, EMA 20/50/200 etc. were chosen with some
knowledge of what works historically, so results will be slightly optimistic vs
live trading. Use results to compare strategies against each other and against
buy-and-hold, not as precise future return predictions.
"""
import logging, sys, os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pandas_ta as ta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.db import get_conn

logger = logging.getLogger(__name__)


# ── Data loading ──────────────────────────────────────────────────────────────

def load_price_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Load OHLCV from DB for a date range."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM   price_history
        WHERE  ticker = ? AND date >= ? AND date <= ?
        ORDER  BY date ASC
    """, (ticker.upper(), start, end)).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.rename(columns={"open":"Open","high":"High","low":"Low",
                        "close":"Close","volume":"Volume"}, inplace=True)
    return df


def compute_signals_history(df: pd.DataFrame, strategy: str = "unassigned") -> pd.Series:
    """
    Compute daily composite signal score for the full price history.
    Returns a Series of scores (0-100) indexed by date.
    Only uses data available up to each point in time (no lookahead).
    """
    if df.empty or len(df) < 50:
        return pd.Series(dtype=float)

    close  = df["Close"]
    high   = df["High"]
    low    = df["Low"]
    volume = df["Volume"]

    # Compute all indicators on full history
    rsi     = ta.rsi(close, length=14)
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    ema_20  = ta.ema(close, length=20)
    ema_50  = ta.ema(close, length=50)
    ema_200 = ta.ema(close, length=200)
    bb      = ta.bbands(close, length=20, std=2.0)
    atr     = ta.atr(high, low, close, length=14)
    obv     = ta.obv(close, volume)

    # Extract MACD columns by name
    macd_val, macd_sig = None, None
    if macd_df is not None and not macd_df.empty:
        cols     = list(macd_df.columns)
        macd_col = next((c for c in cols if "MACD_" in c and "MACDh" not in c and "MACDs" not in c), cols[0])
        sig_col  = next((c for c in cols if "MACDs" in c), cols[2])
        macd_val = macd_df[macd_col]
        macd_sig = macd_df[sig_col]

    bb_pct_b = None
    if bb is not None and not bb.empty:
        bb_upper = bb.iloc[:, 2]
        bb_lower = bb.iloc[:, 0]
        denom    = bb_upper - bb_lower
        bb_pct_b = (close - bb_lower) / denom.replace(0, np.nan)

    avg_vol  = volume.rolling(20).mean()
    rel_vol  = volume / avg_vol.replace(0, np.nan)
    zscore   = (close - close.rolling(20).mean()) / close.rolling(20).std()

    scores = pd.Series(index=df.index, dtype=float)

    for i in range(len(df)):
        if i < 30:   # Not enough history
            scores.iloc[i] = 50.0
            continue

        ws, wt = 0.0, 0.0

        def vote(val, bull_fn, bear_fn, weight):
            nonlocal ws, wt
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return
            v = 1 if bull_fn(val) else (-1 if bear_fn(val) else 0)
            ws += v * weight
            wt += weight

        cur   = close.iloc[i]
        e20   = ema_20.iloc[i]  if ema_20  is not None else None
        e50   = ema_50.iloc[i]  if ema_50  is not None else None
        e200  = ema_200.iloc[i] if ema_200 is not None else None
        rsi_v = rsi.iloc[i]     if rsi     is not None else None
        mv    = macd_val.iloc[i] if macd_val is not None else None
        ms    = macd_sig.iloc[i] if macd_sig is not None else None
        bb_v  = bb_pct_b.iloc[i] if bb_pct_b is not None else None
        zs    = zscore.iloc[i]
        rv    = rel_vol.iloc[i]

        # Strategy-weighted scoring
        ema_w  = 2.0 if strategy == "trend"           else 1.0
        rsi_w  = 2.0 if strategy in ("mean_reversion","rubber_band") else 1.0
        bb_w   = 2.0 if strategy in ("mean_reversion","rubber_band") else 1.0

        # EMA stack
        if all(v is not None and not np.isnan(v) for v in [cur, e20, e50, e200]):
            v = 1 if cur>e20>e50>e200 else (-1 if cur<e20<e50<e200 else 0)
            ws += v * ema_w; wt += ema_w

        # MACD
        if mv is not None and ms is not None and not np.isnan(mv) and not np.isnan(ms):
            v = 1 if mv > ms else -1
            ws += v * 1.5; wt += 1.5

        # RSI
        vote(rsi_v, lambda v: v < 35, lambda v: v > 65, rsi_w)

        # Bollinger Bands %B
        vote(bb_v, lambda v: v < 0.1, lambda v: v > 0.9, bb_w)

        # Z-Score
        vote(zs, lambda v: v < -1.5, lambda v: v > 1.5, 0.5)

        # Relative Volume
        vote(rv, lambda v: v > 1.5, lambda v: v < 0.5, 0.8)

        score = (ws / wt + 1) / 2 * 100 if wt > 0 else 50.0
        scores.iloc[i] = round(score, 1)

    return scores


# ── Trade simulation ──────────────────────────────────────────────────────────

def simulate_trades(df: pd.DataFrame, scores: pd.Series,
                    position_size_usd: float = 1000.0,
                    buy_threshold: float = 60.0,
                    sell_threshold: float = 40.0,
                    strategy: str = "unassigned",
                    use_trend_filter: bool = True,
                    use_asymmetric_hold: bool = True) -> dict:
    """
    Simulate trades based on signal scores.

    Rules:
      - Score >= buy_threshold  AND not in position → BUY at next open
      - Score <= sell_threshold AND in position     → SELL at next open
      - Entry and exit at next day's open price (realistic)

    Improvements:
      - Trend filter: only buy when S&P 500 is above its 200-day average
        (avoids fighting the broad market in bear regimes)
      - Strategy-specific thresholds: trend stocks use higher sell threshold
        to stay in longer; rubber band uses lower buy threshold for earlier entries
      - Asymmetric hold: once a position is profitable, require a stronger
        sell signal (score 5 points lower) before exiting
    """
    if df.empty or scores.empty:
        return {"trades": [], "portfolio": pd.Series(dtype=float), "error": "No data"}

    # ── Strategy-specific threshold adjustments ───────────────────────────────
    if strategy == "trend":
        # Trend stocks: harder to exit (stay in longer during uptrends)
        effective_sell = sell_threshold - 5   # e.g. 35 instead of 40
    elif strategy in ("rubber_band", "mean_reversion"):
        # Mean-reverting stocks: enter earlier, exit quicker
        effective_buy  = buy_threshold - 5    # e.g. 55 instead of 60
        effective_sell = sell_threshold + 5   # e.g. 45 instead of 40
    else:
        effective_sell = sell_threshold

    effective_buy = buy_threshold if strategy != "rubber_band" else buy_threshold - 5

    # ── Load S&P 500 for trend filter ─────────────────────────────────────────
    sp500_200ema = {}
    if use_trend_filter:
        try:
            conn = get_conn()
            rows = conn.execute("""
                SELECT date, value FROM macro_data WHERE series_id='^GSPC'
                ORDER BY date ASC
            """).fetchall()
            conn.close()
            if rows:
                sp_df = pd.DataFrame([dict(r) for r in rows])
                sp_df["date"] = pd.to_datetime(sp_df["date"])
                sp_df.set_index("date", inplace=True)
                sp_df["ema200"] = sp_df["value"].ewm(span=200, adjust=False).mean()
                sp500_200ema = dict(zip(sp_df.index.strftime("%Y-%m-%d"),
                                       (sp_df["value"] > sp_df["ema200"]).astype(int)))
        except Exception:
            pass   # trend filter optional — skip if data unavailable

    dates      = df.index
    opens      = df["Open"].values
    closes     = df["Close"].values
    score_vals = scores.reindex(dates).fillna(50).values

    trades         = []
    in_position    = False
    entry_price    = 0.0
    entry_date     = None
    shares_held    = 0.0
    cash           = position_size_usd
    portfolio_vals = []

    for i in range(1, len(dates)):
        prev_score  = score_vals[i - 1]
        today_open  = opens[i]
        today_date  = dates[i]
        date_str    = today_date.strftime("%Y-%m-%d")

        # Portfolio value today
        port_val = shares_held * today_open if in_position else cash
        portfolio_vals.append((today_date, port_val))

        # Trend filter: only allow buys when S&P is above 200-day average
        trend_allows_buy = True
        if use_trend_filter and sp500_200ema:
            trend_allows_buy = sp500_200ema.get(date_str, 1) == 1

        # Asymmetric hold: if in profit, require stronger sell signal
        if in_position and use_asymmetric_hold:
            current_return = (today_open - entry_price) / entry_price * 100
            if current_return > 0:
                actual_sell_threshold = effective_sell - 5   # harder to exit when winning
            else:
                actual_sell_threshold = effective_sell
        else:
            actual_sell_threshold = effective_sell

        # Entry
        if not in_position and prev_score >= effective_buy and trend_allows_buy:
            shares_held = cash / today_open
            entry_price = today_open
            entry_date  = today_date
            in_position = True

        # Exit
        elif in_position and prev_score <= actual_sell_threshold:
            exit_price   = today_open
            gross_return = (exit_price - entry_price) / entry_price * 100
            cash         = shares_held * exit_price
            trades.append({
                "entry_date":       entry_date.strftime("%Y-%m-%d"),
                "exit_date":        today_date.strftime("%Y-%m-%d"),
                "entry_price":      round(entry_price, 4),
                "exit_price":       round(exit_price, 4),
                "shares":           round(shares_held, 4),
                "gross_return_pct": round(gross_return, 3),
                "profit_usd":       round((exit_price - entry_price) * shares_held, 2),
                "holding_days":     (today_date - entry_date).days,
                "outcome":          "Win" if gross_return > 0 else "Loss",
            })
            in_position = False
            shares_held = 0.0

    # Close open position at last price
    if in_position and len(closes) > 0:
        exit_price   = closes[-1]
        gross_return = (exit_price - entry_price) / entry_price * 100
        cash         = shares_held * exit_price
        trades.append({
            "entry_date":       entry_date.strftime("%Y-%m-%d"),
            "exit_date":        dates[-1].strftime("%Y-%m-%d") + " (open)",
            "entry_price":      round(entry_price, 4),
            "exit_price":       round(exit_price, 4),
            "shares":           round(shares_held, 4),
            "gross_return_pct": round(gross_return, 3),
            "profit_usd":       round((exit_price - entry_price) * shares_held, 2),
            "holding_days":     (dates[-1] - entry_date).days,
            "outcome":          "Win" if gross_return > 0 else "Loss (open)",
        })

    portfolio = pd.Series(
        [v for _, v in portfolio_vals],
        index=[d for d, _ in portfolio_vals]
    )
    return {"trades": trades, "portfolio": portfolio}


# ── Buy and hold benchmark ────────────────────────────────────────────────────

def buy_and_hold(df: pd.DataFrame, position_size_usd: float = 1000.0) -> dict:
    """
    Simulate a simple buy-and-hold strategy from first to last day.
    Buys at first available open, holds until last close.
    Used as the benchmark to beat.
    """
    if df.empty:
        return {"total_return_pct": 0, "portfolio": pd.Series(dtype=float)}
    first_open  = df["Open"].iloc[0]
    shares      = position_size_usd / first_open
    portfolio   = df["Close"] * shares
    total_return = (df["Close"].iloc[-1] - first_open) / first_open * 100
    return {
        "total_return_pct": round(total_return, 2),
        "portfolio":        portfolio,
        "start_price":      round(first_open, 4),
        "end_price":        round(df["Close"].iloc[-1], 4),
    }


# ── Metrics computation ───────────────────────────────────────────────────────

def compute_metrics(trades: list, portfolio: pd.Series,
                    bah_return: float, position_size_usd: float) -> dict:
    """
    Compute all performance metrics from trade list and portfolio series.
    Returns a comprehensive metrics dict with full names for every field.
    """
    if not trades:
        return {"error": "No completed trades in this period"}

    returns = [t["gross_return_pct"] for t in trades]
    wins    = [t for t in trades if t["outcome"].startswith("Win")]
    losses  = [t for t in trades if t["outcome"].startswith("Loss")]

    # Basic counts
    total_trades  = len(trades)
    winning_trades = len(wins)
    losing_trades  = len(losses)
    win_rate       = round(winning_trades / total_trades * 100, 1) if total_trades else 0

    # Return metrics
    total_profit_usd  = sum(t["profit_usd"] for t in trades)
    avg_win_pct       = round(np.mean([t["gross_return_pct"] for t in wins]), 2) if wins else 0
    avg_loss_pct      = round(np.mean([t["gross_return_pct"] for t in losses]), 2) if losses else 0
    best_trade_pct    = round(max(returns), 2) if returns else 0
    worst_trade_pct   = round(min(returns), 2) if returns else 0
    avg_holding_days  = round(np.mean([t["holding_days"] for t in trades]), 1) if trades else 0

    # Total return of strategy
    start_val    = position_size_usd
    end_val      = portfolio.iloc[-1] if not portfolio.empty else start_val
    total_return = round((end_val - start_val) / start_val * 100, 2)
    alpha        = round(total_return - bah_return, 2)

    # Profit Factor = Total gross profit / Total gross loss
    gross_profit = sum(t["profit_usd"] for t in wins)
    gross_loss   = abs(sum(t["profit_usd"] for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

    # Maximum Drawdown
    if not portfolio.empty:
        rolling_max  = portfolio.cummax()
        drawdown     = (portfolio - rolling_max) / rolling_max * 100
        max_drawdown = round(drawdown.min(), 2)
    else:
        max_drawdown = 0.0

    # Sharpe Ratio (annualized, assumes 252 trading days, risk-free rate ~4%)
    if not portfolio.empty and len(portfolio) > 1:
        daily_returns  = portfolio.pct_change().dropna()
        excess_returns = daily_returns - (0.04 / 252)   # risk-free rate daily
        sharpe = round(excess_returns.mean() / excess_returns.std() * np.sqrt(252), 2) \
                 if excess_returns.std() > 0 else 0.0
    else:
        sharpe = 0.0

    # Expectancy = (Win Rate × Avg Win) + (Loss Rate × Avg Loss)
    loss_rate  = 1 - win_rate / 100
    expectancy = round((win_rate / 100 * avg_win_pct) + (loss_rate * avg_loss_pct), 3)

    return {
        # Trade counts
        "total_trades":       total_trades,
        "winning_trades":     winning_trades,
        "losing_trades":      losing_trades,
        "win_rate_pct":       win_rate,

        # Return metrics
        "total_return_pct":   total_return,
        "buy_hold_return_pct": bah_return,
        "alpha_pct":          alpha,
        "total_profit_usd":   round(total_profit_usd, 2),

        # Per-trade metrics
        "average_win_pct":    avg_win_pct,
        "average_loss_pct":   avg_loss_pct,
        "best_trade_pct":     best_trade_pct,
        "worst_trade_pct":    worst_trade_pct,
        "average_holding_days": avg_holding_days,
        "expectancy_pct":     expectancy,

        # Risk metrics
        "profit_factor":      profit_factor,
        "max_drawdown_pct":   max_drawdown,
        "sharpe_ratio":       sharpe,

        # Portfolio series for charting
        "portfolio":          portfolio,
    }


# ── Full backtest runner ──────────────────────────────────────────────────────

def run_backtest(ticker: str, strategy: str = "unassigned",
                 start: str = None, end: str = None,
                 position_size_usd: float = 1000.0,
                 buy_threshold: float = 60.0,
                 sell_threshold: float = 40.0,
                 use_trend_filter: bool = True,
                 use_asymmetric_hold: bool = True) -> dict:
    """
    Run a full backtest for a single ticker.

    Parameters:
        ticker               : Stock or ETF ticker symbol
        strategy             : Strategy tag — affects indicator weights and thresholds
        start                : Start date YYYY-MM-DD (default: 2 years ago)
        end                  : End date YYYY-MM-DD (default: today)
        position_size_usd    : Dollar amount per trade
        buy_threshold        : Score above which we buy (0-100)
        sell_threshold       : Score below which we sell (0-100)
        use_trend_filter     : Only buy when S&P 500 is above its 200-day average
        use_asymmetric_hold  : Require stronger sell signal when position is profitable
    """
    if start is None:
        start = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")
    if end is None:
        end = datetime.today().strftime("%Y-%m-%d")

    logger.info("Running backtest for %s (%s to %s)", ticker, start, end)

    df = load_price_history(ticker, start, end)
    if df.empty:
        return {"error": f"No price data for {ticker} in date range {start} to {end}"}
    if len(df) < 60:
        return {"error": f"Insufficient data for {ticker} — need at least 60 trading days"}

    scores  = compute_signals_history(df, strategy)
    bah     = buy_and_hold(df, position_size_usd)
    sim     = simulate_trades(df, scores, position_size_usd, buy_threshold, sell_threshold,
                               strategy, use_trend_filter, use_asymmetric_hold)
    if "error" in sim:
        return sim

    metrics = compute_metrics(sim["trades"], sim["portfolio"],
                              bah["total_return_pct"], position_size_usd)

    return {
        "ticker":             ticker,
        "strategy":           strategy,
        "start_date":         start,
        "end_date":           end,
        "position_size_usd":  position_size_usd,
        "buy_threshold":      buy_threshold,
        "sell_threshold":     sell_threshold,
        "use_trend_filter":   use_trend_filter,
        "use_asymmetric_hold":use_asymmetric_hold,
        "trading_days":       len(df),
        "trades":             sim["trades"],
        "portfolio":          sim["portfolio"],
        "buy_hold_portfolio": bah["portfolio"],
        "buy_hold_return":    bah["total_return_pct"],
        **metrics,
    }


def run_backtest_all(tickers_strategies: list, **kwargs) -> dict:
    """
    Run backtest for multiple tickers.
    tickers_strategies: list of dicts with ticker and strategy keys.
    Returns dict keyed by ticker.
    """
    results = {}
    for item in tickers_strategies:
        ticker   = item["ticker"]
        strategy = item.get("strategy", "unassigned")
        try:
            results[ticker] = run_backtest(ticker, strategy, **kwargs)
            logger.info("Backtest complete for %s", ticker)
        except Exception as e:
            logger.error("Backtest failed for %s: %s", ticker, e)
            results[ticker] = {"error": str(e)}
    return results
