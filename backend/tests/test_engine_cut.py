"""
Tests for the 2026-07-13 reorg cut: potential-only dispatch, max-hold
enforcement through the trade-level close path, and the shared session
classifier.
"""
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import patch

from app.models.agent import TradingAgent, AgentTrade
from app.services.agent.engine import AgentRunner
from app.services.agent.filters import classify_session, SESSIONS
from app.services.broker.base import CloseResult


class _TicketCloseAdapter:
    def __init__(self):
        self.close_trade_calls = []

    async def close_trade(self, trade_id: str) -> CloseResult:
        self.close_trade_calls.append(trade_id)
        return CloseResult(success=True, pnl=-5.0, message="ok")


def _make_agent(db, user_id, symbol="US30", agent_type="potential", **risk):
    agent = TradingAgent(
        created_by=user_id, name=f"t-{symbol}", symbol=symbol,
        agent_type=agent_type, broker_name="oanda", mode="paper",
        status="running", risk_config=risk or {},
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def _make_trade(db, agent, entry_offset_h=0.0, ticket="7"):
    trade = AgentTrade(
        agent_id=agent.id, symbol=agent.symbol, direction="BUY",
        entry_price=100.0, stop_loss=95.0, take_profit=110.0, lot_size=1.0,
        status="open", broker_ticket=ticket,
        entry_time=datetime.now(timezone.utc) - timedelta(hours=entry_offset_h),
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def _runner(agent):
    runner = AgentRunner(agent.id)
    runner._log_to_db = lambda db, level, msg, data=None: None
    return runner


# ── Dispatch: potential-only ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_type", ["flowrex", "flowrex_v2", "scout", "scalping", "expert"])
async def test_start_refuses_removed_agent_types(db_session, test_user, legacy_type):
    from tests.conftest import TestingSessionLocal

    agent = _make_agent(db_session, test_user.id, agent_type=legacy_type)
    with patch("app.services.agent.engine.SessionLocal", TestingSessionLocal):
        runner = AgentRunner(agent.id)
        await runner.start()

    assert runner._agent is None
    # The refusal must be logged so the user sees WHY the agent won't run
    logs = [l.message for l in agent.logs]
    assert any("removed in the reorg" in m for m in logs)


# ── Max-hold enforcement (now via trade-level close) ───────────────────


@pytest.mark.asyncio
async def test_max_hold_closes_stale_trade(db_session, test_user):
    agent = _make_agent(db_session, test_user.id, max_hold_hours=24)
    stale = _make_trade(db_session, agent, entry_offset_h=30)
    fresh = _make_trade(db_session, agent, entry_offset_h=1, ticket="8")
    runner = _runner(agent)
    adapter = _TicketCloseAdapter()

    await runner._enforce_max_hold_time(adapter, agent, db_session)

    assert stale.status == "closed"
    assert stale.exit_reason == "MAX_HOLD_TIME"
    assert fresh.status == "open"
    assert adapter.close_trade_calls == ["7"]


@pytest.mark.asyncio
async def test_max_hold_disabled_leaves_trades_open(db_session, test_user):
    agent = _make_agent(db_session, test_user.id, max_hold_hours=0)
    stale = _make_trade(db_session, agent, entry_offset_h=100)
    runner = _runner(agent)
    adapter = _TicketCloseAdapter()

    await runner._enforce_max_hold_time(adapter, agent, db_session)

    assert stale.status == "open"
    assert adapter.close_trade_calls == []


# ── Shared session classifier ──────────────────────────────────────────


@pytest.mark.parametrize("hour,expected", [
    (0, "asian"), (7, "asian"),
    (8, "london"), (12, "london"),
    (13, "ny_open"), (16, "ny_open"),
    (17, "ny_close"), (20, "ny_close"),
    (21, "off_hours"), (23, "off_hours"),
])
def test_classify_session_buckets(hour, expected):
    assert classify_session(hour) == expected


def test_classify_session_covers_all_buckets():
    seen = {classify_session(h) for h in range(24)}
    assert seen == set(SESSIONS)
