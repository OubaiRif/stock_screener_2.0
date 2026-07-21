# Changelog

## [2.5.0] — 2026-07-21

### Prediction logic overhaul

**Two-horizon predictions**
- `predict_swing(ticker)` added to `engine/predictor.py`. Generates a strategy-tied directional prediction at 5–20 trading-day horizons depending on strategy (`STRATEGY_HORIZONS` in `config.py`). Composite uses all three components (technical + fundamental + sentiment). Price band scales with `sqrt(horizon)`; asymmetric downside multiplier applies in downtrends.
- `predict()` (next_day) composite is now **technicals only**. Fundamentals and sentiment are still computed and stored for display but carry zero weight in the 1-day composite. Rationale: fundamentals and sentiment have no predictive edge on a 24-hour price move.
- Nightly pipeline (`run.py cmd_nightly`) now calls `predict_swing()` for all watchlist tickers after next_day predictions.
- `predictions` table gains `horizon_days INTEGER` column (guarded migration).
- New CLI command: `python3 run.py predict_swing [TICKER ...]`

**Trend regime filter** (`engine/predictor.py _score_technical`)
- Oversold mean-reversion votes (RSI, BB %B, Z-Score, Williams %R) suppressed when `close < EMA-200`. A stock can be oversold for months in a freefall.
- Overbought bearish votes suppressed when `close > EMA-200`. Strong uptrending stocks can stay overbought legitimately.
- Suppressed votes become 0 (not -1). Signal notes record "vote suppressed" for UI transparency.

**Asymmetric price bands** (`engine/predictor.py _price_range`)
- Downside band multiplier raised from 0.8 to 1.1 when `close < EMA-200`. Observed break-below rate in downtrending small caps is roughly 5× break-above.
- Upside multiplier unchanged at 0.8.
- Same asymmetry applied to swing bands.

**ML price blend guards** (`engine/predictor.py predict`)
- Guard 1: ML price blend now gated by `val_mae / last_close ≤ 0.10`. Direction accuracy alone is insufficient — ADIL/TPET (July 2026) passed the 50% direction gate while price targets were 22–194% stale post-crash.
- Guard 2: ML price clamped to `±2 ATR` from last close before blending. Backstop for post-training regime breaks that slip past Guard 1 before next retrain.
- When gated, Stock Detail shows ML price as strikethrough with "ML price excluded — model MAE too high vs price" note.

**Sentiment event-risk flag** (`engine/predictor.py _event_risk`)
- Mention spike detection: today's total `mention_count` vs 20-day average. Ratio ≥ 3.0 triggers `event_risk = True`.
- Effect: both price bands widen by 1.25×, confidence multiplied by 0.8.
- Computed at predict time, not stored — widened band is what persists.
- Stock Detail shows gold `⚠ Event risk` banner with ratio and mention count when active.

### Accuracy improvements

**Naive persistence baseline** (`engine/accuracy.py`, `pages/8_Accuracy.py`)
- `naive_error_pct` computed and stored in `accuracy_log` for every scored prediction. Naive forecast = yesterday's close carried forward.
- Accuracy page Overall section shows "Model avg error X% vs Naive Y%" with green/amber verdict.
- Per-ticker cards show naive avg error as secondary label under Average Error.
- Direction accuracy shows "(coin-flip baseline: 50%)" as static reference.
- Buy-and-hold up-days computed per-ticker from `price_history` and shown on ticker cards.
- `accuracy_log` gains `naive_error_pct REAL` column (guarded migration).

**Swing maturation scoring** (`engine/accuracy.py`)
- `score_predictions()` split into `_score_next_day()` and `_score_swing_matured()`.
- Swing predictions score only when `prediction_date + horizon_days ≤ target_date` (calendar-day approximation).
- Direction for swing: matured close vs close on prediction date (not prev_close).
- NOT EXISTS dedup guard prevents double-scoring.
- `get_recent_log()` gains `since` parameter for date-bounded queries.

### UI additions

**Stock Detail** (`pages/1_Stock_Detail.py`)
- Swing prediction mini-card rendered below next_day card, showing horizon, signal, confidence, score breakdown, and price range.
- Event-risk gold banner added to prediction card.
- ML price gated: strikethrough display with exclusion note.

**Swing Trades** (`pages/3_Swing_Trades.py`)
- Signal column now sources swing prediction signal when available, with "Swing Nd · direction focus" label. Falls back to next_day gracefully.
- Price column shows swing price range when available.

**Accuracy page** (`pages/8_Accuracy.py`)
- Prediction-type filter (All / next_day / swing) added to controls. Filters both summary and Recent Predictions Log table.
- Day-count off-by-one fixed: `get_recent_log` now filtered by same `since_date` as `get_accuracy_summary`.

**Stock Detail watchlist** (`pages/1_Stock_Detail.py`)
- Remove-ticker button (🗑) added per watchlist row with two-step confirmation guard.
- `remove_stock()` imported and wired — cascades deletes across all tables.

**Portfolio page** (`pages/6_Portfolio.py`)
- Action buttons changed from narrow 3-column emoji-only layout to stacked labeled buttons to fix CSS overflow on cloud.

### Infrastructure

**Version bump**
- All page titles and `dashboard.py` updated from "Stock Screener 2.0" to "Stock Screener 2.5" via sed pass.
- `STRATEGY_HORIZONS` dict added to `config.py`.

**Launch script** (`launch_2.5.sh`)
- Background launch with `nohup`, PID file, `start/stop/restart` commands, log to `logs/streamlit_2.5.log`.
- Stock Screener 2.5 runs on port 8502; 2.0 stays on 8501.

**Documentation**
- `docs/METHODOLOGY.md` added covering two-horizon design, all indicators, regime suppression, fundamental scoring, sentiment + event-risk, XGBoost layer, price bands, accuracy methodology, and known limitations.

---

## [2.0.0] — 2026-07-14

Initial release. 10-page Streamlit app with dashboard, Stock Detail, ETF Screener, Swing Trades, Gold Dashboard, Trading Assistant, Portfolio, Journal, Accuracy, and Backtest pages. SQLite backend with DEMO_MODE for Streamlit Cloud. Nightly cron pipeline. XGBoost per-ticker models. FinBERT sentiment via HuggingFace Inference API.
