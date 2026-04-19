"""
backtest_potential_tpsl.py — compare old vs new TP/SL on Potential Agent.

Usage (from /app inside backend container):
    python -m scripts.backtest_potential_tpsl \
        --symbol BTCUSD --starting-balance 10000

Prints an old-behaviour vs new-behaviour summary side by side so we can see
whether wiring `symbol_config` into runtime TP/SL actually moves the needle.

Old behaviour = what lived in `potential_agent.py:299-300` before the fix:
  tp_mult = 1.5, sl_mult = 1.0, confidence >= 0.52
New behaviour = per-symbol values from `symbol_config.py` + asset-class-default
  confidence threshold as wired in `potential_agent.py:36-90`.
"""
import os, sys, argparse, warnings
import numpy as np
import pandas as pd
import joblib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")

from app.services.ml.features_potential import compute_potential_features
from app.services.ml.symbol_config import get_symbol_config
from app.services.backtest.indicators import atr as atr_fn
from app.services.agent.potential_agent import PotentialAgent

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


def simulate(
    *,
    symbol, preds, probas, closes, opens, highs, lows, atr_vals, timestamps,
    oos_idx, starting_balance, risk_pct, max_lot,
    tp_mult, sl_mult, conf_threshold, hold_bars,
    cost_bps, slippage_bps,
):
    """Run one trade simulation with given TP/SL/threshold, return summary dict."""
    balance = starting_balance
    peak_balance = balance
    pnls, tp_n, sl_n, to_n = [], 0, 0, 0
    daily_pnl = {}

    cost_frac_rt = (cost_bps + slippage_bps) / 10_000.0  # round-trip cost as price fraction

    i = oos_idx
    while i < len(closes) - hold_bars - 1:
        sig = int(preds[i])
        if sig not in (0, 2):
            i += 1
            continue
        # Confidence filter
        conf = float(probas[i, sig]) if probas is not None else 1.0
        if conf < conf_threshold:
            i += 1
            continue

        entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]
        if entry <= 0 or np.isnan(atr_vals[i]) or atr_vals[i] <= 0:
            i += 1
            continue

        atr_val = float(atr_vals[i])
        is_long = (sig == 2)

        tp_dist = atr_val * tp_mult
        sl_dist = atr_val * sl_mult
        tp_price = entry + tp_dist if is_long else entry - tp_dist
        sl_price = entry - sl_dist if is_long else entry + sl_dist

        # Position sizing — risk-based, capped. Instrument-neutral: use price
        # distance and balance fraction; all symbols end up with a lot size
        # that risks roughly `risk_pct` per trade when SL hits.
        risk_amount = balance * risk_pct
        lot_size = risk_amount / max(sl_dist, 1e-9)
        lot_size = min(lot_size, max_lot * entry) if entry > 100 else min(lot_size, max_lot)
        lot_size = max(lot_size, 1e-4)

        exit_price, exit_bar, exit_type = None, None, "timeout"
        for j in range(i + 1, min(i + hold_bars + 1, len(closes))):
            hi, lo = highs[j], lows[j]
            if is_long:
                sl_hit = lo <= sl_price; tp_hit = hi >= tp_price
            else:
                sl_hit = hi >= sl_price; tp_hit = lo <= tp_price
            if sl_hit and tp_hit:
                exit_price, exit_bar, exit_type = sl_price, j, "SL"; break
            elif sl_hit:
                exit_price, exit_bar, exit_type = sl_price, j, "SL"; break
            elif tp_hit:
                exit_price, exit_bar, exit_type = tp_price, j, "TP"; break

        if exit_price is None:
            exit_bar = min(i + hold_bars, len(closes) - 1)
            exit_price = closes[exit_bar]
            exit_type = "timeout"

        raw = (exit_price - entry) if is_long else (entry - exit_price)
        cost_in_price = entry * cost_frac_rt  # proportional cost
        net = raw - cost_in_price
        dollar_pnl = net * lot_size

        balance += dollar_pnl
        if balance > peak_balance:
            peak_balance = balance
        if exit_type == "TP":
            tp_n += 1
        elif exit_type == "SL":
            sl_n += 1
        else:
            to_n += 1
        pnls.append(dollar_pnl)

        date_str = pd.to_datetime(timestamps[i + 1], unit="s", utc=True).strftime("%Y-%m-%d")
        daily_pnl[date_str] = daily_pnl.get(date_str, 0.0) + dollar_pnl

        i = i + hold_bars

    n_trades = len(pnls)
    if n_trades == 0:
        return {"trades": 0}

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    max_dd = 0.0
    peak = starting_balance
    running = starting_balance
    for p in pnls:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    daily_rets = list(daily_pnl.values())
    daily_std = np.std(daily_rets) if len(daily_rets) > 1 else 1.0
    sharpe = (np.mean(daily_rets) / daily_std * np.sqrt(252)) if daily_std > 0 else 0.0
    return {
        "trades": n_trades,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(len(wins) / n_trades * 100, 1),
        "profit_factor": round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) != 0 else float("inf"),
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / starting_balance * 100, 1),
        "sharpe": round(sharpe, 2),
        "tp_sl_to": f"{tp_n}/{sl_n}/{to_n}",
        "avg_win":  round(np.mean(wins) if wins else 0, 2),
        "avg_loss": round(np.mean(losses) if losses else 0, 2),
        "final":    round(starting_balance + total_pnl, 2),
    }


def run(symbol, starting_balance=10000.0, risk_pct=0.01, max_lot=0.1,
        oos_start="2024-09-01"):
    m5 = _load_tf(symbol, "M5")
    h1 = _load_tf(symbol, "H1")
    h4 = _load_tf(symbol, "H4")
    d1 = _load_tf(symbol, "D1")
    if m5 is None:
        raise FileNotFoundError(f"No M5 data for {symbol}")

    cap = 500_000
    if len(m5) > cap:
        m5 = m5.iloc[-cap:].reset_index(drop=True)
        start_ts = m5["time"].iloc[0]
        if h1 is not None: h1 = h1[h1["time"] >= start_ts].reset_index(drop=True)
        if h4 is not None: h4 = h4[h4["time"] >= start_ts].reset_index(drop=True)
        if d1 is not None: d1 = d1[d1["time"] >= start_ts].reset_index(drop=True)

    timestamps = m5["time"].values.astype(np.int64)
    closes = m5["close"].values.astype(float)
    opens = m5["open"].values.astype(float)
    highs = m5["high"].values.astype(float)
    lows = m5["low"].values.astype(float)

    cfg = get_symbol_config(symbol)
    print(f"\n=== {symbol} — symbol_config: tp={cfg.get('tp_atr_mult', '?')} "
          f"sl={cfg.get('sl_atr_mult', '?')} cost_bps={cfg.get('cost_bps', '?')} "
          f"slip_bps={cfg.get('slippage_bps', '?')} class={cfg.get('asset_class', '?')} ===")

    # Slice OOS
    oos_ts = pd.to_datetime(oos_start).timestamp()
    oos_idx = int(np.searchsorted(timestamps, oos_ts))
    if oos_idx >= len(timestamps) - 100:
        print(f"  ! OOS start {oos_start} has too little data; using last 30% of series")
        oos_idx = int(len(timestamps) * 0.7)

    # Features + predictions (XGBoost preferred, fallback LightGBM)
    feat_names, X = compute_potential_features(m5, h1, h4, d1, symbol=symbol)
    atr_vals = atr_fn(highs, lows, closes, 14)

    mpath_xgb = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_xgboost.joblib")
    mpath_lgb = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_lightgbm.joblib")
    mpath = mpath_xgb if os.path.exists(mpath_xgb) else mpath_lgb
    if not os.path.exists(mpath):
        print(f"  ! No potential model deployed for {symbol}: {mpath}")
        return
    print(f"  Model: {os.path.basename(mpath)}")
    model = joblib.load(mpath)
    if isinstance(model, dict):
        model = model.get("model") or model.get("estimator") or list(model.values())[0]
    preds = model.predict(X)
    try:
        probas = model.predict_proba(X)
    except Exception:
        probas = None

    # ATR gate (mirrors runtime): suppress signals in low-vol regimes.
    atr_ser = pd.Series(atr_vals.astype(np.float64))
    atr_pctile = atr_ser.rolling(100, min_periods=50).rank(pct=True).values
    low_vol = atr_pctile < 0.25
    preds = np.where(low_vol & (preds != 1), np.int64(1), preds)

    # Old-behaviour defaults: 1.5/1.0 ATR + confidence 0.52
    old = simulate(
        symbol=symbol, preds=preds, probas=probas,
        closes=closes, opens=opens, highs=highs, lows=lows,
        atr_vals=atr_vals, timestamps=timestamps,
        oos_idx=oos_idx, starting_balance=starting_balance,
        risk_pct=risk_pct, max_lot=max_lot,
        tp_mult=1.5, sl_mult=1.0, conf_threshold=0.52, hold_bars=10,
        cost_bps=cfg.get("cost_bps", 3.0),
        slippage_bps=cfg.get("slippage_bps", 1.0),
    )

    # New behaviour: whatever `symbol_config` + asset-class default says.
    asset_class = cfg.get("asset_class", "index")
    new_conf = PotentialAgent.DEFAULT_CONFIDENCE_BY_CLASS.get(asset_class, 0.52)
    new_hold = cfg.get("hold_bars", 10)
    new = simulate(
        symbol=symbol, preds=preds, probas=probas,
        closes=closes, opens=opens, highs=highs, lows=lows,
        atr_vals=atr_vals, timestamps=timestamps,
        oos_idx=oos_idx, starting_balance=starting_balance,
        risk_pct=risk_pct, max_lot=max_lot,
        tp_mult=cfg.get("tp_atr_mult", 1.5),
        sl_mult=cfg.get("sl_atr_mult", 1.0),
        conf_threshold=new_conf, hold_bars=new_hold,
        cost_bps=cfg.get("cost_bps", 3.0),
        slippage_bps=cfg.get("slippage_bps", 1.0),
    )

    hdr = f"{'metric':<14}{'OLD (1.5/1.0@0.52)':>22}{'NEW (per-symbol)':>22}"
    print("\n  " + hdr)
    print("  " + "-" * len(hdr))
    keys = ["trades", "total_pnl", "win_rate", "profit_factor", "max_dd_pct",
            "sharpe", "tp_sl_to", "avg_win", "avg_loss", "final"]
    for k in keys:
        ov = old.get(k, "-")
        nv = new.get(k, "-")
        print(f"  {k:<14}{str(ov):>22}{str(nv):>22}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSD,US30,NAS100,XAUUSD",
                    help="Comma-separated symbols")
    ap.add_argument("--starting-balance", type=float, default=10000.0)
    ap.add_argument("--risk-pct", type=float, default=0.01)
    ap.add_argument("--max-lot", type=float, default=0.1)
    ap.add_argument("--oos-start", default="2024-09-01")
    args = ap.parse_args()

    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            run(sym, starting_balance=args.starting_balance,
                risk_pct=args.risk_pct, max_lot=args.max_lot,
                oos_start=args.oos_start)
        except Exception as e:
            import traceback
            print(f"\n  ! {sym} failed: {e}")
            traceback.print_exc()
