"""
train_walkforward.py - Realistic Walk-Forward Training & Evaluation

What this script does vs train_scalping_pipeline.py:
  1. ALL features used (ICT/SMC + Williams + Quant + COT + correlations)
  2. Expanding-window walk-forward: 4 folds within train period, each fold retrains
  3. Strategy-informed labels: triple barrier + ICT quality scoring + sample weights
  4. Meta-labeling: secondary model filters low-confidence trades (Lopez de Prado)
  5. Realistic execution:
       - Fill at open[i+1]  (not close[i])
       - TP/SL via intra-bar OHLC detection
       - Slippage + spread cost (per symbol_config)
  6. Combined WF metrics (all fold OOS predictions concatenated)
  7. True OOS metrics (Oct 2024+ holdout)
  8. SHAP feature importance league table (top 20, on final model)

Usage:
    cd backend
    python -m scripts.train_walkforward --symbol US30 [--trials 20] [--folds 4]
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

# Force UTF-8 stdout/stderr on Windows (avoids cp1252 encode errors)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.symbol_config import get_symbol_config
from scripts.model_utils import (
    create_labels,
    compute_backtest_metrics,
    grade_model,
    shap_feature_filter,
    save_model_record,
)
from scripts.strategy_labels import compute_strategy_labels
from app.services.ml.meta_labeler_v2 import MetaLabeler
from app.services.agent.m5_signal_generator import generate_scalp_signals

DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR     = os.path.join(DATA_DIR, "ml_models")
# History Data folder — contains longer CSV history (M1/M5/M15/H1/H4 per symbol)
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)
OOS_START = "2026-01-01"
WARMUP    = 500   # bars before any training slice starts
MAX_M5_BARS = 400_000  # Cap M5 bars — 400k for more training data with 2025 included


# ── Data loading ────────────────────────────────────────────────────────────

ALL_SYMBOLS = ["BTCUSD", "XAUUSD", "US30", "NAS100", "ES", "EURUSD", "GBPUSD"]


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise History Data format → pipeline format.
    History Data uses 'ts_event' (ISO datetime) instead of 'time' (Unix seconds).
    Uses datetime64[s] cast to avoid microsecond-precision errors from utc=True.
    """
    df = df.copy()
    if "ts_event" in df.columns and "time" not in df.columns:
        # Convert ISO datetime → Unix seconds via numpy datetime64[s] (avoids pandas utc microsecond issue)
        df["time"] = (
            pd.to_datetime(df["ts_event"])
            .values.astype("datetime64[s]")
            .astype(np.int64)
        )
        df = df.drop(columns=["ts_event"])
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("time").reset_index(drop=True)


def _load_tf(symbol: str, tf: str) -> pd.DataFrame | None:
    """
    Load a single timeframe CSV, preferring History Data (more history).
    Falls back to the legacy backend/data/ folder.
    """
    hist_path = os.path.join(HIST_DATA_DIR, symbol, f"{symbol}_{tf}.csv")
    curr_path = os.path.join(DATA_DIR, f"{symbol}_{tf}.csv")
    if os.path.exists(hist_path):
        return _normalize_ohlcv(pd.read_csv(hist_path))
    if os.path.exists(curr_path):
        return pd.read_csv(curr_path)
    return None


def load_ohlcv(symbol: str) -> tuple:
    """Load M5, M15, H1, H4, D1 — prefers History Data folder for max coverage.
    Caps M5 to MAX_M5_BARS (most recent) to keep feature computation fast."""
    m5  = _load_tf(symbol, "M5")
    m15 = _load_tf(symbol, "M15")
    h1  = _load_tf(symbol, "H1")
    h4  = _load_tf(symbol, "H4")
    d1  = _load_tf(symbol, "D1")
    if m5 is None:
        raise FileNotFoundError(f"No M5 data for {symbol}")
    # Cap M5 to most recent MAX_M5_BARS to prevent OOM / timeout on huge datasets
    if len(m5) > MAX_M5_BARS:
        print(f"  [INFO] Capping M5 from {len(m5):,} to {MAX_M5_BARS:,} bars (most recent)")
        m5 = m5.iloc[-MAX_M5_BARS:].reset_index(drop=True)
        # Also trim HTF data to matching time range
        start_ts = m5["time"].iloc[0]
        if m15 is not None:
            m15 = m15[m15["time"] >= start_ts].reset_index(drop=True)
        if h1 is not None:
            h1 = h1[h1["time"] >= start_ts].reset_index(drop=True)
        if h4 is not None:
            h4 = h4[h4["time"] >= start_ts].reset_index(drop=True)
        if d1 is not None:
            d1 = d1[d1["time"] >= start_ts].reset_index(drop=True)
    return m5, m15, h1, h4, d1


def load_peer_m5(symbol: str) -> dict:
    """Load M5 data for all peer symbols (not the symbol itself)."""
    peers = {}
    for sym in ALL_SYMBOLS:
        if sym == symbol:
            continue
        df = _load_tf(sym, "M5")
        if df is not None:
            peers[sym] = df
    return peers


# ── Walk-forward fold generation ────────────────────────────────────────────

def get_wf_folds(timestamps: np.ndarray, oos_start: str, n_folds: int = 4) -> list:
    """
    Generate expanding-window folds within the training period.
    Each fold: train=[WARMUP, fold_end), test=[fold_end, next_fold_end)

    Returns list of dicts: {fold, train_end, test_start, test_end}
    """
    oos_ts  = int(pd.Timestamp(oos_start, tz="UTC").timestamp())
    oos_idx = int(np.argmax(timestamps >= oos_ts)) if (timestamps >= oos_ts).any() else len(timestamps)

    n_train = oos_idx - WARMUP
    if n_train < n_folds * 500:
        # Too little data — use 2 folds
        n_folds = max(2, n_folds // 2)

    # Divide training period into (n_folds + 1) equal chunks
    # First n_folds chunks are train; each chunk[k] is tested on chunk[k+1]
    chunk = n_train // (n_folds + 1)
    folds = []
    for k in range(n_folds):
        train_end  = WARMUP + (k + 1) * chunk
        test_start = train_end
        test_end   = WARMUP + (k + 2) * chunk
        folds.append({
            "fold":       k + 1,
            "train_end":  train_end,
            "test_start": test_start,
            "test_end":   test_end,
        })
    return folds, oos_idx


# ── Model training ──────────────────────────────────────────────────────────

def _xgb_objective(trial, Xtr, ytr, Xval, yval):
    import xgboost as xgb
    params = {
        "max_depth":          trial.suggest_int("max_depth", 3, 8),
        "learning_rate":      trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators":       trial.suggest_int("n_estimators", 100, 500),
        "subsample":          trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree":   trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "min_child_weight":   trial.suggest_int("min_child_weight", 1, 20),
        "reg_alpha":          trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda":         trial.suggest_float("reg_lambda", 0.5, 5.0),
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
        "num_leaves":        trial.suggest_int("num_leaves", 15, 80),
        "max_depth":         trial.suggest_int("max_depth", 3, 8),
        "learning_rate":     trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators":      trial.suggest_int("n_estimators", 100, 500),
        "subsample":         trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree":  trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha":         trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda":        trial.suggest_float("reg_lambda", 0.5, 5.0),
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


def train_model(model_type: str, Xtr, ytr, Xval, yval, n_trials: int,
                sw_tr=None, sw_val=None):
    """Optuna-tune + refit a model with optional sample weights. Returns (model, best_cv_acc)."""
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
                     "random_state": 42, "early_stopping_rounds": 20})
        model = xgb.XGBClassifier(**best)
        model.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False,
                  sample_weight=sw_tr)
    else:
        best.update({"objective": "multiclass", "num_class": 3,
                     "metric": "multi_logloss", "verbosity": -1, "random_state": 42})
        model = lgb.LGBMClassifier(**best)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(Xtr, ytr, eval_set=[(Xval, yval)],
                      sample_weight=sw_tr,
                      callbacks=[lgb.early_stopping(20, verbose=False),
                                 lgb.log_evaluation(-1)])

    return model, study.best_value


# ── SHAP feature importance ──────────────────────────────────────────────────

def shap_importance_table(model, X: np.ndarray, feature_names: list, top_n: int = 20) -> list:
    """Return sorted list of (feature_name, mean_abs_shap) for top_n features."""
    try:
        import shap
        sample = X[:3000] if len(X) > 3000 else X
        explainer  = shap.TreeExplainer(model)
        shap_vals  = explainer.shap_values(sample)
        if isinstance(shap_vals, list):
            mean_abs = np.mean([np.abs(sv).mean(axis=0) for sv in shap_vals], axis=0)
        elif np.asarray(shap_vals).ndim == 3:
            mean_abs = np.abs(shap_vals).mean(axis=(0, 2))
        else:
            mean_abs = np.abs(shap_vals).mean(axis=0)
        total = mean_abs.sum()
        ranked = sorted(zip(feature_names, mean_abs / total),
                        key=lambda x: x[1], reverse=True)
        return ranked[:top_n]
    except Exception as e:
        print(f"  [WARN] SHAP failed: {e}")
        return []


# ── Main walk-forward loop ───────────────────────────────────────────────────

def run_walkforward(symbol: str, n_trials: int = 20, n_folds: int = 4):
    cfg      = get_symbol_config(symbol)
    cost_bps     = cfg.get("cost_bps", 5.0)
    slippage_bps = cfg.get("slippage_bps", 1.0)
    tp_mult      = cfg.get("tp_atr_mult", 1.0)
    sl_mult      = cfg.get("sl_atr_mult", 0.8)
    bpd          = cfg.get("bars_per_day", 288)
    # hold_bars: use explicit config value, else fall back to label_forward_bars
    hold_bars    = cfg.get("hold_bars", cfg.get("label_forward_bars", 10))
    # trend_filter: per-symbol override (default True; set False for highly mean-reverting assets)
    use_trend_filter = cfg.get("trend_filter", True)

    print(f"\n{'='*65}")
    print(f"  WALK-FORWARD: {symbol}  |  {n_folds} folds  |  {n_trials} trials/fold")
    print(f"  Execution: open[i+1] fill | TP={tp_mult}×ATR SL={sl_mult}×ATR | "
          f"cost={cost_bps+slippage_bps:.1f}bps total")
    print(f"{'='*65}")

    # ── 1. Load data ─────────────────────────────────────────────────────
    m5, m15, h1, h4, d1 = load_ohlcv(symbol)
    print(f"  M5={len(m5):,}  M15={len(m15) if m15 is not None else 0:,}  "
          f"H1={len(h1) if h1 is not None else 0:,}  "
          f"H4={len(h4) if h4 is not None else 0:,}  D1={len(d1) if d1 is not None else 0:,}")

    # Skip peer correlations during walk-forward (saves ~50% compute time + memory)
    # Correlation features are low-priority and can be added in monthly retrain
    peer_m5 = None
    print("  Peer correlations: skipped (saves compute time)", flush=True)

    timestamps = m5["time"].values.astype(np.int64)
    closes     = m5["close"].values
    opens      = m5["open"].values
    highs      = m5["high"].values
    lows       = m5["low"].values

    # ── 2. Compute features ONCE on full dataset (no leakage — all rolling) ─
    print("  Computing features (all tiers + correlations, full dataset)...", flush=True)
    feature_names, X_all = compute_expert_features(
        m5, h1, h4, d1, symbol=symbol, include_external=True,
        other_m5=peer_m5 if peer_m5 else None,
        m15_bars=m15,
    )
    print(f"  Features: {len(feature_names)}")

    # ── 3. Create strategy-informed labels on full dataset ──────────────
    atr_idx  = feature_names.index("atr_14") if "atr_14" in feature_names else None
    atr_vals = X_all[:, atr_idx] if atr_idx is not None else None

    # Strategy labels: triple barrier + ICT quality scoring
    print("  Computing strategy-informed labels (triple barrier + ICT scoring)...", flush=True)
    strat_labels_df = compute_strategy_labels(
        m5, symbol=symbol,
        tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
        max_hold_bars=hold_bars,
        use_dynamic_barriers=True,
    )
    # Map strategy labels (-1=short, 0=hold, +1=long) -> model classes (0=sell, 1=hold, 2=buy)
    raw_labels = strat_labels_df["label"].values
    y_all = np.where(raw_labels == -1, 0, np.where(raw_labels == 1, 2, 1)).astype(np.int8)
    # Sample weights from ICT quality (higher = better setup)
    sample_weights_all = strat_labels_df["label_weighted"].values.astype(np.float32)
    # Normalize weights: ensure positive, min 0.1
    sample_weights_all = np.abs(sample_weights_all)
    sample_weights_all = np.where(sample_weights_all > 0, sample_weights_all, 0.1)
    # Scale so mean weight = 1.0
    sw_mean = sample_weights_all.mean()
    if sw_mean > 0:
        sample_weights_all = sample_weights_all / sw_mean

    # ── 3b. Rule-based pre-filter: force HOLD where ICT rules don't fire ──
    # This teaches the ML to only predict BUY/SELL when strategy rules align.
    print("  Applying ICT rule-based pre-filter (min 3 rules)...", flush=True)
    h1_trend_arr = X_all[:, feature_names.index("h1_trend")] if "h1_trend" in feature_names else None
    rule_signals = generate_scalp_signals(m5, h1_trend=h1_trend_arr, min_rules=3)
    # Where rules say "no signal" AND label is BUY/SELL, force to HOLD
    no_rule = rule_signals == 0
    forced_hold = no_rule & (y_all != 1)
    y_all[forced_hold] = 1
    sample_weights_all[forced_hold] = 0.1  # low weight for forced holds
    print(f"  Rule filter: {forced_hold.sum():,} labels forced to HOLD "
          f"({forced_hold.sum()/len(y_all)*100:.1f}%)")

    print(f"  Labels: sell={np.sum(y_all==0):,}  hold={np.sum(y_all==1):,}  "
          f"buy={np.sum(y_all==2):,}")
    print(f"  Sample weight range: [{sample_weights_all.min():.2f}, {sample_weights_all.max():.2f}] "
          f"mean={sample_weights_all.mean():.2f}")

    # ── 4. OOS split ────────────────────────────────────────────────────
    oos_ts    = int(pd.Timestamp(OOS_START, tz="UTC").timestamp())
    oos_mask  = timestamps >= oos_ts
    oos_start_idx = int(np.argmax(oos_mask)) if oos_mask.any() else len(X_all)

    X_oos_full  = X_all[oos_start_idx:]
    y_oos       = y_all[oos_start_idx:]
    closes_oos  = closes[oos_start_idx:]
    opens_oos   = opens[oos_start_idx:]
    highs_oos   = highs[oos_start_idx:]
    lows_oos    = lows[oos_start_idx:]
    atr_oos     = atr_vals[oos_start_idx:] if atr_vals is not None else None

    print(f"  Train rows: {oos_start_idx:,}  |  OOS rows: {len(X_oos_full):,}")

    # ── 5. Walk-forward folds ─────────────────────────────────────────────
    folds, _ = get_wf_folds(timestamps, OOS_START, n_folds)
    print(f"\n  {'-'*60}")
    print(f"  Walk-Forward Folds  ({len(folds)} folds, expanding window)")
    print(f"  {'-'*60}")

    os.makedirs(MODEL_DIR, exist_ok=True)
    wf_results = []   # one row per (fold, model_type)
    fold_preds  = {}  # model_type -> list of (preds, y_true, closes, opens, highs, lows, atr)

    for model_type in ["xgboost", "lightgbm"]:
        fold_preds[model_type] = []

    for fold_info in folds:
        fnum      = fold_info["fold"]
        tr_end    = fold_info["train_end"]
        ts_start  = fold_info["test_start"]
        ts_end    = fold_info["test_end"]

        X_tr   = X_all[WARMUP:tr_end];    y_tr   = y_all[WARMUP:tr_end]
        sw_tr  = sample_weights_all[WARMUP:tr_end]
        X_val  = X_all[ts_start:ts_end];  y_val  = y_all[ts_start:ts_end]

        # Use last 20% of train as Optuna eval set (within fold)
        val_split = int(len(X_tr) * 0.8)
        Xtr_opt, ytr_opt = X_tr[:val_split], y_tr[:val_split]
        sw_tr_opt = sw_tr[:val_split]
        Xv_opt,  yv_opt  = X_tr[val_split:], y_tr[val_split:]
        sw_v_opt = sw_tr[val_split:]

        ts_start_human = pd.to_datetime(timestamps[ts_start], unit="s", utc=True).strftime("%Y-%m")
        ts_end_human   = pd.to_datetime(timestamps[min(ts_end-1, len(timestamps)-1)],
                                         unit="s", utc=True).strftime("%Y-%m")
        tr_start_human = pd.to_datetime(timestamps[WARMUP], unit="s", utc=True).strftime("%Y-%m")
        tr_end_human   = pd.to_datetime(timestamps[tr_end-1], unit="s", utc=True).strftime("%Y-%m")

        print(f"\n  Fold {fnum}: Train {tr_start_human}->{tr_end_human} ({len(X_tr):,} bars) | "
              f"Test {ts_start_human}->{ts_end_human} ({len(X_val):,} bars)")

        for model_type in ["xgboost", "lightgbm"]:
            print(f"    [{model_type}] {n_trials} trials...", end=" ", flush=True)
            model, cv_acc = train_model(model_type, Xtr_opt, ytr_opt, Xv_opt, yv_opt, n_trials,
                                        sw_tr=sw_tr_opt, sw_val=sw_v_opt)
            print(f"cv_acc={cv_acc:.4f}", flush=True)

            # Evaluate on this fold's test slice
            c_val  = closes[ts_start:ts_end]
            o_val  = opens[ts_start:ts_end]
            h_val  = highs[ts_start:ts_end]
            lo_val = lows[ts_start:ts_end]
            a_val  = atr_vals[ts_start:ts_end] if atr_vals is not None else None
            t_val  = timestamps[ts_start:ts_end]

            metrics = compute_backtest_metrics(
                model, X_val, y_val, c_val,
                opens_test=o_val, highs_test=h_val, lows_test=lo_val,
                atr_test=a_val, times_test=t_val,
                cost_bps=cost_bps, slippage_bps=slippage_bps,
                bars_per_day=bpd, hold_bars=hold_bars,
                tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
                trend_filter=use_trend_filter,
            )
            grade = grade_model(metrics)

            print(f"    [{model_type}] Grade={grade}  Sharpe={metrics['sharpe']:.3f}  "
                  f"WR={metrics['win_rate']:.1f}%  DD={metrics['max_drawdown']:.1f}%  "
                  f"Trades={metrics['total_trades']}")

            wf_results.append({
                "fold": fnum, "model": model_type, "grade": grade, **metrics,
                "train_bars": len(X_tr), "test_bars": len(X_val),
            })

            # Store fold predictions for combined WF metrics
            fold_preds[model_type].append({
                "preds": model.predict(X_val),
                "y": y_val, "c": c_val, "o": o_val, "h": h_val, "lo": lo_val,
                "atr": a_val, "X": X_val,
            })

    # ── 6. Final model (full training set) ──────────────────────────────
    print(f"\n  {'-'*60}")
    print(f"  Final Model (full train 2020->{OOS_START})  {n_trials*2} trials")
    print(f"  {'-'*60}")

    X_full_tr = X_all[WARMUP:oos_start_idx]
    y_full_tr = y_all[WARMUP:oos_start_idx]
    sw_full_tr = sample_weights_all[WARMUP:oos_start_idx]
    val_split = int(len(X_full_tr) * 0.85)
    Xf_tr, yf_tr = X_full_tr[:val_split], y_full_tr[:val_split]
    swf_tr = sw_full_tr[:val_split]
    Xf_val, yf_val = X_full_tr[val_split:], y_full_tr[val_split:]
    swf_val = sw_full_tr[val_split:]

    final_models = {}
    for model_type in ["xgboost", "lightgbm"]:
        print(f"  [{model_type}] {n_trials*2} trials...", end=" ", flush=True)
        model, cv_acc = train_model(model_type, Xf_tr, yf_tr, Xf_val, yf_val, n_trials * 2,
                                    sw_tr=swf_tr, sw_val=swf_val)
        print(f"cv_acc={cv_acc:.4f}", flush=True)
        final_models[model_type] = model

    # ── 7. OOS evaluation (true holdout) ────────────────────────────────
    print(f"\n  {'-'*60}")
    print(f"  True OOS: {OOS_START} -> present  ({len(X_oos_full):,} bars)")
    print(f"  {'-'*60}")

    oos_results = {}
    for model_type, model in final_models.items():
        times_oos = timestamps[oos_start_idx:]
        m = compute_backtest_metrics(
            model, X_oos_full, y_oos, closes_oos,
            opens_test=opens_oos, highs_test=highs_oos,
            lows_test=lows_oos, atr_test=atr_oos, times_test=times_oos,
            cost_bps=cost_bps, slippage_bps=slippage_bps,
            bars_per_day=bpd, hold_bars=hold_bars,
            tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
            trend_filter=use_trend_filter,
        )
        g = grade_model(m)
        oos_results[model_type] = (g, m)
        print(f"  [{model_type}] Grade={g}  Sharpe={m['sharpe']:.3f}  "
              f"WR={m['win_rate']:.1f}%  DD={m['max_drawdown']:.1f}%  "
              f"Return={m['total_return']:.1f}%  Trades={m['total_trades']}")

    # ── 8. Combined walk-forward aggregate ───────────────────────────────
    print(f"\n  {'-'*60}")
    print(f"  Combined Walk-Forward OOS (all fold predictions)")
    print(f"  {'-'*60}")

    for model_type in ["xgboost", "lightgbm"]:
        # Concatenate all fold test predictions and evaluate as one run
        all_preds  = np.concatenate([fp["preds"] for fp in fold_preds[model_type]])
        all_y      = np.concatenate([fp["y"]     for fp in fold_preds[model_type]])
        all_c      = np.concatenate([fp["c"]     for fp in fold_preds[model_type]])
        all_o      = np.concatenate([fp["o"]     for fp in fold_preds[model_type]])
        all_h      = np.concatenate([fp["h"]     for fp in fold_preds[model_type]])
        all_lo     = np.concatenate([fp["lo"]    for fp in fold_preds[model_type]])
        all_atr    = (np.concatenate([fp["atr"]  for fp in fold_preds[model_type]])
                      if fold_preds[model_type][0]["atr"] is not None else None)

        # Build a dummy model that just returns the pre-computed preds
        class _PrecomputedModel:
            def __init__(self, preds, y):
                self._preds = preds
                self._y     = y
            def predict(self, X):
                return self._preds

        from sklearn.metrics import accuracy_score
        dummy = _PrecomputedModel(all_preds, all_y)
        cm = compute_backtest_metrics(
            dummy, None, all_y, all_c,
            opens_test=all_o, highs_test=all_h, lows_test=all_lo, atr_test=all_atr,
            cost_bps=cost_bps, slippage_bps=slippage_bps,
            bars_per_day=bpd, hold_bars=hold_bars,
            tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
            trend_filter=use_trend_filter,
        )
        # Override accuracy manually since dummy model doesn't have predict_proba
        cm["accuracy"] = float(accuracy_score(all_y, all_preds))
        cg = grade_model(cm)
        print(f"  [{model_type}] Combined WF Grade={cg}  Sharpe={cm['sharpe']:.3f}  "
              f"WR={cm['win_rate']:.1f}%  DD={cm['max_drawdown']:.1f}%  "
              f"Trades={cm['total_trades']}")

    # ── 9. SHAP feature importance table ─────────────────────────────────
    print(f"\n  {'-'*60}")
    print(f"  Top-20 Features by SHAP (XGBoost, final model)")
    print(f"  {'-'*60}")

    ranked = shap_importance_table(final_models["xgboost"], Xf_tr, feature_names, top_n=20)
    if ranked:
        cumulative = 0.0
        for rank, (fname, imp) in enumerate(ranked, 1):
            cumulative += imp
            bar = "#" * int(imp * 300)
            print(f"  {rank:2d}. {fname:<35}  {imp*100:5.2f}%  {bar}")
        print(f"       {'Cumulative top-20':35}  {cumulative*100:.1f}%")

    # ── 10. Meta-labeling (Lopez de Prado two-stage) ──────────────────────
    print(f"\n  {'-'*60}")
    print(f"  Meta-Labeling: training secondary model per primary model")
    print(f"  {'-'*60}")

    meta_labelers = {}
    for model_type, model in final_models.items():
        try:
            # Primary model predictions on training data
            primary_preds = model.predict(X_full_tr)
            # Map to direction: 0=sell->-1, 1=hold->0, 2=buy->1
            primary_dirs = np.where(primary_preds == 0, -1,
                          np.where(primary_preds == 2, 1, 0)).astype(np.float32)
            # Actual outcomes (same mapping)
            actual_dirs = np.where(y_full_tr == 0, -1,
                         np.where(y_full_tr == 2, 1, 0)).astype(np.float32)

            ml = MetaLabeler(threshold=0.45)
            fit_info = ml.fit(X_full_tr, primary_dirs, actual_dirs,
                             feature_names=feature_names)
            meta_labelers[model_type] = ml

            # Evaluate meta-labeler on OOS
            oos_primary = model.predict(X_oos_full)
            oos_dirs = np.where(oos_primary == 0, -1,
                       np.where(oos_primary == 2, 1, 0)).astype(np.float32)
            oos_conf = ml.predict_confidence(X_oos_full, oos_dirs)
            n_filtered = np.sum(oos_conf < ml.threshold)
            n_total = np.sum(oos_dirs != 0)
            print(f"  [{model_type}] Meta-labeler trained. OOS: {n_filtered}/{n_total} "
                  f"low-confidence signals filtered ({n_filtered/max(n_total,1)*100:.0f}%)")
        except Exception as e:
            print(f"  [{model_type}] Meta-labeler FAILED: {e}")
            meta_labelers[model_type] = None

    # ── 11. Save final models + meta-labelers ──────────────────────────────
    print(f"\n  {'-'*60}")
    for model_type, model in final_models.items():
        grade, oos_m = oos_results[model_type]
        top_feats = [f for f, _ in ranked] if ranked else feature_names
        feat_imp  = {}
        if hasattr(model, "feature_importances_"):
            feat_imp = dict(zip(feature_names, model.feature_importances_.tolist()))

        path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{model_type}.joblib")
        save_dict = {
            "model":              model,
            "feature_names":      feature_names,
            "grade":              grade,
            "oos_metrics":        oos_m,
            "wf_results":         wf_results,
            "top_features_shap":  top_feats,
            "feature_importances": feat_imp,
            "symbol":             symbol,
            "oos_start":          OOS_START,
            "execution":          {
                "fill":       "open[i+1]",
                "tp_atr_mult": tp_mult,
                "sl_atr_mult": sl_mult,
                "cost_bps":    cost_bps,
                "slippage_bps": slippage_bps,
                "hold_bars":   hold_bars,
            },
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "v6_strategy_informed",
        }
        # Include meta-labeler if trained
        ml = meta_labelers.get(model_type)
        if ml is not None:
            save_dict["meta_labeler"] = ml
            save_dict["meta_threshold"] = ml.threshold

        joblib.dump(save_dict, path)
        ml_tag = " +meta" if ml else ""
        print(f"  Saved: {os.path.basename(path)}  (Grade={grade}{ml_tag})")
        save_model_record(symbol, "M5", model_type, "scalping", path, grade, oos_m)

    return wf_results, oos_results


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",  default="US30",
                        choices=["BTCUSD", "XAUUSD", "US30", "ES", "NAS100"])
    parser.add_argument("--trials",  type=int, default=20,
                        help="Optuna trials per fold (default 20; final model uses 2×)")
    parser.add_argument("--folds",   type=int, default=4,
                        help="Walk-forward folds (default 4)")
    args = parser.parse_args()

    run_walkforward(args.symbol, n_trials=args.trials, n_folds=args.folds)

    print(f"\n{'='*65}")
    print("  Walk-forward training complete.")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
