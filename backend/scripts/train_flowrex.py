"""
train_flowrex.py - Flowrex Agent v2 Walk-Forward Training

3-model ensemble: XGBoost + LightGBM + CatBoost
120 curated features from features_flowrex.py
Walk-forward 4-fold validation with Optuna hyperparameter tuning
SHAP pruning: keep top 80-100 of 120 features

Usage:
    cd backend
    python -m scripts.train_flowrex --symbol US30 [--trials 15] [--folds 4]
    python -m scripts.train_flowrex --symbol all   # train all 5 symbols
"""
import os
import sys
import gc
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

from app.services.ml.features_flowrex import compute_flowrex_features
from app.services.ml.symbol_config import get_symbol_config
from scripts.model_utils import (
    create_labels, compute_backtest_metrics, grade_model, shap_feature_filter,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)
OOS_START = "2026-01-01"
WARMUP = 500
MAX_M5_BARS = 500_000
ALL_SYMBOLS = ["US30", "BTCUSD", "XAUUSD", "ES", "NAS100"]

RISK_CONFIG = {
    "max_drawdown_pct": 0.10,
    "daily_loss_limit_pct": 0.03,
    "risk_per_trade_pct": 0.01,
    "max_trades_per_day": 5,
}

MODEL_TYPES = ["xgboost", "lightgbm", "catboost"]


# ── Data loading ─────────────────────────────────────────────────────────

def _normalize_ohlcv(df):
    df = df.copy()
    if "ts_event" in df.columns and "time" not in df.columns:
        df["time"] = (
            pd.to_datetime(df["ts_event"]).values.astype("datetime64[s]").astype(np.int64)
        )
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


def load_ohlcv(symbol):
    m5 = _load_tf(symbol, "M5")
    h1 = _load_tf(symbol, "H1")
    h4 = _load_tf(symbol, "H4")
    d1 = _load_tf(symbol, "D1")
    if m5 is None:
        raise FileNotFoundError(f"No M5 data for {symbol}")
    if len(m5) > MAX_M5_BARS:
        print(f"  [INFO] Capping M5 from {len(m5):,} to {MAX_M5_BARS:,} bars")
        m5 = m5.iloc[-MAX_M5_BARS:].reset_index(drop=True)
        start_ts = m5["time"].iloc[0]
        if h1 is not None:
            h1 = h1[h1["time"] >= start_ts].reset_index(drop=True)
        if h4 is not None:
            h4 = h4[h4["time"] >= start_ts].reset_index(drop=True)
        if d1 is not None:
            d1 = d1[d1["time"] >= start_ts].reset_index(drop=True)
    return m5, h1, h4, d1


# ── Walk-forward folds ───────────────────────────────────────────────────

def get_wf_folds(timestamps, oos_start, n_folds=4):
    oos_ts = int(pd.Timestamp(oos_start, tz="UTC").timestamp())
    oos_idx = int(np.argmax(timestamps >= oos_ts)) if (timestamps >= oos_ts).any() else len(timestamps)
    n_train = oos_idx - WARMUP
    if n_train < n_folds * 500:
        n_folds = max(2, n_folds // 2)
    chunk = n_train // (n_folds + 1)
    folds = []
    for k in range(n_folds):
        train_end = WARMUP + (k + 1) * chunk
        test_start = train_end
        test_end = WARMUP + (k + 2) * chunk
        folds.append({"fold": k + 1, "train_end": train_end,
                      "test_start": test_start, "test_end": test_end})
    return folds, oos_idx


# ── Model training with Optuna ───────────────────────────────────────────

def _xgb_objective(trial, Xtr, ytr, Xval, yval):
    import xgboost as xgb
    params = {
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 100, 500),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "min_child_weight": trial.suggest_int("min_child_weight", 1, 20),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 5.0),
        "objective": "multi:softprob", "num_class": 3,
        "eval_metric": "mlogloss", "verbosity": 0, "random_state": 42,
        "early_stopping_rounds": 20,
    }
    m = xgb.XGBClassifier(**params)
    m.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    return m.score(Xval, yval)


def _lgb_objective(trial, Xtr, ytr, Xval, yval):
    import lightgbm as lgb
    params = {
        "num_leaves": trial.suggest_int("num_leaves", 15, 80),
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 100, 500),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 5.0),
        "min_child_samples": trial.suggest_int("min_child_samples", 10, 60),
        "objective": "multiclass", "num_class": 3,
        "metric": "multi_logloss", "verbosity": -1, "random_state": 42,
    }
    m = lgb.LGBMClassifier(**params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(Xtr, ytr, eval_set=[(Xval, yval)],
              callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)])
    return m.score(Xval, yval)


def _cat_objective(trial, Xtr, ytr, Xval, yval):
    import gc
    from catboost import CatBoostClassifier
    params = {
        "depth": trial.suggest_int("depth", 4, 6),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "iterations": trial.suggest_int("iterations", 200, 400),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1.0, 10.0),
        "bagging_temperature": trial.suggest_float("bagging_temperature", 0.0, 1.0),
        "random_strength": trial.suggest_float("random_strength", 0.0, 2.0),
        "loss_function": "MultiClass", "classes_count": 3,
        "verbose": 0, "random_seed": 42,
        "early_stopping_rounds": 20,
        "thread_count": 1,
        "allow_writing_files": False,
        "boosting_type": "Plain",
    }
    m = CatBoostClassifier(**params)
    m.fit(Xtr, ytr, eval_set=(Xval, yval), verbose=0)
    score = m.score(Xval, yval)
    del m
    gc.collect()
    return score


def train_model(model_type, Xtr, ytr, Xval, yval, n_trials):
    objectives = {
        "xgboost": _xgb_objective,
        "lightgbm": _lgb_objective,
        "catboost": _cat_objective,
    }
    obj_fn = objectives[model_type]
    study = optuna.create_study(direction="maximize")
    study.optimize(lambda t: obj_fn(t, Xtr, ytr, Xval, yval),
                   n_trials=n_trials, show_progress_bar=False)
    best = study.best_params

    if model_type == "xgboost":
        import xgboost as xgb
        best.update({"objective": "multi:softprob", "num_class": 3,
                     "eval_metric": "mlogloss", "verbosity": 0,
                     "random_state": 42, "early_stopping_rounds": 20})
        model = xgb.XGBClassifier(**best)
        model.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    elif model_type == "lightgbm":
        import lightgbm as lgb
        best.update({"objective": "multiclass", "num_class": 3,
                     "metric": "multi_logloss", "verbosity": -1, "random_state": 42})
        model = lgb.LGBMClassifier(**best)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(Xtr, ytr, eval_set=[(Xval, yval)],
                      callbacks=[lgb.early_stopping(20, verbose=False),
                                 lgb.log_evaluation(-1)])
    else:  # catboost
        from catboost import CatBoostClassifier
        best.update({"loss_function": "MultiClass", "classes_count": 3,
                     "verbose": 0, "random_seed": 42, "early_stopping_rounds": 20,
                     "thread_count": 2, "allow_writing_files": False})
        model = CatBoostClassifier(**best)
        model.fit(Xtr, ytr, eval_set=(Xval, yval), verbose=0)

    return model, study.best_value


# ── SHAP ─────────────────────────────────────────────────────────────────

def shap_importance_table(model, X, feature_names, top_n=20):
    try:
        import shap
        sample = X[:3000] if len(X) > 3000 else X
        explainer = shap.TreeExplainer(model)
        shap_vals = explainer.shap_values(sample)
        if isinstance(shap_vals, list):
            mean_abs = np.mean([np.abs(sv).mean(axis=0) for sv in shap_vals], axis=0)
        elif np.asarray(shap_vals).ndim == 3:
            mean_abs = np.abs(shap_vals).mean(axis=(0, 2))
        else:
            mean_abs = np.abs(shap_vals).mean(axis=0)
        total = mean_abs.sum()
        if total == 0:
            return []
        ranked = sorted(zip(feature_names, mean_abs / total), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]
    except Exception as e:
        print(f"  [WARN] SHAP failed: {e}")
        return []


# ── Main training ────────────────────────────────────────────────────────

def run_flowrex_training(symbol="US30", n_trials=15, n_folds=4):
    cfg = get_symbol_config(symbol)
    cost_bps = cfg.get("cost_bps", 5.0)
    slippage_bps = cfg.get("slippage_bps", 1.0)
    tp_mult = 1.2
    sl_mult = 0.8
    bpd = cfg.get("bars_per_day", 288)
    hold_bars = 10

    print(f"\n{'=' * 65}")
    print(f"  FLOWREX AGENT v2: {symbol}  |  {n_folds} folds  |  {n_trials} trials/fold")
    print(f"  3-model ensemble: XGBoost + LightGBM + CatBoost")
    print(f"{'=' * 65}")

    # 1. Load data
    m5, h1, h4, d1 = load_ohlcv(symbol)
    print(f"  M5={len(m5):,}  H1={len(h1) if h1 is not None else 0:,}  "
          f"H4={len(h4) if h4 is not None else 0:,}  D1={len(d1) if d1 is not None else 0:,}")

    timestamps = m5["time"].values.astype(np.int64)
    closes = m5["close"].values
    opens = m5["open"].values
    highs = m5["high"].values
    lows = m5["low"].values

    # 2. Compute Flowrex features (120)
    print("  Computing Flowrex v2 features (120)...", flush=True)
    feature_names, X_all = compute_flowrex_features(m5, h1, h4, d1, symbol=symbol)
    print(f"  Features: {len(feature_names)}")

    # 3. Create labels (triple barrier)
    from app.services.backtest.indicators import atr as atr_fn
    atr_vals = atr_fn(highs, lows, closes, 14)
    y_all = create_labels(closes, atr_vals, forward_bars=hold_bars, atr_mult=tp_mult)

    # Force HOLD in low-vol periods
    valid_atr = atr_vals[~np.isnan(atr_vals)]
    if len(valid_atr) > 0:
        atr_25 = np.percentile(valid_atr, 25)
        low_vol = atr_vals < atr_25
        low_vol[np.isnan(atr_vals)] = True
        forced = low_vol & (y_all != 1)
        y_all[forced] = 1
        print(f"  Low-vol filter: {forced.sum():,} labels forced to HOLD "
              f"({forced.sum() / len(y_all) * 100:.1f}%)")

    print(f"  Labels: sell={np.sum(y_all == 0):,}  hold={np.sum(y_all == 1):,}  "
          f"buy={np.sum(y_all == 2):,}")

    # 4. Walk-forward
    folds, oos_idx = get_wf_folds(timestamps, OOS_START, n_folds)

    X_oos = X_all[oos_idx:]
    y_oos = y_all[oos_idx:]
    closes_oos = closes[oos_idx:]
    opens_oos = opens[oos_idx:]
    highs_oos = highs[oos_idx:]
    lows_oos = lows[oos_idx:]
    atr_oos = atr_vals[oos_idx:]

    print(f"  Train rows: {oos_idx:,}  |  OOS rows: {len(X_oos):,}")
    print(f"\n  {'-' * 60}")
    print(f"  Walk-Forward ({len(folds)} folds)")
    print(f"  {'-' * 60}")

    os.makedirs(MODEL_DIR, exist_ok=True)
    wf_results = []

    for fold_info in folds:
        fnum = fold_info["fold"]
        tr_end = fold_info["train_end"]
        ts_start = fold_info["test_start"]
        ts_end = fold_info["test_end"]

        X_tr = X_all[WARMUP:tr_end]
        y_tr = y_all[WARMUP:tr_end]
        X_val = X_all[ts_start:ts_end]
        y_val = y_all[ts_start:ts_end]

        val_split = int(len(X_tr) * 0.8)
        Xtr_opt, ytr_opt = X_tr[:val_split], y_tr[:val_split]
        Xv_opt, yv_opt = X_tr[val_split:], y_tr[val_split:]

        ts_start_h = pd.to_datetime(
            timestamps[ts_start], unit="s", utc=True
        ).strftime("%Y-%m")
        ts_end_h = pd.to_datetime(
            timestamps[min(ts_end - 1, len(timestamps) - 1)], unit="s", utc=True
        ).strftime("%Y-%m")

        print(f"\n  Fold {fnum}: Train ({len(X_tr):,}) | "
              f"Test {ts_start_h}->{ts_end_h} ({len(X_val):,})")

        for model_type in MODEL_TYPES:
            print(f"    [{model_type}] {n_trials} trials...", end=" ", flush=True)
            model, cv_acc = train_model(
                model_type, Xtr_opt, ytr_opt, Xv_opt, yv_opt, n_trials
            )
            print(f"cv_acc={cv_acc:.4f}", flush=True)

            c_val = closes[ts_start:ts_end]
            o_val = opens[ts_start:ts_end]
            h_val = highs[ts_start:ts_end]
            lo_val = lows[ts_start:ts_end]
            a_val = atr_vals[ts_start:ts_end]
            t_val = timestamps[ts_start:ts_end]

            metrics = compute_backtest_metrics(
                model, X_val, y_val, c_val,
                opens_test=o_val, highs_test=h_val, lows_test=lo_val,
                atr_test=a_val, times_test=t_val,
                cost_bps=cost_bps, slippage_bps=slippage_bps,
                bars_per_day=bpd, hold_bars=hold_bars,
                tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
                trend_filter=False,
            )
            grade = grade_model(metrics)
            print(f"    [{model_type}] Grade={grade}  Sharpe={metrics['sharpe']:.3f}  "
                  f"WR={metrics['win_rate']:.1f}%  DD={metrics['max_drawdown']:.1f}%  "
                  f"Trades={metrics['total_trades']}")

            wf_results.append({
                "fold": fnum, "model": model_type, "grade": grade, **metrics
            })

            # Free memory between models
            del model
            gc.collect()

    # 5. Final models on full training set
    print(f"\n  {'-' * 60}")
    print(f"  Final Models (full train -> {OOS_START})")
    print(f"  {'-' * 60}")

    X_full_tr = X_all[WARMUP:oos_idx]
    y_full_tr = y_all[WARMUP:oos_idx]
    val_split = int(len(X_full_tr) * 0.85)

    final_models = {}
    for model_type in MODEL_TYPES:
        print(f"  [{model_type}] {n_trials * 2} trials...", end=" ", flush=True)
        model, cv_acc = train_model(
            model_type,
            X_full_tr[:val_split], y_full_tr[:val_split],
            X_full_tr[val_split:], y_full_tr[val_split:],
            n_trials * 2,
        )
        print(f"cv_acc={cv_acc:.4f}", flush=True)
        final_models[model_type] = model
        gc.collect()

    # 6. OOS evaluation
    print(f"\n  {'-' * 60}")
    print(f"  True OOS: {OOS_START} -> present ({len(X_oos):,} bars)")
    print(f"  {'-' * 60}")

    oos_results = {}
    for model_type, model in final_models.items():
        m = compute_backtest_metrics(
            model, X_oos, y_oos, closes_oos,
            opens_test=opens_oos, highs_test=highs_oos, lows_test=lows_oos,
            atr_test=atr_oos, times_test=timestamps[oos_idx:],
            cost_bps=cost_bps, slippage_bps=slippage_bps,
            bars_per_day=bpd, hold_bars=hold_bars,
            tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
            trend_filter=False,
        )
        g = grade_model(m)
        oos_results[model_type] = (g, m)
        print(f"  [{model_type}] Grade={g}  Sharpe={m['sharpe']:.3f}  "
              f"WR={m['win_rate']:.1f}%  DD={m['max_drawdown']:.1f}%  "
              f"Return={m['total_return']:.1f}%  Trades={m['total_trades']}")

    # 7. SHAP feature importance
    print(f"\n  {'-' * 60}")
    print(f"  SHAP Feature Importance (XGBoost)")
    print(f"  {'-' * 60}")

    ranked = shap_importance_table(
        final_models["xgboost"], X_full_tr[:val_split], feature_names, top_n=25
    )
    if ranked:
        for rank, (fname, imp) in enumerate(ranked, 1):
            bar = "#" * int(imp * 300)
            print(f"  {rank:2d}. {fname:<40}  {imp * 100:5.2f}%  {bar}")

    # 8. Save models
    print(f"\n  {'-' * 60}")
    print(f"  Saving Models")
    print(f"  {'-' * 60}")

    for model_type, model in final_models.items():
        grade, oos_m = oos_results[model_type]
        path = os.path.join(MODEL_DIR, f"flowrex_{symbol}_M5_{model_type}.joblib")
        save_dict = {
            "model": model,
            "feature_names": feature_names,
            "grade": grade,
            "oos_metrics": oos_m,
            "wf_results": [r for r in wf_results if r["model"] == model_type],
            "symbol": symbol,
            "oos_start": OOS_START,
            "risk_config": RISK_CONFIG,
            "execution": {
                "fill": "open[i+1]",
                "tp_atr_mult": tp_mult,
                "sl_atr_mult": sl_mult,
                "cost_bps": cost_bps,
                "slippage_bps": slippage_bps,
                "hold_bars": hold_bars,
            },
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "flowrex_v2",
            "agent_type": "flowrex_v2",
            "ensemble_type": model_type,
        }
        joblib.dump(save_dict, path)
        print(f"  Saved: {os.path.basename(path)}  (Grade={grade})")

    gc.collect()
    return wf_results, oos_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Flowrex Agent v2 Training")
    parser.add_argument("--symbol", default="US30",
                        help="Symbol to train (or 'all' for all 5)")
    parser.add_argument("--trials", type=int, default=15,
                        help="Optuna trials per fold per model")
    parser.add_argument("--folds", type=int, default=4,
                        help="Walk-forward folds")
    args = parser.parse_args()

    symbols = ALL_SYMBOLS if args.symbol.lower() == "all" else [args.symbol]

    for sym in symbols:
        try:
            run_flowrex_training(sym, args.trials, args.folds)
        except Exception as e:
            print(f"\n  [ERROR] {sym}: {e}")
            import traceback
            traceback.print_exc()
        gc.collect()

    print(f"\n{'=' * 65}")
    print(f"  Training complete for: {', '.join(symbols)}")
    print(f"{'=' * 65}")
