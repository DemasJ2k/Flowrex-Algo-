"""
train_strategy_model.py — Generic per-strategy model training for Rapid Agent.

Trains a single strategy model (ICT, Williams, Donchian, Momentum, or OFI)
on US30 M5 data using walk-forward validation.

Usage:
    cd backend
    python -m scripts.train_strategy_model --strategy ict --symbol US30 [--trials 15] [--folds 4]
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

from app.services.ml.meta_labeler_v2 import MetaLabeler

DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR     = os.path.join(DATA_DIR, "ml_models")
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)
OOS_START     = "2026-01-01"
WARMUP        = 500
MAX_M5_BARS   = 900_000

# Strategy → feature module mapping
STRATEGY_MODULES = {
    "ict": {
        "name": "ICT/SMC",
        "compute": "app.services.ml.features_ict:compute_ict_features",
        "args": lambda o, h, l, c, v, t: dict(opens=o, highs=h, lows=l, closes=c, volumes=v, times=t),
        "tp_mult": 1.2, "sl_mult": 0.8, "hold_bars": 10,
    },
    "williams": {
        "name": "Larry Williams",
        "compute": "app.services.ml.features_williams:compute_williams_features",
        "args": lambda o, h, l, c, v, t: dict(opens=o, highs=h, lows=l, closes=c, volumes=v),
        "tp_mult": 1.2, "sl_mult": 0.8, "hold_bars": 10,
    },
    "donchian": {
        "name": "Donchian/Quant",
        "compute": "app.services.ml.features_quant:compute_quant_features",
        "args": lambda o, h, l, c, v, t: dict(opens=o, highs=h, lows=l, closes=c, volumes=v),
        "tp_mult": 1.2, "sl_mult": 0.8, "hold_bars": 10,
    },
    "momentum": {
        "name": "Momentum",
        "compute": "app.services.ml.features_momentum:compute_momentum_features",
        "args": lambda o, h, l, c, v, t: dict(opens=o, highs=h, lows=l, closes=c, volumes=v),
        "tp_mult": 1.2, "sl_mult": 0.8, "hold_bars": 10,
    },
    "ofi": {
        "name": "OFI",
        "compute": "app.services.ml.features_ofi:compute_ofi_features",
        "args": lambda o, h, l, c, v, t: dict(opens=o, highs=h, lows=l, closes=c, volumes=v),
        "tp_mult": 1.2, "sl_mult": 0.8, "hold_bars": 10,
    },
}


def _normalize(df):
    df = df.copy()
    if "ts_event" in df.columns and "time" not in df.columns:
        df["time"] = pd.to_datetime(df["ts_event"]).values.astype("datetime64[s]").astype(np.int64)
        df = df.drop(columns=["ts_event"])
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("time").reset_index(drop=True)


def _load_m5(symbol):
    for path in [
        os.path.join(HIST_DATA_DIR, symbol, f"{symbol}_M5.csv"),
        os.path.join(DATA_DIR, f"{symbol}_M5.csv"),
    ]:
        if os.path.exists(path):
            df = _normalize(pd.read_csv(path))
            if len(df) > MAX_M5_BARS:
                df = df.iloc[-MAX_M5_BARS:].reset_index(drop=True)
            return df
    raise FileNotFoundError(f"No M5 data for {symbol}")


def _load_feature_fn(strategy):
    module_path, fn_name = STRATEGY_MODULES[strategy]["compute"].split(":")
    mod = __import__(module_path, fromlist=[fn_name])
    return getattr(mod, fn_name)


def create_labels(closes, atr, tp_mult, sl_mult, hold_bars):
    """Triple-barrier labels with ATR-based TP/SL."""
    n = len(closes)
    labels = np.ones(n, dtype=np.int8)  # default HOLD

    s = pd.Series(closes.astype(np.float64))
    rev = s.iloc[::-1].reset_index(drop=True)
    future_max = rev.rolling(hold_bars, min_periods=1).max().iloc[::-1].reset_index(drop=True).shift(-1).values
    future_min = rev.rolling(hold_bars, min_periods=1).min().iloc[::-1].reset_index(drop=True).shift(-1).values

    valid = ~(np.isnan(future_max) | np.isnan(future_min))
    atr_safe = np.where(atr > 0, atr, closes * 0.002)
    tp_dist = atr_safe * tp_mult
    sl_dist = atr_safe * sl_mult

    up_move = future_max - closes
    down_move = closes - future_min

    buy_mask = valid & (up_move > tp_dist) & (up_move > down_move)
    sell_mask = valid & (down_move > sl_dist) & (down_move > up_move)
    labels[buy_mask] = 2
    labels[sell_mask] = 0

    # Force HOLD in low-vol
    atr_pctile = pd.Series(atr).rolling(100, min_periods=50).rank(pct=True).values
    labels[atr_pctile < 0.25] = 1

    return labels


def _xgb_objective(trial, Xtr, ytr, Xval, yval):
    import xgboost as xgb
    p = {
        "max_depth": trial.suggest_int("md", 3, 7),
        "learning_rate": trial.suggest_float("lr", 0.01, 0.15, log=True),
        "n_estimators": trial.suggest_int("ne", 100, 400),
        "subsample": trial.suggest_float("ss", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("cs", 0.5, 1.0),
        "min_child_weight": trial.suggest_int("mcw", 5, 30),
        "reg_alpha": trial.suggest_float("ra", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("rl", 0.5, 5.0),
        "objective": "multi:softprob", "num_class": 3,
        "eval_metric": "mlogloss", "verbosity": 0, "random_state": 42,
        "early_stopping_rounds": 15,
    }
    m = xgb.XGBClassifier(**p)
    m.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    return m.score(Xval, yval)


def _lgb_objective(trial, Xtr, ytr, Xval, yval):
    import lightgbm as lgb
    p = {
        "num_leaves": trial.suggest_int("nl", 15, 60),
        "max_depth": trial.suggest_int("md", 3, 7),
        "learning_rate": trial.suggest_float("lr", 0.01, 0.15, log=True),
        "n_estimators": trial.suggest_int("ne", 100, 400),
        "subsample": trial.suggest_float("ss", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("cs", 0.5, 1.0),
        "reg_alpha": trial.suggest_float("ra", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("rl", 0.5, 5.0),
        "min_child_samples": trial.suggest_int("mcs", 10, 50),
        "objective": "multiclass", "num_class": 3,
        "metric": "multi_logloss", "verbosity": -1, "random_state": 42,
    }
    m = lgb.LGBMClassifier(**p)
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

    # Expand shortened keys
    key_map = {"md": "max_depth", "lr": "learning_rate", "ne": "n_estimators",
               "ss": "subsample", "cs": "colsample_bytree", "mcw": "min_child_weight",
               "ra": "reg_alpha", "rl": "reg_lambda", "nl": "num_leaves", "mcs": "min_child_samples"}
    best = {key_map.get(k, k): v for k, v in best.items()}

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


def backtest(model, X, closes, opens, highs, lows, atr, cfg, times=None):
    """Backtest with TP/SL, ATR gate, session filter, daily loss limit."""
    preds = model.predict(X)
    n = len(closes)
    tp_m = cfg["tp_mult"]; sl_m = cfg["sl_mult"]; hold = cfg["hold_bars"]
    cost = 2.5 / 10_000; bpd = 102  # US30 bars per day

    # ATR gate
    atr_pctile = pd.Series(atr).rolling(100, min_periods=50).rank(pct=True).values
    preds = np.where(atr_pctile < 0.25, 1, preds)

    # Donchian squeeze gate
    h_s = pd.Series(highs); l_s = pd.Series(lows)
    dcw = h_s.rolling(20).max() - l_s.rolling(20).min()
    squeeze = dcw.rolling(100, min_periods=50).rank(pct=True).values < 0.20
    preds = np.where(squeeze & (preds != 1), 1, preds)

    # Session filter
    if times is not None:
        hours = pd.to_datetime(times, unit='s', utc=True).hour
        mins = pd.to_datetime(times, unit='s', utc=True).minute
        hf = hours + mins / 60.0
        in_session = ((hf >= 13.5) & (hf < 16.0)) | ((hf >= 19.0) & (hf < 20.5))
        preds = np.where(~in_session & (preds != 1), 1, preds)

    equity = np.zeros(n); trades = []
    daily_pnl = 0.0; last_day = 0; i = 0

    while i < n - hold - 1:
        day = i // bpd
        if day != last_day: daily_pnl = 0.0; last_day = day
        if daily_pnl <= -0.01: i += 1; continue

        sig = preds[i]
        if sig not in (0, 2): i += 1; continue

        entry = opens[i+1] if opens[i+1] > 0 else closes[i]
        a = atr[i] if atr[i] > 0 else entry * 0.002
        is_long = sig == 2
        tp = entry + a * tp_m if is_long else entry - a * tp_m
        sl_p = entry - a * sl_m if is_long else entry + a * sl_m

        exit_ret = None
        for j in range(i+1, min(i+hold+1, n)):
            if is_long:
                if lows[j] <= sl_p: exit_ret = -(a * sl_m / entry) - cost; break
                if highs[j] >= tp: exit_ret = (a * tp_m / entry) - cost; break
            else:
                if highs[j] >= sl_p: exit_ret = -(a * sl_m / entry) - cost; break
                if lows[j] <= tp: exit_ret = (a * tp_m / entry) - cost; break

        if exit_ret is None:
            c_exit = closes[min(i+hold, n-1)]
            raw = (c_exit - entry) / entry
            exit_ret = (raw if is_long else -raw) - cost

        equity[min(i+hold, n-1)] += exit_ret
        trades.append(exit_ret)
        daily_pnl += exit_ret
        i += hold

    if not trades:
        return {"sharpe": 0, "win_rate": 0, "max_drawdown": 0, "total_return": 0, "total_trades": 0}

    tr = np.array(trades); wins = tr[tr > 0]
    n_days = max(1, n // bpd)
    trimmed = equity[:n_days * bpd]
    daily = trimmed.reshape(n_days, bpd).sum(axis=1) if len(trimmed) >= bpd else np.array([equity.sum()])
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0
    cum = np.cumsum(equity); peak = np.maximum.accumulate(cum)
    return {
        "sharpe": round(sharpe, 4),
        "win_rate": round(len(wins) / len(trades) * 100, 2),
        "max_drawdown": round(float(np.max(peak - cum) * 100), 2),
        "total_return": round(float(cum[-1] * 100), 4),
        "total_trades": len(trades),
    }


def grade(m):
    s, wr, dd = m["sharpe"], m["win_rate"], m["max_drawdown"]
    if s > 1.5 and wr > 55 and dd < 15: return "A"
    if s > 1.0 and wr > 50 and dd < 20: return "B"
    if s > 0.5 and wr > 45 and dd < 25: return "C"
    if s > 0 and m["total_return"] > 0: return "D"
    return "F"


def run(strategy, symbol, n_trials=15, n_folds=4):
    cfg = STRATEGY_MODULES[strategy]
    print(f"\n{'='*65}")
    print(f"  STRATEGY MODEL: {cfg['name']}  |  {symbol}  |  {n_folds} folds  |  {n_trials} trials")
    print(f"{'='*65}")

    m5 = _load_m5(symbol)
    print(f"  M5: {len(m5):,} bars")

    timestamps = m5["time"].values.astype(np.int64)
    closes = m5["close"].values; opens = m5["open"].values
    highs = m5["high"].values; lows = m5["low"].values
    volumes = m5["volume"].values.astype(float) if "volume" in m5.columns else np.ones(len(m5))

    # Compute strategy-specific features
    print(f"  Computing {cfg['name']} features...", flush=True)
    compute_fn = _load_feature_fn(strategy)
    args_fn = cfg["args"]
    result = compute_fn(**args_fn(opens, highs, lows, closes, volumes, timestamps))

    if isinstance(result, tuple):
        feature_names, X_all = result
    else:
        feature_names = list(result.keys())
        X_all = np.column_stack([result[k] for k in feature_names])
        X_all = np.nan_to_num(X_all, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    print(f"  Features: {len(feature_names)}")

    # ATR for labels
    atr_arr = pd.Series(highs - lows).rolling(14, min_periods=1).mean().values
    y_all = create_labels(closes, atr_arr, cfg["tp_mult"], cfg["sl_mult"], cfg["hold_bars"])
    print(f"  Labels: sell={np.sum(y_all==0):,}  hold={np.sum(y_all==1):,}  buy={np.sum(y_all==2):,}")

    # OOS split
    oos_ts = int(pd.Timestamp(OOS_START, tz="UTC").timestamp())
    oos_idx = int(np.argmax(timestamps >= oos_ts)) if (timestamps >= oos_ts).any() else len(X_all)
    print(f"  Train: {oos_idx:,}  OOS: {len(X_all) - oos_idx:,}")

    # Walk-forward folds
    n_train = oos_idx - WARMUP
    chunk = n_train // (n_folds + 1)

    print(f"\n  {'-'*60}")
    os.makedirs(MODEL_DIR, exist_ok=True)

    for k in range(n_folds):
        tr_end = WARMUP + (k + 1) * chunk
        ts_start = tr_end
        ts_end = WARMUP + (k + 2) * chunk

        X_tr = X_all[WARMUP:tr_end]; y_tr = y_all[WARMUP:tr_end]
        X_val = X_all[ts_start:ts_end]; y_val = y_all[ts_start:ts_end]
        val_split = int(len(X_tr) * 0.8)

        ts_s = pd.to_datetime(timestamps[ts_start], unit="s", utc=True).strftime("%Y-%m")
        ts_e = pd.to_datetime(timestamps[min(ts_end-1, len(timestamps)-1)], unit="s", utc=True).strftime("%Y-%m")
        print(f"\n  Fold {k+1}: Train {len(X_tr):,} | Test {ts_s}->{ts_e} ({len(X_val):,})")

        for mt in ["xgboost", "lightgbm"]:
            print(f"    [{mt}] {n_trials} trials...", end=" ", flush=True)
            model, cv = train_model(mt, X_tr[:val_split], y_tr[:val_split],
                                    X_tr[val_split:], y_tr[val_split:], n_trials)
            print(f"cv={cv:.4f}", flush=True)

            bt = backtest(model, X_val, closes[ts_start:ts_end], opens[ts_start:ts_end],
                         highs[ts_start:ts_end], lows[ts_start:ts_end],
                         atr_arr[ts_start:ts_end],
                         {"tp_mult": cfg["tp_mult"], "sl_mult": cfg["sl_mult"], "hold_bars": cfg["hold_bars"]},
                         times=timestamps[ts_start:ts_end])
            g = grade(bt)
            print(f"    [{mt}] Grade={g}  Sharpe={bt['sharpe']:.3f}  WR={bt['win_rate']:.1f}%  "
                  f"DD={bt['max_drawdown']:.1f}%  Trades={bt['total_trades']}")

    # Final model
    print(f"\n  {'-'*60}")
    print(f"  Final Model ({n_trials*2} trials)")
    X_full = X_all[WARMUP:oos_idx]; y_full = y_all[WARMUP:oos_idx]
    val_split = int(len(X_full) * 0.85)

    for mt in ["xgboost", "lightgbm"]:
        print(f"  [{mt}] {n_trials*2} trials...", end=" ", flush=True)
        model, cv = train_model(mt, X_full[:val_split], y_full[:val_split],
                                X_full[val_split:], y_full[val_split:], n_trials * 2)
        print(f"cv={cv:.4f}", flush=True)

        # OOS evaluation
        X_oos = X_all[oos_idx:]; t_oos = timestamps[oos_idx:]
        bt = backtest(model, X_oos, closes[oos_idx:], opens[oos_idx:],
                     highs[oos_idx:], lows[oos_idx:], atr_arr[oos_idx:],
                     {"tp_mult": cfg["tp_mult"], "sl_mult": cfg["sl_mult"], "hold_bars": cfg["hold_bars"]},
                     times=t_oos)
        g = grade(bt)
        print(f"  [{mt}] OOS Grade={g}  Sharpe={bt['sharpe']:.3f}  WR={bt['win_rate']:.1f}%  "
              f"DD={bt['max_drawdown']:.1f}%  Return={bt['total_return']:.1f}%  Trades={bt['total_trades']}")

        # Meta-labeler
        try:
            primary = model.predict(X_full)
            dirs = np.where(primary == 0, -1, np.where(primary == 2, 1, 0)).astype(np.float32)
            actual = np.where(y_full == 0, -1, np.where(y_full == 2, 1, 0)).astype(np.float32)
            ml = MetaLabeler(threshold=0.45)
            ml.fit(X_full, dirs, actual, feature_names=feature_names)
        except Exception:
            ml = None

        path = os.path.join(MODEL_DIR, f"rapid_{symbol}_M5_{strategy}_{mt}.joblib")
        joblib.dump({
            "model": model, "feature_names": feature_names,
            "strategy": strategy, "strategy_name": cfg["name"],
            "meta_labeler": ml, "meta_threshold": 0.45 if ml else None,
            "symbol": symbol, "oos_start": OOS_START, "oos_metrics": bt, "oos_grade": g,
            "pipeline_version": "rapid_v1",
            "execution": {"tp_mult": cfg["tp_mult"], "sl_mult": cfg["sl_mult"],
                         "hold_bars": cfg["hold_bars"]},
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }, path)
        tag = " +meta" if ml else ""
        print(f"  Saved: {os.path.basename(path)} (Grade={g}{tag})")

    print(f"\n{'='*65}")
    print(f"  {cfg['name']} training complete.")
    print(f"{'='*65}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True, choices=list(STRATEGY_MODULES.keys()))
    parser.add_argument("--symbol", default="US30")
    parser.add_argument("--trials", type=int, default=15)
    parser.add_argument("--folds", type=int, default=4)
    args = parser.parse_args()
    run(args.strategy, args.symbol, args.trials, args.folds)


if __name__ == "__main__":
    main()
