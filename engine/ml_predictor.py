"""
engine/ml_predictor.py — XGBoost price and direction prediction.

Two models per ticker:
  1. Regressor  → predicts next-day closing price
  2. Classifier → predicts direction (Up/Down) with bullish/bearish probability

Features: lagged prices + technical indicators + day-of-week
Trained on full price history, validated on last 20% of data.
Models stored in SQLite as binary blobs (no file system clutter).
"""
import logging, sys, os, pickle, json
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.db import get_conn

logger = logging.getLogger(__name__)

# ── Feature engineering ───────────────────────────────────────────────────────

PRICE_LAGS    = 5      # t-1 through t-5
FEATURE_COLS  = (
    [f"lag_{i}" for i in range(1, PRICE_LAGS+1)] +
    ["rsi","macd","ema_20","ema_50","bb_pct_b","atr","adx","rel_volume","zscore","day_of_week"]
)


def _build_features(ticker: str) -> pd.DataFrame | None:
    """
    Join price_history + indicators into a feature matrix.
    Returns DataFrame with FEATURE_COLS + target columns, or None if insufficient data.
    """
    ticker = ticker.upper()
    conn   = get_conn()

    ph = conn.execute("""
        SELECT date, close FROM price_history WHERE ticker=?
        ORDER BY date ASC
    """, (ticker,)).fetchall()

    ind = conn.execute("""
        SELECT date, rsi, macd, ema_20, ema_50, bb_pct_b, atr, adx, rel_volume, zscore
        FROM indicators WHERE ticker=?
        ORDER BY date ASC
    """, (ticker,)).fetchall()
    conn.close()

    if len(ph) < 100:
        logger.warning("Insufficient price history for %s (%d rows)", ticker, len(ph))
        return None

    df_p = pd.DataFrame([dict(r) for r in ph])
    df_p["date"] = pd.to_datetime(df_p["date"])
    df_p.set_index("date", inplace=True)

    df_i = pd.DataFrame([dict(r) for r in ind])
    if df_i.empty:
        logger.warning("No indicator data for %s", ticker)
        return None
    df_i["date"] = pd.to_datetime(df_i["date"])
    df_i.set_index("date", inplace=True)

    df = df_p.join(df_i, how="inner")
    if len(df) < 60:
        logger.warning("Insufficient joined data for %s (%d rows)", ticker, len(df))
        return None

    # Lagged prices
    for i in range(1, PRICE_LAGS+1):
        df[f"lag_{i}"] = df["close"].shift(i)

    # Day of week (0=Monday, 4=Friday)
    df["day_of_week"] = df.index.dayofweek

    # Targets
    df["target_price"] = df["close"].shift(-1)          # next day close
    df["target_dir"]   = (df["target_price"] > df["close"]).astype(int)  # 1=up, 0=down

    df.dropna(inplace=True)
    return df


# ── Training ──────────────────────────────────────────────────────────────────

def train(ticker: str) -> dict:
    """
    Train XGBoost regressor + classifier for a ticker.
    Saves models to DB. Returns metrics dict.
    """
    from xgboost import XGBRegressor, XGBClassifier
    from sklearn.metrics import mean_absolute_error, accuracy_score

    ticker = ticker.upper()
    logger.info("Training ML models for %s", ticker)

    df = _build_features(ticker)
    if df is None:
        return {"error": "insufficient_data"}

    X   = df[FEATURE_COLS].values
    y_p = df["target_price"].values
    y_d = df["target_dir"].values

    # Chronological split — last 20% for validation
    split = int(len(X) * 0.8)
    X_tr, X_val   = X[:split],   X[split:]
    yp_tr, yp_val = y_p[:split], y_p[split:]
    yd_tr, yd_val = y_d[:split], y_d[split:]

    # Regressor
    reg = XGBRegressor(
        n_estimators=300, learning_rate=0.05, max_depth=4,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbosity=0
    )
    reg.fit(X_tr, yp_tr, eval_set=[(X_val, yp_val)], verbose=False)
    val_mae = float(mean_absolute_error(yp_val, reg.predict(X_val)))

    # Classifier
    clf = XGBClassifier(
        n_estimators=300, learning_rate=0.05, max_depth=4,
        subsample=0.8, colsample_bytree=0.8,
        use_label_encoder=False, eval_metric="logloss",
        random_state=42, verbosity=0
    )
    clf.fit(X_tr, yd_tr, eval_set=[(X_val, yd_val)], verbose=False)
    val_acc = float(accuracy_score(yd_val, clf.predict(X_val)))

    # Serialize both models together
    blob = pickle.dumps({"reg": reg, "clf": clf})

    conn = get_conn()
    conn.execute("""
        INSERT INTO ml_models (ticker, model_blob, feature_cols, trained_on, n_samples, val_mae, val_accuracy)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            model_blob=excluded.model_blob, feature_cols=excluded.feature_cols,
            trained_on=excluded.trained_on, n_samples=excluded.n_samples,
            val_mae=excluded.val_mae, val_accuracy=excluded.val_accuracy,
            trained_at=datetime('now')
    """, (ticker, blob, json.dumps(FEATURE_COLS), date.today().isoformat(),
          len(df), round(val_mae, 4), round(val_acc * 100, 1)))
    conn.commit(); conn.close()

    logger.info("Trained %s: MAE=%.2f Acc=%.1f%%", ticker, val_mae, val_acc*100)
    return {
        "ticker":       ticker,
        "n_samples":    len(df),
        "val_mae":      round(val_mae, 4),
        "val_accuracy": round(val_acc * 100, 1),
        "feature_cols": FEATURE_COLS,
    }


# ── Prediction ────────────────────────────────────────────────────────────────

def predict_ml(ticker: str) -> dict | None:
    """
    Run the trained XGBoost models on the latest available feature row.
    Returns dict with predicted_price, bullish_prob, bearish_prob, direction, val_mae, val_accuracy.
    Returns None if no model exists for the ticker.
    """
    ticker = ticker.upper()
    conn   = get_conn()
    row    = conn.execute(
        "SELECT model_blob, val_mae, val_accuracy, trained_at FROM ml_models WHERE ticker=?",
        (ticker,)
    ).fetchone()
    conn.close()

    if row is None:
        logger.info("No ML model for %s — train first", ticker)
        return None

    models = pickle.loads(row["model_blob"])
    reg    = models["reg"]
    clf    = models["clf"]

    # Build latest feature row
    df = _build_features(ticker)
    if df is None or df.empty:
        return None

    latest = df[FEATURE_COLS].iloc[[-1]].values  # shape (1, n_features)

    predicted_price = float(reg.predict(latest)[0])
    proba           = clf.predict_proba(latest)[0]   # [P(down), P(up)]
    bullish_prob    = round(float(proba[1]) * 100, 1)
    bearish_prob    = round(float(proba[0]) * 100, 1)
    direction       = "BULLISH" if bullish_prob >= 50 else "BEARISH"

    return {
        "predicted_price": round(predicted_price, 4),
        "bullish_prob":    bullish_prob,
        "bearish_prob":    bearish_prob,
        "direction":       direction,
        "val_mae":         round(row["val_mae"] or 0, 4),
        "val_accuracy":    round(row["val_accuracy"] or 0, 1),
        "trained_at":      row["trained_at"],
    }


def get_model_info(ticker: str) -> dict | None:
    """Return metadata about a ticker's trained model without running prediction."""
    ticker = ticker.upper()
    conn   = get_conn()
    row    = conn.execute("""
        SELECT ticker, trained_on, n_samples, val_mae, val_accuracy, trained_at
        FROM ml_models WHERE ticker=?
    """, (ticker,)).fetchone()
    conn.close()
    return dict(row) if row else None


def train_all(tickers: list) -> list:
    """Train/retrain models for all tickers. Used by nightly job."""
    results = []
    for t in tickers:
        try:
            r = train(t)
            results.append(r)
        except Exception as e:
            logger.error("ML training failed for %s: %s", t, e)
            results.append({"ticker": t, "error": str(e)})
    return results


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    # Quick smoke test
    r = train("VOO")
    print("Train result:", r)
    p = predict_ml("VOO")
    print("Prediction:", p)
