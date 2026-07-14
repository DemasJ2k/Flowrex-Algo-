"""
Backtest for the rules-first swing system (Phase 2, 2026-07-13 reorg).

Drives EXACTLY the same rule code the live SwingAgent uses
(app/services/agent/swing_rules.py) over the History Data H1 CSVs.
H4 and D1 series are resampled from H1 with UTC anchors so the test is
deterministic and source-consistent. (Live uses broker H4/D1 candles whose
session anchors can differ slightly — a known, accepted v1 gap.)

Execution model (pessimistic by construction):
  * Signal evaluated on H1 close → filled at the NEXT H1 open.
  * Spread + slippage charged on entry (price moved against us).
  * If an H1 bar contains both SL and TP, the SL is assumed to hit first.
  * Gaps through the stop fill at the bar's open (full gap taken).
  * Max hold: 240 H1 bars (~10 trading days) → exit at close.

Usage:
  python scripts/backtest_swing.py                     # US30 + XAUUSD, all history
  python scripts/backtest_swing.py --symbols US30 --start 2020-01-01
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent.swing_rules import (  # noqa: E402
    SwingConfig, ZoneTracker, check_entry, d1_trend_series, h4_atr_series,
)

HIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data"))
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backtests")

# Execution costs (points), mirroring api/backtest.py _EXEC_COSTS (Oanda paper)
COSTS = {
    "US30":   {"spread": 3.0, "slippage": 1.0},
    "XAUUSD": {"spread": 0.30, "slippage": 0.10},
}

MAX_HOLD_H1_BARS = 240
RISK_PCT = 0.005
START_BALANCE = 10_000.0


def load_h1(symbol: str) -> pd.DataFrame:
    path = os.path.join(HIST_DIR, symbol, f"{symbol}_H1.csv")
    df = pd.read_csv(path)
    df = df.dropna().reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = (
        df.set_index("dt")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    r["time"] = (r["dt"].astype("int64") // 10**9).astype(int)
    return r


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def run_symbol(symbol: str, cfg: SwingConfig, start: str = None, end: str = None) -> dict:
    h1 = load_h1(symbol)
    if start:
        h1 = h1[h1["dt"] >= pd.Timestamp(start, tz="UTC")].reset_index(drop=True)
    if end:
        h1 = h1[h1["dt"] < pd.Timestamp(end, tz="UTC")].reset_index(drop=True)
    if len(h1) < 500:
        raise SystemExit(f"{symbol}: only {len(h1)} H1 bars — not enough")

    h4 = resample(h1, "4h")
    d1 = resample(h1, "1D")

    h1_t = h1["time"].to_numpy()
    h1_o = h1["open"].to_numpy(dtype=np.float64)
    h1_h = h1["high"].to_numpy(dtype=np.float64)
    h1_l = h1["low"].to_numpy(dtype=np.float64)
    h1_c = h1["close"].to_numpy(dtype=np.float64)

    h4_t = h4["time"].to_numpy()
    h4_o = h4["open"].to_numpy(dtype=np.float64)
    h4_h = h4["high"].to_numpy(dtype=np.float64)
    h4_l = h4["low"].to_numpy(dtype=np.float64)
    h4_c = h4["close"].to_numpy(dtype=np.float64)
    atr_h4 = h4_atr_series(h4_h, h4_l, h4_c, cfg.atr_period)
    # An H4 bar resampled from H1 is complete once its last H1 bar has
    # closed. Its coverage end = open time + 4h (may include fewer H1 bars
    # around weekend edges — end time is still the safe bound).
    h4_end = h4_t + 14400

    d1_t = d1["time"].to_numpy()
    d1_c = d1["close"].to_numpy(dtype=np.float64)
    d1_trend_arr = d1_trend_series(d1_c, cfg.d1_ema_period, cfg.d1_slope_bars)
    d1_end = d1_t + 86400

    cost = COSTS.get(symbol, {"spread": 0.0, "slippage": 0.0})
    friction = cost["spread"] + cost["slippage"]

    tracker = ZoneTracker(cfg)
    fed_h4 = 0          # H4 bars fed to the tracker so far
    trades: list[dict] = []
    balance = START_BALANCE
    peak = balance
    max_dd = 0.0
    position = None
    last_entry_i = -10**9
    pending = None      # signal awaiting next-bar-open fill

    warmup = cfg.d1_ema_period  # need EMA history before trend is meaningful

    for i in range(1, len(h1_c)):
        now_close_ts = int(h1_t[i]) + 3600

        # 1. Feed newly-completed H4 bars to the zone tracker
        while fed_h4 < len(h4_t) and h4_end[fed_h4] <= int(h1_t[i]):
            tracker.on_h4_bar(h4_o[fed_h4], h4_h[fed_h4], h4_l[fed_h4],
                              h4_c[fed_h4], atr_h4[fed_h4])
            fed_h4 += 1
        if fed_h4 < cfg.atr_period + 5:
            continue

        o, hi, lo, cl = h1_o[i], h1_h[i], h1_l[i], h1_c[i]

        # 2. Fill any pending entry at this bar's open
        if pending is not None and position is None:
            direction = pending["direction"]
            entry = o + direction * friction
            sl, tp = pending["stop_loss"], pending["take_profit"]
            sl_dist = direction * (entry - sl)
            # Abandon if the open gapped through the stop (or TP) overnight
            if sl_dist > 0 and direction * (tp - entry) > 0:
                risk_amount = balance * RISK_PCT
                position = {
                    **pending, "entry": entry, "entry_i": i,
                    "risk_amount": risk_amount, "sl_dist": sl_dist,
                }
                last_entry_i = i
            pending = None

        # 3. Manage open position on this bar
        if position is not None:
            direction = position["direction"]
            sl, tp = position["stop_loss"], position["take_profit"]
            exit_price = None
            reason = None
            if direction > 0:
                if o <= sl:                       # gap through stop
                    exit_price, reason = o, "SL_GAP"
                elif lo <= sl:                    # SL first (pessimistic)
                    exit_price, reason = sl, "SL"
                elif hi >= tp:
                    exit_price, reason = tp, "TP"
            else:
                if o >= sl:
                    exit_price, reason = o, "SL_GAP"
                elif hi >= sl:
                    exit_price, reason = sl, "SL"
                elif lo <= tp:
                    exit_price, reason = tp, "TP"
            if exit_price is None and i - position["entry_i"] >= MAX_HOLD_H1_BARS:
                exit_price, reason = cl, "MAX_HOLD"

            if exit_price is not None:
                r_mult = direction * (exit_price - position["entry"]) / position["sl_dist"]
                pnl = position["risk_amount"] * r_mult
                balance += pnl
                peak = max(peak, balance)
                max_dd = max(max_dd, (peak - balance) / peak)
                trades.append({
                    "symbol": symbol,
                    "entry_ts": int(h1_t[position["entry_i"]]),
                    "exit_ts": int(h1_t[i]),
                    "direction": "BUY" if direction > 0 else "SELL",
                    "entry": round(position["entry"], 4),
                    "exit": round(exit_price, 4),
                    "r": round(r_mult, 3),
                    "pnl": round(pnl, 2),
                    "reason": reason,
                    "zone": f"{position['zone_kind']}/{position['zone_source']}",
                    "hold_h1_bars": i - position["entry_i"],
                    "year": datetime.fromtimestamp(int(h1_t[i]), tz=timezone.utc).year,
                })
                position = None

        # 4. Look for a new signal on this bar's close (flat only)
        if position is None and pending is None:
            di = int(np.searchsorted(d1_end, now_close_ts, side="right")) - 1
            if di < warmup:
                continue
            trend = int(d1_trend_arr[di])
            atr_now = float(atr_h4[fed_h4 - 1])
            sig = check_entry(h1_o, h1_h, h1_l, h1_c, i, tracker, trend,
                              atr_now, cfg, last_entry_i)
            if sig is not None:
                pending = sig

    return summarize(symbol, trades, balance, max_dd, h1)


def summarize(symbol: str, trades: list[dict], balance: float, max_dd: float, h1: pd.DataFrame) -> dict:
    n = len(trades)
    years = max(0.5, (h1["dt"].iloc[-1] - h1["dt"].iloc[0]).days / 365.25)
    if n == 0:
        return {"symbol": symbol, "trades": 0, "years": round(years, 1)}
    rs = np.array([t["r"] for t in trades])
    wins = int((rs > 0).sum())
    wr = wins / n
    ci_lo, ci_hi = wilson_ci(wins, n)
    gross_win = rs[rs > 0].sum()
    gross_loss = abs(rs[rs <= 0].sum())
    pf = float(gross_win / gross_loss) if gross_loss > 0 else float("inf")
    per_year = {}
    for t in trades:
        y = per_year.setdefault(t["year"], {"n": 0, "r": 0.0})
        y["n"] += 1
        y["r"] += t["r"]
    exit_reasons = {}
    for t in trades:
        exit_reasons[t["reason"]] = exit_reasons.get(t["reason"], 0) + 1
    return {
        "symbol": symbol,
        "years": round(years, 1),
        "trades": n,
        "trades_per_year": round(n / years, 1),
        "win_rate": round(wr, 4),
        "win_rate_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
        "avg_r": round(float(rs.mean()), 4),
        "expectancy_note": "avg_r is expectancy in R per trade after costs",
        "profit_factor": round(pf, 3),
        "max_dd_pct": round(max_dd * 100, 2),
        "end_balance": round(balance, 2),
        "total_r": round(float(rs.sum()), 1),
        "exit_reasons": exit_reasons,
        "per_year": {str(k): {"n": v["n"], "r": round(v["r"], 1)} for k, v in sorted(per_year.items())},
        "trades_list": trades,
    }


def print_report(results: list[dict], cfg: SwingConfig):
    breakeven_wr = 1.0 / (1.0 + cfg.target_rr)
    print(f"\n{'='*72}")
    print(f"SWING RULES v1 BACKTEST — target {cfg.target_rr}R, risk {RISK_PCT*100:.2f}%/trade, "
          f"costs included")
    print(f"Breakeven win rate at {cfg.target_rr}R (ex-costs): {breakeven_wr:.1%}")
    print(f"{'='*72}")
    all_rs = []
    for r in results:
        if r["trades"] == 0:
            print(f"\n{r['symbol']}: NO TRADES in {r['years']}y")
            continue
        all_rs.extend(t["r"] for t in r["trades_list"])
        lo, hi = r["win_rate_ci95"]
        print(f"\n{r['symbol']} — {r['years']}y, {r['trades']} trades ({r['trades_per_year']}/yr)")
        print(f"  WR {r['win_rate']:.1%}  (95% CI {lo:.1%}–{hi:.1%})   "
              f"avg {r['avg_r']:+.3f}R   PF {r['profit_factor']}   total {r['total_r']:+.1f}R")
        print(f"  $10k → ${r['end_balance']:,.0f}   maxDD {r['max_dd_pct']}%   exits {r['exit_reasons']}")
        yr_line = "  " + "  ".join(f"{y}:{v['r']:+.0f}R" for y, v in r["per_year"].items())
        print(yr_line)
    if all_rs:
        rs = np.array(all_rs)
        n = len(rs)
        wins = int((rs > 0).sum())
        lo, hi = wilson_ci(wins, n)
        print(f"\nPOOLED — {n} trades: WR {wins/n:.1%} (CI {lo:.1%}–{hi:.1%}), "
              f"avg {rs.mean():+.3f}R, total {rs.sum():+.1f}R")
        verdict = "EDGE (CI floor clears breakeven)" if lo > breakeven_wr else \
                  "NOT PROVEN (CI floor does not clear breakeven WR)"
        print(f"VERDICT vs {breakeven_wr:.1%} breakeven: {verdict}")
    print(f"{'='*72}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="US30,XAUUSD")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--target-rr", type=float, default=None)
    ap.add_argument("--ob-only", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--d1-slope-bars", type=int, default=None)
    ap.add_argument("--cooldown", type=int, default=None)
    ap.add_argument("--displacement", type=float, default=None)
    ap.add_argument("--out-name", default="swing_v1_backtest.json")
    args = ap.parse_args()

    cfg = SwingConfig()
    if args.target_rr:
        cfg.target_rr = args.target_rr
    if args.ob_only is not None:
        cfg.ob_only = args.ob_only
    if args.d1_slope_bars is not None:
        cfg.d1_slope_bars = args.d1_slope_bars
    if args.cooldown is not None:
        cfg.cooldown_h1_bars = args.cooldown
    if args.displacement is not None:
        cfg.displacement_atr_mult = args.displacement

    results = []
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        print(f"Running {sym}…")
        results.append(run_symbol(sym, cfg, args.start, args.end))

    print_report(results, cfg)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, args.out_name)
    slim = [{k: v for k, v in r.items() if k != "trades_list"} for r in results]
    with open(out, "w") as f:
        json.dump({"config": cfg.__dict__, "risk_pct": RISK_PCT,
                   "run_at": datetime.now(timezone.utc).isoformat(),
                   "results": slim}, f, indent=2)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
