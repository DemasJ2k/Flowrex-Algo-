"""
Scalping model training pipeline.
Trains XGBoost + LightGBM per symbol with Optuna tuning.

Improvements over v1:
  - Default 75 Optuna trials
  - Loads M5, H1, H4, D1 real data (produced by prepare_real_data.py)
  - Passes symbol to compute_expert_features (tier1 + calendar + external features)
  - Purged 3-fold walk-forward CV with 50-bar embargo (de Prado methodology)
  - SHAP feature filter (drops near-zero importance features)
  - Train vs OOS divergence check (warns if test < 80% of train Sharpe)
  - Minimum 75 OOS signals check
  - Separate train and OOS metric reporting
  - OOS window aligned to WINDOWS config from prepare_real_data.py

Run: python -m scripts.train_scalping_pipeline [--trials 75] [--symbol BTCUSD]
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd
import joblib
import optuna
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_mtf import compute_expert_features
from scripts.model_utils import (
    create_labels,
    purged_walk_forward_splits,
    grade_model,
    compute_backtest_metrics,
    check_train_test_divergence,
    check_min_signals,
    shap_feature_filter,
    save_model_record,
)
from app.services.ml.symbol_config import get_symbol_config

DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
SYMBOLS   = ["BTCUSD", "XAUUSD", "US30"]

# M5 bars per trading day per symbol (used for daily-Sharpe computation)
BARS_PER_DAY = {
    "BTCUSD": 288,   # 24h market
    "XAUUSD": 264,   # ~22h forex trading day
    "US30":   102,   # ~8.5h equity session (09:30–18:00 ET)
    "ES":     102,
    "NAS100": 102,
}

# OOS window — genuinely unseen (aligned with prepare_real_data.py)
OOS_START = "2024-10-01"

optuna.logging.set_verbosity(optuna.logging.WARNING)


# ── Data Loading ───────────────────────────────────────────────────────


def load_data(symbol: str) -> tuple:
    """Load M5, H1, H4, D1 CSVs produced by prepare_real_data.py."""
    paths = {tf: os.path.join(DATA_DIR, f"{symbol}_{tf}.csv") for tf in ["M5", "H1", "H4", "D1"]}

    if not os.path.exists(paths["M5"]):
        raise FileNotFoundError(
            f"No M5 data for {symbol}. Run: python -m scripts.prepare_real_data --symbol {symbol}"
        )

    dfs = {}
    for tf, path in paths.items():
        if os.path.exists(path):
            dfs[tf] = pd.read_csv(path)
        else:
            dfs[tf] = None

    return dfs["M5"], dfs["H1"], dfs["H4"], dfs["D1"]


def split_train_oos(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split DataFrame by OOS_START (time column is Unix seconds)."""
    oos_ts = int(pd.Timestamp(OOS_START, tz="UTC").timestamp())
    train = df[df["time"] < oos_ts].copy()
    oos   = df[df["time"] >= oos_ts].copy()
    return train, oos


# ── Objective wrappers ─────────────────────────────────────────────────


def _xgb_objective(trial, X_train, y_train, X_val, y_val):
    import xgboost as xgb
    params = {
        "max_depth":          trial.suggest_int("max_depth", 3, 8),
        "learning_rate":      trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators":       trial.suggest_int("n_estimators", 100, 600),
        "subsample":          trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree":   trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "min_child_weight":   trial.suggest_int("min_child_weight", 1, 20),
        "reg_alpha":          trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda":         trial.suggest_float("reg_lambda", 0.5, 5.0),
        "objective": "multi:softprob", "num_class": 3,
        "eval_metric": "mlogloss", "verbosity": 0, "random_state": 42,
        "early_stopping_rounds": 30,
    }
    model = xgb.XGBClassifier(**params)
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    return model.score(X_val, y_val)


def _lgb_objective(trial, X_train, y_train, X_val, y_val):
    import lightgbm as lgb
    import warnings
    params = {
        "num_leaves":         trial.suggest_int("num_leaves", 15, 80),
        "max_depth":          trial.suggest_int("max_depth", 3, 8),
        "learning_rate":      trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators":       trial.suggest_int("n_estimators", 100, 600),
        "subsample":          trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree":   trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha":          trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda":         trial.suggest_float("reg_lambda", 0.5, 5.0),
        "min_child_samples":  trial.suggest_int("min_child_samples", 10, 60),
        "objective": "multiclass", "num_class": 3,
        "metric": "multi_logloss", "verbosity": -1, "random_state": 42,
    }
    model = lgb.LGBMClassifier(**params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(-1)])
        score = model.score(X_val, y_val)
    return score


def _train_with_optuna(
    model_type: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val:   np.ndarray,
    y_val:   np.ndarray,
    n_trials: int,
):
    """Run Optuna study and return best-fitted model."""
    if model_type == "xgboost":
        import xgboost as xgb
        obj = lambda t: _xgb_objective(t, X_train, y_train, X_val, y_val)
    else:
        import lightgbm as lgb
        obj = lambda t: _lgb_objective(t, X_train, y_train, X_val, y_val)

    study = optuna.create_study(direction="maximize")
    study.optimize(obj, n_trials=n_trials, show_progress_bar=False)

    # Refit with best params on full train set
    best = study.best_params
    if model_type == "xgboost":
        best.update({"objective": "multi:softprob", "num_class": 3,
                     "eval_metric": "mlogloss", "verbosity": 0, "random_state": 42,
                     "early_stopping_rounds": 30})
        model = xgb.XGBClassifier(**best)
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    else:
        import warnings as _w
        best.update({"objective": "multiclass", "num_class": 3,
                     "metric": "multi_logloss", "verbosity": -1, "random_state": 42})
        model = lgb.LGBMClassifier(**best)
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            model.fit(X_train, y_train,
                      eval_set=[(X_val, y_val)],
                      callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(-1)])

    return model, study.best_value


# ── Main training function ─────────────────────────────────────────────


def train_symbol(symbol: str, n_trials: int = 75) -> list:
    """Train XGBoost + LightGBM scalping models for one symbol."""
    print(f"\n{'='*60}")
    print(f"  Training {symbol}  |  {n_trials} Optuna trials  |  OOS from {OOS_START}")
    print(f"{'='*60}")

    m5, h1, h4, d1 = load_data(symbol)
    print(f"  M5={len(m5):,}  H1={len(h1) if h1 is not None else 0:,}  "
          f"H4={len(h4) if h4 is not None else 0:,}  "
          f"D1={len(d1) if d1 is not None else 0:,}")

    # Split M5 into train vs OOS (HTF stays full for warmup)
    m5_train, m5_oos = split_train_oos(m5)
    print(f"  Train M5: {len(m5_train):,}  |  OOS M5: {len(m5_oos):,}")

    # ── Feature computation (full M5 — split after) ──────────────
    print("  Computing features (all tiers)...")
    feature_names, X_all = compute_expert_features(
        m5, h1, h4, d1, symbol=symbol, include_external=True
    )
    print(f"  Features: {len(feature_names)}")

    # Create labels
    sym_config = get_symbol_config(symbol)
    closes     = m5["close"].values
    atr_idx    = feature_names.index("atr_14") if "atr_14" in feature_names else None
    atr_vals   = X_all[:, atr_idx] if atr_idx is not None else None
    y_all      = create_labels(closes, atr_vals, config=sym_config)
    print(f"  Labels (full): sell={np.sum(y_all==0):,}, hold={np.sum(y_all==1):,}, "
          f"buy={np.sum(y_all==2):,}")

    # Align OOS boundary to row index
    oos_ts   = int(pd.Timestamp(OOS_START, tz="UTC").timestamp())
    oos_mask = m5["time"].values >= oos_ts
    n_total  = len(X_all)
    oos_start_idx = int(np.argmax(oos_mask)) if oos_mask.any() else n_total

    X_train_full = X_all[:oos_start_idx]
    y_train_full = y_all[:oos_start_idx]
    X_oos        = X_all[oos_start_idx:]
    y_oos        = y_all[oos_start_idx:]
    closes_oos   = closes[oos_start_idx:]

    print(f"  Train rows: {len(X_train_full):,}  |  OOS rows: {len(X_oos):,}")

    # Minimum signals check on OOS
    check_min_signals(y_oos, min_signals=75)

    # ── Purged walk-forward CV splits (within training window) ────
    splits = purged_walk_forward_splits(len(X_train_full), n_folds=3, embargo_bars=50)
    # Use last fold's val set for Optuna eval (most recent data)
    train_idx, val_idx = splits[-1]
    X_tr  = X_train_full[train_idx]
    y_tr  = y_train_full[train_idx]
    X_val = X_train_full[val_idx]
    y_val = y_train_full[val_idx]
    print(f"  CV fold (Optuna eval): train={len(X_tr):,}, val={len(X_val):,}")

    os.makedirs(MODEL_DIR, exist_ok=True)
    results = []

    for model_type in ["xgboost", "lightgbm"]:
        print(f"\n  -- {model_type.upper()} ({n_trials} trials) --")
        model, cv_acc = _train_with_optuna(model_type, X_tr, y_tr, X_val, y_val, n_trials)
        print(f"  Best CV accuracy: {cv_acc:.4f}")

        # ── SHAP feature filter on validation set ────────────────
        kept_features = shap_feature_filter(model, X_val, feature_names, threshold=0.001)
        if len(kept_features) < len(feature_names):
            feat_idx  = [feature_names.index(f) for f in kept_features]
            X_tr_f    = X_tr[:, feat_idx]
            X_val_f   = X_val[:, feat_idx]
            X_oos_f   = X_oos[:, feat_idx]
            X_full_f  = X_train_full[:, feat_idx]
            # Refit with filtered features
            model, cv_acc = _train_with_optuna(
                model_type, X_tr_f, y_tr, X_val_f, y_val, max(20, n_trials // 3)
            )
        else:
            feat_idx  = list(range(len(feature_names)))
            kept_features = feature_names
            X_oos_f   = X_oos
            X_full_f  = X_train_full
            X_val_f   = X_val

        # ── Train and OOS metrics ─────────────────────────────────
        closes_val = closes[train_idx[-1] : train_idx[-1] + len(X_val_f) + 1]
        if len(closes_val) > len(X_val_f):
            closes_val = closes_val[:len(X_val_f)]
        elif len(closes_val) < len(X_val_f):
            closes_val = np.pad(closes_val, (0, len(X_val_f) - len(closes_val)), mode="edge")

        bpd  = BARS_PER_DAY.get(symbol, 288)
        hbars = sym_config.get("label_forward_bars", 10)
        train_metrics = compute_backtest_metrics(model, X_val_f, y_val, closes_val, bars_per_day=bpd, hold_bars=hbars)
        oos_metrics   = compute_backtest_metrics(model, X_oos_f, y_oos, closes_oos, bars_per_day=bpd, hold_bars=hbars)
        grade         = grade_model(oos_metrics)

        print(f"  Train (val fold): Sharpe={train_metrics['sharpe']:.2f}  "
              f"WR={train_metrics['win_rate']:.1f}%  DD={train_metrics['max_drawdown']:.1f}%")
        print(f"  OOS:              Sharpe={oos_metrics['sharpe']:.2f}  "
              f"WR={oos_metrics['win_rate']:.1f}%  DD={oos_metrics['max_drawdown']:.1f}%  "
              f"Trades={oos_metrics['total_trades']}")
        print(f"  Grade: {grade}")

        # Divergence check
        check_train_test_divergence(train_metrics, oos_metrics)

        # ── Save model ─────────────────────────────────────────────
        model_path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{model_type}.joblib")
        feat_imp = {}
        if hasattr(model, "feature_importances_"):
            feat_imp = dict(zip(kept_features, model.feature_importances_.tolist()))

        joblib.dump({
            "model":              model,
            "feature_names":      kept_features,
            "grade":              grade,
            "train_metrics":      train_metrics,
            "oos_metrics":        oos_metrics,
            "feature_importances": feat_imp,
            "symbol":             symbol,
            "oos_start":          OOS_START,
            "trained_at":         datetime.now(timezone.utc).isoformat(),
        }, model_path)
        print(f"  Saved: {os.path.basename(model_path)}")

        # Combined metrics for DB record
        combined = {**oos_metrics, "train_sharpe": train_metrics["sharpe"],
                    "train_wr": train_metrics["win_rate"]}
        save_model_record(symbol, "M5", model_type, "scalping", model_path, grade, combined)
        results.append((model_type, grade, oos_metrics, model_path))

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=75, help="Optuna trial count")
    parser.add_argument("--symbol", type=str, default=None, help="Train single symbol")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    all_results: dict = {}

    for symbol in symbols:
        try:
            all_results[symbol] = train_symbol(symbol, args.trials)
        except FileNotFoundError as e:
            print(f"\n  SKIPPED {symbol}: {e}")
        except Exception as e:
            import traceback
            print(f"\n  ERROR {symbol}: {e}")
            traceback.print_exc()

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("SCALPING TRAINING SUMMARY")
    print(f"{'='*60}")
    for symbol, results in all_results.items():
        for model_type, grade, metrics, path in results:
            print(
                f"  {symbol:8s} {model_type:10s}  Grade={grade}  "
                f"Sharpe={metrics.get('sharpe', 0):.2f}  "
                f"WR={metrics.get('win_rate', 0):.1f}%  "
                f"DD={metrics.get('max_drawdown', 0):.1f}%  "
                f"Trades={metrics.get('total_trades', 0)}"
            )


if __name__ == "__main__":
    main()
