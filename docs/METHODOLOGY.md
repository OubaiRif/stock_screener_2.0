# Stock Screener 2.5 — Prediction Methodology

This document explains how the screener generates predictions, scores accuracy, and manages risk. It is written for a technically literate reader who wants to understand what the system is actually doing, not just what it claims.

---

## 1. Two-Horizon Design

The system generates two distinct prediction types per ticker each night.

**next_day** is a price and volatility estimate for the next trading session. It uses technical indicators only — fundamentals and sentiment are computed and stored for display but carry zero weight in the composite. The reasoning: P/E ratios and FinBERT scores have no predictive edge on a 24-hour price move. The next_day band is an ATR-anchored range showing where price is most likely to trade, not a directional bet.

**swing_Nd** is a directional prediction over a strategy-appropriate horizon — 5 days for mean-reversion and rubber-band strategies, 7 days for mean_reversion, 20 days for trend. It uses the full three-component composite (technical + fundamental + sentiment). At swing horizons, fundamentals and sentiment carry real signal: a company with deteriorating margins or negative news flow is more likely to underperform over a week or more than it is to gap down tomorrow. The horizon mapping lives in `STRATEGY_HORIZONS` in `config.py` and is the only reserved judgment call the user controls.

---

## 2. Technical Scoring

`_score_technical(ind, strategy)` produces a score from 0–100. Each indicator casts a vote of +1 (bullish), 0 (neutral), or -1 (bearish), weighted by a strategy-specific multiplier from the `_W` table. The final score is `(weighted_sum / total_weight + 1) / 2 * 100`, mapping the [-1, +1] range to [0, 100].

### Indicators and vote conditions

| Indicator | Bullish vote | Bearish vote | Weight source |
|-----------|-------------|--------------|---------------|
| EMA Stack | close > EMA20 > EMA50 > EMA200 | close < EMA20 < EMA50 < EMA200 | `ema` key |
| MACD | MACD line > signal | MACD line < signal | `macd` key |
| ADX | ADX > 25 (trend strategies only) | — | fixed 1.0 |
| RSI | RSI < 35 | RSI > 65 | `rsi` key |
| BB %B | BB %B < 0.1 | BB %B > 0.9 | `bb` key |
| Z-Score | Z < -1.5 | Z > 1.5 | `z` key |
| Stochastic %K | %K < 20 | %K > 80 | fixed 1.0 |
| Williams %R | W%R < -80 | W%R > -20 | `wr` key |
| Relative Volume | RelVol > 1.5x | RelVol < 0.5x | fixed 0.8 |
| OBV vs EMA | OBV > OBV EMA | OBV < OBV EMA | fixed 1.0 |
| Breakout (breakout_volume only) | Price > resistance on 1.5x+ vol | Low volume — no conviction | fixed 2.5 |

### Trend regime suppression

Mean-reversion indicators (RSI, BB %B, Z-Score, Williams %R) are suppressed when the ticker is in a confirmed trend. Specifically:

- **Oversold bullish votes** are suppressed when `close < EMA-200` (downtrend). A stock can be oversold for months in a freefall — the signal adds noise, not edge.
- **Overbought bearish votes** are suppressed when `close > EMA-200` (uptrend). Strong uptrending stocks stay overbought; the signal generates false exits.

When suppressed, the vote becomes 0 (neutral), not -1. The signal note records "Oversold but downtrend — vote suppressed" so the UI explains the reasoning. Trend-following indicators (EMA stack, MACD, ADX, OBV, Breakout) are never suppressed.

---

## 3. Fundamental Scoring

`_score_fundamental(fund)` starts at 50 and applies adjustments:

| Factor | Bullish adjustment | Bearish adjustment |
|--------|-------------------|-------------------|
| P/E trailing | +10 if < 15, +5 if < 25 | -5 if < 40, -10 if ≥ 40 |
| PEG ratio | +8 if < 1 | -5 if > 2 |
| Profit margin | +8 if > 20%, +3 if > 5% | -8 if negative |
| Debt/equity | +5 if < 0.5 | -5 if > 2 |
| Short ratio | +5 if > 5 (squeeze potential) | — |

ETFs return no fundamentals data from Yahoo Finance; the score defaults to 50 (neutral) for all ETFs. Fundamentals only contribute to the **swing** composite — they are stored but weighted at zero in next_day predictions.

---

## 4. Sentiment Scoring

`_score_sentiment(ticker)` queries today's FinBERT scores from the `sentiment` table. Raw FinBERT scores range from -1 to +1; these are mapped to 0–100 and averaged across sources. Sources include StockTwits (processed through FinBERT on 30 recent messages) and NewsAPI headlines (batch-scored via the HuggingFace Inference API).

**Event-risk flag**: at predict time, today's total `mention_count` across all sources is compared to the 20-day average. If the ratio ≥ 3.0, `event_risk = True`. Effect: both price bands widen by 1.25× from the mid, and confidence is multiplied by 0.8. The flag is computed fresh each prediction run and is not stored in the database — the widened band is what gets persisted. The event-risk flag fires regardless of sentiment direction: a spike in negative news and a spike in positive news are equally uncertain.

Sentiment is in the **swing** composite at its configured weight. It is **excluded** from the next_day composite.

---

## 5. XGBoost Layer

An XGBoost model is trained per ticker on rolling price history features (returns, volume ratios, lagged indicators). It produces two outputs: a `predicted_price` (regression) and a `bullish_prob` direction probability (classification).

The direction blend is 50/50 rules composite + XGBoost bullish_prob, gated by `val_accuracy >= 50%` — if the model's held-out direction accuracy is below random, it is disabled entirely.

The price blend is gated separately by `val_mae / last_close <= 0.10`. Direction accuracy is an insufficient gate for price blending: a model that achieves 51% direction accuracy can simultaneously have a catastrophically stale price target after a regime break. ADIL and TPET (July 2026) demonstrated this — both passed the direction gate while XGBoost price targets were 22–194% above their actual price levels post-crash.

When the price gate passes, the ML price is additionally clamped to `±2 ATR` from last close before blending — a backstop for post-training regime breaks that slip past the MAE gate before the next retrain.

The XGBoost layer applies only to **next_day** predictions. Swing predictions are direction-focused and use only the rules composite.

---

## 6. Price Bands and Risk Framing

**next_day band**: `mid ± ATR × multiplier`. Mid is anchored at `last_close × (1 + bias × 0.003)` where bias is `(composite - 50) / 50`. The downside multiplier is 1.1 when `close < EMA-200` (asymmetric band — small caps in downtrends have observed break-below rates roughly 5× break-above). The upside multiplier is 0.8.

**swing band**: `mid ± ATR × sqrt(horizon) × multiplier`. Volatility scales with the square root of time. The same asymmetric downside multiplier applies. Mid drift is capped at `±1.5% × sqrt(horizon / 5)` to prevent unrealistic multi-week price targets.

**Event risk widening**: when the event-risk flag is active, both bands widen by 1.25× from the mid before saving.

---

## 7. Accuracy Methodology

### next_day scoring
Each night after market close, `score_predictions()` compares stored `price_mid` against actual close from `price_history`. Price error is `|actual - predicted| / actual × 100`. Direction is scored only when the signal was BULLISH or BEARISH — NEUTRAL predictions are explicitly excluded from direction scoring (stored as NULL, not 0) because a neutral signal is not a directional bet.

**Naive baseline**: simultaneously computed as `|actual - prev_close| / actual × 100`. This is the persistence forecast — predicting tomorrow equals today. The Accuracy page shows "Model beats naive by X pts" or "Model does not beat naive" as an honest benchmark. A model that merely predicts persistence is worthless.

**Coin-flip baseline**: direction accuracy is shown alongside "(coin-flip baseline: 50%)" as static reference text. Buy-and-hold up-days (% of trading days the ticker closed up over the same window) are computed per-ticker from price_history and shown on per-ticker cards.

### swing maturation scoring
Swing predictions score when `prediction_date + horizon_days ≤ target_date` (calendar-day approximation of trading days — noted as acceptable given the typical 5–20 day horizons involved). Direction is scored as BULLISH correct if the matured close exceeds the close on the prediction date — not vs the day before maturation, since the horizon is days, not overnight. The NOT EXISTS dedup guard prevents double-scoring.

---

## 8. Known Limitations

**Small sample sizes**: the watchlist is hand-picked and typically contains 10–20 tickers. Statistical conclusions from the Accuracy page are illustrative, not statistically robust.

**Persistence dominance in next_day**: the next_day price band is ATR-anchored near last close. In calm markets, most closes fall within a narrow range around the previous close, making the model look accurate without being skillful. The naive baseline makes this visible — if model error and naive error are similar, the model is not adding value.

**No transaction-cost modeling**: backtest P&L figures do not deduct commissions, spreads, or slippage. Live results will underperform backtest figures.

**Survivorship of a hand-picked watchlist**: tickers are added because they are interesting, not randomly selected. This introduces selection bias into all accuracy statistics.

**XGBoost retraining lag**: models are retrained nightly, but a sharp intraday regime break (crash, halt, squeeze) will not be reflected until the next training run. The MAE gate and ATR clamp limit the damage but do not eliminate it. The ADIL/TPET case (July 2026) is the canonical example: direction accuracy passed at 54–55%, but price targets were 22–194% stale.

**Calendar-day swing maturation**: swing predictions mature on calendar days, not trading days. A 5-day swing prediction made on a Friday matures the following Wednesday (5 calendar days), not the following Friday (5 trading days). The error is small for short horizons but grows for trend predictions (20 days = ~4 trading weeks vs ~3 calendar weeks).
