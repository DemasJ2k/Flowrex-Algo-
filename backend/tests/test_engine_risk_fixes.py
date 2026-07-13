"""
Tests for the Phase-0 risk/execution fixes (2026-07-13 reorg):

1. Trade-level broker close (Oanda close_position can't close by ticket).
2. Reconcile never marks a DB trade closed while the broker says OPEN.
3. Daily P&L / trade count derived from the DB, account-level.
4. Hard-stop liquidation closes open trades.
5. DB-backed duplicate-trade guard (cross-agent, per-symbol).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.agent import TradingAgent, AgentTrade
from app.services.agent.engine import AgentRunner
from app.services.broker.base import CloseResult


# ── Test doubles ───────────────────────────────────────────────────────


class _TicketCloseAdapter:
    """Adapter exposing trade-level close (like Oanda after the fix)."""

    def __init__(self, success=True, pnl=25.0):
        self.close_trade_calls = []
        self.close_position_calls = []
        self._success = success
        self._pnl = pnl

    async def close_trade(self, trade_id: str) -> CloseResult:
        self.close_trade_calls.append(trade_id)
        return CloseResult(success=self._success, pnl=self._pnl, message="ok")

    async def close_position(self, position_id: str) -> CloseResult:
        self.close_position_calls.append(position_id)
        return CloseResult(success=False, message="Invalid position ID format")


class _PositionOnlyAdapter:
    """Adapter without close_trade — engine must fall back to close_position."""

    def __init__(self):
        self.close_position_calls = []

    async def close_position(self, position_id: str) -> CloseResult:
        self.close_position_calls.append(position_id)
        return CloseResult(success=True, pnl=1.0, message="ok")


class _OandaLikeAdapter:
    """Fake with Oanda's per-ticket trade endpoint."""

    def __init__(self, state, realized_pl=0.0, close_price=0.0, positions=None):
        self._account_id = "acct-1"
        self._state = state
        self._realized_pl = realized_pl
        self._close_price = close_price
        self._positions = positions or []

    async def _request(self, method, path, **kwargs):
        return {
            "trade": {
                "state": self._state,
                "realizedPL": str(self._realized_pl),
                "averageClosePrice": str(self._close_price),
            }
        }

    async def get_positions(self):
        return self._positions


class _NoTicketApiAdapter:
    """Broker without a per-ticket endpoint (non-Oanda path)."""

    def __init__(self, positions):
        self._positions = positions

    async def get_positions(self):
        return self._positions


class _Pos:
    def __init__(self, symbol, direction):
        self.id = f"{symbol}:{direction.lower()}"
        self.symbol = symbol
        self.direction = direction


# ── Helpers ────────────────────────────────────────────────────────────


def _make_agent(db, user_id, symbol="US30", broker="oanda", **risk):
    agent = TradingAgent(
        created_by=user_id,
        name=f"test-{symbol}",
        symbol=symbol,
        agent_type="potential",
        broker_name=broker,
        mode="paper",
        status="running",
        risk_config=risk or {},
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def _make_trade(db, agent, status="open", pnl=None, direction="BUY",
                entry_offset_h=0.0, exit_offset_h=None, ticket="393"):
    now = datetime.now(timezone.utc)
    trade = AgentTrade(
        agent_id=agent.id,
        symbol=agent.symbol,
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        lot_size=1.0,
        status=status,
        pnl=pnl,
        broker_pnl=pnl,
        broker_ticket=ticket,
        entry_time=now - timedelta(hours=entry_offset_h),
        exit_time=(now - timedelta(hours=exit_offset_h)) if exit_offset_h is not None else None,
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def _runner(agent):
    runner = AgentRunner(agent.id)
    # Don't hit the DB-backed logger's websocket path in unit tests
    runner._log_to_db = lambda db, level, msg, data=None: None
    return runner


# ── 1. Trade-level close ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_broker_trade_prefers_trade_level_close(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    runner = _runner(agent)
    adapter = _TicketCloseAdapter()

    result = await runner._close_broker_trade(adapter, 393)

    assert result.success is True
    assert adapter.close_trade_calls == ["393"]
    assert adapter.close_position_calls == []


@pytest.mark.asyncio
async def test_close_broker_trade_falls_back_to_close_position(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    runner = _runner(agent)
    adapter = _PositionOnlyAdapter()

    result = await runner._close_broker_trade(adapter, "77")

    assert result.success is True
    assert adapter.close_position_calls == ["77"]


# ── 2. Reconcile must not close OPEN broker trades ─────────────────────


@pytest.mark.asyncio
async def test_reconcile_leaves_trade_open_when_broker_says_open(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    trade = _make_trade(db_session, agent)
    runner = _runner(agent)
    adapter = _OandaLikeAdapter(state="OPEN")

    await runner._reconcile_closed_trade_from_broker(adapter, trade, db_session, reason="FORCE_FLAT_RECONCILED")

    assert trade.status == "open"
    assert trade.exit_time is None


@pytest.mark.asyncio
async def test_reconcile_closes_trade_when_broker_confirms_closed(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    trade = _make_trade(db_session, agent)
    runner = _runner(agent)
    adapter = _OandaLikeAdapter(state="CLOSED", realized_pl=-42.5, close_price=98.0)

    await runner._reconcile_closed_trade_from_broker(adapter, trade, db_session, reason="MAX_HOLD_RECONCILED")

    assert trade.status == "closed"
    assert trade.pnl == -42.5
    assert trade.exit_price == 98.0
    assert trade.exit_reason == "MAX_HOLD_RECONCILED"


@pytest.mark.asyncio
async def test_reconcile_non_oanda_keeps_open_on_matching_position(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    trade = _make_trade(db_session, agent, direction="BUY")
    runner = _runner(agent)
    adapter = _NoTicketApiAdapter(positions=[_Pos("US30", "BUY")])

    await runner._reconcile_closed_trade_from_broker(adapter, trade, db_session, reason="FORCE_FLAT_RECONCILED")

    assert trade.status == "open"


@pytest.mark.asyncio
async def test_reconcile_non_oanda_closes_when_no_matching_position(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    trade = _make_trade(db_session, agent, direction="BUY")
    runner = _runner(agent)
    adapter = _NoTicketApiAdapter(positions=[_Pos("XAUUSD", "SELL")])

    await runner._reconcile_closed_trade_from_broker(adapter, trade, db_session, reason="FORCE_FLAT_RECONCILED")

    assert trade.status == "closed"


# ── 3. DB-derived, account-level daily stats ───────────────────────────


def test_daily_stats_aggregate_across_agents_same_broker(db_session, test_user):
    agent_a = _make_agent(db_session, test_user.id, symbol="US30")
    agent_b = _make_agent(db_session, test_user.id, symbol="XAUUSD")
    # Closed today: -100 (agent A) and +30 (agent B)
    _make_trade(db_session, agent_a, status="closed", pnl=-100.0, exit_offset_h=1)
    _make_trade(db_session, agent_b, status="closed", pnl=30.0, exit_offset_h=2)
    # Closed 2 days ago — must not count
    _make_trade(db_session, agent_a, status="closed", pnl=-500.0,
                entry_offset_h=50, exit_offset_h=49)
    # Still open — not realized
    _make_trade(db_session, agent_b, status="open")

    runner = _runner(agent_a)
    realized, opened = runner._compute_daily_stats(db_session, agent_a)

    assert realized == pytest.approx(-70.0)
    # Opened today: the two closed-today trades + the open one
    assert opened == 3


def test_daily_stats_exclude_other_broker(db_session, test_user):
    agent_a = _make_agent(db_session, test_user.id, symbol="US30", broker="oanda")
    agent_other = _make_agent(db_session, test_user.id, symbol="US30", broker="mt5")
    _make_trade(db_session, agent_other, status="closed", pnl=-999.0, exit_offset_h=1)

    runner = _runner(agent_a)
    realized, opened = runner._compute_daily_stats(db_session, agent_a)

    assert realized == 0.0
    assert opened == 0


# ── 4. Hard-stop liquidation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_all_open_trades_liquidates_and_tags_reason(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    t1 = _make_trade(db_session, agent, ticket="1")
    t2 = _make_trade(db_session, agent, ticket="2", direction="SELL")
    runner = _runner(agent)
    adapter = _TicketCloseAdapter(success=True, pnl=-10.0)

    closed = await runner._close_all_open_trades(adapter, db_session, reason="DAILY_HARD_STOP")

    assert closed == 2
    assert t1.status == "closed" and t2.status == "closed"
    assert t1.exit_reason == "DAILY_HARD_STOP"
    assert adapter.close_trade_calls == ["1", "2"]


@pytest.mark.asyncio
async def test_close_all_open_trades_noop_when_flat(db_session, test_user):
    agent = _make_agent(db_session, test_user.id)
    runner = _runner(agent)
    adapter = _TicketCloseAdapter()

    closed = await runner._close_all_open_trades(adapter, db_session, reason="DAILY_HARD_STOP")

    assert closed == 0
    assert adapter.close_trade_calls == []


# ── 5. Duplicate-trade guard ───────────────────────────────────────────


class _SpyOrderAdapter:
    def __init__(self):
        self.place_order_calls = []

    async def get_account_info(self):
        from app.services.broker.base import AccountInfo
        return AccountInfo(balance=10000.0, margin_available=9000.0)

    async def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        from app.services.broker.base import OrderResult
        return OrderResult(success=True, order_id="T1", message="ok")


_SIGNAL = {
    "direction": "BUY",
    "entry_price": 100.0,
    "stop_loss": 95.0,
    "take_profit": 110.0,
    "lot_size": 1.0,
    "confidence": 0.9,
    "reason": "test",
}


@pytest.mark.asyncio
async def test_create_trade_blocked_by_other_agents_open_trade(db_session, test_user):
    agent_a = _make_agent(db_session, test_user.id, symbol="US30")
    agent_b = _make_agent(db_session, test_user.id, symbol="US30")
    _make_trade(db_session, agent_b, direction="SELL")  # other agent holds US30

    runner = _runner(agent_a)
    adapter = _SpyOrderAdapter()

    await runner._create_trade(dict(_SIGNAL), adapter, agent_a, db_session)

    assert adapter.place_order_calls == []


@pytest.mark.asyncio
async def test_create_trade_blocked_by_own_open_trade_any_direction(db_session, test_user):
    agent = _make_agent(db_session, test_user.id, symbol="US30")
    _make_trade(db_session, agent, direction="SELL")  # opposite direction still blocks

    runner = _runner(agent)
    adapter = _SpyOrderAdapter()

    await runner._create_trade(dict(_SIGNAL), adapter, agent, db_session)

    assert adapter.place_order_calls == []


@pytest.mark.asyncio
async def test_create_trade_proceeds_when_symbol_flat(db_session, test_user):
    agent = _make_agent(db_session, test_user.id, symbol="US30")
    other = _make_agent(db_session, test_user.id, symbol="XAUUSD")
    _make_trade(db_session, other)  # open trade on a DIFFERENT symbol is fine

    runner = _runner(agent)
    adapter = _SpyOrderAdapter()

    await runner._create_trade(dict(_SIGNAL), adapter, agent, db_session)

    assert len(adapter.place_order_calls) == 1
    db_session.commit()
    trade = (
        db_session.query(AgentTrade)
        .filter(AgentTrade.agent_id == agent.id, AgentTrade.status == "open")
        .first()
    )
    assert trade is not None
    assert trade.direction == "BUY"
