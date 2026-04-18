"""Backtest API endpoints — with transaction cost configuration."""
import os, traceback
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
from dataclasses import asdict
import numpy as np
import pandas as pd
import joblib
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db, SessionLocal
from app.models.user import User
from app.models.backtest import BacktestResult
from app.services.backtest.engine import BacktestEngine

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "History Data", "data")
)

_results: dict[str, dict] = {}
_status: dict = {"active": False, "symbol": None, "progress": ""}

# Potential Agent backtest state (separate from legacy)
_potential_results: dict[str, dict] = {}
_potential_status: dict = {"active": False, "symbol": None, "progress": ""}


class BacktestRequest(BaseModel):
    symbol: str
    agent_type: str = "scalping"
    risk_per_trade: float = 0.005
    spread_pips: Optional[float] = None
    slippage_pips: Optional[float] = None
    commission_per_lot: float = 0.0
    prime_hours_only: bool = True
    include_monte_carlo: bool = True


@router.post("/run")
def run_backtest(
    body: BacktestRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    if _status["active"]:
        return {"status": "busy", "message": f"Backtest running for {_status['symbol']}"}

    _status.update({"active": True, "symbol": body.symbol, "progress": "fetching dukascopy data"})

    def _run():
        bundle_run_id = None
        try:
            # Backtest data is ALWAYS fetched fresh from Dukascopy — never reads
            # the persistent History Data files that training uses. Tempdir is
            # cleaned up after load; DataFrames live only in-memory for ~10 min
            # via the fetcher's cache.
            from app.services.backtest.data_fetcher import get_backtest_fetcher
            fetcher = get_backtest_fetcher()
            try:
                bundle = fetcher.fetch(body.symbol, days=2500, timeframes=["M5", "H1"])
                bundle_run_id = bundle.run_id
            except Exception as e:
                _results[body.symbol] = {"error": f"Dukascopy fetch failed: {e}"}
                return

            if bundle.m5 is None or len(bundle.m5) == 0:
                _results[body.symbol] = {"error": "Dukascopy returned no M5 data"}
                return

            _status["progress"] = "loaded data"
            m5 = bundle.m5
            h1 = bundle.h1

            _status["progress"] = "running simulation"
            engine = BacktestEngine()
            result = engine.run(
                symbol=body.symbol,
                agent_type=body.agent_type,
                risk_config={"risk_per_trade": body.risk_per_trade, "max_daily_loss_pct": 0.04, "cooldown_bars": 3},
                m5_data=m5,
                h1_data=h1,
                spread_pips=body.spread_pips,
                slippage_pips=body.slippage_pips,
                commission_per_lot=body.commission_per_lot,
                prime_hours_only=body.prime_hours_only,
                include_monte_carlo=body.include_monte_carlo,
            )

            # Serialize trade list (limit to last 200 for API response size)
            trade_list = [
                {
                    "direction": t.direction, "entry_price": round(t.entry_price, 5),
                    "exit_price": round(t.exit_price, 5), "lot_size": t.lot_size,
                    "gross_pnl": round(t.gross_pnl, 2), "pnl": round(t.pnl, 2),
                    "spread_cost": round(t.spread_cost, 2), "commission": round(t.commission, 2),
                    "exit_reason": t.exit_reason, "confidence": round(t.confidence, 3),
                    "duration_bars": t.duration_bars, "entry_time": t.entry_time, "exit_time": t.exit_time,
                }
                for t in result.trades[-200:]
            ]

            _results[body.symbol] = {
                "gross_pnl": result.gross_pnl,
                "net_pnl": result.net_pnl,
                "total_costs": result.total_costs,
                "total_spread_cost": result.total_spread_cost,
                "total_slippage_cost": result.total_slippage_cost,
                "total_commission": result.total_commission,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "max_drawdown": result.max_drawdown,
                "sharpe_ratio": result.sharpe_ratio,
                "expectancy": result.expectancy,
                "risk_reward_ratio": result.risk_reward_ratio,
                "calmar_ratio": result.calmar_ratio,
                "avg_win": result.avg_win,
                "avg_loss": result.avg_loss,
                "avg_trade_duration_bars": result.avg_trade_duration_bars,
                "max_consecutive_wins": result.max_consecutive_wins,
                "max_consecutive_losses": result.max_consecutive_losses,
                "total_trades": result.total_trades,
                "winning_trades": result.winning_trades,
                "losing_trades": result.losing_trades,
                "equity_curve": result.equity_curve[-200:],
                "drawdown_curve": result.drawdown_curve[-200:],
                "monthly_returns": result.monthly_returns,
                "trades": trade_list,
                "monte_carlo": asdict(result.monte_carlo) if result.monte_carlo else None,
            }
        except Exception as e:
            _results[body.symbol] = {"error": str(e)}
        finally:
            _status["active"] = False
            _status["progress"] = "done"
            # Best-effort cleanup of the tempdir (fetch() already removes it
            # after load but we call cleanup in case of crash mid-fetch)
            if bundle_run_id:
                try:
                    from app.services.backtest.data_fetcher import get_backtest_fetcher
                    get_backtest_fetcher().cleanup(bundle_run_id)
                except Exception:
                    pass

    background_tasks.add_task(_run)
    return {"status": "started", "symbol": body.symbol}


@router.get("/results")
def list_results(current_user: User = Depends(get_current_user)):
    return {"results": _results, "running": _status}


@router.get("/results/{symbol}")
def get_result(symbol: str, current_user: User = Depends(get_current_user)):
    if symbol not in _results:
        raise HTTPException(status_code=404, detail="No backtest result for this symbol")
    return _results[symbol]


# ── Potential Agent Backtest ──────────────────────────────────────────────

class PotentialBacktestRequest(BaseModel):
    symbol: str = "US30"
    start_date: Optional[str] = None   # ISO date e.g. "2024-01-01"
    end_date: Optional[str] = None     # ISO date e.g. "2025-01-01"
    balance: float = 10000.0
    max_lot: float = 0.10
    risk_pct: float = 0.01
    # "dukascopy" (DEFAULT as of 2026-04-15): fetch fresh from Dukascopy per run
    # "broker": live Oanda data (up to 5000 bars)
    # "history": legacy — reads persistent CSV files from History Data/data/
    data_source: str = "dukascopy"


# Execution cost defaults per symbol (Oanda paper)
_EXEC_COSTS = {
    "US30":   {"spread_pts": 3.0, "slippage_pts": 1.0, "point_value": 1.0},
    "BTCUSD": {"spread_pts": 50.0, "slippage_pts": 10.0, "point_value": 1.0},
    "XAUUSD": {"spread_pts": 0.30, "slippage_pts": 0.10, "point_value": 100.0},
    "ES":     {"spread_pts": 0.50, "slippage_pts": 0.25, "point_value": 50.0},
    "NAS100": {"spread_pts": 1.0, "slippage_pts": 0.50, "point_value": 20.0},
}


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "ts_event" in df.columns and "time" not in df.columns:
        df["time"] = pd.to_datetime(df["ts_event"]).values.astype("datetime64[s]").astype(np.int64)
        df = df.drop(columns=["ts_event"])
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("time").reset_index(drop=True)


def _load_tf(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    for path in [
        os.path.join(HIST_DATA_DIR, symbol, f"{symbol}_{tf}.csv"),
        os.path.join(DATA_DIR, f"{symbol}_{tf}.csv"),
    ]:
        if os.path.exists(path):
            return _normalize_ohlcv(pd.read_csv(path))
    return None


def _run_potential_backtest(body: PotentialBacktestRequest, result_id: int = None):
    """Background worker for Potential Agent backtest."""
    try:
        from app.services.ml.features_potential import compute_potential_features
        from app.services.ml.symbol_config import get_symbol_config
        from app.services.backtest.indicators import atr as atr_fn

        symbol = body.symbol
        _potential_status["progress"] = "loading data"

        # Data source: broker (live from Oanda) or history (CSV files)
        if body.data_source == "broker":
            _potential_status["progress"] = "fetching from broker (up to 5000 bars)"
            try:
                import asyncio
                from app.services.broker.manager import get_broker_manager
                manager = get_broker_manager()
                # Find the user's active adapter
                adapter = None
                for key, adp in manager._adapters.items():
                    adapter = adp
                    break
                if adapter is None:
                    err = "No broker connected. Connect Oanda in Settings first."
                    _potential_results[symbol] = {"error": err}
                    if result_id:
                        _update_backtest_record(result_id, status="error", error_message=err)
                    return

                # Fetch M5 candles (max 5000 from Oanda)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                candles_m5 = loop.run_until_complete(adapter.get_candles(symbol, "M5", 5000))
                candles_h1 = loop.run_until_complete(adapter.get_candles(symbol, "H1", 500))
                candles_h4 = loop.run_until_complete(adapter.get_candles(symbol, "H4", 200))
                candles_d1 = loop.run_until_complete(adapter.get_candles(symbol, "D1", 100))
                loop.close()

                from dataclasses import asdict
                m5 = pd.DataFrame([asdict(c) for c in candles_m5])
                h1 = pd.DataFrame([asdict(c) for c in candles_h1]) if candles_h1 else None
                h4 = pd.DataFrame([asdict(c) for c in candles_h4]) if candles_h4 else None
                d1 = pd.DataFrame([asdict(c) for c in candles_d1]) if candles_d1 else None

                if m5.empty or len(m5) < 300:
                    err = f"Broker returned only {len(m5)} M5 bars (need 300+)"
                    _potential_results[symbol] = {"error": err}
                    if result_id:
                        _update_backtest_record(result_id, status="error", error_message=err)
                    return

            except Exception as e:
                err = f"Broker data fetch failed: {e}"
                _potential_results[symbol] = {"error": err}
                if result_id:
                    _update_backtest_record(result_id, status="error", error_message=err)
                return
        elif body.data_source == "dukascopy":
            # Fresh Dukascopy fetch into a tempdir, loaded into memory, tempdir deleted.
            _potential_status["progress"] = "fetching fresh Dukascopy data"
            try:
                from app.services.backtest.data_fetcher import get_backtest_fetcher
                bundle = get_backtest_fetcher().fetch(symbol, days=2500)
                m5 = _normalize_ohlcv(bundle.m5) if bundle.m5 is not None else None
                h1 = _normalize_ohlcv(bundle.h1) if bundle.h1 is not None else None
                h4 = _normalize_ohlcv(bundle.h4) if bundle.h4 is not None else None
                d1 = _normalize_ohlcv(bundle.d1) if bundle.d1 is not None else None
            except Exception as e:
                err = f"Dukascopy fetch failed: {e}"
                _potential_results[symbol] = {"error": err}
                if result_id:
                    _update_backtest_record(result_id, status="error", error_message=err)
                return

            if m5 is None or len(m5) == 0:
                err = f"Dukascopy returned no M5 data for {symbol}"
                _potential_results[symbol] = {"error": err}
                if result_id:
                    _update_backtest_record(result_id, status="error", error_message=err)
                return

            # Cap for memory
            cap = 500_000
            if len(m5) > cap:
                m5 = m5.iloc[-cap:].reset_index(drop=True)
                start_ts = m5["time"].iloc[0]
                if h1 is not None: h1 = h1[h1["time"] >= start_ts].reset_index(drop=True)
                if h4 is not None: h4 = h4[h4["time"] >= start_ts].reset_index(drop=True)
                if d1 is not None: d1 = d1[d1["time"] >= start_ts].reset_index(drop=True)
        else:
            # Legacy history data from CSV files (data_source == "history")
            m5 = _load_tf(symbol, "M5")
            h1 = _load_tf(symbol, "H1")
            h4 = _load_tf(symbol, "H4")
            d1 = _load_tf(symbol, "D1")
            if m5 is None:
                err = f"No M5 data for {symbol}"
                _potential_results[symbol] = {"error": err}
                if result_id:
                    _update_backtest_record(result_id, status="error", error_message=err)
                return

            # Cap rows for memory
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

        # Determine OOS range from dates
        if body.start_date:
            oos_ts = int(pd.Timestamp(body.start_date, tz="UTC").timestamp())
        else:
            # Default: last 6 months
            oos_ts = int((pd.Timestamp.utcnow() - pd.Timedelta(days=180)).timestamp())
        oos_idx = int(np.argmax(timestamps >= oos_ts)) if (timestamps >= oos_ts).any() else 0

        if body.end_date:
            end_ts = int(pd.Timestamp(body.end_date, tz="UTC").timestamp())
            end_idx = int(np.argmax(timestamps > end_ts)) if (timestamps > end_ts).any() else len(timestamps)
        else:
            end_idx = len(timestamps)

        # ── Compute features ──────────────────────────────────────────────
        _potential_status["progress"] = "computing features"
        feat_names, X = compute_potential_features(m5, h1, h4, d1, symbol=symbol)

        # Pad extra features if model expects them
        model_path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_xgboost.joblib")
        if os.path.exists(model_path):
            model_data = joblib.load(model_path)
            model_feats = model_data.get("feature_names", [])
            for extra in model_feats[len(feat_names):]:
                feat_names.append(extra)
                X = np.column_stack([X, np.zeros(len(X), dtype=np.float32)])

        atr_vals = atr_fn(highs, lows, closes, 14)

        # ── Load model ────────────────────────────────────────────────────
        _potential_status["progress"] = "loading model"
        models = {}
        for mtype in ["xgboost", "lightgbm"]:
            path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_{mtype}.joblib")
            if os.path.exists(path):
                data = joblib.load(path)
                models[mtype] = {"model": data["model"], "grade": data.get("grade", "?")}

        if not models:
            err = f"No trained model found for {symbol}"
            _potential_results[symbol] = {"error": err}
            if result_id:
                _update_backtest_record(result_id, status="error", error_message=err)
            return

        model_name = "xgboost" if "xgboost" in models else list(models.keys())[0]
        model = models[model_name]["model"]
        model_grade = models[model_name]["grade"]

        # ── Predictions ───────────────────────────────────────────────────
        _potential_status["progress"] = "generating predictions"
        preds = model.predict(X)

        # ATR gate: suppress signals in low-vol
        atr_ser = pd.Series(atr_vals.astype(np.float64))
        atr_pctile = atr_ser.rolling(100, min_periods=50).rank(pct=True).values
        low_vol = atr_pctile < 0.25
        preds = np.where(low_vol & (preds != 1), np.int64(1), preds)

        # ── Simulation ────────────────────────────────────────────────────
        _potential_status["progress"] = "running simulation"
        cfg = get_symbol_config(symbol)
        costs = _EXEC_COSTS.get(symbol, {"spread_pts": 2.0, "slippage_pts": 1.0, "point_value": 1.0})
        tp_mult = cfg.get("tp_atr_mult", 1.2)
        sl_mult = cfg.get("sl_atr_mult", 0.8)
        hold_bars = cfg.get("hold_bars", 10)
        total_cost_points = costs["spread_pts"] + costs["slippage_pts"]
        point_value = costs["point_value"]

        balance = body.balance
        starting_balance = body.balance
        peak_balance = balance
        trades = []
        daily_pnl = {}
        equity_history = [(timestamps[oos_idx] if oos_idx < len(timestamps) else 0, balance)]

        i = oos_idx
        while i < min(end_idx, len(closes) - hold_bars - 1):
            sig = preds[i]
            if sig not in (0, 2):
                i += 1
                continue

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

            risk_amount = balance * body.risk_pct
            lot_size = risk_amount / (sl_dist * point_value) if sl_dist * point_value > 0 else 0.01
            lot_size = min(lot_size, body.max_lot)
            lot_size = max(lot_size, 0.01)
            lot_size = round(lot_size * 100) / 100

            # Scan forward
            exit_price = None
            exit_bar = None
            exit_type = "timeout"
            scan_end = min(i + hold_bars + 1, end_idx, len(closes))

            for j in range(i + 1, scan_end):
                hi, lo = highs[j], lows[j]
                if is_long:
                    sl_hit = lo <= sl_price
                    tp_hit = hi >= tp_price
                else:
                    sl_hit = hi >= sl_price
                    tp_hit = lo <= tp_price

                if sl_hit and tp_hit:
                    exit_price = sl_price
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
                exit_bar = min(i + hold_bars, len(closes) - 1, end_idx - 1)
                exit_price = closes[exit_bar]
                exit_type = "timeout"

            raw_points = (exit_price - entry) if is_long else (entry - exit_price)
            net_points = raw_points - total_cost_points
            dollar_pnl = net_points * lot_size * point_value

            balance += dollar_pnl
            if balance > peak_balance:
                peak_balance = balance

            entry_time = int(timestamps[i + 1])
            exit_time_ts = int(timestamps[exit_bar])
            date_str = pd.to_datetime(entry_time, unit="s", utc=True).strftime("%Y-%m-%d")

            trade = {
                "trade_num": len(trades) + 1,
                "date": date_str,
                "entry_time": pd.to_datetime(entry_time, unit="s", utc=True).strftime("%Y-%m-%d %H:%M"),
                "exit_time": pd.to_datetime(exit_time_ts, unit="s", utc=True).strftime("%Y-%m-%d %H:%M"),
                "direction": "BUY" if is_long else "SELL",
                "entry": round(float(entry), 2),
                "exit": round(float(exit_price), 2),
                "lot_size": lot_size,
                "points": round(float(raw_points), 2),
                "net_points": round(float(net_points), 2),
                "dollar_pnl": round(float(dollar_pnl), 2),
                "balance": round(float(balance), 2),
                "exit_type": exit_type,
                "bars_held": exit_bar - i,
            }
            trades.append(trade)

            if date_str not in daily_pnl:
                daily_pnl[date_str] = 0.0
            daily_pnl[date_str] += dollar_pnl

            equity_history.append((exit_time_ts, round(float(balance), 2)))

            i = i + hold_bars

        # ── Compute summary stats ─────────────────────────────────────────
        _potential_status["progress"] = "computing results"
        n_trades = len(trades)
        if n_trades == 0:
            err = "No trades in selected period"
            _potential_results[symbol] = {"error": err, "total_trades": 0}
            if result_id:
                _update_backtest_record(result_id, status="error", error_message=err)
            return

        pnls = [t["dollar_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        win_rate = len(wins) / n_trades * 100
        avg_win = float(np.mean(wins)) if wins else 0
        avg_loss = float(np.mean(losses)) if losses else 0
        profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 999.99

        # Max drawdown
        max_dd_dollar = 0.0
        peak = starting_balance
        for t in trades:
            if t["balance"] > peak:
                peak = t["balance"]
            dd = peak - t["balance"]
            if dd > max_dd_dollar:
                max_dd_dollar = dd
        max_dd_pct = max_dd_dollar / starting_balance * 100

        # Sharpe from daily returns
        daily_rets = list(daily_pnl.values())
        daily_mean = float(np.mean(daily_rets))
        daily_std = float(np.std(daily_rets)) if len(daily_rets) > 1 else 1.0
        sharpe = (daily_mean / daily_std * np.sqrt(252)) if daily_std > 0 else 0

        # Monthly breakdown
        monthly = {}
        for t in trades:
            month = t["date"][:7]
            if month not in monthly:
                monthly[month] = {"month": month, "pnl": 0.0, "trades": 0, "wins": 0}
            monthly[month]["pnl"] += t["dollar_pnl"]
            monthly[month]["trades"] += 1
            if t["dollar_pnl"] > 0:
                monthly[month]["wins"] += 1

        monthly_list = []
        cum = 0.0
        for month_key in sorted(monthly.keys()):
            m = monthly[month_key]
            cum += m["pnl"]
            wr = m["wins"] / m["trades"] * 100 if m["trades"] > 0 else 0
            monthly_list.append({
                "month": month_key,
                "pnl": round(m["pnl"], 2),
                "trades": m["trades"],
                "win_rate": round(wr, 1),
                "cumulative_pnl": round(cum, 2),
            })

        # Equity curve points (subsample if too many)
        eq_points = equity_history
        if len(eq_points) > 300:
            step = len(eq_points) // 300
            eq_points = eq_points[::step] + [eq_points[-1]]

        result_data = {
            "symbol": symbol,
            "model": model_name,
            "grade": model_grade,
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / starting_balance * 100, 2),
            "final_balance": round(balance, 2),
            "starting_balance": starting_balance,
            "win_rate": round(win_rate, 1),
            "sharpe_ratio": round(float(sharpe), 2),
            "max_drawdown": round(max_dd_dollar, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "profit_factor": round(float(profit_factor), 2),
            "total_trades": n_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "monthly_breakdown": monthly_list,
            "equity_curve": [{"time": int(t), "value": v} for t, v in eq_points],
            "trades": [
                {
                    "direction": t["direction"],
                    "entry_time": t["entry_time"],
                    "exit_time": t["exit_time"],
                    "entry_price": t["entry"],
                    "exit_price": t["exit"],
                    "lot_size": t["lot_size"],
                    "pnl": t["dollar_pnl"],
                    "exit_reason": t["exit_type"],
                    "bars_held": t["bars_held"],
                }
                for t in trades[-50:]
            ],
        }
        _potential_results[symbol] = result_data

        # Persist to DB
        if result_id:
            _update_backtest_record(result_id, status="completed", results=result_data)

    except Exception as e:
        traceback.print_exc()
        error_msg = str(e)
        _potential_results[body.symbol] = {"error": error_msg}
        if result_id:
            _update_backtest_record(result_id, status="error", error_message=error_msg)
    finally:
        _potential_status["active"] = False
        _potential_status["progress"] = "done"


def _update_backtest_record(result_id: int, status: str, results: dict = None, error_message: str = None):
    """Update a BacktestResult record in a new DB session (safe for background tasks)."""
    db = SessionLocal()
    try:
        record = db.query(BacktestResult).filter(BacktestResult.id == result_id).first()
        if record:
            record.status = status
            record.completed_at = datetime.now(timezone.utc)
            if results is not None:
                record.results = results
            if error_message is not None:
                record.error_message = error_message[:500]
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@router.post("/potential")
def run_potential_backtest(
    body: PotentialBacktestRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if _potential_status["active"]:
        return {"status": "busy", "message": f"Potential backtest running for {_potential_status['symbol']}"}

    valid_symbols = ["US30", "BTCUSD", "XAUUSD", "ES", "NAS100"]
    if body.symbol not in valid_symbols:
        raise HTTPException(status_code=400, detail=f"Symbol must be one of: {valid_symbols}")

    # Create DB record before starting background task
    bt_record = BacktestResult(
        user_id=current_user.id,
        symbol=body.symbol,
        agent_type="potential",
        config={
            "start_date": body.start_date,
            "end_date": body.end_date,
            "balance": body.balance,
            "max_lot": body.max_lot,
            "risk_pct": body.risk_pct,
            "data_source": body.data_source,
        },
        status="running",
    )
    db.add(bt_record)
    db.commit()
    db.refresh(bt_record)
    result_id = bt_record.id

    _potential_status.update({"active": True, "symbol": body.symbol, "progress": "starting"})

    background_tasks.add_task(_run_potential_backtest, body, result_id)
    return {"status": "started", "symbol": body.symbol, "result_id": result_id}


@router.get("/potential/status")
def potential_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Check DB for the latest running backtest for this user
    latest = db.query(BacktestResult).filter(
        BacktestResult.user_id == current_user.id,
        BacktestResult.agent_type == "potential",
    ).order_by(BacktestResult.created_at.desc()).first()

    db_info = None
    if latest:
        db_info = {
            "id": latest.id,
            "symbol": latest.symbol,
            "status": latest.status,
            "config": latest.config,
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
            "completed_at": latest.completed_at.isoformat() if latest.completed_at else None,
            "results": latest.results if latest.status == "completed" else None,
            "error_message": latest.error_message,
        }

    return {"running": _potential_status, "results": _potential_results, "latest_db": db_info}


@router.get("/potential/results/{symbol}")
def potential_result(
    symbol: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # First check in-memory cache
    if symbol in _potential_results:
        return _potential_results[symbol]

    # Fall back to DB for latest completed backtest for this user + symbol
    latest = db.query(BacktestResult).filter(
        BacktestResult.user_id == current_user.id,
        BacktestResult.symbol == symbol,
        BacktestResult.agent_type == "potential",
        BacktestResult.status == "completed",
    ).order_by(BacktestResult.created_at.desc()).first()

    if latest and latest.results:
        return latest.results

    raise HTTPException(status_code=404, detail="No potential backtest result for this symbol")
