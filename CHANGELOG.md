# Changelog

## [2.5.1] — 2026-08-13

### Fixed

**Technical indicators — silent total failure**
- `pandas_ta` is uninstallable on Python 3.14 / numpy 2.x (it imports `numpy.NaN`, removed in numpy 2.0; the project's last release is from 2021). The import in `engine/indicators.py` was wrapped in a `try/except` that set `PANDAS_TA_AVAILABLE = False`, after which `compute_indicators()` returned the raw price frame and `save_indicators()` wrote rows with all 23 indicator columns NULL.
- Effect: from 2026-07-22 to 2026-08-12 the technical score defaulted to exactly 50.0 for every ticker on every day, producing NEUTRAL @ 0% confidence across the entire watchlist.
- Migrated all 10 pandas_ta-derived indicators to the `ta` library (0.11.0) using named accessors rather than positional `iloc` indexing. Removed the availability guard — `ta` is now a hard dependency with nothing to degrade to.
- `save_indicators()` now refuses to write a row where every indicator value is None, and logs at ERROR. An empty row must never look like a successful write.

**Accuracy scoring — next_day predictions never caught up**
- `_score_next_day()` selected only `WHERE p.date = ?`, so any day the pipeline did not run was never scored, permanently. Price history backfills; scoring did not.
- Rewritten to sweep all unscored matured predictions using the same `NOT EXISTS` pattern `_score_swing_matured()` already used.
- Added `idx_accuracy_unique` on `accuracy_log(ticker, date, prediction_type)` in both `engine/db.py` and `demo_db.py`. The existing `ON CONFLICT DO NOTHING` clauses were no-ops — there was no unique constraint to conflict against, and two duplicate rows had already accumulated.

**Calendar days replaced with NYSE trading days**
- New `engine/calendar_utils.py` wrapping `pandas_market_calendars` (NYSE): `is_trading_day()`, `next_trading_day()`, `add_trading_days()`.
- `_save()` in `engine/predictor.py` now refuses to persist a prediction dated on a non-trading day. Predictions are still computed and returned, so on-demand UI use on a weekend still works — they are simply not written. Previously the pipeline running on a Saturday wrote rows that could never be scored (8 such rows exist, dated 2026-07-04).
- Swing maturation moved out of SQL `date(p.date, '+N day')` arithmetic into Python trading-day arithmetic. A swing_5d prediction now matures after 5 trading days rather than 5 calendar days (~3 trading days).

**Launch script**
- `launch_2.5.sh` derived `VENV` from a hardcoded path pointing at the 1.0 directory (`~/Desktop/stock_screener/venv`). Now derives `APPDIR` from `BASH_SOURCE` and uses `$APPDIR/venv`, making the script correct and location-independent. Added `status` command and start verification.

### Data corrections
- Deleted 14 `accuracy_log` rows with `prediction_type LIKE 'swing_%'` dated 2026-07-21. All were written outside the normal scorer during an earlier implementation session and scored against same-day closes — a 20-day prediction scored on the day it was made. Model error read 0.03-0.6%; the same predictions scored correctly on maturity read 7-38%.
- Deduplicated 2 `accuracy_log` rows (AAPL, PDYN @ 2026-07-10), keeping the lowest id.
- Recomputed all indicator rows from 2026-07-21 onward. The 2026-07-21 rows had `rel_volume` and `obv` computed against a malformed rolling window (values 15-20x too small across all 14 tickers).
- Deleted hollow predictions dated 2026-08-12 that were generated against NULL technicals.

### Infrastructure
- Nightly run moved from cron to a systemd user timer (`screener.timer`), scheduled `Mon-Fri 17:00 America/New_York` with `Persistent=true`. The previous cron entry pointed at the deleted `stock_screener_2.0` directory and fired at 21:15 Europe/Tbilisi — 13:15 US Eastern, roughly three hours before the market close — so every historical nightly run computed on intraday rather than settled prices.
- `HF_API_KEY` moved to a gitignored `.env` read via `EnvironmentFile`, since systemd services do not inherit the interactive shell environment. FinBERT had been silently falling back to keyword sentiment.
- Added `huggingface_hub`, `ta`, and `pandas_market_calendars` to `requirements.txt`; removed `pandas_ta`.

### Known issues
- 8 predictions dated 2026-07-04 (a Saturday, and the observed July 4th market holiday) remain permanently unscoreable. Left in place as a record.
- `save_indicators()` only writes rows after `MAX(date)` for a ticker, so an existing bad row blocks recomputation of that date. Deletion is currently required before a rebuild.
- `price_history` for 2026-08-11 contains only 6 of 14 tickers — the large caps and ETFs. Cause unconfirmed.


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
