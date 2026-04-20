"""
backtest_bolt_challenge.py — simulate a FundedNext Bolt challenge end-to-end.

Takes a deployed `potential_{SYMBOL}` model, a starting date, and runs a
day-by-day simulation enforcing every Bolt rule:

  - $50,000 starting balance
  - $3,000 profit target
  - $1,000 daily loss limit (EOD aggregate)
  - $2,000 trailing drawdown (EOD trailing; stops at balance ≥ $50,100)
  - 40 % consistency rule: no single day's profit > 40 % of total profit
  - Force-flat: all positions closed by 21:00 UTC every day
  - 0.75 % base risk per trade

Prints pass/fail verdict + day-by-day ledger. Run a handful of start dates
to estimate challenge-pass probability before risking the $99.99 fee.

Usage (inside backend container):
    python -m scripts.backtest_bolt_challenge \
        --symbol XAUUSD \
        --start-date 2026-01-02 --end-date 2026-04-15

Symbol note: for Bolt we'll be trading CME futures via Tradovate, so our
canonical names map like:
    XAUUSD → GC   (gold futures)
    ES     → ES   (S&P 500 E-mini)
    NAS100 → NQ   (Nasdaq 100 E-mini)
    US30   → YM   (Dow E-mini)
The simulation uses OHLCV from the persistent `History Data/data/` store —
that data is CME-contract-level for ES/NAS100 (Databento), and XAU/USD spot
for XAUUSD (Dukascopy, a good proxy for GC since the instruments correlate
~0.99 on bars).
"""
import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd
import joblib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")

from app.services.ml.features_potential import compute_potential_features
from app.services.ml.symbol_config import get_symbol_config
from app.services.backtest.indicators import atr as atr_fn

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
HIST_CANDIDATES = [
    "/app/History Data/data",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")),
]
HIST_DIR = next((p for p in HIST_CANDIDATES if os.path.isdir(p)), HIST_CANDIDATES[0])

# Per-symbol dollar value per point (approximation — real Tradovate tick
# values: ES $12.50/tick at 0.25 point, NQ $5/tick at 0.25, GC $10/tick
# at 0.10). We use point-value here for simplicity since our lot-sizing is
# balance-based.
POINT_VALUE = {
    "XAUUSD": 100.0,   # $100 per $1 move on GC (100 oz)
    "ES":     50.0,
    "NAS100": 20.0,
    "US30":   5.0,
}


def _load_tf(symbol: str, tf: str) -> pd.DataFrame:
    path = os.path.join(HIST_DIR, symbol, f"{symbol}_{tf}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).astype({"time": "int64"})
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("time").reset_index(drop=True)


def run_bolt(
    symbol: str,
    start_date: str,
    end_date: str,
    starting_balance: float = 50_000.0,
    profit_target: float = 3_000.0,
    daily_loss_limit: float = 1_000.0,
    trailing_dd: float = 2_000.0,
    trail_lock_at: float = 50_100.0,
    consistency_cap: float = 0.40,
    force_flat_hour: int = 21,
    base_risk_pct: float = 0.0075,
    max_trades_per_day: int = 5,
    hold_bars: int = 10,
) -> dict:
    # ── Data ─────────────────────────────────────────────────────────
    m5 = _load_tf(symbol, "M5")
    h1 = _load_tf(symbol, "H1")
    h4 = _load_tf(symbol, "H4")
    d1 = _load_tf(symbol, "D1")

    start_ts = int(pd.Timestamp(start_date, tz="UTC").timestamp())
    end_ts   = int(pd.Timestamp(end_date,   tz="UTC").timestamp())
    m5 = m5[(m5["time"] >= start_ts - 2 * 86400 * 30) & (m5["time"] <= end_ts)].reset_index(drop=True)
    if len(m5) < 1000:
        raise ValueError(f"Not enough data for {symbol} {start_date} → {end_date}")

    print(f"\n== Bolt simulation: {symbol} {start_date} → {end_date} ==")
    print(f"   M5 bars: {len(m5):,}  |  Starting balance: ${starting_balance:,.0f}")

    # ── Features + predictions ────────────────────────────────────────
    # Compute features on the windowed m5 INCLUDING the 60-day warmup;
    # we'll trim to the simulation region right before the trade loop.
    feat_names, X = compute_potential_features(m5, h1, h4, d1, symbol=symbol)
    atr_vals_full = atr_fn(m5["high"].values, m5["low"].values, m5["close"].values, 14)

    model_path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_xgboost.joblib")
    if not os.path.exists(model_path):
        model_path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_lightgbm.joblib")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"No potential model for {symbol}")
    print(f"   Model: {os.path.basename(model_path)}")
    data = joblib.load(model_path)
    model = data["model"] if isinstance(data, dict) else data
    preds = model.predict(X)

    # ── Bolt simulation state ────────────────────────────────────────
    cfg = get_symbol_config(symbol)
    tp_mult = float(cfg.get("tp_atr_mult", 1.5))
    sl_mult = float(cfg.get("sl_atr_mult", 1.0))
    cost_bps = float(cfg.get("cost_bps", 3.0))
    slip_bps = float(cfg.get("slippage_bps", 1.0))
    point_val = POINT_VALUE.get(symbol, 1.0)

    balance = starting_balance
    high_water = starting_balance
    trail_lock_hit = False
    daily_ledger: list[dict] = []
    all_trades: list[dict] = []

    # Trim to the simulation region. preds and atr_vals_full are indexed on
    # the windowed m5 (warmup + simulation), so we use the same slice.
    sim_mask = m5["time"].values >= start_ts
    sim_offset = int(np.argmax(sim_mask)) if sim_mask.any() else 0
    m5 = m5.iloc[sim_offset:].reset_index(drop=True)
    preds = preds[sim_offset:sim_offset + len(m5)]
    atr_vals = atr_vals_full[sim_offset:sim_offset + len(m5)]

    times = m5["time"].values.astype(np.int64)
    day_series = pd.to_datetime(times, unit="s", utc=True).strftime("%Y-%m-%d")
    unique_days = sorted(set(day_series))

    closes = m5["close"].values.astype(float)
    opens  = m5["open"].values.astype(float)
    highs  = m5["high"].values.astype(float)
    lows   = m5["low"].values.astype(float)

    fail_reason = None

    for day in unique_days:
        day_mask = day_series == day
        day_idxs = np.where(day_mask)[0]
        if len(day_idxs) == 0:
            continue

        day_pnl = 0.0
        day_trades = 0
        consistency_cap_usd = consistency_cap * profit_target
        first_idx = day_idxs[0]

        # Compute cutoff index: last bar whose UTC hour < force_flat_hour
        cutoff_idxs = [i for i in day_idxs
                       if pd.to_datetime(int(times[i]), unit="s", utc=True).hour < force_flat_hour]
        cutoff_idx = cutoff_idxs[-1] if cutoff_idxs else first_idx

        i = first_idx
        while i <= cutoff_idx - hold_bars - 1:
            # Stop for today if daily loss limit hit
            if day_pnl <= -daily_loss_limit:
                break
            # Stop for today if consistency cap would be breached
            if day_pnl >= consistency_cap_usd:
                break
            # Stop for today if max-trades-per-day
            if day_trades >= max_trades_per_day:
                break

            sig = int(preds[i]) if i < len(preds) else 1
            if sig not in (0, 2):
                i += 1
                continue

            entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]
            atr_v = atr_vals[i] if i < len(atr_vals) else 0
            if atr_v <= 0 or np.isnan(atr_v) or entry <= 0:
                i += 1
                continue

            is_long = sig == 2
            tp_dist = atr_v * tp_mult
            sl_dist = atr_v * sl_mult
            tp_price = entry + tp_dist if is_long else entry - tp_dist
            sl_price = entry - sl_dist if is_long else entry + sl_dist

            risk_usd = balance * base_risk_pct
            lot = risk_usd / max(sl_dist * point_val, 1e-6)
            lot = max(0.01, min(lot, 100))

            # Scan forward but STOP at cutoff_idx — force-flat enforcement
            exit_price = None
            exit_reason = "timeout"
            j_end = min(i + hold_bars + 1, cutoff_idx + 1, len(closes))
            for j in range(i + 1, j_end):
                hi, lo = highs[j], lows[j]
                sl_hit = (lo <= sl_price) if is_long else (hi >= sl_price)
                tp_hit = (hi >= tp_price) if is_long else (lo <= tp_price)
                if sl_hit:
                    exit_price, exit_reason = sl_price, "SL"; break
                if tp_hit:
                    exit_price, exit_reason = tp_price, "TP"; break
            if exit_price is None:
                # Force-flat or hold-bars timeout — close at bar's close
                j = min(i + hold_bars, cutoff_idx, len(closes) - 1)
                exit_price = closes[j]
                exit_reason = "force_flat" if j == cutoff_idx else "timeout"

            raw_pts = (exit_price - entry) if is_long else (entry - exit_price)
            cost_frac = (cost_bps + slip_bps) / 10_000.0
            net_pts = raw_pts - entry * cost_frac
            trade_pnl = net_pts * lot * point_val

            day_pnl += trade_pnl
            balance += trade_pnl
            day_trades += 1
            all_trades.append({
                "day": day, "entry_ts": int(times[i + 1]), "exit_ts": int(times[j]),
                "direction": "BUY" if is_long else "SELL",
                "pnl": round(trade_pnl, 2), "exit": exit_reason, "balance": round(balance, 2),
            })

            # Check Bolt kill-switches immediately
            if day_pnl <= -daily_loss_limit:
                fail_reason = f"{day}: daily loss limit hit ({day_pnl:,.2f} ≤ -${daily_loss_limit:,.0f})"
                break

            i = j + 1

        # End-of-day bookkeeping
        if not trail_lock_hit and balance >= trail_lock_at:
            trail_lock_hit = True
            # Lock the trailing DD floor at starting balance
            high_water = starting_balance
        if not trail_lock_hit and balance > high_water:
            high_water = balance
        trail_floor = (starting_balance if trail_lock_hit
                       else high_water - trailing_dd)

        daily_ledger.append({
            "day": day, "pnl": round(day_pnl, 2), "trades": day_trades,
            "balance": round(balance, 2), "trail_floor": round(trail_floor, 2),
            "trail_lock": trail_lock_hit,
        })

        if balance < trail_floor:
            fail_reason = f"{day}: trailing DD floor breached (balance ${balance:,.2f} < floor ${trail_floor:,.2f})"
            break
        if fail_reason:
            break
        if balance >= starting_balance + profit_target:
            # Target hit — challenge passes (but must still respect consistency)
            break

    # ── Verdict ──────────────────────────────────────────────────────
    total_pnl = balance - starting_balance
    days_traded = sum(1 for d in daily_ledger if d["trades"] > 0)

    # 40 % consistency check over the whole run
    if daily_ledger and total_pnl > 0:
        best_day = max(daily_ledger, key=lambda x: x["pnl"])
        if best_day["pnl"] > consistency_cap * total_pnl:
            consistency_breach = (best_day["day"], best_day["pnl"])
        else:
            consistency_breach = None
    else:
        consistency_breach = None

    passed = (
        fail_reason is None
        and total_pnl >= profit_target
        and consistency_breach is None
    )

    print()
    print("── DAILY LEDGER ──")
    for d in daily_ledger[-20:]:
        lock_mark = "🔒" if d["trail_lock"] else "  "
        print(f"  {d['day']}  {lock_mark}  P&L ${d['pnl']:>+9,.2f}   balance ${d['balance']:>9,.2f}   "
              f"floor ${d['trail_floor']:>9,.2f}   trades {d['trades']}")
    if len(daily_ledger) > 20:
        print(f"  … {len(daily_ledger) - 20} more days not shown")
    print()
    print("── VERDICT ──")
    print(f"  Total P&L:          ${total_pnl:>+9,.2f}  ({total_pnl / starting_balance * 100:+.2f} %)")
    print(f"  Final balance:      ${balance:>9,.2f}")
    print(f"  Days traded:        {days_traded} / {len(daily_ledger)}")
    print(f"  Trades total:       {len(all_trades)}")
    if fail_reason:
        print(f"  FAIL reason:        {fail_reason}")
    if consistency_breach:
        d, p = consistency_breach
        print(f"  40% consistency:   BREACH — {d} day P&L ${p:,.2f} > 40% of total P&L ${total_pnl:,.2f}")
    else:
        print(f"  40% consistency:   ok")
    print(f"  Trailing DD locked: {trail_lock_hit}")
    print()
    print(f"  CHALLENGE {'✓ PASSED' if passed else '✗ FAILED'}")

    return {
        "passed": passed,
        "total_pnl": total_pnl,
        "final_balance": balance,
        "fail_reason": fail_reason,
        "consistency_breach": consistency_breach,
        "days_traded": days_traded,
        "trades": len(all_trades),
        "trail_lock_hit": trail_lock_hit,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD",
                    help="Symbol to backtest (uses existing potential_{SYMBOL} model)")
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date",   required=True, help="YYYY-MM-DD")
    ap.add_argument("--starting-balance", type=float, default=50_000.0)
    args = ap.parse_args()
    run_bolt(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        starting_balance=args.starting_balance,
    )
