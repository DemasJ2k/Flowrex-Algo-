"""
D1 Bias Filter Ablation — Flowrex v2

For each trained Flowrex v2 symbol, run OOS backtest with and without
the live agent's "Gate B" D1 bias veto. Compares:

  (A) Backtest as-trained (existing trend/squeeze/ATR/session filters)
  (B) Backtest + live D1 EMA21 bias veto (what agent does in production)

Goal: quantify whether Gate B helps or hurts, per symbol.

Usage:
    cd backend
    python3 -m scripts.ablate_d1_filter
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import joblib

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_flowrex import compute_flowrex_features
from app.services.ml.symbol_config import get_symbol_config
from scripts.model_utils import create_labels, compute_backtest_metrics
from scripts.train_flowrex import load_ohlcv, OOS_START, WARMUP

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "ml_models")
SYMBOLS = ["XAUUSD", "NAS100", "US30", "BTCUSD"]
MODEL_TYPES = ["xgboost", "lightgbm", "catboost"]


def load_model_bundle(symbol: str, mtype: str):
    path = os.path.join(MODEL_DIR, f"flowrex_{symbol}_M5_{mtype}.joblib")
    if not os.path.exists(path):
        return None
    return joblib.load(path)


def compute_d1_bias_per_m5(m5_times: np.ndarray, d1_df: pd.DataFrame) -> np.ndarray:
    """
    For each M5 timestamp, compute the D1 EMA21 bias that would be visible
    to the live agent at that moment (using the most recent CLOSED D1 bar).
    Returns int array: +1 bull, -1 bear, 0 neutral/unknown.
    """
    if d1_df is None or len(d1_df) < 25:
        return np.zeros(len(m5_times), dtype=np.int64)

    d1 = d1_df.sort_values("time").reset_index(drop=True)
    d1_closes = d1["close"].values.astype(float)
    d1_times = d1["time"].values.astype(np.int64)

    # EMA21 of D1 closes
    alpha = 2.0 / (21 + 1)
    ema = np.zeros_like(d1_closes)
    ema[0] = d1_closes[0]
    for i in range(1, len(d1_closes)):
        ema[i] = alpha * d1_closes[i] + (1 - alpha) * ema[i - 1]

    # +1 if close > ema21, else -1
    d1_bias_series = np.where(d1_closes > ema, 1, -1).astype(np.int64)

    # For each M5 time, find the most recent D1 close with d1_time < m5_time
    # (agent can only see CLOSED D1 bars)
    idx = np.searchsorted(d1_times, m5_times, side="right") - 1
    bias = np.zeros(len(m5_times), dtype=np.int64)
    valid = idx >= 0
    bias[valid] = d1_bias_series[idx[valid]]
    return bias


def metrics_with_live_gate_b(
    model, X_test, y_test, closes, opens, highs, lows, atr, times,
    d1_bias, execution_cfg,
):
    """
    Same as compute_backtest_metrics, but with an additional filter:
    before the backtest runs, any signal going against the live D1 bias
    is converted to HOLD (matching flowrex_agent_v2.py:229 "Signal vs D1 bias mismatch").
    """
    from sklearn.metrics import accuracy_score

    preds = np.asarray(model.predict(X_test)).ravel().astype(np.int64)
    accuracy = accuracy_score(y_test, preds)

    # Apply live Gate B: block sells against bull bias, blocks buys against bear bias
    # Class mapping: 0=SELL, 1=HOLD, 2=BUY
    preds = np.where((preds == 0) & (d1_bias == 1),  np.int64(1), preds)  # block SELL in bull
    preds = np.where((preds == 2) & (d1_bias == -1), np.int64(1), preds)  # block BUY in bear

    # Feed the filtered predictions to the standard backtest by wrapping the model
    class _FilteredModel:
        def predict(self, X):
            return preds

    return compute_backtest_metrics(
        _FilteredModel(), X_test, y_test,
        closes_test=closes, opens_test=opens, highs_test=highs, lows_test=lows,
        atr_test=atr, times_test=times,
        cost_bps=execution_cfg.get("cost_bps", 5.0),
        slippage_bps=execution_cfg.get("slippage_bps", 1.0),
        bars_per_day=288,
        hold_bars=execution_cfg.get("hold_bars", 10),
        tp_atr_mult=execution_cfg.get("tp_atr_mult", 1.0),
        sl_atr_mult=execution_cfg.get("sl_atr_mult", 0.8),
        trend_filter=True,
    )


def run_symbol(symbol: str):
    print(f"\n{'=' * 72}")
    print(f"  {symbol}")
    print(f"{'=' * 72}")

    # Load one model bundle to get feature list + execution config
    bundle = load_model_bundle(symbol, "xgboost")
    if bundle is None:
        print(f"  No xgboost model — skipping {symbol}")
        return

    feat_names = bundle["feature_names"]
    execution_cfg = bundle.get("execution", {})
    tp_mult = execution_cfg.get("tp_atr_mult", 1.0)
    sl_mult = execution_cfg.get("sl_atr_mult", 0.8)
    hold_bars = execution_cfg.get("hold_bars", 10)

    # Load data
    print("  Loading M5/H1/H4/D1...")
    m5, h1, h4, d1 = load_ohlcv(symbol)
    print(f"    M5={len(m5):,}  H1={len(h1) if h1 is not None else 0:,}  "
          f"H4={len(h4) if h4 is not None else 0:,}  D1={len(d1) if d1 is not None else 0:,}")

    # Compute features (returns tuple: feat_names, X_array)
    print("  Computing 120 Flowrex v2 features (slow, one-time)...")
    feat_names_computed, X_full = compute_flowrex_features(m5, h1, h4, d1, symbol=symbol)
    assert len(feat_names_computed) == len(feat_names), \
        f"Feature count mismatch: {len(feat_names_computed)} vs {len(feat_names)}"

    times_full = m5["time"].values.astype(np.int64)
    closes_full = m5["close"].values.astype(np.float64)
    opens_full = m5["open"].values.astype(np.float64)
    highs_full = m5["high"].values.astype(np.float64)
    lows_full = m5["low"].values.astype(np.float64)

    # ATR (same as training pipeline)
    from app.services.backtest.indicators import atr as atr_fn
    atr_full = atr_fn(highs_full, lows_full, closes_full, 14)

    # Labels
    labels = create_labels(closes_full, atr_full, forward_bars=hold_bars, atr_mult=tp_mult)

    # Split to OOS
    oos_ts = int(pd.Timestamp(OOS_START, tz="UTC").timestamp())
    oos_mask = times_full >= oos_ts
    oos_idx = np.where(oos_mask)[0]
    if len(oos_idx) == 0:
        print(f"  No OOS data after {OOS_START} — skipping")
        return

    start = oos_idx[0]
    end = len(times_full) - 1  # inclusive end

    X_oos     = X_full[start:end + 1]
    y_oos     = labels[start:end + 1]
    closes_o  = closes_full[start:end + 1]
    opens_o   = opens_full[start:end + 1]
    highs_o   = highs_full[start:end + 1]
    lows_o    = lows_full[start:end + 1]
    atr_o     = atr_full[start:end + 1]
    times_o   = times_full[start:end + 1]

    # D1 bias time series aligned to OOS M5 bars
    d1_bias = compute_d1_bias_per_m5(times_o, d1)
    bull_frac = (d1_bias == 1).mean()
    bear_frac = (d1_bias == -1).mean()
    print(f"  OOS bars: {len(X_oos):,}  | D1 bias distribution: {bull_frac*100:.0f}% bull, {bear_frac*100:.0f}% bear")

    # Run comparison for each model
    print(f"\n  {'Model':<10} {'Filter':<12} {'Grade':<6} {'Sharpe':>8} {'WR':>7} {'Trades':>8} {'Return':>9} {'DD':>7}")
    print(f"  {'-'*72}")

    for mtype in MODEL_TYPES:
        b = load_model_bundle(symbol, mtype)
        if b is None:
            continue
        model = b["model"]

        # A) Backtest as-trained (existing filters only)
        m_baseline = compute_backtest_metrics(
            model, X_oos, y_oos,
            closes_test=closes_o, opens_test=opens_o, highs_test=highs_o, lows_test=lows_o,
            atr_test=atr_o, times_test=times_o,
            cost_bps=execution_cfg.get("cost_bps", 5.0),
            slippage_bps=execution_cfg.get("slippage_bps", 1.0),
            bars_per_day=288,
            hold_bars=execution_cfg.get("hold_bars", 10),
            tp_atr_mult=execution_cfg.get("tp_atr_mult", 1.0),
            sl_atr_mult=execution_cfg.get("sl_atr_mult", 0.8),
            trend_filter=True,
        )

        # B) Backtest + live Gate B
        m_gate_b = metrics_with_live_gate_b(
            model, X_oos, y_oos, closes_o, opens_o, highs_o, lows_o, atr_o, times_o,
            d1_bias, execution_cfg,
        )

        def fmt(m, label):
            grade = (
                "A" if m["sharpe"] >= 2.0 and m["max_drawdown"] <= 0.15 else
                "B" if m["sharpe"] >= 1.0 else
                "C" if m["sharpe"] >= 0.5 else
                "D" if m["sharpe"] >= 0.0 else "F"
            )
            print(f"  {mtype:<10} {label:<12} {grade:<6} "
                  f"{m['sharpe']:>8.2f} {m['win_rate']*100:>6.1f}% "
                  f"{m['total_trades']:>8} {m['total_return']*100:>8.1f}% "
                  f"{m['max_drawdown']*100:>6.1f}%")

        fmt(m_baseline, "NO Gate B")
        fmt(m_gate_b, "WITH Gate B")


def main():
    for sym in SYMBOLS:
        try:
            run_symbol(sym)
        except Exception as e:
            print(f"\n[ERROR] {sym}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
