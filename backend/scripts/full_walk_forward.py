"""
Full Walk-Forward Test — the ONLY honest evaluation.
Uses all available data: trains on 60%, walks forward on 40%.
With 105k bars (~6 months), this gives ~63k bars to train and ~42k to test.
"""
import os, sys
import numpy as np
import pandas as pd
import joblib, shutil
import optuna

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
optuna.logging.set_verbosity(optuna.logging.WARNING)

from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.symbol_config import get_symbol_config
from app.services.backtest.engine import BacktestEngine
from scripts.model_utils import create_labels
from scripts.train_scalping_pipeline import train_xgboost, train_lightgbm

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")


def run_full_walk_forward(symbol="XAUUSD", n_trials=20):
    print("=" * 70)
    print(f"FULL WALK-FORWARD TEST: {symbol}")
    print("Train on 60% of all data, test on remaining 40%")
    print("=" * 70)

    # Load full dataset
    m5_path = os.path.join(DATA_DIR, f"{symbol}_M5.csv")
    h1_path = os.path.join(DATA_DIR, f"{symbol}_H1.csv")
    m5 = pd.read_csv(m5_path)
    h1 = pd.read_csv(h1_path) if os.path.exists(h1_path) else None
    print(f"Total data: {len(m5)} M5 bars")

    # Split 60/40
    split_idx = int(len(m5) * 0.6)
    train_m5 = m5.iloc[:split_idx].reset_index(drop=True)
    test_m5 = m5.iloc[split_idx:].reset_index(drop=True)
    print(f"Train: {len(train_m5)} bars (~{len(train_m5)//288} days)")
    print(f"Test:  {len(test_m5)} bars (~{len(test_m5)//288} days)")

    # ── STEP 1: Compute features on TRAINING data ──────────────
    print("\nComputing features on training data...")
    sym_config = get_symbol_config(symbol)
    feature_names, X_train_full = compute_expert_features(train_m5, h1)
    closes_train = train_m5["close"].values
    atr_vals = X_train_full[:, feature_names.index("atr_14")]
    y_train_full = create_labels(closes_train, atr_vals, config=sym_config)
    print(f"Features: {len(feature_names)}")
    print(f"Labels: sell={np.sum(y_train_full==0)}, hold={np.sum(y_train_full==1)}, buy={np.sum(y_train_full==2)}")

    # Inner train/val split for Optuna
    warmup = 200
    inner_split = int((len(X_train_full) - warmup) * 0.8) + warmup
    X_tr = X_train_full[warmup:inner_split]
    y_tr = y_train_full[warmup:inner_split]
    X_val = X_train_full[inner_split:]
    y_val = y_train_full[inner_split:]
    print(f"Inner split: train={len(X_tr)}, val={len(X_val)}")

    # ── STEP 2: Train models on TRAINING data only ─────────────
    print(f"\nTraining XGBoost ({n_trials} Optuna trials)...")
    xgb_model, xgb_acc = train_xgboost(X_tr, y_tr, X_val, y_val, n_trials=n_trials)
    print(f"  XGBoost val accuracy: {xgb_acc:.4f}")

    print(f"Training LightGBM ({n_trials} Optuna trials)...")
    lgb_model, lgb_acc = train_lightgbm(X_tr, y_tr, X_val, y_val, n_trials=n_trials)
    print(f"  LightGBM val accuracy: {lgb_acc:.4f}")

    # ── STEP 3: Swap models temporarily ────────────────────────
    xgb_path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_xgboost.joblib")
    lgb_path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_lightgbm.joblib")
    shutil.copy2(xgb_path, xgb_path + ".bak")
    shutil.copy2(lgb_path, lgb_path + ".bak")

    joblib.dump({"model": xgb_model, "feature_names": feature_names, "grade": "WF", "metrics": {}}, xgb_path)
    joblib.dump({"model": lgb_model, "feature_names": feature_names, "grade": "WF", "metrics": {}}, lgb_path)

    # ── STEP 4: Backtest on UNSEEN test data ───────────────────
    engine = BacktestEngine()

    print(f"\nBacktesting on {len(test_m5)} UNSEEN bars...")
    r_wf = engine.run(
        symbol=symbol, m5_data=test_m5, h1_data=h1,
        spread_pips=sym_config.get("spread_pips", 1.0),
        slippage_pips=sym_config.get("spread_pips", 1.0) * 0.3,
        prime_hours_only=True, include_monte_carlo=True,
    )

    # Also run in-sample backtest for comparison
    print("Running in-sample backtest for comparison...")
    # Restore original models for in-sample
    shutil.move(xgb_path + ".bak", xgb_path)
    shutil.move(lgb_path + ".bak", lgb_path)

    r_is = engine.run(
        symbol=symbol, m5_data=m5, h1_data=h1,
        spread_pips=sym_config.get("spread_pips", 1.0),
        slippage_pips=sym_config.get("spread_pips", 1.0) * 0.3,
        prime_hours_only=True, include_monte_carlo=True,
    )

    # ── RESULTS ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"\n{'Metric':<25} {'In-Sample':<18} {'Walk-Forward':<18} {'Degradation':<12}")
    print("-" * 73)

    metrics = [
        ("Trades", r_is.total_trades, r_wf.total_trades),
        ("Net P&L ($)", r_is.net_pnl, r_wf.net_pnl),
        ("Win Rate (%)", r_is.win_rate, r_wf.win_rate),
        ("Profit Factor", r_is.profit_factor, r_wf.profit_factor),
        ("Sharpe Ratio", r_is.sharpe_ratio, r_wf.sharpe_ratio),
        ("Max Drawdown ($)", r_is.max_drawdown, r_wf.max_drawdown),
        ("Expectancy ($/trade)", r_is.expectancy, r_wf.expectancy),
        ("R:R Ratio", r_is.risk_reward_ratio, r_wf.risk_reward_ratio),
        ("Avg Duration (bars)", r_is.avg_trade_duration_bars, r_wf.avg_trade_duration_bars),
        ("Win Streak", r_is.max_consecutive_wins, r_wf.max_consecutive_wins),
        ("Loss Streak", r_is.max_consecutive_losses, r_wf.max_consecutive_losses),
    ]

    for name, a, b in metrics:
        if a != 0:
            change = f"{((b - a) / abs(a) * 100):+.1f}%"
        else:
            change = "N/A"
        print(f"{name:<25} {str(a):<18} {str(b):<18} {change:<12}")

    if r_is.monte_carlo and r_wf.monte_carlo:
        print(f"\n{'MC DD 95th ($)':<25} {r_is.monte_carlo.drawdown_95th:<18} {r_wf.monte_carlo.drawdown_95th:<18}")
        print(f"{'MC DD 99th ($)':<25} {r_is.monte_carlo.drawdown_99th:<18} {r_wf.monte_carlo.drawdown_99th:<18}")
        print(f"{'MC Worst DD ($)':<25} {r_is.monte_carlo.worst_drawdown:<18} {r_wf.monte_carlo.worst_drawdown:<18}")

    # Verdict
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)

    if r_wf.total_trades == 0:
        print("  RESULT: No trades on unseen data")
        print("  Models could not produce signals above confidence threshold")
        print("  LIVE EXPECTANCY: $0.00/trade")
        return

    degradation = abs((r_wf.net_pnl - r_is.net_pnl) / r_is.net_pnl * 100) if r_is.net_pnl != 0 else 100

    print(f"  In-sample P&L:    ${r_is.net_pnl}")
    print(f"  Walk-forward P&L: ${r_wf.net_pnl}")
    print(f"  Degradation:      {degradation:.1f}%")
    print()

    if r_wf.net_pnl > 0 and r_wf.win_rate > 50:
        print("  STATUS: PROFITABLE on unseen data")
        if degradation < 30:
            print("  GRADE: EXCELLENT — model generalizes very well")
        elif degradation < 50:
            print("  GRADE: GOOD — acceptable overfitting")
        else:
            print("  GRADE: FAIR — some overfitting, monitor closely")
    elif r_wf.net_pnl > 0:
        print("  STATUS: Marginally profitable (low win rate)")
        print("  GRADE: WEAK — strategy needs improvement")
    else:
        print("  STATUS: UNPROFITABLE on unseen data")
        print("  GRADE: FAIL — strategy is overfitted")

    print(f"\n  LIVE EXPECTANCY: ${r_wf.expectancy}/trade")
    print(f"  Based on {r_wf.total_trades} trades on {len(test_m5)} bars the model NEVER saw")
    print(f"  Expected monthly P&L: ~${r_wf.expectancy * (r_wf.total_trades / max(len(test_m5)/288/30, 1)):.2f}")


if __name__ == "__main__":
    run_full_walk_forward("XAUUSD", n_trials=20)
