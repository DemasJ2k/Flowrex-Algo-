"""Backtest API endpoints — with transaction cost configuration."""
import os
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
from dataclasses import asdict
import pandas as pd

from app.core.auth import get_current_user
from app.models.user import User
from app.services.backtest.engine import BacktestEngine

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")

_results: dict[str, dict] = {}
_status: dict = {"active": False, "symbol": None, "progress": ""}


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

    _status.update({"active": True, "symbol": body.symbol, "progress": "loading data"})

    def _run():
        try:
            m5_path = os.path.join(DATA_DIR, f"{body.symbol}_M5.csv")
            h1_path = os.path.join(DATA_DIR, f"{body.symbol}_H1.csv")
            if not os.path.exists(m5_path):
                _results[body.symbol] = {"error": "No data available"}
                return

            _status["progress"] = "loading data"
            m5 = pd.read_csv(m5_path)
            h1 = pd.read_csv(h1_path) if os.path.exists(h1_path) else None

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
