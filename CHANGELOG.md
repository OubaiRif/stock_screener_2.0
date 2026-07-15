# Changelog — Stock Screener 2.0

All notable changes to this project are documented here.

---

## [2.1.2] — 2026-07-15

### Fixed
- **Accuracy: direction scoring now excludes NEUTRAL signals**
  NEUTRAL predictions are not directional bets. Previously they were
  scored as wrong against any price move, producing misleadingly low
  direction accuracy (~20%). Only BULLISH and BEARISH calls are now
  scored. Historical rows corrected in DB.

- **Accuracy: actual close prices now read from local DB instead of yfinance**
  `yfinance` returns dividend-adjusted prices which differ from the
  unadjusted prices stored in `price_history`. This caused actual_close
  mismatches and incorrect direction scores. All scoring now uses
  `price_history` as the single source of truth. 39 historical rows
  corrected.

---

## [2.1.1] — 2026-07-10

### Fixed
- **Backtest page: graceful error when `pandas_ta` unavailable**
  On Streamlit Cloud where `pandas_ta` is not installed, the Backtest
  page now shows a clear message instead of crashing with an
  `AttributeError`.

- **Demo DB: rebuilt from scratch — no `screener.db` required**
  `demo_db.py` now generates the demo database independently using
  the engine's own schema and data fetchers. Streamlit Cloud no longer
  needs a pre-existing database file. The demo DB is auto-generated
  on first boot if missing.

- **Security: `demo_screener.db` removed from git tracking**
  The demo database is no longer committed to the repository.
  Users who download the app generate their own clean database via
  `setup.sh`. Streamlit Cloud generates the demo DB automatically.

### Changed
- **Trading Assistant: fully redesigned Trade Analyzer (Buy/Sell flow)**
  - Ticker + Buy/Sell mode toggle + Analyze button in one row
  - Step 1: setup verdict + entry/exit checklist
  - Step 2: calculator with inputs and results side by side
  - Stop/target now calculated from entry price in Sell mode (not current price)
  - Clear error message for invalid or misspelled tickers
  - Detail button links to Stock Detail page
  - Demo limitation banner only shows in demo mode

---

## [2.1.0] — 2026-07-09

### Fixed
- **Trading Assistant: "No Setup Setup" label** — guard against quality
  value already containing "Setup" as a suffix.
- **Portfolio: action button icon overflow** — CSS fix for emoji buttons
  on Streamlit Cloud's font stack.
- **Backtest: `ta.rsi` crash on short price series** — all `pandas_ta`
  indicator calls wrapped in a `_safe()` guard that returns `None`
  instead of raising on insufficient data.
- **Gold Dashboard: `KeyError: 'confidence'`** — `.get()` fallback added.
- **Gold swing signal: pre-computed and cached in DB** — signal now
  reads from `gold_swing_cache` table in demo mode instead of computing
  live, which fails on Streamlit Cloud.
- **Pre-market HTML in Mkt Price column** — `_clean_num()` helper strips
  HTML tags that yfinance occasionally injects into numeric fields.

### Added
- **Demo limitation banners** — informational banners on Stock Detail,
  ETF Screener, Swing Trades, Gold Dashboard, Trading Assistant, and
  Accuracy pages explaining cloud limitations. Banners only show when
  `DEMO_MODE=true`.
- **`demo_banner()` utility** in `utils.py` — shared styled banner
  renderer for all demo limitation notices.
- **`gold_swing_cache` table** in DB schema for persisting swing signals.

---

## [2.0.0] — 2026-07-01

### Added
- Full app rewrite with 10-page Streamlit interface
- XGBoost trading signals with rule-based fallback
- FinBERT sentiment analysis via HuggingFace Inference API
- Gold & macro dashboard with FRED integration
- Portfolio tracker with Fidelity CSV import
- Trading journal with P&L tracking
- Swing trade screener with entry checklist
- ETF screener with macro-driven signals
- Prediction accuracy tracker with nightly scoring
- Backtesting engine with 4 strategy modes
- Demo mode for Streamlit Cloud deployment
- SQLite pipeline with nightly update cron job
