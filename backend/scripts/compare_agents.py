"""
compare_agents.py - Compare Beginner (v8) vs Potential Agent on forward test.

Loads both model sets, runs backtest on the same OOS period, prints side-by-side.
Usage: cd backend && python -m scripts.compare_agents --symbol US30
"""
import os, sys, argparse, warnings
import numpy as np
import pandas as pd
import joblib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")

from scripts.model_utils import compute_backtest_metrics, grade_model
from app.services.ml.symbol_config import get_symbol_config

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)


def _normalize_ohlcv(df):
    df = df.copy()
    if "ts_event" in df.columns and "time" not in df.columns:
        df["time"] = pd.to_datetime(df["ts_event"]).values.astype("datetime64[s]").astype(np.int64)
        df = df.drop(columns=["ts_event"])
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("time").reset_index(drop=True)


def _load_tf(symbol, tf):
    for path in [os.path.join(HIST_DATA_DIR, symbol, f"{symbol}_{tf}.csv"),
                 os.path.join(DATA_DIR, f"{symbol}_{tf}.csv")]:
        if os.path.exists(path):
            return _normalize_ohlcv(pd.read_csv(path))
    return None


def run_comparison(symbol="US30"):
    cfg = get_symbol_config(symbol)
    cost_bps = cfg.get("cost_bps", 5.0)
    slippage_bps = cfg.get("slippage_bps", 1.0)
    bpd = cfg.get("bars_per_day", 288)

    # Load M5 data
    m5 = _load_tf(symbol, "M5")
    h1 = _load_tf(symbol, "H1")
    h4 = _load_tf(symbol, "H4")
    d1 = _load_tf(symbol, "D1")
    if m5 is None:
        raise FileNotFoundError(f"No M5 data for {symbol}")

    timestamps = m5["time"].values.astype(np.int64)
    closes = m5["close"].values
    opens = m5["open"].values
    highs = m5["high"].values
    lows = m5["low"].values

    # OOS period: 2024-09-01 to present (17 months of forward test)
    oos_start = "2024-09-01"
    oos_ts = int(pd.Timestamp(oos_start, tz="UTC").timestamp())
    oos_mask = timestamps >= oos_ts
    oos_idx = int(np.argmax(oos_mask)) if oos_mask.any() else len(timestamps)

    print(f"\n{'='*70}")
    print(f"  AGENT COMPARISON: {symbol}  |  Forward Test: {oos_start} -> present")
    print(f"  OOS bars: {len(timestamps) - oos_idx:,}")
    print(f"{'='*70}")

    results = {}

    # ── Beginner Agent (v8 = scalping models) ─────────────────────────
    print(f"\n  {'─'*60}")
    print(f"  BEGINNER AGENT (v8 — scalping models)")
    print(f"  {'─'*60}")

    from app.services.ml.features_mtf import compute_expert_features

    # Cap to last 500k for feature computation
    cap = 500_000
    if len(m5) > cap:
        m5_capped = m5.iloc[-cap:].reset_index(drop=True)
        offset = len(m5) - cap
    else:
        m5_capped = m5
        offset = 0

    feat_names_b, X_b = compute_expert_features(m5_capped, h1, h4, d1, symbol=symbol)
    oos_idx_b = oos_idx - offset if offset > 0 else oos_idx
    X_oos_b = X_b[oos_idx_b:]
    from app.services.backtest.indicators import atr as atr_fn
    atr_b = atr_fn(m5_capped["high"].values, m5_capped["low"].values, m5_capped["close"].values, 14)

    # Create labels for evaluation
    from scripts.model_utils import create_labels
    y_all_b = create_labels(m5_capped["close"].values, atr_b)
    y_oos_b = y_all_b[oos_idx_b:]

    for mtype in ["xgboost", "lightgbm"]:
        path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{mtype}.joblib")
        if not os.path.exists(path):
            print(f"  [{mtype}] Model not found — skipping")
            continue
        data = joblib.load(path)
        model = data["model"]
        tp_mult = data.get("execution", {}).get("tp_atr_mult", 1.0)
        sl_mult = data.get("execution", {}).get("sl_atr_mult", 0.8)
        hold_bars = data.get("execution", {}).get("hold_bars", 10)
        grade_stored = data.get("grade", "?")

        m = compute_backtest_metrics(
            model, X_oos_b, y_oos_b,
            m5_capped["close"].values[oos_idx_b:],
            opens_test=m5_capped["open"].values[oos_idx_b:],
            highs_test=m5_capped["high"].values[oos_idx_b:],
            lows_test=m5_capped["low"].values[oos_idx_b:],
            atr_test=atr_b[oos_idx_b:],
            times_test=m5_capped["time"].values[oos_idx_b:],
            cost_bps=cost_bps, slippage_bps=slippage_bps,
            bars_per_day=bpd, hold_bars=hold_bars,
            tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
            trend_filter=False,
        )
        g = grade_model(m)
        results[f"beginner_{mtype}"] = {"grade": g, **m, "stored_grade": grade_stored}
        print(f"  [{mtype}] Grade={g}  Sharpe={m['sharpe']:.3f}  WR={m['win_rate']:.1f}%  "
              f"DD={m['max_drawdown']:.1f}%  Return={m['total_return']:.1f}%  Trades={m['total_trades']}")

    # ── Potential Agent ───────────────────────────────────────────────
    print(f"\n  {'─'*60}")
    print(f"  POTENTIAL AGENT (v1 — institutional features)")
    print(f"  {'─'*60}")

    from app.services.ml.features_potential import compute_potential_features

    feat_names_p, X_p = compute_potential_features(m5_capped, h1, h4, d1, symbol=symbol)

    # Models may expect LSTM feature — pad with zeros if missing
    for mtype in ["xgboost", "lightgbm"]:
        path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_{mtype}.joblib")
        if os.path.exists(path):
            model_feats = joblib.load(path).get("feature_names", [])
            for extra in model_feats[len(feat_names_p):]:
                feat_names_p.append(extra)
                X_p = np.column_stack([X_p, np.zeros(len(X_p), dtype=np.float32)])
            break

    X_oos_p = X_p[oos_idx_b:]

    atr_idx_p = feat_names_p.index("pot_atr_14") if "pot_atr_14" in feat_names_p else None
    atr_p = X_p[:, atr_idx_p] if atr_idx_p is not None else atr_b

    y_all_p = create_labels(m5_capped["close"].values, atr_p, atr_mult=1.2)
    y_oos_p = y_all_p[oos_idx_b:]

    for mtype in ["xgboost", "lightgbm"]:
        path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_{mtype}.joblib")
        if not os.path.exists(path):
            print(f"  [{mtype}] Model not found — skipping")
            continue
        data = joblib.load(path)
        model = data["model"]
        tp_mult = data.get("execution", {}).get("tp_atr_mult", 1.2)
        sl_mult = data.get("execution", {}).get("sl_atr_mult", 0.8)
        hold_bars = data.get("execution", {}).get("hold_bars", 10)

        m = compute_backtest_metrics(
            model, X_oos_p, y_oos_p,
            m5_capped["close"].values[oos_idx_b:],
            opens_test=m5_capped["open"].values[oos_idx_b:],
            highs_test=m5_capped["high"].values[oos_idx_b:],
            lows_test=m5_capped["low"].values[oos_idx_b:],
            atr_test=atr_p[oos_idx_b:],
            times_test=m5_capped["time"].values[oos_idx_b:],
            cost_bps=cost_bps, slippage_bps=slippage_bps,
            bars_per_day=bpd, hold_bars=hold_bars,
            tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
            trend_filter=False,
        )
        g = grade_model(m)
        results[f"potential_{mtype}"] = {"grade": g, **m}
        print(f"  [{mtype}] Grade={g}  Sharpe={m['sharpe']:.3f}  WR={m['win_rate']:.1f}%  "
              f"DD={m['max_drawdown']:.1f}%  Return={m['total_return']:.1f}%  Trades={m['total_trades']}")

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n  {'='*60}")
    print(f"  SIDE-BY-SIDE COMPARISON")
    print(f"  {'='*60}")
    print(f"  {'Agent':<22} {'Grade':>6} {'Sharpe':>8} {'WR':>7} {'DD':>7} {'Return':>8} {'Trades':>7}")
    print(f"  {'-'*60}")
    for name, r in sorted(results.items()):
        print(f"  {name:<22} {r['grade']:>6} {r['sharpe']:>8.3f} {r['win_rate']:>6.1f}% "
              f"{r['max_drawdown']:>6.1f}% {r['total_return']:>7.1f}% {r['total_trades']:>7}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="US30")
    args = parser.parse_args()
    run_comparison(args.symbol)
