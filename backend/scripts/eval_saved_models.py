"""
eval_saved_models.py
Re-evaluate saved models with corrected compute_backtest_metrics (daily Sharpe + costs).
Does NOT retrain — just recomputes features for the OOS window and re-scores.

Usage:
    cd backend
    python -m scripts.eval_saved_models [--symbol BTCUSD]
"""
import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd
import joblib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.model_utils import (
    compute_backtest_metrics,
    grade_model,
    create_labels,
    save_model_record,
)
from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.symbol_config import get_symbol_config

DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
OOS_START = "2024-10-01"

BARS_PER_DAY = {
    "BTCUSD": 288,
    "XAUUSD": 264,
    "US30":   102,
    "ES":     102,
    "NAS100": 102,
}


def load_data(symbol: str):
    dfs = {}
    for tf in ["M5", "H1", "H4", "D1"]:
        path = os.path.join(DATA_DIR, f"{symbol}_{tf}.csv")
        if os.path.exists(path):
            dfs[tf] = pd.read_csv(path)
    return dfs.get("M5"), dfs.get("H1"), dfs.get("H4"), dfs.get("D1")


def eval_symbol(symbol: str):
    print(f"\n{'='*60}")
    print(f"  Re-evaluating {symbol}  (OOS from {OOS_START})")
    print(f"{'='*60}")

    m5, h1, h4, d1 = load_data(symbol)
    if m5 is None:
        print(f"  [SKIP] No M5 data for {symbol}")
        return

    print(f"  M5={len(m5):,}  Computing OOS features...")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        feature_names, X_all = compute_expert_features(
            m5, h1, h4, d1, symbol=symbol, include_external=True
        )

    sym_config = get_symbol_config(symbol)
    closes     = m5["close"].values
    atr_idx    = feature_names.index("atr_14") if "atr_14" in feature_names else None
    atr_vals   = X_all[:, atr_idx] if atr_idx is not None else None
    y_all      = create_labels(closes, atr_vals, config=sym_config)

    oos_ts        = int(pd.Timestamp(OOS_START, tz="UTC").timestamp())
    oos_mask      = m5["time"].values >= oos_ts
    oos_start_idx = int(np.argmax(oos_mask)) if oos_mask.any() else len(X_all)

    X_oos     = X_all[oos_start_idx:]
    y_oos     = y_all[oos_start_idx:]
    closes_oos = closes[oos_start_idx:]
    bpd        = BARS_PER_DAY.get(symbol, 288)

    print(f"  OOS rows: {len(X_oos):,}  |  Features: {len(feature_names)}")

    for model_type in ["xgboost", "lightgbm"]:
        model_path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{model_type}.joblib")
        if not os.path.exists(model_path):
            print(f"  [SKIP] No saved model: {model_type}")
            continue

        d = joblib.load(model_path)
        model        = d["model"]
        saved_feats  = d["feature_names"]

        # Align features — only use columns the model was trained on
        try:
            feat_idx = [feature_names.index(f) for f in saved_feats]
            X_oos_f  = X_oos[:, feat_idx]
        except ValueError as e:
            print(f"  [WARN] Feature mismatch for {model_type}: {e}")
            X_oos_f = X_oos[:, : len(saved_feats)]

        sym_config = get_symbol_config(symbol)
        hbars = sym_config.get("label_forward_bars", 10)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            oos_metrics = compute_backtest_metrics(
                model, X_oos_f, y_oos, closes_oos,
                cost_bps=5.0, bars_per_day=bpd, hold_bars=hbars
            )

        grade = grade_model(oos_metrics)

        print(f"\n  {model_type.upper()}")
        print(f"    Grade:       {grade}")
        print(f"    Sharpe:      {oos_metrics['sharpe']:.3f}  (corrected: daily x sqrt(252), 5bps cost)")
        print(f"    Win Rate:    {oos_metrics['win_rate']:.1f}%")
        print(f"    Max DD:      {oos_metrics['max_drawdown']:.1f}%")
        print(f"    Total Return:{oos_metrics['total_return']:.1f}%")
        print(f"    Trades:      {oos_metrics['total_trades']:,}")
        print(f"    Profit Factor:{oos_metrics['profit_factor']:.3f}")

        # Update saved model with corrected metrics
        d["oos_metrics"] = oos_metrics
        d["grade"]       = grade
        joblib.dump(d, model_path)
        print(f"    [OK] Saved updated metrics -> {os.path.basename(model_path)}")

    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default=None,
                        help="Single symbol to eval (default: all saved models)")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else ["BTCUSD", "XAUUSD", "US30"]
    for sym in symbols:
        eval_symbol(sym)

    print("=" * 60)
    print("  Re-evaluation complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
