"""Backtest API endpoints — with transaction cost configuration."""
import logging
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
from dataclasses import asdict
import numpy as np
import pandas as pd
import joblib
from sqlalchemy.orm import Session

logger = logging.getLogger("flowrex.backtest")

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

    _status.update({"active": True, "symbol": body.symbol, "progress": "updating Dukascopy data..."})

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
    # Per-run cost overrides. When omitted, backend falls back to the
    # symbol's entry in _EXEC_COSTS. User can override via the backtest UI
    # to simulate a different broker's fee structure (e.g. FundedNext Bolt
    # Tradovate commissions vs Oanda practice). All three optional.
    spread_pts_override: Optional[float] = None
    slippage_pts_override: Optional[float] = None
    commission_per_lot_override: Optional[float] = None
    # "dukascopy" (DEFAULT as of 2026-04-15): fetch fresh from Dukascopy per run
    # "broker": live broker data — uses the specified broker (or user's first
    #           connected broker) up to that broker's per-request cap
    # "history": legacy — reads persistent CSV files from History Data/data/
    data_source: str = "dukascopy"
    # Optional broker name for data_source=broker (otherwise user's first
    # connected broker is used). Agents already track their own broker; the
    # backtest UI can pass this when the user has multiple connections.
    broker: Optional[str] = None
    # Agent behaviour. "potential" = immediate entry on signal (original).
    # "scout" = lookback + pullback/BOS entry state machine (mirrors
    # ScoutAgent runtime). Scout reuses the same deployed potential_* models;
    # only the entry gating differs.
    agent_type: str = "potential"
    lookback_bars: int = 40
    instant_entry_confidence: float = 0.85
    max_pending_bars: int = 10
    pullback_atr_fraction: float = 0.50
    dedupe_window_bars: int = 20
    # Filter sandbox. Lets users A/B test filter settings against a symbol
    # without touching the live agent's config. Defaults here mean "run as
    # if the agent had all filters off" — the old behaviour. If the user
    # provides non-default values, the simulation gates bars the same way
    # PotentialAgent / FlowrexAgent v2 would at runtime.
    session_filter: bool = False
    allowed_sessions: Optional[list[str]] = None
    regime_filter: bool = False
    allowed_regimes: Optional[list[str]] = None
    use_correlations: bool = True


def _scout_check_triggers(
    pending: dict,
    i: int,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    lookback_bars: int,
    pullback_atr_fraction: float,
    instant_entry_confidence: float,
) -> Optional[str]:
    """Return the first Scout trigger that fires at bar i, or None.

    Mirrors `ScoutAgent._check_triggers` so backtest behaviour matches live.
    Order matters: instant > pullback > break-of-structure.
    """
    conf = float(pending["confidence"])
    if conf >= instant_entry_confidence:
        return "instant_confidence"

    direction = int(pending["direction"])
    ref_close = float(pending["ref_close"])
    ref_atr = float(pending["ref_atr"]) or 1.0
    bars_waited = int(pending["bars_waited"])
    pullback_distance = ref_atr * pullback_atr_fraction

    # Pullback: price moved against pending direction, current bar reverses.
    start_idx = max(0, i - bars_waited)
    if direction > 0:
        lowest_since = float(np.min(lows[start_idx:i + 1]))
        if (ref_close - lowest_since) >= pullback_distance and float(closes[i]) > float(closes[i - 1]):
            return "pullback"
    else:
        highest_since = float(np.max(highs[start_idx:i + 1]))
        if (highest_since - ref_close) >= pullback_distance and float(closes[i]) < float(closes[i - 1]):
            return "pullback"

    # Break-of-structure: current bar extends past lookback extreme.
    win_start = max(0, i - lookback_bars)
    win_end = i
    if win_end > win_start:
        if direction > 0:
            prior_high = float(np.max(highs[win_start:win_end]))
            if float(highs[i]) > prior_high:
                return "break_of_structure"
        else:
            prior_low = float(np.min(lows[win_start:win_end]))
            if float(lows[i]) < prior_low:
                return "break_of_structure"

    return None


# Per-broker hard ceilings on bars returned by `get_candles` in a single call.
# These are what each broker's API actually allows — pulling above these is
# silently truncated by the broker, so surfacing a real limit here lets the
# date-range picker show honest bounds instead of hallucinating 2,500 days.
# (Minute-per-bar is computed from the timeframe.)
BROKER_MAX_CANDLES = {
    "oanda":               5_000,   # Oanda v20 /candles `count` max
    "ctrader":             5_000,   # cTrader REST trendbars — conservative
    "mt5":                50_000,   # MT5 copy_rates_from_pos — broker-dependent but generous
    "tradovate":           5_000,   # getChart practical cap
    "interactive_brokers": 1_000,   # IBKR Client Portal marketdata/history practical cap
}

_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _broker_cap(broker: str, tf: str = "M5") -> int:
    """Max bars of `tf` a broker can return in one REST call."""
    return BROKER_MAX_CANDLES.get(broker, 5_000)


# Execution cost defaults per symbol (Oanda paper)
_EXEC_COSTS = {
    "US30":   {"spread_pts": 3.0, "slippage_pts": 1.0, "point_value": 1.0},
    "BTCUSD": {"spread_pts": 50.0, "slippage_pts": 10.0, "point_value": 1.0},
    "XAUUSD": {"spread_pts": 0.30, "slippage_pts": 0.10, "point_value": 100.0},
    "ES":     {"spread_pts": 0.50, "slippage_pts": 0.25, "point_value": 50.0},
    "NAS100": {"spread_pts": 1.0, "slippage_pts": 0.50, "point_value": 20.0},
}


class RegimeValidateRequest(BaseModel):
    symbol: str
    days: int = 90
    forward_bars: int = 10


@router.post("/regime-validate")
def regime_validate(
    body: RegimeValidateRequest,
    current_user: User = Depends(get_current_user),
):
    """Classify every M5 bar of the last N days and show next-forward-bar
    return per regime bucket. Helps confirm the classifier earns its keep
    before flipping `regime_filter` on live.

    Uses Dukascopy (same source as /api/backtest/potential) so results are
    reproducible and not broker-dependent.
    """
    from app.services.backtest.data_fetcher import get_backtest_fetcher
    from app.services.ml.regime_detector import validate_regime_on_history

    days = max(7, min(int(body.days), 365))
    forward = max(1, min(int(body.forward_bars), 100))

    try:
        bundle = get_backtest_fetcher().fetch(
            body.symbol, days=days, timeframes=["M5"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dukascopy fetch failed: {e}")

    if bundle.m5 is None or len(bundle.m5) == 0:
        raise HTTPException(status_code=400, detail="No M5 data for symbol")

    m5 = _normalize_ohlcv(bundle.m5)
    # Clip to the requested window — the fetcher may return more bars from
    # cached History Data than we asked for.
    cutoff_ts = int((pd.Timestamp.utcnow() - pd.Timedelta(days=days)).timestamp())
    m5 = m5[m5["time"] >= cutoff_ts].reset_index(drop=True)
    if len(m5) < 500:
        raise HTTPException(
            status_code=400,
            detail=f"Only {len(m5)} M5 bars in the last {days} days — need 500+",
        )

    result = validate_regime_on_history(
        m5["high"].to_numpy(dtype=np.float64),
        m5["low"].to_numpy(dtype=np.float64),
        m5["close"].to_numpy(dtype=np.float64),
        forward_bars=forward,
    )
    result["symbol"] = body.symbol.upper()
    result["days"] = days
    return result


@router.get("/cost-defaults/{symbol}")
def get_cost_defaults(
    symbol: str,
    current_user: User = Depends(get_current_user),
):
    """Symbol's default spread / slippage / commission for the backtest UI.

    Used to pre-fill the cost inputs when the user selects a symbol, so
    they start from realistic values and can nudge up/down to simulate
    tighter-or-wider broker conditions without blind guessing.
    """
    fallback = {"spread_pts": 2.0, "slippage_pts": 1.0, "point_value": 1.0}
    costs = _EXEC_COSTS.get(symbol.upper(), fallback)
    return {
        "symbol": symbol.upper(),
        "spread_pts": costs["spread_pts"],
        "slippage_pts": costs["slippage_pts"],
        "commission_per_lot": 0.0,   # no symbol-default commission today
        "point_value": costs["point_value"],
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


def _run_potential_backtest(body: PotentialBacktestRequest, result_id: int = None,
                            user_id: int = None):
    """Background worker for Potential Agent backtest.

    `user_id` must be passed in by the route handler so broker-mode can scope
    adapter lookup to the logged-in user (see bug note on the broker branch).
    """
    try:
        from app.services.ml.features_potential import compute_potential_features
        from app.services.ml.symbol_config import get_symbol_config
        from app.services.backtest.indicators import atr as atr_fn

        symbol = body.symbol
        _potential_status["progress"] = "loading data"

        # Data source: broker (live) or history (CSV files)
        if body.data_source == "broker":
            try:
                import asyncio
                from dataclasses import asdict
                from app.services.broker.manager import get_broker_manager
                manager = get_broker_manager()

                # Scope adapter lookup to the current user. Previously the
                # worker picked the first connected adapter globally, which
                # could leak another user's broker into a backtest.
                own_adapters: dict[str, object] = {}
                for (uid, bname), adp in manager._adapters.items():
                    if uid == user_id:
                        own_adapters[bname] = adp

                if not own_adapters:
                    err = "No broker connected. Connect a broker in Settings first."
                    _potential_results[symbol] = {"error": err}
                    if result_id:
                        _update_backtest_record(result_id, status="error", error_message=err)
                    return

                preferred = body.broker if body.broker in own_adapters else None
                broker_name = preferred or next(iter(own_adapters.keys()))
                adapter = own_adapters[broker_name]

                # Honour the broker's actual per-call caps. Pull max M5 and
                # proportionally fewer H1/H4/D1 (since we only need enough
                # HTF context to match the M5 window).
                m5_cap = _broker_cap(broker_name, "M5")
                _potential_status["progress"] = (
                    f"fetching from {broker_name} (up to {m5_cap:,} M5 bars)"
                )

                # Dispatch the async adapter calls onto the FastAPI main loop
                # where the adapter's httpx client was created. Creating a
                # fresh loop in this thread and awaiting the adapter breaks
                # httpx's asyncio.Event affinity — manifests as
                # "<asyncio.locks.Event object ... is bound to a different
                # event loop>" in the user-visible error toast.
                candles_m5 = manager.run_coroutine_on_loop(
                    adapter.get_candles(symbol, "M5", m5_cap), timeout=60,
                )
                h1_need = max(200, (m5_cap // 12) + 50)
                h4_need = max(100, (m5_cap // 48) + 25)
                d1_need = max(50, (m5_cap // 288) + 10)
                candles_h1 = manager.run_coroutine_on_loop(
                    adapter.get_candles(symbol, "H1", min(h1_need, _broker_cap(broker_name, "H1"))), timeout=30,
                )
                candles_h4 = manager.run_coroutine_on_loop(
                    adapter.get_candles(symbol, "H4", min(h4_need, _broker_cap(broker_name, "H4"))), timeout=30,
                )
                candles_d1 = manager.run_coroutine_on_loop(
                    adapter.get_candles(symbol, "D1", min(d1_need, _broker_cap(broker_name, "D1"))), timeout=30,
                )

                m5 = pd.DataFrame([asdict(c) for c in candles_m5])
                h1 = pd.DataFrame([asdict(c) for c in candles_h1]) if candles_h1 else None
                h4 = pd.DataFrame([asdict(c) for c in candles_h4]) if candles_h4 else None
                d1 = pd.DataFrame([asdict(c) for c in candles_d1]) if candles_d1 else None

                if m5.empty or len(m5) < 300:
                    err = f"{broker_name} returned only {len(m5)} M5 bars (need 300+). This broker's per-call cap is {m5_cap:,} — try Dukascopy for longer history."
                    _potential_results[symbol] = {"error": err}
                    if result_id:
                        _update_backtest_record(result_id, status="error", error_message=err)
                    return

                # Surface the true broker window so the UI + stats know what
                # was actually available. Any `start_date` earlier than this
                # is clamped silently — the M5 just doesn't exist.
                broker_first_ts = int(m5["time"].iloc[0])
                broker_last_ts = int(m5["time"].iloc[-1])
                _potential_status["broker_window"] = {
                    "broker": broker_name,
                    "m5_bars": int(len(m5)),
                    "first_ts": broker_first_ts,
                    "last_ts": broker_last_ts,
                    "cap_requested": m5_cap,
                }

            except Exception as e:
                err = f"Broker data fetch failed: {e}"
                _potential_results[symbol] = {"error": err}
                if result_id:
                    _update_backtest_record(result_id, status="error", error_message=err)
                return
        elif body.data_source == "dukascopy":
            # Delta-merge against persistent History Data CSV. First fetch for
            # a symbol bootstraps ~2500 days (few minutes); subsequent fetches
            # only download bars since the newest stored bar (seconds).
            _potential_status["progress"] = "updating Dukascopy data..."
            try:
                from app.services.backtest.data_fetcher import get_backtest_fetcher
                bundle = get_backtest_fetcher().fetch(symbol, days=2500)
                if bundle.bootstrap:
                    _potential_status["progress"] = f"Dukascopy bootstrap complete ({bundle.new_rows:,} bars)"
                else:
                    _potential_status["progress"] = f"Dukascopy updated (+{bundle.new_rows:,} new bars)"
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

        # Align the feature vector with the trained model's shape.
        #   - Pad zeros if the model expects more cols than the feature
        #     module produced (old path).
        #   - Trim if the feature module now emits MORE cols than the model
        #     was trained on (happens when we append features like the new
        #     regime columns — old joblibs don't know about them).
        model_path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_xgboost.joblib")
        if os.path.exists(model_path):
            model_data = joblib.load(model_path)
            model_feats = model_data.get("feature_names", [])
            if len(model_feats) > len(feat_names):
                for extra in model_feats[len(feat_names):]:
                    feat_names.append(extra)
                    X = np.column_stack([X, np.zeros(len(X), dtype=np.float32)])
            elif len(model_feats) and len(feat_names) > len(model_feats):
                feat_names = feat_names[:len(model_feats)]
                X = X[:, :len(model_feats)]

        # Correlation-feature toggle (filter sandbox). Zero-mask corr columns
        # so the model's feature vector shape stays intact but the values
        # carry no signal — matches the live agent's use_correlations=False
        # path.
        if not body.use_correlations:
            for col_idx, fname in enumerate(feat_names):
                if fname.startswith("corr_") or fname.startswith("pot_corr_") or fname.startswith("fx_corr_"):
                    X[:, col_idx] = 0.0

        atr_vals = atr_fn(highs, lows, closes, 14)

        # Regime filter sandbox. Computes regime bar-by-bar using the same
        # rule tree as ScoutAgent / PotentialAgent runtime so backtest
        # rejection behaviour matches live.
        regime_by_bar: Optional[np.ndarray] = None
        if body.regime_filter and body.allowed_regimes:
            from app.services.ml.regime_detector import validate_regime_on_history  # vectorized
            _potential_status["progress"] = "classifying regimes"
            # We only need the per-bar labels, not the return aggregation.
            # Reuse the helper's internal loop by asking for forward_bars=1
            # (cheapest valid input) and pulling the labels — cleaner than
            # duplicating the rule tree here.
            from app.services.backtest.indicators import atr as _atr_fn
            from app.services.backtest.indicators import adx as _adx_fn
            from app.services.backtest.indicators import ema as _ema_fn
            _atr = _atr_fn(highs, lows, closes, 14)
            _adx, _, _ = _adx_fn(highs, lows, closes, 14)
            _ema = _ema_fn(closes, 50)
            n = len(closes)
            labels = np.empty(n, dtype=object)
            labels[:] = ""
            min_bars = max(100, 50 + 20, 14 * 2) + 5
            for i in range(min_bars, n):
                ca = _atr[i]
                if np.isnan(ca):
                    continue
                recent = _atr[max(0, i - 100 + 1): i + 1]
                recent = recent[~np.isnan(recent)]
                if len(recent) < 20:
                    continue
                thresh = float(np.percentile(recent, 75))
                if thresh > 0 and ca >= thresh:
                    labels[i] = "volatile"
                    continue
                cax = _adx[i]
                if np.isnan(cax):
                    cax = 0.0
                if cax < 20.0:
                    labels[i] = "ranging"
                    continue
                if i - 20 < 0 or np.isnan(_ema[i]) or np.isnan(_ema[i - 20]):
                    continue
                labels[i] = "trending_up" if (_ema[i] - _ema[i - 20]) > 0 else "trending_down"
            regime_by_bar = labels

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

        # Read the model's true-OOS boundary (walk-forward training stamps
        # `oos_start` into the joblib). Anything before this timestamp is
        # in-sample; anything at/after is the only window where results are
        # genuinely out-of-sample.
        model_full = joblib.load(os.path.join(MODEL_DIR, f"potential_{symbol}_M5_{model_name}.joblib"))
        oos_start_str = model_full.get("oos_start", "2026-01-01")
        try:
            oos_start_ts = int(pd.Timestamp(oos_start_str, tz="UTC").timestamp())
        except Exception:
            oos_start_ts = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp())

        # ── Predictions ───────────────────────────────────────────────────
        _potential_status["progress"] = "generating predictions"
        preds = model.predict(X)
        # predict_proba so we can tag each trade with its class-probability
        # confidence for the breakdown charts.
        try:
            probas = model.predict_proba(X)
        except Exception:
            probas = None

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
        # Per-run cost overrides (from UI). None → symbol default.
        spread_pts = (
            body.spread_pts_override
            if body.spread_pts_override is not None
            else costs["spread_pts"]
        )
        slippage_pts = (
            body.slippage_pts_override
            if body.slippage_pts_override is not None
            else costs["slippage_pts"]
        )
        commission_per_lot_rt = float(body.commission_per_lot_override or 0.0)
        total_cost_points = spread_pts + slippage_pts
        point_value = costs["point_value"]

        balance = body.balance
        starting_balance = body.balance
        peak_balance = balance
        trades = []
        daily_pnl = {}
        equity_history = [(timestamps[oos_idx] if oos_idx < len(timestamps) else 0, balance)]

        is_scout = (body.agent_type == "scout")
        pending_scout: Optional[dict] = None
        scout_entry_reason: Optional[str] = None
        allowed_sessions_set = set(body.allowed_sessions or []) if body.session_filter else None
        allowed_regimes_set = set(body.allowed_regimes or []) if body.regime_filter else None
        rejected_counts = {"session": 0, "regime": 0}

        def _session_for_ts(ts: int) -> str:
            hr = int(pd.to_datetime(ts, unit="s", utc=True).hour)
            if hr < 8:    return "asian"
            if hr < 13:   return "london"
            if hr < 17:   return "ny_open"
            if hr < 21:   return "ny_close"
            return "off_hours"

        i = oos_idx
        while i < min(end_idx, len(closes) - hold_bars - 1):
            sig = preds[i]
            scout_entry_reason = None

            # Filter-sandbox gates (shared across potential + scout): check
            # BEFORE any entry decision. If the agent's live config would
            # reject this bar, the backtest should too.
            if sig in (0, 2):
                if allowed_sessions_set is not None:
                    sess = _session_for_ts(int(timestamps[i + 1]) if i + 1 < len(timestamps) else int(timestamps[i]))
                    if sess not in allowed_sessions_set:
                        rejected_counts["session"] += 1
                        i += 1
                        continue
                if allowed_regimes_set is not None and regime_by_bar is not None:
                    reg = regime_by_bar[i]
                    if reg and reg not in allowed_regimes_set:
                        rejected_counts["regime"] += 1
                        i += 1
                        continue

            if is_scout:
                if pending_scout is None:
                    # No pending — look for a fresh signal to stash.
                    if sig not in (0, 2):
                        i += 1
                        continue
                    direction_int = 1 if sig == 2 else -1

                    # Dedupe: skip if last trade was same-direction within
                    # `dedupe_window_bars` × 5min. Keeps scout from stacking
                    # in chop when the model re-emits the same side.
                    if trades:
                        last_ts = trades[-1].get("entry_ts", 0)
                        bars_since = (int(timestamps[i]) - int(last_ts)) // 300
                        if bars_since < body.dedupe_window_bars:
                            last_dir_int = 1 if trades[-1]["direction"] == "BUY" else -1
                            if last_dir_int == direction_int:
                                i += 1
                                continue

                    conf_i = float(probas[i][int(sig)]) if probas is not None else 0.0
                    if conf_i >= body.instant_entry_confidence:
                        scout_entry_reason = "instant_confidence"
                        # fall through to execute below
                    else:
                        atr_ref = float(atr_vals[i]) if not np.isnan(atr_vals[i]) else 0.0
                        pending_scout = {
                            "direction": direction_int,
                            "confidence": conf_i,
                            "ref_close": float(closes[i]),
                            "ref_high": float(highs[i]),
                            "ref_low": float(lows[i]),
                            "ref_atr": atr_ref,
                            "ref_idx": i,
                            "bars_waited": 0,
                        }
                        i += 1
                        continue
                else:
                    # Pending exists — check expiry first, then triggers.
                    pending_scout["bars_waited"] += 1
                    if pending_scout["bars_waited"] > body.max_pending_bars:
                        pending_scout = None
                        i += 1
                        continue
                    trigger = _scout_check_triggers(
                        pending_scout, i, closes, highs, lows,
                        body.lookback_bars, body.pullback_atr_fraction,
                        body.instant_entry_confidence,
                    )
                    if trigger is None:
                        i += 1
                        continue
                    scout_entry_reason = trigger
                    # Override sig to match pending direction so shared code
                    # below picks the right side.
                    sig = 2 if pending_scout["direction"] == 1 else 0
                    pending_scout = None
            else:
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
            # Commission charged per lot, round-trip (entry + exit). Subtracted
            # in $ terms after the points-based P&L. 0 when no override.
            commission_cost = commission_per_lot_rt * lot_size * 2
            dollar_pnl = (net_points * lot_size * point_value) - commission_cost

            balance += dollar_pnl
            if balance > peak_balance:
                peak_balance = balance

            entry_time = int(timestamps[i + 1])
            exit_time_ts = int(timestamps[exit_bar])
            date_str = pd.to_datetime(entry_time, unit="s", utc=True).strftime("%Y-%m-%d")

            # Class confidence (predict_proba[i][sig]). Dropped to 0 if the
            # underlying model doesn't implement predict_proba (shouldn't
            # happen for xgb/lgb).
            if probas is not None:
                try:
                    conf = float(probas[i][int(sig)])
                except Exception:
                    conf = 0.0
            else:
                conf = 0.0

            # Entry-hour for session attribution (UTC bins matching the
            # supervisor and agent's session logic).
            entry_hour = int(pd.to_datetime(entry_time, unit="s", utc=True).hour)
            if entry_hour < 8:       session = "asian"
            elif entry_hour < 13:    session = "london"
            elif entry_hour < 17:    session = "ny_open"
            elif entry_hour < 21:    session = "ny_close"
            else:                    session = "off_hours"

            trade = {
                "trade_num": len(trades) + 1,
                "date": date_str,
                "entry_time": pd.to_datetime(entry_time, unit="s", utc=True).strftime("%Y-%m-%d %H:%M"),
                "exit_time": pd.to_datetime(exit_time_ts, unit="s", utc=True).strftime("%Y-%m-%d %H:%M"),
                "entry_ts": int(entry_time),
                "exit_ts": int(exit_time_ts),
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
                "confidence": round(conf, 3),
                "session": session,
                "is_oos": bool(int(entry_time) >= oos_start_ts),
                "entry_reason": scout_entry_reason or "signal",
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

        # Monthly breakdown — also flag each month as fully-OOS / fully-IS /
        # straddles-boundary so the UI can shade the chart honestly.
        monthly = {}
        for t in trades:
            month = t["date"][:7]
            if month not in monthly:
                monthly[month] = {
                    "month": month, "pnl": 0.0, "trades": 0, "wins": 0,
                    "oos_trades": 0, "in_sample_trades": 0,
                }
            monthly[month]["pnl"] += t["dollar_pnl"]
            monthly[month]["trades"] += 1
            if t["dollar_pnl"] > 0:
                monthly[month]["wins"] += 1
            if t.get("is_oos"):
                monthly[month]["oos_trades"] += 1
            else:
                monthly[month]["in_sample_trades"] += 1

        # ── Breakdowns (for stat-card display + AI prompt) ────────────────
        def _summarize(subset):
            if not subset:
                return {"trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
            pnl = sum(x["dollar_pnl"] for x in subset)
            wins_n = sum(1 for x in subset if x["dollar_pnl"] > 0)
            return {
                "trades": len(subset),
                "win_rate": round(100 * wins_n / len(subset), 1),
                "total_pnl": round(pnl, 2),
                "avg_pnl": round(pnl / len(subset), 2),
            }

        breakdowns = {
            "direction": {
                "BUY":  _summarize([t for t in trades if t["direction"] == "BUY"]),
                "SELL": _summarize([t for t in trades if t["direction"] == "SELL"]),
            },
            "exit_type": {
                "TP":      _summarize([t for t in trades if t["exit_type"] == "TP"]),
                "SL":      _summarize([t for t in trades if t["exit_type"] == "SL"]),
                "timeout": _summarize([t for t in trades if t["exit_type"] == "timeout"]),
            },
            "session": {
                s: _summarize([t for t in trades if t["session"] == s])
                for s in ("asian", "london", "ny_open", "ny_close", "off_hours")
            },
            "confidence": {
                "0.50-0.55": _summarize([t for t in trades if 0.50 <= t["confidence"] < 0.55]),
                "0.55-0.60": _summarize([t for t in trades if 0.55 <= t["confidence"] < 0.60]),
                "0.60-0.65": _summarize([t for t in trades if 0.60 <= t["confidence"] < 0.65]),
                "0.65-0.70": _summarize([t for t in trades if 0.65 <= t["confidence"] < 0.70]),
                "0.70+":     _summarize([t for t in trades if t["confidence"] >= 0.70]),
            },
            "oos_split": {
                "in_sample": _summarize([t for t in trades if not t.get("is_oos")]),
                "oos":       _summarize([t for t in trades if t.get("is_oos")]),
            },
        }

        monthly_list = []
        cum = 0.0
        for month_key in sorted(monthly.keys()):
            m = monthly[month_key]
            cum += m["pnl"]
            wr = m["wins"] / m["trades"] * 100 if m["trades"] > 0 else 0
            if m["in_sample_trades"] == 0 and m["oos_trades"] > 0:
                phase = "oos"
            elif m["oos_trades"] == 0 and m["in_sample_trades"] > 0:
                phase = "in_sample"
            else:
                phase = "boundary"
            monthly_list.append({
                "month": month_key,
                "pnl": round(m["pnl"], 2),
                "trades": m["trades"],
                "win_rate": round(wr, 1),
                "cumulative_pnl": round(cum, 2),
                "phase": phase,
                "oos_trades": m["oos_trades"],
                "in_sample_trades": m["in_sample_trades"],
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
            # True walk-forward OOS boundary stamped into the trained model.
            # Anything at/after this ts is out-of-sample; before is in-sample
            # (i.e. the model saw it during training). UI shades accordingly.
            "oos_start_ts": int(oos_start_ts),
            "breakdowns": breakdowns,
            # Filter-sandbox counters — surfaces how many bars each filter
            # rejected so the user sees the impact of their config.
            "filter_rejections": {
                "session": int(rejected_counts["session"]),
                "regime": int(rejected_counts["regime"]),
                "session_filter_on": bool(body.session_filter),
                "regime_filter_on": bool(body.regime_filter),
                "use_correlations": bool(body.use_correlations),
                "allowed_sessions": list(body.allowed_sessions or []) if body.session_filter else [],
                "allowed_regimes": list(body.allowed_regimes or []) if body.regime_filter else [],
            },
            # Surfaces the real data window so the UI can show the honest
            # coverage — if broker caps at 5k M5 bars but user asked for
            # "1 year", this will show the ~17 days they actually got.
            "data_window": {
                "source": body.data_source,
                "broker": _potential_status.get("broker_window", {}).get("broker") if body.data_source == "broker" else None,
                "first_bar_ts": int(timestamps[oos_idx]) if oos_idx < len(timestamps) else None,
                "last_bar_ts": int(timestamps[min(end_idx, len(timestamps)) - 1]) if end_idx > 0 else None,
                "m5_bars_in_window": max(0, int(end_idx) - int(oos_idx)),
                "requested_start": body.start_date,
                "requested_end": body.end_date,
                "broker_cap": _potential_status.get("broker_window", {}).get("cap_requested") if body.data_source == "broker" else None,
            },
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
        logger.error(f"Potential backtest failed for {body.symbol}: {e}", exc_info=True)
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

    # Accept any symbol — validation happens downstream when the model file
    # lookup runs. This lets us surface "No trained model for SYMBOL" once
    # the user expands past the five original symbols (ETHUSD, XAGUSD, etc.),
    # without a stale allowlist blocking legitimate symbols.
    if not body.symbol or len(body.symbol) > 16:
        raise HTTPException(status_code=400, detail="Symbol missing or too long")

    # Create DB record before starting background task
    db_agent_type = "scout" if body.agent_type == "scout" else "potential"
    bt_record = BacktestResult(
        user_id=current_user.id,
        symbol=body.symbol,
        agent_type=db_agent_type,
        config={
            "start_date": body.start_date,
            "end_date": body.end_date,
            "balance": body.balance,
            "max_lot": body.max_lot,
            "risk_pct": body.risk_pct,
            "data_source": body.data_source,
            "agent_type": body.agent_type,
            "lookback_bars": body.lookback_bars if body.agent_type == "scout" else None,
            "instant_entry_confidence": body.instant_entry_confidence if body.agent_type == "scout" else None,
            "max_pending_bars": body.max_pending_bars if body.agent_type == "scout" else None,
            "pullback_atr_fraction": body.pullback_atr_fraction if body.agent_type == "scout" else None,
            "dedupe_window_bars": body.dedupe_window_bars if body.agent_type == "scout" else None,
        },
        status="running",
    )
    db.add(bt_record)
    db.commit()
    db.refresh(bt_record)
    result_id = bt_record.id

    _potential_status.update({"active": True, "symbol": body.symbol, "progress": "starting"})

    background_tasks.add_task(
        _run_potential_backtest, body, result_id, int(current_user.id),
    )
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


# ── AI analysis of a completed backtest ─────────────────────────────────


class BacktestAnalyzeRequest(BaseModel):
    result_id: Optional[int] = None
    symbol: Optional[str] = None  # fallback if no result_id — uses latest for symbol


def _format_backtest_for_ai(r: dict) -> str:
    """Assemble a compact markdown prompt block from a backtest result dict."""
    lines = []
    lines.append(f"# Backtest: {r.get('symbol', '?')} · {r.get('model', '?').upper()} model · Grade {r.get('grade', '?')}")
    lines.append("")
    lines.append("## Headline")
    lines.append(f"- Total P&L: ${r.get('total_pnl', 0):,.2f} ({r.get('total_pnl_pct', 0):+.2f}%)")
    lines.append(f"- Trades: {r.get('total_trades', 0)} | Win rate: {r.get('win_rate', 0)}% | Sharpe: {r.get('sharpe_ratio', 0)}")
    lines.append(f"- Profit factor: {r.get('profit_factor', 0)} | Max DD: ${r.get('max_drawdown', 0):,.2f} ({r.get('max_drawdown_pct', 0):.2f}%)")
    lines.append(f"- Avg win / loss: ${r.get('avg_win', 0):,.2f} / ${r.get('avg_loss', 0):,.2f}")
    lines.append("")

    dw = r.get("data_window") or {}
    if dw:
        lines.append(f"## Data window")
        lines.append(f"- Source: {dw.get('source')} (broker: {dw.get('broker') or '—'})")
        lines.append(f"- M5 bars in window: {dw.get('m5_bars_in_window', 0):,}")
        lines.append(f"- First → Last: {dw.get('first_bar_ts')} → {dw.get('last_bar_ts')} (unix)")
        if dw.get("broker_cap"):
            lines.append(f"- Broker per-request cap: {dw.get('broker_cap'):,} M5 bars")
        lines.append("")

    oos = r.get("oos_start_ts")
    if oos:
        lines.append(f"## Walk-forward boundary")
        lines.append(f"- Model's OOS cutoff (unix): {oos}")
        split = (r.get("breakdowns") or {}).get("oos_split", {})
        is_blob = split.get("in_sample", {})
        oos_blob = split.get("oos", {})
        lines.append(f"- In-sample: {is_blob.get('trades', 0)} trades · WR {is_blob.get('win_rate', 0)}% · P&L ${is_blob.get('total_pnl', 0):,.2f}")
        lines.append(f"- True OOS:  {oos_blob.get('trades', 0)} trades · WR {oos_blob.get('win_rate', 0)}% · P&L ${oos_blob.get('total_pnl', 0):,.2f}")
        lines.append("")

    br = r.get("breakdowns") or {}
    for key, label in [
        ("direction", "Direction"),
        ("exit_type", "Exit type"),
        ("session", "Session (UTC)"),
        ("confidence", "Confidence bucket"),
    ]:
        bucket = br.get(key, {})
        if not bucket:
            continue
        lines.append(f"## {label}")
        for k, v in bucket.items():
            if v.get("trades", 0) == 0:
                continue
            lines.append(
                f"- {k}: {v.get('trades', 0)} trades · WR {v.get('win_rate', 0)}% · "
                f"P&L ${v.get('total_pnl', 0):,.2f} · Avg ${v.get('avg_pnl', 0):,.2f}"
            )
        lines.append("")

    monthly = r.get("monthly_breakdown") or []
    if monthly:
        lines.append("## Monthly breakdown")
        for m in monthly:
            tag = "OOS " if m.get("phase") == "oos" else ("IS  " if m.get("phase") == "in_sample" else "BND ")
            lines.append(
                f"- [{tag}] {m.get('month')}: ${m.get('pnl', 0):,.2f} · "
                f"{m.get('trades', 0)} trades · WR {m.get('win_rate', 0)}% · cum ${m.get('cumulative_pnl', 0):,.2f}"
            )
    return "\n".join(lines)


@router.post("/analyze")
async def analyze_backtest(
    body: BacktestAnalyzeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Send a completed backtest to the user's Claude supervisor for a written
    analysis. Returns `{markdown: str}` — the UI renders it in a panel.

    Requires the user to have configured their API key (Settings → AI
    Supervisor). Not a new LLM stack — reuses the existing per-user supervisor.
    """
    # Locate the result.
    result_data: Optional[dict] = None
    if body.result_id:
        rec = db.query(BacktestResult).filter(
            BacktestResult.id == body.result_id,
            BacktestResult.user_id == current_user.id,
        ).first()
        if rec and rec.results:
            result_data = rec.results
    if result_data is None and body.symbol:
        # Fall back to in-memory cache, then latest completed DB row for user.
        result_data = _potential_results.get(body.symbol)
        if not result_data or "error" in result_data:
            rec = db.query(BacktestResult).filter(
                BacktestResult.user_id == current_user.id,
                BacktestResult.symbol == body.symbol,
                BacktestResult.agent_type == "potential",
                BacktestResult.status == "completed",
            ).order_by(BacktestResult.created_at.desc()).first()
            if rec and rec.results:
                result_data = rec.results
    if not result_data or result_data.get("error"):
        raise HTTPException(status_code=404, detail="No completed backtest to analyze")

    # Ensure supervisor is configured for this user.
    from app.services.llm.supervisor import get_supervisor
    from app.services.llm.monitoring import _ensure_supervisor_configured
    if not _ensure_supervisor_configured(db, current_user.id):
        raise HTTPException(
            status_code=400,
            detail="AI Supervisor not configured. Add your Anthropic API key in Settings.",
        )

    summary_md = _format_backtest_for_ai(result_data)
    instruction = (
        "You are reviewing a backtest for a trading agent. Using the structured "
        "summary below, produce a Markdown report with these sections (concise, "
        "no fluff, bullet points where possible):\n"
        "  1. **TL;DR** — single-sentence verdict.\n"
        "  2. **Where it performed well** — symbols/sessions/directions/exit types/confidence "
        "buckets with the strongest edge, and why that might be.\n"
        "  3. **Where it performed poorly** — the weakest regions; flag drawdowns, low-WR "
        "buckets, timeout-heavy patterns.\n"
        "  4. **Overfitting / OOS risk** — compare the In-sample vs True-OOS rows in the "
        "split; if OOS is much worse, say so plainly. If they're similar, note that confidence.\n"
        "  5. **Actionable next steps** — 2-4 concrete changes the trader could try "
        "(e.g. direction gate, session filter, confidence threshold, retrain).\n"
        "Keep it under 400 words total. Use concrete numbers from the summary; do NOT invent.\n\n"
        "Summary:\n\n"
        + summary_md
    )

    reply = await get_supervisor().chat(current_user.id, instruction, context=None)
    return {"markdown": reply or "_No response from AI._", "result_symbol": result_data.get("symbol")}
