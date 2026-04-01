"""
train_swing.py - Walk-Forward Training for Expert/Swing Agent (H4 entries)

Trains on H4 bars with D1 context. Strategy-informed labels with wider TP/SL.
Hold time: 6-48 H4 bars (1-8 days). Meta-labeling for signal filtering.

Usage:
    cd backend
    python -m scripts.train_swing --symbol US30 [--trials 15] [--folds 4]
"""
import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd
import joblib
import optuna
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

from app.services.ml.features_swing import compute_swing_features
from scripts.model_utils import grade_model, save_model_record
from app.services.ml.meta_labeler_v2 import MetaLabeler

DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR     = os.path.join(DATA_DIR, "ml_models")
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)
OOS_START = "2026-01-01"
WARMUP    = 100  # H4 warmup bars (~17 days)

# Swing-specific config per symbol
SWING_CONFIG = {
    "US30": {
        "tp_atr_mult": 3.0,    # 3x ATR TP on H4
        "sl_atr_mult": 1.5,    # 1.5x ATR SL
        "hold_bars": 24,       # max 24 H4 bars = 4 days
        "cost_bps": 1.5,       # same as scalping (spread is fixed)
        "slippage_bps": 1.0,
        "bars_per_day": 6,     # ~6 H4 bars per trading day
    },
    "BTCUSD": {
        "tp_atr_mult": 4.0,
        "sl_atr_mult": 2.0,
        "hold_bars": 36,
        "cost_bps": 5.0,
        "slippage_bps": 2.0,
        "bars_per_day": 6,
    },
    "XAUUSD": {
        "tp_atr_mult": 3.0,
        "sl_atr_mult": 1.5,
        "hold_bars": 24,
        "cost_bps": 3.0,
        "slippage_bps": 1.0,
        "bars_per_day": 5,
    },
}


def _normalize_ohlcv(df):
    df = df.copy()
    if "ts_event" in df.columns and "time" not in df.columns:
        df["time"] = (pd.to_datetime(df["ts_event"]).values.astype("datetime64[s]").astype(np.int64))
        df = df.drop(columns=["ts_event"])
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("time").reset_index(drop=True)


def _load_tf(symbol, tf):
    hist_path = os.path.join(HIST_DATA_DIR, symbol, f"{symbol}_{tf}.csv")
    curr_path = os.path.join(DATA_DIR, f"{symbol}_{tf}.csv")
    if os.path.exists(hist_path):
        return _normalize_ohlcv(pd.read_csv(hist_path))
    if os.path.exists(curr_path):
        return pd.read_csv(curr_path)
    return None


def create_swing_labels(closes, atr, tp_mult, sl_mult, max_hold):
    """Triple-barrier labels for swing trading on H4."""
    n = len(closes)
    labels = np.ones(n, dtype=np.int8)  # default HOLD

    for i in range(n - max_hold - 1):
        entry = closes[i]
        a = atr[i] if atr[i] > 0 else entry * 0.002
        tp_long = entry + a * tp_mult
        sl_long = entry - a * sl_mult
        tp_short = entry - a * tp_mult
        sl_short = entry + a * sl_mult

        long_result = 0
        short_result = 0

        for j in range(i + 1, min(i + max_hold + 1, n)):
            if long_result == 0:
                if closes[j] <= sl_long:
                    long_result = -1
                elif closes[j] >= tp_long:
                    long_result = 1
            if short_result == 0:
                if closes[j] >= sl_short:
                    short_result = -1
                elif closes[j] <= tp_short:
                    short_result = 1
            if long_result != 0 and short_result != 0:
                break

        # Timeout: check P&L at max_hold
        if long_result == 0:
            final = closes[min(i + max_hold, n - 1)]
            long_result = 1 if final > entry + a * 0.5 else (-1 if final < entry - a * 0.5 else 0)
        if short_result == 0:
            final = closes[min(i + max_hold, n - 1)]
            short_result = 1 if final < entry - a * 0.5 else (-1 if final > entry + a * 0.5 else 0)

        if long_result == 1 and short_result != 1:
            labels[i] = 2  # BUY
        elif short_result == 1 and long_result != 1:
            labels[i] = 0  # SELL
        elif long_result == 1 and short_result == 1:
            labels[i] = 2  # prefer long for index
        # else: keep HOLD (1)

    # Force HOLD in low-vol
    atr_ser = pd.Series(atr)
    low_vol = atr_ser.rolling(50, min_periods=20).rank(pct=True).values < 0.25
    labels[low_vol] = 1

    return labels


def _xgb_objective(trial, Xtr, ytr, Xval, yval):
    import xgboost as xgb
    params = {
        "max_depth": trial.suggest_int("max_depth", 3, 7),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 100, 400),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "min_child_weight": trial.suggest_int("min_child_weight", 5, 30),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 5.0),
        "objective": "multi:softprob", "num_class": 3,
        "eval_metric": "mlogloss", "verbosity": 0, "random_state": 42,
        "early_stopping_rounds": 15,
    }
    m = xgb.XGBClassifier(**params)
    m.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    return m.score(Xval, yval)


def _lgb_objective(trial, Xtr, ytr, Xval, yval):
    import lightgbm as lgb
    params = {
        "num_leaves": trial.suggest_int("num_leaves", 15, 60),
        "max_depth": trial.suggest_int("max_depth", 3, 7),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 100, 400),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 5.0),
        "min_child_samples": trial.suggest_int("min_child_samples", 10, 50),
        "objective": "multiclass", "num_class": 3,
        "metric": "multi_logloss", "verbosity": -1, "random_state": 42,
    }
    m = lgb.LGBMClassifier(**params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(Xtr, ytr, eval_set=[(Xval, yval)],
              callbacks=[lgb.early_stopping(15, verbose=False), lgb.log_evaluation(-1)])
    return m.score(Xval, yval)


def train_model(model_type, Xtr, ytr, Xval, yval, n_trials):
    if model_type == "xgboost":
        import xgboost as xgb
        obj = lambda t: _xgb_objective(t, Xtr, ytr, Xval, yval)
    else:
        import lightgbm as lgb
        obj = lambda t: _lgb_objective(t, Xtr, ytr, Xval, yval)

    study = optuna.create_study(direction="maximize")
    study.optimize(obj, n_trials=n_trials, show_progress_bar=False)
    best = study.best_params

    if model_type == "xgboost":
        best.update({"objective": "multi:softprob", "num_class": 3,
                     "eval_metric": "mlogloss", "verbosity": 0,
                     "random_state": 42, "early_stopping_rounds": 15})
        model = xgb.XGBClassifier(**best)
        model.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    else:
        best.update({"objective": "multiclass", "num_class": 3,
                     "metric": "multi_logloss", "verbosity": -1, "random_state": 42})
        model = lgb.LGBMClassifier(**best)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(Xtr, ytr, eval_set=[(Xval, yval)],
                      callbacks=[lgb.early_stopping(15, verbose=False), lgb.log_evaluation(-1)])
    return model, study.best_value


def swing_backtest(model, X, closes, opens, highs, lows, atr, cfg):
    """Simple swing backtest with TP/SL/hold."""
    preds = model.predict(X)
    n = len(closes)
    tp_mult = cfg["tp_atr_mult"]
    sl_mult = cfg["sl_atr_mult"]
    hold = cfg["hold_bars"]
    cost = (cfg["cost_bps"] + cfg["slippage_bps"]) / 10_000
    bpd = cfg["bars_per_day"]

    equity = np.zeros(n)
    trades = []
    daily_pnl = 0.0
    last_day = 0
    i = 0

    while i < n - hold - 1:
        # Daily loss limit
        day = i // bpd
        if day != last_day:
            daily_pnl = 0.0
            last_day = day
        if daily_pnl <= -0.01:
            i += 1
            continue

        sig = preds[i]
        if sig not in (0, 2):
            i += 1
            continue

        entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]
        a = atr[i] if atr[i] > 0 else entry * 0.002
        is_long = sig == 2

        tp = entry + a * tp_mult if is_long else entry - a * tp_mult
        sl = entry - a * sl_mult if is_long else entry + a * sl_mult

        exit_ret = None
        for j in range(i + 1, min(i + hold + 1, n)):
            if is_long:
                if lows[j] <= sl:
                    exit_ret = -(a * sl_mult / entry) - cost; break
                if highs[j] >= tp:
                    exit_ret = (a * tp_mult / entry) - cost; break
            else:
                if highs[j] >= sl:
                    exit_ret = -(a * sl_mult / entry) - cost; break
                if lows[j] <= tp:
                    exit_ret = (a * tp_mult / entry) - cost; break

        if exit_ret is None:
            c_exit = closes[min(i + hold, n - 1)]
            raw = (c_exit - entry) / entry
            exit_ret = (raw if is_long else -raw) - cost

        equity[min(i + hold, n - 1)] += exit_ret
        trades.append(exit_ret)
        daily_pnl += exit_ret
        i += hold

    if len(trades) == 0:
        return {"sharpe": 0, "win_rate": 0, "max_drawdown": 0, "total_return": 0,
                "profit_factor": 0, "total_trades": 0}

    tr = np.array(trades)
    wins = tr[tr > 0]
    losses = tr[tr < 0]
    n_days = max(1, n // bpd)
    trimmed = equity[:n_days * bpd]
    daily = trimmed.reshape(n_days, bpd).sum(axis=1) if len(trimmed) >= bpd else np.array([equity.sum()])
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0
    cum = np.cumsum(equity)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum) * 100)

    return {
        "sharpe": round(sharpe, 4),
        "win_rate": round(len(wins) / len(trades) * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "total_return": round(float(cum[-1] * 100), 4),
        "profit_factor": round(float(wins.sum() / abs(losses.sum())) if len(losses) > 0 else 99, 4),
        "total_trades": len(trades),
    }


def run_swing_walkforward(symbol, n_trials=15, n_folds=4):
    cfg = SWING_CONFIG.get(symbol, SWING_CONFIG["US30"])

    print(f"\n{'='*65}")
    print(f"  SWING WALK-FORWARD: {symbol}  |  {n_folds} folds  |  {n_trials} trials")
    print(f"  TP={cfg['tp_atr_mult']}x ATR  SL={cfg['sl_atr_mult']}x ATR  Hold={cfg['hold_bars']} bars")
    print(f"{'='*65}")

    h4 = _load_tf(symbol, "H4")
    d1 = _load_tf(symbol, "D1")
    if h4 is None:
        raise FileNotFoundError(f"No H4 data for {symbol}")

    print(f"  H4={len(h4):,}  D1={len(d1) if d1 is not None else 0:,}")

    timestamps = h4["time"].values.astype(np.int64)
    closes = h4["close"].values
    opens = h4["open"].values
    highs = h4["high"].values
    lows = h4["low"].values

    print("  Computing swing features...", flush=True)
    feature_names, X_all = compute_swing_features(h4, d1, symbol=symbol)
    print(f"  Features: {len(feature_names)}")

    # Labels
    atr_idx = feature_names.index("atr_14")
    atr_vals = X_all[:, atr_idx]
    y_all = create_swing_labels(closes, atr_vals, cfg["tp_atr_mult"], cfg["sl_atr_mult"], cfg["hold_bars"])
    print(f"  Labels: sell={np.sum(y_all==0):,}  hold={np.sum(y_all==1):,}  buy={np.sum(y_all==2):,}")

    # OOS split
    oos_ts = int(pd.Timestamp(OOS_START, tz="UTC").timestamp())
    oos_idx = int(np.argmax(timestamps >= oos_ts)) if (timestamps >= oos_ts).any() else len(X_all)

    X_oos = X_all[oos_idx:]
    y_oos = y_all[oos_idx:]
    print(f"  Train: {oos_idx:,}  OOS: {len(X_oos):,}")

    # Walk-forward folds
    n_train = oos_idx - WARMUP
    chunk = n_train // (n_folds + 1)
    folds = []
    for k in range(n_folds):
        folds.append({
            "fold": k + 1,
            "train_end": WARMUP + (k + 1) * chunk,
            "test_start": WARMUP + (k + 1) * chunk,
            "test_end": WARMUP + (k + 2) * chunk,
        })

    print(f"\n  {'-'*60}")
    os.makedirs(MODEL_DIR, exist_ok=True)

    for fi in folds:
        fnum = fi["fold"]
        X_tr = X_all[WARMUP:fi["train_end"]]
        y_tr = y_all[WARMUP:fi["train_end"]]
        X_val = X_all[fi["test_start"]:fi["test_end"]]
        y_val = y_all[fi["test_start"]:fi["test_end"]]

        ts_s = pd.to_datetime(timestamps[fi["test_start"]], unit="s", utc=True).strftime("%Y-%m")
        ts_e = pd.to_datetime(timestamps[min(fi["test_end"]-1, len(timestamps)-1)], unit="s", utc=True).strftime("%Y-%m")
        print(f"\n  Fold {fnum}: Train {len(X_tr):,} | Test {ts_s}->{ts_e} ({len(X_val):,})")

        val_split = int(len(X_tr) * 0.8)
        for mt in ["xgboost", "lightgbm"]:
            print(f"    [{mt}] {n_trials} trials...", end=" ", flush=True)
            model, cv = train_model(mt, X_tr[:val_split], y_tr[:val_split],
                                    X_tr[val_split:], y_tr[val_split:], n_trials)
            print(f"cv={cv:.4f}", flush=True)

            m = swing_backtest(model, X_val, closes[fi["test_start"]:fi["test_end"]],
                              opens[fi["test_start"]:fi["test_end"]],
                              highs[fi["test_start"]:fi["test_end"]],
                              lows[fi["test_start"]:fi["test_end"]],
                              atr_vals[fi["test_start"]:fi["test_end"]], cfg)
            g = grade_model(m)
            print(f"    [{mt}] Grade={g}  Sharpe={m['sharpe']:.3f}  WR={m['win_rate']:.1f}%  "
                  f"DD={m['max_drawdown']:.1f}%  Trades={m['total_trades']}")

    # Final model
    print(f"\n  {'-'*60}")
    print(f"  Final Model (full train -> {OOS_START})  {n_trials*2} trials")
    X_full = X_all[WARMUP:oos_idx]
    y_full = y_all[WARMUP:oos_idx]
    val_split = int(len(X_full) * 0.85)

    final_models = {}
    for mt in ["xgboost", "lightgbm"]:
        print(f"  [{mt}] {n_trials*2} trials...", end=" ", flush=True)
        model, cv = train_model(mt, X_full[:val_split], y_full[:val_split],
                                X_full[val_split:], y_full[val_split:], n_trials * 2)
        print(f"cv={cv:.4f}", flush=True)
        final_models[mt] = model

    # OOS evaluation
    print(f"\n  {'-'*60}")
    print(f"  True OOS: {OOS_START} -> present  ({len(X_oos):,} bars)")
    for mt, model in final_models.items():
        m = swing_backtest(model, X_oos, closes[oos_idx:], opens[oos_idx:],
                          highs[oos_idx:], lows[oos_idx:], atr_vals[oos_idx:], cfg)
        g = grade_model(m)
        print(f"  [{mt}] Grade={g}  Sharpe={m['sharpe']:.3f}  WR={m['win_rate']:.1f}%  "
              f"DD={m['max_drawdown']:.1f}%  Return={m['total_return']:.1f}%  Trades={m['total_trades']}")

    # Meta-labeling
    print(f"\n  {'-'*60}")
    print(f"  Meta-Labeling")
    for mt, model in final_models.items():
        try:
            primary = model.predict(X_full)
            dirs = np.where(primary == 0, -1, np.where(primary == 2, 1, 0)).astype(np.float32)
            actual = np.where(y_full == 0, -1, np.where(y_full == 2, 1, 0)).astype(np.float32)
            ml = MetaLabeler(threshold=0.45)
            ml.fit(X_full, dirs, actual, feature_names=feature_names)

            path = os.path.join(MODEL_DIR, f"swing_{symbol}_H4_{mt}.joblib")
            joblib.dump({
                "model": model, "feature_names": feature_names,
                "meta_labeler": ml, "meta_threshold": 0.45,
                "symbol": symbol, "oos_start": OOS_START,
                "pipeline_version": "v8_swing",
                "execution": cfg,
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }, path)
            print(f"  Saved: {os.path.basename(path)} +meta")
        except Exception as e:
            print(f"  [{mt}] Meta-labeler failed: {e}")

    print(f"\n{'='*65}")
    print("  Swing walk-forward training complete.")
    print(f"{'='*65}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="US30", choices=["BTCUSD", "XAUUSD", "US30"])
    parser.add_argument("--trials", type=int, default=15)
    parser.add_argument("--folds", type=int, default=4)
    args = parser.parse_args()
    run_swing_walkforward(args.symbol, n_trials=args.trials, n_folds=args.folds)


if __name__ == "__main__":
    main()
