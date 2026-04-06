"""
forward_test_potential.py — Dollar P&L forward test for Potential Agent.

$10,000 starting balance, MT5 US30, 0.1 lot max cap, risk-based sizing.
Full trade log, equity curve, daily breakdown.

MT5 US30 specs:
  - 1 standard lot = $1/point (100k contract)
  - 0.1 lot = $0.10/point
  - Spread: ~3 points ($0.30 per 0.1 lot round-trip)
  - Commission: ~$0 (built into spread on most MT5 brokers)
  - Slippage: ~1 point

Usage: cd backend && python -m scripts.forward_test_potential --symbol US30
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

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)

# ── MT5 US30 execution costs ──────────────────────────────────────────────
MT5_SPREAD_POINTS = 3.0      # typical MT5 US30 spread
MT5_SLIPPAGE_POINTS = 1.0    # slippage per fill
MT5_COMMISSION = 0.0          # built into spread on most brokers
POINT_VALUE_PER_LOT = 1.0    # US30: $1 per point per 1.0 lot


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


def run_forward_test(symbol="US30", starting_balance=10000.0, max_lot=0.10,
                     risk_pct=0.01, oos_start="2024-09-01"):
    # ── Load data ─────────────────────────────────────────────────────────
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

    oos_ts = int(pd.Timestamp(oos_start, tz="UTC").timestamp())
    oos_idx = int(np.argmax(timestamps >= oos_ts)) if (timestamps >= oos_ts).any() else len(timestamps)

    print(f"\n{'='*70}")
    print(f"  POTENTIAL AGENT — DOLLAR P&L FORWARD TEST")
    print(f"  Symbol: {symbol}  |  Period: {oos_start} → present")
    print(f"  Starting Balance: ${starting_balance:,.0f}  |  Max Lot: {max_lot}")
    print(f"  Risk/Trade: {risk_pct*100:.1f}%  |  Broker: MT5")
    print(f"  MT5 Costs: Spread={MT5_SPREAD_POINTS}pts  Slip={MT5_SLIPPAGE_POINTS}pts")
    print(f"  OOS Bars: {len(timestamps) - oos_idx:,}")
    print(f"{'='*70}")

    # ── Compute features ──────────────────────────────────────────────────
    print("  Computing features...", flush=True)
    feat_names, X = compute_potential_features(m5, h1, h4, d1, symbol=symbol)

    # Pad LSTM feature if model expects it
    model_path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_xgboost.joblib")
    if os.path.exists(model_path):
        model_feats = joblib.load(model_path).get("feature_names", [])
        for extra in model_feats[len(feat_names):]:
            feat_names.append(extra)
            X = np.column_stack([X, np.zeros(len(X), dtype=np.float32)])

    atr_vals = atr_fn(highs, lows, closes, 14)
    print(f"  Features: {len(feat_names)}")

    # ── Load models ───────────────────────────────────────────────────────
    models = {}
    for mtype in ["xgboost", "lightgbm"]:
        path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_{mtype}.joblib")
        if os.path.exists(path):
            data = joblib.load(path)
            models[mtype] = data["model"]
            print(f"  Loaded: {mtype} (Grade={data.get('grade', '?')})")

    if not models:
        print("  ERROR: No models found!")
        return

    # ── Generate predictions ──────────────────────────────────────────────
    # Use best model (XGBoost preferred for lower DD)
    model_name = "xgboost" if "xgboost" in models else list(models.keys())[0]
    model = models[model_name]
    print(f"  Using: {model_name}")

    preds = model.predict(X)

    # ATR gate: suppress signals in low-vol
    atr_ser = pd.Series(atr_vals.astype(np.float64))
    atr_pctile = atr_ser.rolling(100, min_periods=50).rank(pct=True).values
    low_vol = atr_pctile < 0.25
    preds = np.where(low_vol & (preds != 1), np.int64(1), preds)

    # ── Dollar P&L simulation ─────────────────────────────────────────────
    print(f"\n  Running dollar P&L simulation...\n")

    tp_mult = 1.2
    sl_mult = 0.8
    hold_bars = 10
    total_cost_points = MT5_SPREAD_POINTS + MT5_SLIPPAGE_POINTS  # per trade

    balance = starting_balance
    peak_balance = balance
    trades = []
    daily_pnl = {}  # date -> pnl
    equity_history = []  # (timestamp, balance)

    i = oos_idx
    while i < len(closes) - hold_bars - 1:
        sig = preds[i]
        if sig not in (0, 2):
            i += 1
            continue

        # Entry at next bar open
        entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]
        if entry <= 0 or np.isnan(atr_vals[i]) or atr_vals[i] <= 0:
            i += 1
            continue

        atr_val = atr_vals[i]
        is_long = (sig == 2)

        tp_dist = atr_val * tp_mult
        sl_dist = atr_val * sl_mult
        tp_price = entry + tp_dist if is_long else entry - tp_dist
        sl_price = entry - sl_dist if is_long else entry + sl_dist

        # Position sizing: risk-based, capped at max_lot
        risk_amount = balance * risk_pct  # e.g., $100 at 1%
        # lot_size = risk_amount / (sl_distance_points * point_value_per_lot)
        lot_size = risk_amount / (sl_dist * POINT_VALUE_PER_LOT)
        lot_size = min(lot_size, max_lot)
        lot_size = max(lot_size, 0.01)  # min lot
        lot_size = round(lot_size * 100) / 100  # round to 0.01

        # Scan forward for TP/SL
        exit_price = None
        exit_bar = None
        exit_type = "timeout"

        for j in range(i + 1, min(i + hold_bars + 1, len(closes))):
            hi, lo = highs[j], lows[j]
            if is_long:
                sl_hit = lo <= sl_price
                tp_hit = hi >= tp_price
            else:
                sl_hit = hi >= sl_price
                tp_hit = lo <= tp_price

            if sl_hit and tp_hit:
                exit_price = sl_price  # pessimistic
                exit_bar = j
                exit_type = "SL"
                break
            elif sl_hit:
                exit_price = sl_price
                exit_bar = j
                exit_type = "SL"
                break
            elif tp_hit:
                exit_price = tp_price
                exit_bar = j
                exit_type = "TP"
                break

        if exit_price is None:
            exit_bar = min(i + hold_bars, len(closes) - 1)
            exit_price = closes[exit_bar]
            exit_type = "timeout"

        # Calculate dollar P&L
        if is_long:
            raw_points = exit_price - entry
        else:
            raw_points = entry - exit_price

        net_points = raw_points - total_cost_points
        dollar_pnl = net_points * lot_size * POINT_VALUE_PER_LOT

        balance += dollar_pnl
        if balance > peak_balance:
            peak_balance = balance

        # Track trade
        entry_time = pd.to_datetime(timestamps[i + 1], unit="s", utc=True)
        exit_time = pd.to_datetime(timestamps[exit_bar], unit="s", utc=True)
        date_str = entry_time.strftime("%Y-%m-%d")

        trade = {
            "trade_num": len(trades) + 1,
            "date": date_str,
            "entry_time": entry_time.strftime("%Y-%m-%d %H:%M"),
            "exit_time": exit_time.strftime("%Y-%m-%d %H:%M"),
            "direction": "BUY" if is_long else "SELL",
            "entry": round(entry, 1),
            "exit": round(exit_price, 1),
            "sl": round(sl_price, 1),
            "tp": round(tp_price, 1),
            "lot_size": lot_size,
            "points": round(raw_points, 1),
            "net_points": round(net_points, 1),
            "cost_points": total_cost_points,
            "dollar_pnl": round(dollar_pnl, 2),
            "balance": round(balance, 2),
            "exit_type": exit_type,
            "atr": round(atr_val, 1),
            "bars_held": exit_bar - i,
        }
        trades.append(trade)

        # Daily tracking
        if date_str not in daily_pnl:
            daily_pnl[date_str] = 0.0
        daily_pnl[date_str] += dollar_pnl

        equity_history.append((timestamps[exit_bar], balance))

        # Cooldown
        i = i + hold_bars

    # ── Results ───────────────────────────────────────────────────────────
    n_trades = len(trades)
    if n_trades == 0:
        print("  No trades executed!")
        return

    pnls = [t["dollar_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    tp_trades = sum(1 for t in trades if t["exit_type"] == "TP")
    sl_trades = sum(1 for t in trades if t["exit_type"] == "SL")
    to_trades = sum(1 for t in trades if t["exit_type"] == "timeout")

    total_pnl = sum(pnls)
    win_rate = len(wins) / n_trades * 100
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf")
    max_dd_dollar = 0
    peak = starting_balance
    for t in trades:
        if t["balance"] > peak:
            peak = t["balance"]
        dd = peak - t["balance"]
        if dd > max_dd_dollar:
            max_dd_dollar = dd
    max_dd_pct = max_dd_dollar / starting_balance * 100

    # Daily Sharpe
    daily_rets = list(daily_pnl.values())
    daily_mean = np.mean(daily_rets)
    daily_std = np.std(daily_rets) if len(daily_rets) > 1 else 1
    sharpe = (daily_mean / daily_std * np.sqrt(252)) if daily_std > 0 else 0

    # Monthly breakdown
    monthly = {}
    for t in trades:
        month = t["date"][:7]
        if month not in monthly:
            monthly[month] = {"pnl": 0, "trades": 0, "wins": 0}
        monthly[month]["pnl"] += t["dollar_pnl"]
        monthly[month]["trades"] += 1
        if t["dollar_pnl"] > 0:
            monthly[month]["wins"] += 1

    print(f"  {'='*60}")
    print(f"  RESULTS — {model_name.upper()} | {n_trades} trades")
    print(f"  {'='*60}")
    print(f"  Starting Balance:  ${starting_balance:>10,.2f}")
    print(f"  Final Balance:     ${balance:>10,.2f}")
    print(f"  Total P&L:         ${total_pnl:>10,.2f}  ({total_pnl/starting_balance*100:+.1f}%)")
    print(f"  Max Drawdown:      ${max_dd_dollar:>10,.2f}  ({max_dd_pct:.1f}%)")
    print(f"  Sharpe Ratio:      {sharpe:>10.3f}")
    print(f"  Win Rate:          {win_rate:>9.1f}%  ({len(wins)}/{n_trades})")
    print(f"  Avg Win:           ${avg_win:>10,.2f}")
    print(f"  Avg Loss:          ${avg_loss:>10,.2f}")
    print(f"  Profit Factor:     {profit_factor:>10.2f}")
    print(f"  TP/SL/Timeout:     {tp_trades}/{sl_trades}/{to_trades}")
    print(f"  Avg Lot Size:      {np.mean([t['lot_size'] for t in trades]):.3f}")
    print(f"  Max Lot Used:      {max(t['lot_size'] for t in trades):.2f}")

    print(f"\n  {'─'*60}")
    print(f"  MONTHLY BREAKDOWN")
    print(f"  {'─'*60}")
    print(f"  {'Month':<10} {'P&L':>10} {'Trades':>7} {'WR':>7} {'Cum P&L':>10}")
    cum = 0
    for month in sorted(monthly.keys()):
        m = monthly[month]
        cum += m["pnl"]
        wr = m["wins"] / m["trades"] * 100 if m["trades"] > 0 else 0
        print(f"  {month:<10} ${m['pnl']:>9,.2f} {m['trades']:>7} {wr:>6.1f}% ${cum:>9,.2f}")

    print(f"\n  {'─'*60}")
    print(f"  DAILY P&L SUMMARY")
    print(f"  {'─'*60}")
    daily_vals = list(daily_pnl.values())
    pos_days = sum(1 for d in daily_vals if d > 0)
    neg_days = sum(1 for d in daily_vals if d < 0)
    flat_days = sum(1 for d in daily_vals if d == 0)
    print(f"  Trading Days:      {len(daily_vals)}")
    print(f"  Positive Days:     {pos_days}  ({pos_days/len(daily_vals)*100:.0f}%)")
    print(f"  Negative Days:     {neg_days}  ({neg_days/len(daily_vals)*100:.0f}%)")
    print(f"  Flat Days:         {flat_days}")
    print(f"  Best Day:          ${max(daily_vals):>9,.2f}")
    print(f"  Worst Day:         ${min(daily_vals):>9,.2f}")
    print(f"  Avg Day:           ${np.mean(daily_vals):>9,.2f}")

    # Print last 10 trades
    print(f"\n  {'─'*60}")
    print(f"  LAST 10 TRADES")
    print(f"  {'─'*60}")
    print(f"  {'#':>4} {'Date':>12} {'Dir':>5} {'Entry':>8} {'Exit':>8} {'Lot':>5} {'Pts':>6} {'P&L':>9} {'Type':>4} {'Bal':>10}")
    for t in trades[-10:]:
        print(f"  {t['trade_num']:>4} {t['date']:>12} {t['direction']:>5} {t['entry']:>8.1f} "
              f"{t['exit']:>8.1f} {t['lot_size']:>5.2f} {t['net_points']:>6.1f} "
              f"${t['dollar_pnl']:>8.2f} {t['exit_type']:>4} ${t['balance']:>9.2f}")

    return trades, daily_pnl, monthly


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="US30")
    parser.add_argument("--balance", type=float, default=10000.0)
    parser.add_argument("--max-lot", type=float, default=0.10)
    parser.add_argument("--risk-pct", type=float, default=0.01)
    args = parser.parse_args()
    run_forward_test(args.symbol, args.balance, args.max_lot, args.risk_pct)
