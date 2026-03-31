"""
Walk-Forward Analysis — the honest backtest.
Compares in-sample (biased) vs out-of-sample (honest) performance.
"""
import os, sys, asyncio, shutil
import numpy as np
import pandas as pd
import joblib
import optuna

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
optuna.logging.set_verbosity(optuna.logging.WARNING)

from dotenv import load_dotenv
load_dotenv()


def print_result(label, r):
    print(f"  Trades:       {r.total_trades}")
    print(f"  Net P&L:      {r.net_pnl}")
    print(f"  Total Costs:  {r.total_costs}")
    print(f"  Win Rate:     {r.win_rate}%")
    print(f"  Profit Factor:{r.profit_factor}")
    print(f"  Sharpe:       {r.sharpe_ratio}")
    print(f"  Max DD:       {r.max_drawdown}")
    print(f"  Expectancy:   {r.expectancy}")
    print(f"  R:R Ratio:    {r.risk_reward_ratio}")
    print(f"  Avg Duration: {r.avg_trade_duration_bars} bars")
    if r.monte_carlo:
        print(f"  MC DD 95th:   {r.monte_carlo.drawdown_95th}")
        print(f"  MC DD 99th:   {r.monte_carlo.drawdown_99th}")


async def main():
    from app.services.broker.mt5 import MT5Adapter

    # Connect and fetch data
    adapter = MT5Adapter()
    login = os.getenv("MT5_LOGIN", "")
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    creds = {"login": int(login) if login.isdigit() else int(password),
             "password": password if login.isdigit() else login, "server": server}
    await adapter.connect(creds)

    candles = await adapter.get_candles("XAUUSD", "M5", 5000)
    h1_candles = await adapter.get_candles("XAUUSD", "H1", 500)
    await adapter.disconnect()

    m5 = pd.DataFrame([{"time": c.time, "open": c.open, "high": c.high, "low": c.low,
                         "close": c.close, "volume": c.volume} for c in candles])
    h1 = pd.DataFrame([{"time": c.time, "open": c.open, "high": c.high, "low": c.low,
                         "close": c.close, "volume": c.volume} for c in h1_candles])

    print(f"Total data: {len(m5)} M5 bars")
    print(f"Price range: {m5.close.min():.2f} - {m5.close.max():.2f}")

    from app.services.backtest.engine import BacktestEngine
    engine = BacktestEngine()

    # ═════════════════════════════════════════════════════════════
    # TEST 1: Normal backtest (in-sample — biased)
    # ═════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("TEST 1: NORMAL BACKTEST (in-sample)")
    print("Model tested on data it was trained on — BIASED")
    print("=" * 60)

    r_normal = engine.run(
        symbol="XAUUSD", m5_data=m5, h1_data=h1,
        spread_pips=3.0, slippage_pips=1.0,
        prime_hours_only=True, include_monte_carlo=True,
    )
    print_result("IN-SAMPLE", r_normal)

    # ═════════════════════════════════════════════════════════════
    # TEST 2: Walk-Forward (train 60%, test 40%)
    # ═════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("TEST 2: WALK-FORWARD (train 60%, test 40%)")
    print("Model trained on first 60%, tested on unseen 40%")
    print("=" * 60)

    split_idx = int(len(m5) * 0.6)
    train_m5 = m5.iloc[:split_idx].reset_index(drop=True)
    test_m5 = m5.iloc[split_idx:].reset_index(drop=True)
    print(f"Train: {len(train_m5)} bars | Test: {len(test_m5)} bars")

    # Train fresh models on 60% data
    from app.services.ml.features_mtf import compute_expert_features
    from app.services.ml.symbol_config import get_symbol_config
    from scripts.model_utils import create_labels
    from scripts.train_scalping_pipeline import train_xgboost, train_lightgbm

    sym_config = get_symbol_config("XAUUSD")
    feature_names, X_all = compute_expert_features(train_m5, h1)
    closes = train_m5["close"].values
    atr_vals = X_all[:, feature_names.index("atr_14")]
    y_all = create_labels(closes, atr_vals, config=sym_config)

    warmup = 200
    inner_split = int((len(X_all) - warmup) * 0.8) + warmup
    X_tr, y_tr = X_all[warmup:inner_split], y_all[warmup:inner_split]
    X_val, y_val = X_all[inner_split:], y_all[inner_split:]

    print(f"Training XGBoost (15 trials)...")
    xgb_model, xgb_acc = train_xgboost(X_tr, y_tr, X_val, y_val, n_trials=15)
    print(f"  XGBoost val accuracy: {xgb_acc:.4f}")

    print(f"Training LightGBM (15 trials)...")
    lgb_model, lgb_acc = train_lightgbm(X_tr, y_tr, X_val, y_val, n_trials=15)
    print(f"  LightGBM val accuracy: {lgb_acc:.4f}")

    # Swap models temporarily
    MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "ml_models")
    xgb_path = os.path.join(MODEL_DIR, "scalping_XAUUSD_M5_xgboost.joblib")
    lgb_path = os.path.join(MODEL_DIR, "scalping_XAUUSD_M5_lightgbm.joblib")

    # Backup originals
    shutil.copy2(xgb_path, xgb_path + ".bak")
    shutil.copy2(lgb_path, lgb_path + ".bak")

    # Save walk-forward models
    joblib.dump({"model": xgb_model, "feature_names": feature_names, "grade": "WF", "metrics": {}}, xgb_path)
    joblib.dump({"model": lgb_model, "feature_names": feature_names, "grade": "WF", "metrics": {}}, lgb_path)

    # Backtest on UNSEEN test data
    print(f"Backtesting on {len(test_m5)} unseen bars...")
    r_wf = engine.run(
        symbol="XAUUSD", m5_data=test_m5, h1_data=h1,
        spread_pips=3.0, slippage_pips=1.0,
        prime_hours_only=True, include_monte_carlo=True,
    )
    print_result("WALK-FORWARD (out-of-sample)", r_wf)

    # Restore originals
    shutil.move(xgb_path + ".bak", xgb_path)
    shutil.move(lgb_path + ".bak", lgb_path)

    # ═════════════════════════════════════════════════════════════
    # COMPARISON TABLE
    # ═════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("COMPARISON: In-Sample vs Walk-Forward")
    print("=" * 60)
    print(f"{'Metric':<25} {'In-Sample':<15} {'Walk-Forward':<15} {'Change':<12}")
    print("-" * 67)

    comparisons = [
        ("Trades", r_normal.total_trades, r_wf.total_trades),
        ("Net P&L", r_normal.net_pnl, r_wf.net_pnl),
        ("Win Rate %", r_normal.win_rate, r_wf.win_rate),
        ("Profit Factor", r_normal.profit_factor, r_wf.profit_factor),
        ("Sharpe Ratio", r_normal.sharpe_ratio, r_wf.sharpe_ratio),
        ("Max Drawdown", r_normal.max_drawdown, r_wf.max_drawdown),
        ("Expectancy", r_normal.expectancy, r_wf.expectancy),
        ("R:R Ratio", r_normal.risk_reward_ratio, r_wf.risk_reward_ratio),
    ]

    for name, a, b in comparisons:
        if a != 0:
            change = f"{((b - a) / abs(a) * 100):+.1f}%"
        else:
            change = "N/A"
        print(f"{name:<25} {str(a):<15} {str(b):<15} {change:<12}")

    if r_normal.monte_carlo and r_wf.monte_carlo:
        print(f"\n{'MC DD 95th':<25} {r_normal.monte_carlo.drawdown_95th:<15} {r_wf.monte_carlo.drawdown_95th:<15}")
        print(f"{'MC DD 99th':<25} {r_normal.monte_carlo.drawdown_99th:<15} {r_wf.monte_carlo.drawdown_99th:<15}")

    # Verdict
    print("\n" + "=" * 60)
    print("VERDICT")
    print("=" * 60)

    if r_normal.net_pnl != 0:
        degradation = abs((r_wf.net_pnl - r_normal.net_pnl) / r_normal.net_pnl * 100)
    else:
        degradation = 100

    if r_wf.net_pnl > 0 and r_wf.win_rate > 50:
        print("  RESULT: Walk-forward is PROFITABLE on unseen data")
        print("  The strategy has a genuine predictive edge")
    elif r_wf.net_pnl > 0:
        print("  RESULT: Slightly profitable but weak win rate")
        print("  Strategy may be fragile")
    else:
        print("  RESULT: UNPROFITABLE on unseen data")
        print("  In-sample results were likely overfitted")

    print(f"  P&L degradation: {degradation:.1f}%")
    if degradation < 30:
        print("  Rating: EXCELLENT (<30%) — model generalizes well")
    elif degradation < 50:
        print("  Rating: ACCEPTABLE (30-50%) — some overfitting present")
    elif degradation < 70:
        print("  Rating: CONCERNING (50-70%) — significant overfitting")
    else:
        print("  Rating: POOR (>70%) — heavy overfitting, needs retraining")

    print(f"\n  LIVE EXPECTANCY: ${r_wf.expectancy}/trade")
    print(f"  Based on {r_wf.total_trades} trades on data the model NEVER saw during training")


if __name__ == "__main__":
    asyncio.run(main())
