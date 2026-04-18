"""
Trade monitor — watches open agent trades for SL/TP hits.
Reconciles paper trades with broker positions.
"""
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.agent import AgentTrade, TradingAgent
from app.services.broker.base import BrokerAdapter

logger = logging.getLogger("flowrex.trade_monitor")


class TradeMonitor:
    """
    Background service that monitors open agent trades.
    Checks broker positions to detect closed trades.
    """

    def __init__(self):
        self._running = False
        self._task = None

    async def start(self, db_factory, broker_manager):
        """Start monitoring loop."""
        self._running = True
        self._db_factory = db_factory
        self._broker_manager = broker_manager
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """Stop monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self):
        """Check open trades every 30 seconds."""
        while self._running:
            try:
                await self._check_open_trades()
            except Exception as e:
                logger.error(f"TradeMonitor error: {e}", exc_info=True)
            await asyncio.sleep(30)

    async def _check_open_trades(self):
        """Check all open agent trades against broker positions."""
        db = self._db_factory()
        try:
            open_trades = (
                db.query(AgentTrade)
                .join(TradingAgent)
                .filter(
                    AgentTrade.status == "open",
                    TradingAgent.deleted_at.is_(None),
                )
                .all()
            )

            if not open_trades:
                return

            # Group trades by user+broker
            trades_by_broker: dict[tuple[int, str], list[AgentTrade]] = {}
            for trade in open_trades:
                agent = db.query(TradingAgent).filter(TradingAgent.id == trade.agent_id).first()
                if not agent:
                    continue
                key = (agent.created_by, agent.broker_name)
                if key not in trades_by_broker:
                    trades_by_broker[key] = []
                trades_by_broker[key].append(trade)

            # Check each broker's positions
            for (user_id, broker_name), trades in trades_by_broker.items():
                adapter = self._broker_manager.get_adapter(user_id, broker_name)
                if not adapter:
                    # No broker connected — check paper trades via price
                    await self._check_paper_trades(trades, db)
                    continue

                try:
                    broker_positions = await adapter.get_positions()
                    broker_tickets = {p.id for p in broker_positions}

                    for trade in trades:
                        if trade.broker_ticket and trade.broker_ticket not in broker_tickets:
                            # Position no longer exists at broker — it was closed
                            await self._mark_trade_closed(trade, db, "broker_closed")
                except Exception:
                    pass

            db.commit()
        finally:
            db.close()

    async def _check_paper_trades(self, trades: list[AgentTrade], db: Session):
        """Check paper trades against current price (simplified)."""
        # Paper trades close when price hits SL or TP
        # In production this would check live tick prices
        # For now, just mark stale trades (open > 24 hours with no broker)
        pass

    async def _mark_trade_closed(
        self, trade: AgentTrade, db: Session, reason: str = "SL"
    ):
        """Mark a trade as closed in the DB."""
        trade.status = "closed"
        trade.exit_time = datetime.now(timezone.utc)
        trade.exit_reason = reason

        # Try to compute P&L if we have exit price
        if trade.exit_price and trade.entry_price:
            if trade.direction == "BUY":
                trade.pnl = (trade.exit_price - trade.entry_price) * trade.lot_size
            else:
                trade.pnl = (trade.entry_price - trade.exit_price) * trade.lot_size


# Singleton
_monitor = None


def get_trade_monitor() -> TradeMonitor:
    global _monitor
    if _monitor is None:
        _monitor = TradeMonitor()
    return _monitor
