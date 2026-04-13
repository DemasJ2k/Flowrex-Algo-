from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import datetime, timezone
from typing import Optional

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.models.agent import TradingAgent, AgentLog, AgentTrade
from app.services.agent.engine import get_algo_engine
from app.services.broker.symbol_registry import get_symbol_registry
from app.schemas.agent import (
    AgentCreate, AgentUpdate, AgentResponse,
    LogResponse, TradeResponse, PnlSummaryItem,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _get_agent_or_404(
    agent_id: int, user: User, db: Session
) -> TradingAgent:
    agent = (
        db.query(TradingAgent)
        .filter(
            TradingAgent.id == agent_id,
            TradingAgent.created_by == user.id,
            TradingAgent.deleted_at.is_(None),
        )
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _enrich_agent(agent: TradingAgent, db: Session) -> dict:
    """Add trade_count and total_pnl to agent data."""
    data = {
        "id": agent.id,
        "name": agent.name,
        "symbol": agent.symbol,
        "timeframe": agent.timeframe,
        "agent_type": agent.agent_type,
        "broker_name": agent.broker_name,
        "mode": agent.mode,
        "status": agent.status,
        "risk_config": agent.risk_config,
        "created_at": agent.created_at,
        "deleted_at": agent.deleted_at,
    }
    result = (
        db.query(
            func.count(AgentTrade.id),
            func.coalesce(func.sum(func.coalesce(AgentTrade.broker_pnl, AgentTrade.pnl, 0)), 0),
        )
        .filter(AgentTrade.agent_id == agent.id)
        .first()
    )
    data["trade_count"] = result[0] if result else 0
    data["total_pnl"] = float(result[1]) if result else 0.0
    return data


# ── Static routes FIRST (before /{agent_id}) ──────────────────────────

@router.get("/engine-logs", response_model=list[LogResponse])
def get_engine_logs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    level: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(AgentLog)
        .join(TradingAgent)
        .filter(
            TradingAgent.created_by == current_user.id,
            TradingAgent.deleted_at.is_(None),
        )
    )
    if level:
        query = query.filter(AgentLog.level == level)
    logs = query.order_by(AgentLog.created_at.desc()).offset(offset).limit(limit).all()
    return logs


@router.delete("/logs")
def clear_all_logs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete all agent logs belonging to the current user. Does not affect trades or agents."""
    # Get the agent IDs owned by this user first, then delete logs for those agents
    agent_ids = [
        row.id for row in
        db.query(TradingAgent.id).filter(TradingAgent.created_by == current_user.id).all()
    ]
    if not agent_ids:
        return {"deleted": 0, "message": "Cleared 0 log entries"}

    deleted = (
        db.query(AgentLog)
        .filter(AgentLog.agent_id.in_(agent_ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted, "message": f"Cleared {deleted} log entries"}


@router.get("/all-trades", response_model=list[TradeResponse])
def get_all_trades(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(AgentTrade)
        .join(TradingAgent)
        .filter(
            TradingAgent.created_by == current_user.id,
            TradingAgent.deleted_at.is_(None),
        )
    )
    if status:
        query = query.filter(AgentTrade.status == status)
    trades = query.order_by(AgentTrade.entry_time.desc()).offset(offset).limit(limit).all()
    return trades


@router.get("/pnl-summary", response_model=list[PnlSummaryItem])
def get_pnl_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pnl_col = func.coalesce(AgentTrade.broker_pnl, AgentTrade.pnl, 0)
    results = (
        db.query(
            TradingAgent.id.label("agent_id"),
            TradingAgent.name.label("agent_name"),
            TradingAgent.symbol.label("symbol"),
            func.coalesce(func.sum(pnl_col), 0).label("total_pnl"),
            func.count(AgentTrade.id).label("trade_count"),
            func.sum(case((pnl_col > 0, 1), else_=0)).label("win_count"),
            func.sum(case((pnl_col < 0, 1), else_=0)).label("loss_count"),
        )
        .outerjoin(AgentTrade, (AgentTrade.agent_id == TradingAgent.id) & (AgentTrade.status == "closed"))
        .filter(
            TradingAgent.created_by == current_user.id,
            TradingAgent.deleted_at.is_(None),
        )
        .group_by(TradingAgent.id, TradingAgent.name, TradingAgent.symbol)
        .all()
    )
    return [
        PnlSummaryItem(
            agent_id=r.agent_id,
            agent_name=r.agent_name,
            symbol=r.symbol,
            total_pnl=float(r.total_pnl),
            trade_count=r.trade_count,
            win_count=r.win_count or 0,
            loss_count=r.loss_count or 0,
        )
        for r in results
    ]


# ── Recycle Bin (static routes — must be before /{agent_id}) ───────────


@router.get("/recycle-bin", response_model=list[AgentResponse])
def list_deleted_agents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all soft-deleted agents (recycle bin)."""
    agents = (
        db.query(TradingAgent)
        .filter(TradingAgent.created_by == current_user.id, TradingAgent.deleted_at.isnot(None))
        .order_by(TradingAgent.deleted_at.desc())
        .all()
    )
    return [AgentResponse(**_enrich_agent(a, db)) for a in agents]


@router.post("/recycle-bin/{agent_id}/restore")
def restore_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Restore a soft-deleted agent from recycle bin."""
    agent = db.query(TradingAgent).filter(
        TradingAgent.id == agent_id, TradingAgent.created_by == current_user.id,
        TradingAgent.deleted_at.isnot(None),
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Deleted agent not found")
    agent.deleted_at = None
    agent.status = "stopped"
    db.commit()
    return {"message": "Agent restored", "id": agent_id, "name": agent.name}


@router.delete("/recycle-bin/{agent_id}/purge")
def purge_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently delete an agent and all its trades/logs."""
    agent = db.query(TradingAgent).filter(
        TradingAgent.id == agent_id, TradingAgent.created_by == current_user.id,
        TradingAgent.deleted_at.isnot(None),
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Deleted agent not found")
    db.query(AgentLog).filter(AgentLog.agent_id == agent_id).delete()
    db.query(AgentTrade).filter(AgentTrade.agent_id == agent_id).delete()
    db.delete(agent)
    db.commit()
    return {"message": "Agent permanently deleted", "id": agent_id}


@router.delete("/recycle-bin/purge-all")
def purge_all_deleted(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently delete ALL soft-deleted agents."""
    deleted = db.query(TradingAgent).filter(
        TradingAgent.created_by == current_user.id, TradingAgent.deleted_at.isnot(None),
    ).all()
    count = 0
    for agent in deleted:
        db.query(AgentLog).filter(AgentLog.agent_id == agent.id).delete()
        db.query(AgentTrade).filter(AgentTrade.agent_id == agent.id).delete()
        db.delete(agent)
        count += 1
    db.commit()
    return {"message": f"Permanently deleted {count} agents", "count": count}


# ── CRUD routes ────────────────────────────────────────────────────────

@router.get("/", response_model=list[AgentResponse])
def list_agents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agents = (
        db.query(TradingAgent)
        .filter(
            TradingAgent.created_by == current_user.id,
            TradingAgent.deleted_at.is_(None),
        )
        .order_by(TradingAgent.created_at.desc())
        .all()
    )
    return [AgentResponse(**_enrich_agent(a, db)) for a in agents]


@router.post("/", response_model=AgentResponse, status_code=201)
def create_agent(
    body: AgentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Check for duplicate underlying instrument (e.g., ES and SPX500 both map to SPX500_USD on Oanda)
    registry = get_symbol_registry()
    broker = body.broker_name or "oanda"
    new_broker_symbol = registry.to_broker(body.symbol, broker)

    existing_agents = (
        db.query(TradingAgent)
        .filter(
            TradingAgent.created_by == current_user.id,
            TradingAgent.deleted_at.is_(None),
            TradingAgent.status.in_(["running", "paused"]),
        )
        .all()
    )

    for existing in existing_agents:
        existing_broker = existing.broker_name or "oanda"
        existing_broker_symbol = registry.to_broker(existing.symbol, existing_broker)
        if (
            existing_broker_symbol == new_broker_symbol
            and existing_broker == broker
            and existing.symbol != body.symbol
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Warning: You already have a running agent '{existing.name}' "
                    f"for {existing.symbol} which maps to the same broker instrument "
                    f"({new_broker_symbol}) as {body.symbol}. "
                    f"Stop the existing agent first or use the same symbol."
                ),
            )

    agent = TradingAgent(
        created_by=current_user.id,
        name=body.name,
        symbol=body.symbol,
        timeframe=body.timeframe,
        agent_type=body.agent_type,
        broker_name=body.broker_name,
        mode=body.mode,
        risk_config=body.risk_config,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return AgentResponse(**_enrich_agent(agent, db))


# ── Parameterized routes /{agent_id} ──────────────────────────────────

@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent_or_404(agent_id, current_user, db)
    return AgentResponse(**_enrich_agent(agent, db))


@router.put("/{agent_id}", response_model=AgentResponse)
def update_agent(
    agent_id: int,
    body: AgentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent_or_404(agent_id, current_user, db)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(agent, key, value)
    db.commit()
    db.refresh(agent)

    # Hot-reload the running agent's config so Edit Config changes
    # (risk %, daily loss, cooldown) take effect immediately without
    # requiring a stop/start cycle.
    try:
        from app.services.agent.engine import get_algo_engine
        engine = get_algo_engine()
        if engine.is_running(agent_id):
            engine.reload_agent_config(agent_id)
    except Exception:
        pass  # Non-fatal — DB is updated, agent will pick up on next restart

    return AgentResponse(**_enrich_agent(agent, db))


@router.delete("/{agent_id}")
def delete_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent_or_404(agent_id, current_user, db)
    agent.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Agent deleted", "id": agent_id}


@router.post("/{agent_id}/start")
async def start_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent_or_404(agent_id, current_user, db)
    engine = get_algo_engine()
    await engine.start_agent(agent_id)
    agent.status = "running"
    db.commit()
    return {"status": "running", "message": "Agent started"}


@router.post("/{agent_id}/stop")
async def stop_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent_or_404(agent_id, current_user, db)
    engine = get_algo_engine()
    await engine.stop_agent(agent_id)
    agent.status = "stopped"
    db.commit()
    return {"status": "stopped", "message": "Agent stopped"}


@router.post("/{agent_id}/pause")
async def pause_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent_or_404(agent_id, current_user, db)
    engine = get_algo_engine()
    await engine.pause_agent(agent_id)
    agent.status = "paused"
    db.commit()
    return {"status": "paused", "message": "Agent paused"}


@router.get("/{agent_id}/logs", response_model=list[LogResponse])
def get_agent_logs(
    agent_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    level: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_agent_or_404(agent_id, current_user, db)
    query = db.query(AgentLog).filter(AgentLog.agent_id == agent_id)
    if level:
        query = query.filter(AgentLog.level == level)
    logs = query.order_by(AgentLog.created_at.desc()).offset(offset).limit(limit).all()
    return logs


@router.get("/{agent_id}/trades", response_model=list[TradeResponse])
def get_agent_trades(
    agent_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_agent_or_404(agent_id, current_user, db)
    query = db.query(AgentTrade).filter(AgentTrade.agent_id == agent_id)
    if status:
        query = query.filter(AgentTrade.status == status)
    trades = query.order_by(AgentTrade.entry_time.desc()).offset(offset).limit(limit).all()
    return trades


@router.get("/{agent_id}/performance")
def get_agent_performance(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_agent_or_404(agent_id, current_user, db)
    trades = db.query(AgentTrade).filter(AgentTrade.agent_id == agent_id).all()

    closed = [t for t in trades if t.status == "closed"]
    open_trades = [t for t in trades if t.status == "open"]

    def pnl_of(t):
        return t.broker_pnl if t.broker_pnl is not None else (t.pnl or 0)

    pnls = [pnl_of(t) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0

    import numpy as np

    # Sharpe ratio
    sharpe = 0.0
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252))

    # Max drawdown
    max_drawdown = 0.0
    if pnls:
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        dd = peak - cumulative
        max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0.0

    # Win/loss streaks
    win_streak = 0
    loss_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    for p in pnls:
        if p > 0:
            win_streak += 1
            loss_streak = 0
            max_win_streak = max(max_win_streak, win_streak)
        elif p < 0:
            loss_streak += 1
            win_streak = 0
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            win_streak = 0
            loss_streak = 0

    # Equity curve data points
    equity_curve = []
    if closed:
        cum_pnl = 0.0
        for t in sorted(closed, key=lambda x: x.entry_time or ""):
            cum_pnl += pnl_of(t)
            equity_curve.append({
                "time": t.exit_time.isoformat() if t.exit_time else t.entry_time.isoformat() if t.entry_time else "",
                "pnl": round(cum_pnl, 2),
            })

    avg_win = float(np.mean(wins)) if wins else 0
    avg_loss = float(np.mean(losses)) if losses else 0

    return {
        "total_trades": len(trades),
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "win_rate": (len(wins) / len(closed) * 100) if closed else 0,
        "total_pnl": total_pnl,
        "avg_pnl_per_trade": (total_pnl / len(closed)) if closed else 0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "best_trade": max(pnls) if pnls else 0,
        "worst_trade": min(pnls) if pnls else 0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0,
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown": round(max_drawdown, 2),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "equity_curve": equity_curve,
    }
