"""
Flowrex Agent v2 — Training Experiments

Runs multiple training configurations per symbol with different SL/TP/hold_bar
combinations, saves ALL results, and produces a comparison table at the end.
User picks the winners to deploy.

Usage:
    cd backend
    python3 -m scripts.train_experiments [--symbols US30,BTCUSD,...] [--quick]

Each experiment saves to:
    data/ml_models/experiments/{symbol}/{experiment_name}/
        flowrex_{symbol}_M5_{type}.joblib
        config.json (the config used)
        results.json (grades, Sharpe, WR, trades)

The current production models in data/ml_models/ are NEVER touched.
"""
import os
import sys
import json
import gc
import shutil
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.train_flowrex import run_flowrex_training, load_ohlcv, OOS_START, WARMUP, MODEL_DIR
from app.services.ml.symbol_config import get_symbol_config

EXPERIMENT_DIR = os.path.join(MODEL_DIR, "experiments")


def get_experiments(symbol: str, quick: bool = False) -> dict[str, dict]:
    """Generate experiment configurations for a symbol."""
    cfg = get_symbol_config(symbol)
    asset_class = cfg.get("asset_class", "unknown")
    base_sl = cfg.get("sl_atr_mult", 0.8)
    base_tp = cfg.get("tp_atr_mult", 1.2)
    base_hold = cfg.get("hold_bars", 10)

    experiments = {}

    experiments["default"] = {}

    if quick:
        if asset_class == "crypto":
            experiments["wide_sl_1.5"] = {"sl_atr_mult": 1.5, "tp_atr_mult": 2.5}
        else:
            experiments["tight_rr"] = {"sl_atr_mult": 0.6, "tp_atr_mult": 1.5}
        return experiments

    if asset_class == "crypto":
        for sl in [1.0, 1.2, 1.5, 2.0]:
            if sl != base_sl:
                experiments[f"sl_{sl}"] = {"sl_atr_mult": sl}
    else:
        for sl in [0.6, 0.8, 1.0, 1.2]:
            if sl != base_sl:
                experiments[f"sl_{sl}"] = {"sl_atr_mult": sl}

    for tp in [1.0, 1.5, 2.0, 2.5]:
        if tp != base_tp:
            experiments[f"tp_{tp}"] = {"tp_atr_mult": tp}

    if asset_class == "crypto":
        experiments["wide_1.5_2.5"] = {"sl_atr_mult": 1.5, "tp_atr_mult": 2.5}
        experiments["wide_2.0_3.0"] = {"sl_atr_mult": 2.0, "tp_atr_mult": 3.0}
    else:
        experiments["tight_rr_0.6_1.5"] = {"sl_atr_mult": 0.6, "tp_atr_mult": 1.5}
        experiments["balanced_1.0_1.5"] = {"sl_atr_mult": 1.0, "tp_atr_mult": 1.5}
        experiments["wide_rr_1.2_2.0"] = {"sl_atr_mult": 1.2, "tp_atr_mult": 2.0}

    for hb in [8, 12, 15]:
        if hb != base_hold:
            experiments[f"hold_{hb}"] = {"hold_bars": hb}

    return experiments


def run_experiment(symbol: str, name: str, overrides: dict, trials: int = 15, folds: int = 4):
    """Run a single training experiment and save results."""
    exp_dir = os.path.join(EXPERIMENT_DIR, symbol, name)
    os.makedirs(exp_dir, exist_ok=True)

    cfg = get_symbol_config(symbol)
    for k, v in overrides.items():
        cfg[k] = v

    with open(os.path.join(exp_dir, "config.json"), "w") as f:
        json.dump({"symbol": symbol, "experiment": name, "overrides": overrides, "full_config": cfg}, f, indent=2)

    print(f"\n{'='*70}")
    print(f"  EXPERIMENT: {symbol} / {name}")
    print(f"  Overrides: {overrides or 'default'}")
    print(f"{'='*70}")

    try:
        wf_results, oos_results = run_flowrex_training(
            symbol, n_trials=trials, n_folds=folds, overrides=overrides
        )

        results_summary = {}
        for mtype in ["xgboost", "lightgbm", "catboost"]:
            src = os.path.join(MODEL_DIR, f"flowrex_{symbol}_M5_{mtype}.joblib")
            if os.path.exists(src):
                dst = os.path.join(exp_dir, f"flowrex_{symbol}_M5_{mtype}.joblib")
                shutil.copy2(src, dst)
                if oos_results and mtype in oos_results:
                    grade, metrics = oos_results[mtype]
                    results_summary[mtype] = {
                        "grade": grade,
                        "sharpe": metrics.get("sharpe", 0),
                        "win_rate": metrics.get("win_rate", 0),
                        "max_drawdown": metrics.get("max_drawdown", 0),
                        "total_trades": metrics.get("total_trades", 0),
                        "total_return": metrics.get("total_return", 0),
                    }

        with open(os.path.join(exp_dir, "results.json"), "w") as f:
            json.dump({
                "symbol": symbol,
                "experiment": name,
                "overrides": overrides,
                "oos_results": results_summary,
                "wf_folds": len(wf_results) if wf_results else 0,
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

        gc.collect()
        return results_summary

    except Exception as e:
        print(f"  EXPERIMENT FAILED: {e}")
        import traceback
        traceback.print_exc()
        with open(os.path.join(exp_dir, "results.json"), "w") as f:
            json.dump({"error": str(e), "symbol": symbol, "experiment": name}, f, indent=2)
        return None


def print_comparison_table(all_results: dict):
    """Print a comparison table of all experiments."""
    print(f"\n{'='*90}")
    print(f"  EXPERIMENT COMPARISON TABLE")
    print(f"{'='*90}")
    print(f"{'Symbol':<10} {'Experiment':<25} {'Grade':<6} {'Sharpe':>8} {'WR':>7} {'Trades':>7} {'Return':>9} {'DD':>7}")
    print(f"{'-'*90}")

    for symbol, experiments in sorted(all_results.items()):
        for exp_name, results in sorted(experiments.items()):
            if results is None:
                print(f"{symbol:<10} {exp_name:<25} {'FAIL':<6}")
                continue
            best_type = max(results.keys(), key=lambda t: results[t].get("sharpe", -999)) if results else None
            if best_type:
                r = results[best_type]
                print(f"{symbol:<10} {exp_name:<25} {r.get('grade','?'):<6} "
                      f"{r.get('sharpe',0):>8.2f} {r.get('win_rate',0)*100:>6.1f}% "
                      f"{r.get('total_trades',0):>7} {r.get('total_return',0)*100:>8.1f}% "
                      f"{r.get('max_drawdown',0)*100:>6.1f}%")
        print()

    comp_path = os.path.join(EXPERIMENT_DIR, "comparison.json")
    with open(comp_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nFull comparison saved to: {comp_path}")
    print(f"Experiment models saved in: {EXPERIMENT_DIR}/{{symbol}}/{{experiment}}/")
    print(f"\nTo deploy a winning experiment:")
    print(f"  cp experiments/{{symbol}}/{{experiment}}/flowrex_*.joblib ../")
    print(f"  Then restart the agent to load new models.")


def main():
    parser = argparse.ArgumentParser(description="Flowrex v2 Training Experiments")
    parser.add_argument("--symbols", default="all",
                        help="Comma-separated symbols or 'all'")
    parser.add_argument("--trials", type=int, default=15)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: only default + 1 variation per symbol")
    args = parser.parse_args()

    if args.symbols.lower() == "all":
        symbols = ["US30", "BTCUSD", "XAUUSD", "ES", "NAS100"]
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    os.makedirs(EXPERIMENT_DIR, exist_ok=True)

    all_results = {}
    for symbol in symbols:
        experiments = get_experiments(symbol, quick=args.quick)
        all_results[symbol] = {}

        print(f"\n{'#'*70}")
        print(f"  {symbol}: {len(experiments)} experiments planned")
        print(f"  Experiments: {', '.join(experiments.keys())}")
        print(f"{'#'*70}")

        for exp_name, overrides in experiments.items():
            result = run_experiment(symbol, exp_name, overrides, args.trials, args.folds)
            all_results[symbol][exp_name] = result

    print_comparison_table(all_results)


if __name__ == "__main__":
    main()
