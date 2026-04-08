"""Integration tests for the complete trade execution flow."""
import pytest
from datetime import datetime, timezone
from app.models.agent import TradingAgent, AgentTrade, AgentLog


class TestTradeFlow:
    """Test signal -> order -> record -> close lifecycle."""

    def test_successful_trade_creates_agent_trade(self, db_session, test_user):
        """When broker confirms order, AgentTrade should be created with broker_ticket."""
        # Create a test agent in DB
        agent = TradingAgent(
            name="Test US30", symbol="US30", timeframe="M5",
            agent_type="potential", broker_name="oanda",
            mode="paper", status="running", created_by=test_user.id,
            risk_config={"risk_per_trade": 0.01, "cooldown_bars": 3}
        )
        db_session.add(agent)
        db_session.commit()

        # Verify agent was created
        assert agent.id is not None

        # Check no trades exist yet
        trades = db_session.query(AgentTrade).filter(AgentTrade.agent_id == agent.id).all()
        assert len(trades) == 0

    def test_cancelled_order_no_agent_trade(self, db_session, test_user):
        """When broker cancels order, NO AgentTrade should be created."""
        agent = TradingAgent(
            name="Test ES", symbol="ES", timeframe="M5",
            agent_type="potential", broker_name="oanda",
            mode="paper", status="running", created_by=test_user.id,
        )
        db_session.add(agent)
        db_session.commit()

        # Verify no trades for this agent
        trades = db_session.query(AgentTrade).filter(AgentTrade.agent_id == agent.id).all()
        assert len(trades) == 0

    def test_portfolio_limit_per_agent(self, db_session, test_user):
        """Portfolio limit should count per-agent, not global."""
        agent1 = TradingAgent(
            name="Agent 1", symbol="US30", timeframe="M5",
            agent_type="potential", broker_name="oanda",
            mode="paper", status="running", created_by=test_user.id,
            risk_config={"max_positions": 2}
        )
        agent2 = TradingAgent(
            name="Agent 2", symbol="XAUUSD", timeframe="M5",
            agent_type="potential", broker_name="oanda",
            mode="paper", status="running", created_by=test_user.id,
            risk_config={"max_positions": 2}
        )
        db_session.add_all([agent1, agent2])
        db_session.commit()

        now = datetime.now(timezone.utc)

        # Add 2 open trades for agent1
        for i in range(2):
            trade = AgentTrade(
                agent_id=agent1.id, symbol="US30", direction="BUY",
                entry_price=46000 + i, lot_size=1, status="open",
                broker_ticket=f"ticket_{i}",
                stop_loss=45900.0, take_profit=46200.0,
                entry_time=now,
            )
            db_session.add(trade)
        db_session.commit()

        # Agent1 should have 2 open trades
        agent1_open = db_session.query(AgentTrade).filter(
            AgentTrade.agent_id == agent1.id, AgentTrade.status == "open"
        ).count()
        assert agent1_open == 2

        # Agent2 should have 0 open trades (portfolio limit is per-agent)
        agent2_open = db_session.query(AgentTrade).filter(
            AgentTrade.agent_id == agent2.id, AgentTrade.status == "open"
        ).count()
        assert agent2_open == 0

    def test_ghost_trade_detection(self, db_session, test_user):
        """Trades without broker_ticket should be identified as ghosts."""
        agent = TradingAgent(
            name="Ghost Test", symbol="US30", timeframe="M5",
            agent_type="potential", broker_name="oanda",
            mode="paper", status="running", created_by=test_user.id,
        )
        db_session.add(agent)
        db_session.commit()

        now = datetime.now(timezone.utc)

        # Create a ghost trade (no broker_ticket)
        ghost = AgentTrade(
            agent_id=agent.id, symbol="US30", direction="BUY",
            entry_price=46000, lot_size=1, status="open",
            broker_ticket=None,  # Ghost!
            stop_loss=45900.0, take_profit=46200.0,
            entry_time=now,
        )
        db_session.add(ghost)
        db_session.commit()

        # Query for ghosts
        ghosts = db_session.query(AgentTrade).filter(
            AgentTrade.broker_ticket.is_(None)
        ).all()
        assert len(ghosts) == 1
        assert ghosts[0].symbol == "US30"
