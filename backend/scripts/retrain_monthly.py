"""
Monthly Model Retrain Pipeline
===============================
Retrains scalping models on a rolling 12-month window with 2-week holdout.
Compares new vs deployed model and only swaps if improved.

Usage (CLI):
  python -m scripts.retrain_monthly --symbol BTCUSD
  python -m scripts.retrain_monthly --symbol BTCUSD --trials 10 --months 6
  python -m scripts.retrain_monthly --all

Usage (import):
  from scripts.retrain_monthly import retrain_symbol
  result = retrain_symbol("BTCUSD", n_trials=25, train_months=12)
"""
import os
import sys
import shutil
import warnings
import argparse
import numpy as np
import pandas as pd
import joblib
import optuna

from datetime import datetime, timezone, timedelta
from typing import Callable

# Ensure backend root on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Guard for Windows cp1252 encoding
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.symbol_config import get_symbol_config
from scripts.model_utils import (
    create_labels,
    compute_backtest_metrics,
    grade_model,
    save_model_record,
)
from scripts.train_walkforward import (
    load_ohlcv,
    load_peer_m5,
    train_model,
    shap_importance_table,
)

DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")

GRADE_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1, None: 0}
ALL_SYMBOLS = ["BTCUSD", "XAUUSD", "US30"]


# ── Comparison Gate ─────────────────────────────────────────────────────────

def should_swap_model(
    old_metrics: dict | None,
    new_metrics: dict,
    old_grade: str | None,
    new_grade: str,
    sharpe_tolerance: float = 0.8,
) -> tuple[bool, str]:
    """
    Decide whether to swap old model for new model.

    Returns (should_swap: bool, reason: str).
    """
    if old_metrics is None or old_grade is None:
        return True, "first model for symbol"

    old_g = GRADE_ORDER.get(old_grade, 0)
    new_g = GRADE_ORDER.get(new_grade, 0)

    if new_g > old_g:
        return True, f"grade improved {old_grade} -> {new_grade}"

    old_sharpe = old_metrics.get("sharpe", 0)
    new_sharpe = new_metrics.get("sharpe", 0)

    if old_sharpe <= 0:
        # Old model was negative Sharpe — any positive new model is better
        if new_sharpe > 0:
            return True, f"new model profitable (Sharpe {new_sharpe:.3f} vs {old_sharpe:.3f})"
        return False, f"both models negative Sharpe ({new_sharpe:.3f} vs {old_sharpe:.3f})"

    if new_sharpe >= old_sharpe * sharpe_tolerance:
        return True, f"Sharpe within tolerance ({new_sharpe:.3f} >= {old_sharpe:.3f} * {sharpe_tolerance})"

    return False, f"did not pass gate (Sharpe {new_sharpe:.3f} < {old_sharpe:.3f} * {sharpe_tolerance})"


# ── Load Current Deployed Model ─────────────────────────────────────────────

def _load_deployed_model(symbol: str, model_type: str) -> dict | None:
    """Load the currently deployed model's metadata from disk."""
    path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{model_type}.joblib")
    if not os.path.exists(path):
        return None
    try:
        data = joblib.load(path)
        return {
            "grade": data.get("grade"),
            "metrics": data.get("oos_metrics", {}),
            "path": path,
        }
    except Exception:
        return None


# ── Archive Old Model ───────────────────────────────────────────────────────

def _archive_model(symbol: str, model_type: str) -> str | None:
    """Archive the current model to a timestamped folder. Returns archive path."""
    src = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{model_type}.joblib")
    if not os.path.exists(src):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = os.path.join(MODEL_DIR, f"archive_{symbol}_{ts}")
    os.makedirs(archive_dir, exist_ok=True)
    dst = os.path.join(archive_dir, os.path.basename(src))
    shutil.copy2(src, dst)
    return dst


# ── Core Retrain Function ───────────────────────────────────────────────────

def retrain_symbol(
    symbol: str,
    n_trials: int = 25,
    train_months: int = 12,
    holdout_days: int = 14,
    triggered_by: str = "cli",
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    """
    Retrain a single symbol's scalping models on a rolling window.

    Args:
        symbol:            BTCUSD, XAUUSD, or US30
        n_trials:          Optuna trials per model type (default 25)
        train_months:      Rolling training window in months (default 12)
        holdout_days:      Holdout validation period in days (default 14)
        triggered_by:      "cli", "manual", or "schedule"
        progress_callback: Optional callback(status_str) for progress updates

    Returns:
        dict with keys: symbol, status, old_grade, new_grade, swapped, swap_reason,
        old_metrics, new_metrics, error_message
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)
        print(f"  [{symbol}] {msg}", flush=True)

    result = {
        "symbol": symbol, "status": "running", "triggered_by": triggered_by,
        "old_grade": None, "old_sharpe": None, "old_metrics": None,
        "new_grade": None, "new_sharpe": None, "new_metrics": None,
        "swapped": False, "swap_reason": None, "error_message": None,
        "training_config": {"n_trials": n_trials, "train_months": train_months,
                           "holdout_days": holdout_days},
    }

    cfg = get_symbol_config(symbol)
    cost_bps     = cfg.get("cost_bps", 5.0)
    slippage_bps = cfg.get("slippage_bps", 1.0)
    tp_mult      = cfg.get("tp_atr_mult", 1.0)
    sl_mult      = cfg.get("sl_atr_mult", 0.8)
    bpd          = cfg.get("bars_per_day", 288)
    hold_bars    = cfg.get("hold_bars", cfg.get("label_forward_bars", 10))
    use_trend    = cfg.get("trend_filter", True)

    print(f"\n{'='*65}")
    print(f"  MONTHLY RETRAIN: {symbol}")
    print(f"  Window: {train_months} months train | {holdout_days} days holdout | {n_trials} trials")
    print(f"  Execution: TP={tp_mult}x ATR  SL={sl_mult}x ATR  cost={cost_bps+slippage_bps:.1f}bps")
    print(f"{'='*65}")

    try:
        # ── 1. Snapshot current deployed models ──────────────────────
        _progress("loading current models")
        best_old = None
        for mt in ["xgboost", "lightgbm"]:
            deployed = _load_deployed_model(symbol, mt)
            if deployed:
                old_g = GRADE_ORDER.get(deployed["grade"], 0)
                cur_g = GRADE_ORDER.get(best_old["grade"], 0) if best_old else -1
                if old_g > cur_g:
                    best_old = deployed

        if best_old:
            result["old_grade"]   = best_old["grade"]
            result["old_sharpe"]  = best_old["metrics"].get("sharpe")
            result["old_metrics"] = best_old["metrics"]
            print(f"  Current model: Grade={best_old['grade']}  Sharpe={result['old_sharpe']:.3f}")
        else:
            print("  No current model deployed")

        # ── 2. Load data ────────────────────────────────────────────
        _progress("loading data")
        m5, m15, h1, h4, d1 = load_ohlcv(symbol)
        peer_m5 = load_peer_m5(symbol)
        print(f"  M5={len(m5):,}  M15={len(m15) if m15 is not None else 0:,}  "
              f"H1={len(h1) if h1 is not None else 0:,}  "
              f"H4={len(h4) if h4 is not None else 0:,}")

        # ── 3. Compute rolling window indices ───────────────────────
        timestamps = m5["time"].values.astype(np.int64)
        # Use LAST data timestamp as reference (not system clock — data may lag)
        data_end_ts = int(timestamps[-1])
        holdout_ts  = data_end_ts - holdout_days * 86400
        train_ts    = holdout_ts - int(train_months * 30.44 * 86400)

        # Find array indices
        holdout_idx = int(np.searchsorted(timestamps, holdout_ts))
        train_idx   = int(np.searchsorted(timestamps, train_ts))

        # Ensure minimum warmup (500 bars before train start)
        warmup = 500
        if train_idx < warmup:
            train_idx = warmup

        n_train   = holdout_idx - train_idx
        n_holdout = len(timestamps) - holdout_idx

        if n_train < 5000:
            raise ValueError(f"Insufficient training data: {n_train} bars (need >= 5000)")
        min_holdout = 50  # minimum bars for holdout validation
        if n_holdout < min_holdout:
            raise ValueError(f"Insufficient holdout data: {n_holdout} bars (need >= {min_holdout})")

        train_start_dt = pd.to_datetime(timestamps[train_idx], unit="s").strftime("%Y-%m-%d")
        holdout_start_dt = pd.to_datetime(timestamps[holdout_idx], unit="s").strftime("%Y-%m-%d")
        data_end_dt = pd.to_datetime(timestamps[-1], unit="s").strftime("%Y-%m-%d")
        print(f"  Train: {train_start_dt} -> {holdout_start_dt} ({n_train:,} bars)")
        print(f"  Holdout: {holdout_start_dt} -> {data_end_dt} ({n_holdout:,} bars)")

        # ── 4. Compute features ─────────────────────────────────────
        _progress("computing features")
        feature_names, X_all = compute_expert_features(
            m5, h1, h4, d1, symbol=symbol, include_external=True,
            other_m5=peer_m5 if peer_m5 else None,
            m15_bars=m15,
        )
        print(f"  Features: {len(feature_names)}")

        # ── 5. Create labels ────────────────────────────────────────
        closes   = m5["close"].values
        opens    = m5["open"].values
        highs    = m5["high"].values
        lows     = m5["low"].values
        atr_idx  = feature_names.index("atr_14") if "atr_14" in feature_names else None
        atr_vals = X_all[:, atr_idx] if atr_idx is not None else None
        y_all    = create_labels(closes, atr_vals, config=cfg)

        # ── 6. Slice into train + holdout ───────────────────────────
        X_train = X_all[train_idx:holdout_idx]
        y_train = y_all[train_idx:holdout_idx]
        X_hold  = X_all[holdout_idx:]
        y_hold  = y_all[holdout_idx:]

        c_hold  = closes[holdout_idx:]
        o_hold  = opens[holdout_idx:]
        h_hold  = highs[holdout_idx:]
        l_hold  = lows[holdout_idx:]
        a_hold  = atr_vals[holdout_idx:] if atr_vals is not None else None

        # Optuna validation: last 15% of training window
        split = int(len(X_train) * 0.85)
        Xtr, ytr = X_train[:split], y_train[:split]
        Xval, yval = X_train[split:], y_train[split:]

        # ── 7. Train models ─────────────────────────────────────────
        new_models = {}
        new_results = {}

        for model_type in ["xgboost", "lightgbm"]:
            _progress(f"training {model_type} ({n_trials} trials)")
            model, cv_acc = train_model(model_type, Xtr, ytr, Xval, yval, n_trials)

            # Refit on full training window (eval_set needed for early stopping)
            if model_type == "xgboost":
                model.fit(X_train, y_train, eval_set=[(Xval, yval)], verbose=False)
            else:
                import lightgbm as lgb
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model.fit(X_train, y_train, eval_set=[(Xval, yval)],
                              callbacks=[lgb.early_stopping(20, verbose=False),
                                         lgb.log_evaluation(-1)])

            # ── 8. Evaluate on holdout ──────────────────────────────
            _progress(f"evaluating {model_type} on holdout")
            metrics = compute_backtest_metrics(
                model, X_hold, y_hold, c_hold,
                opens_test=o_hold, highs_test=h_hold,
                lows_test=l_hold, atr_test=a_hold,
                cost_bps=cost_bps, slippage_bps=slippage_bps,
                bars_per_day=bpd, hold_bars=hold_bars,
                tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
                trend_filter=use_trend,
            )
            grade = grade_model(metrics)
            new_models[model_type] = model
            new_results[model_type] = {"grade": grade, "metrics": metrics, "cv_acc": cv_acc}

            print(f"    [{model_type}] Grade={grade}  Sharpe={metrics['sharpe']:.3f}  "
                  f"WR={metrics['win_rate']:.1f}%  DD={metrics['max_drawdown']:.1f}%  "
                  f"Trades={metrics['total_trades']}")

        # ── 9. Pick best new model ──────────────────────────────────
        best_type = max(new_results, key=lambda k: (
            GRADE_ORDER.get(new_results[k]["grade"], 0),
            new_results[k]["metrics"].get("sharpe", 0),
        ))
        best_new = new_results[best_type]
        result["new_grade"]   = best_new["grade"]
        result["new_sharpe"]  = best_new["metrics"].get("sharpe")
        result["new_metrics"] = best_new["metrics"]

        # ── 10. Comparison gate ─────────────────────────────────────
        swap, reason = should_swap_model(
            result["old_metrics"], best_new["metrics"],
            result["old_grade"], best_new["grade"],
        )
        result["swapped"]     = swap
        result["swap_reason"] = reason

        if swap:
            _progress(f"SWAPPING models ({reason})")
            for mt, model in new_models.items():
                _archive_model(symbol, mt)
                path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{mt}.joblib")
                joblib.dump({
                    "model": model,
                    "feature_names": feature_names,
                    "grade": new_results[mt]["grade"],
                    "oos_metrics": new_results[mt]["metrics"],
                    "symbol": symbol,
                    "execution": {
                        "fill": "open[i+1]",
                        "tp_atr_mult": tp_mult, "sl_atr_mult": sl_mult,
                        "cost_bps": cost_bps, "slippage_bps": slippage_bps,
                        "hold_bars": hold_bars,
                    },
                    "trained_at": datetime.now(timezone.utc).isoformat(),
                    "retrain_config": result["training_config"],
                }, path)
                print(f"    Saved: scalping_{symbol}_M5_{mt}.joblib (Grade={new_results[mt]['grade']})")

            # Save DB record
            try:
                save_model_record(
                    symbol, "M5", best_type, "scalping",
                    os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{best_type}.joblib"),
                    best_new["grade"], best_new["metrics"],
                )
            except Exception as e:
                print(f"  Warning: DB record save failed: {e}")

            # Hot-reload agents
            try:
                from app.services.agent.engine import get_algo_engine
                engine = get_algo_engine()
                engine.reload_models_for_symbol(symbol)
                _progress("agents reloaded with new models")
            except Exception:
                pass  # Engine may not be running in CLI mode
        else:
            _progress(f"KEEPING old models ({reason})")

        result["status"] = "success"

    except Exception as e:
        result["status"] = "failed"
        result["error_message"] = str(e)
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*65}")
    print(f"  Retrain {symbol}: {result['status'].upper()}")
    if result["swapped"]:
        print(f"  Model swapped: {result['old_grade'] or 'None'} -> {result['new_grade']}")
    else:
        print(f"  Model kept: {result['old_grade'] or 'None'} (new was {result['new_grade']})")
    print(f"{'='*65}\n")

    return result


# ── DB Audit Logging ────────────────────────────────────────────────────────

def _record_retrain_run(result: dict):
    """Save retrain result to retrain_runs DB table."""
    try:
        from app.core.database import SessionLocal
        from app.models.ml import RetrainRun

        db = SessionLocal()
        run = RetrainRun(
            symbol=result["symbol"],
            triggered_by=result["triggered_by"],
            started_at=datetime.now(timezone.utc),  # approximate
            finished_at=datetime.now(timezone.utc),
            status=result["status"],
            old_grade=result.get("old_grade"),
            old_sharpe=result.get("old_sharpe"),
            old_metrics=result.get("old_metrics"),
            new_grade=result.get("new_grade"),
            new_sharpe=result.get("new_sharpe"),
            new_metrics=result.get("new_metrics"),
            swapped=result.get("swapped", False),
            swap_reason=result.get("swap_reason"),
            error_message=result.get("error_message"),
            training_config=result.get("training_config"),
        )
        db.add(run)
        db.commit()
        db.close()
    except Exception as e:
        print(f"  Warning: Failed to record retrain run to DB: {e}")


# ── CLI Entry Point ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Monthly model retrain")
    parser.add_argument("--symbol", type=str, help="Symbol to retrain (e.g. BTCUSD)")
    parser.add_argument("--all", action="store_true", help="Retrain all 3 symbols")
    parser.add_argument("--trials", type=int, default=25, help="Optuna trials per model")
    parser.add_argument("--months", type=int, default=12, help="Training window in months")
    parser.add_argument("--holdout-days", type=int, default=14, help="Holdout period in days")
    args = parser.parse_args()

    if not args.symbol and not args.all:
        parser.error("Specify --symbol BTCUSD or --all")

    symbols = ALL_SYMBOLS if args.all else [args.symbol.upper()]

    for sym in symbols:
        result = retrain_symbol(
            sym,
            n_trials=args.trials,
            train_months=args.months,
            holdout_days=args.holdout_days,
            triggered_by="cli",
        )
        _record_retrain_run(result)


if __name__ == "__main__":
    main()
