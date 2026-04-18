"""
Tests for the agent lifecycle API — covers fixes from Batch 1.

- POST /agents/{id}/resume now exists (was missing — agents could pause but never resume)
- DELETE /agents/{id} now stops the runner first (was just setting deleted_at)
- _poll_and_evaluate_inner now checks deleted_at (deleted agents stay alive issue)
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.models.agent import TradingAgent
from app.services.agent.engine import AlgoEngine, AgentRunner


def test_resume_endpoint_exists(client_with_broker, db_session, test_user):
    """REGRESSION: C24 — /resume endpoint was missing entirely."""
    # Create an agent
    agent = TradingAgent(
        created_by=test_user.id,
        name="Test", symbol="XAUUSD",
        agent_type="potential", broker_name="fake",
        status="paused",
        risk_config={"risk_per_trade": 0.001},
    )
    db_session.add(agent)
    db_session.commit()

    # Patch in app.api.agent (the import site), not at the source
    with patch("app.api.agent.get_algo_engine") as mock_engine_factory:
        mock_engine = MagicMock()
        mock_engine.is_running.return_value = True
        mock_engine.resume_agent = AsyncMock()
        mock_engine.start_agent = AsyncMock()
        mock_engine_factory.return_value = mock_engine

        r = client_with_broker.post(f"/api/agents/{agent.id}/resume")
        assert r.status_code == 200
        assert r.json()["status"] == "running"

    db_session.refresh(agent)
    assert agent.status == "running"


def test_delete_endpoint_stops_runner_first(client_with_broker, db_session, test_user):
    """REGRESSION: C25 — delete used to just set deleted_at, leaving the
    runner alive and trading."""
    agent = TradingAgent(
        created_by=test_user.id,
        name="DelMe", symbol="BTCUSD",
        agent_type="potential", broker_name="fake",
        status="running",
        risk_config={"risk_per_trade": 0.001},
    )
    db_session.add(agent)
    db_session.commit()

    stop_calls = []

    class MockEngine:
        def is_running(self, agent_id):
            return True
        async def stop_agent(self, agent_id):
            stop_calls.append(agent_id)

    with patch("app.api.agent.get_algo_engine", return_value=MockEngine()):
        r = client_with_broker.delete(f"/api/agents/{agent.id}")
        assert r.status_code == 200

    db_session.refresh(agent)
    assert agent.deleted_at is not None
    assert agent.status == "stopped"
    assert agent.id in stop_calls, "delete must call engine.stop_agent before marking deleted"


def test_delete_handles_already_stopped_agent(client_with_broker, db_session, test_user):
    """If the agent isn't running, delete still succeeds without erroring."""
    agent = TradingAgent(
        created_by=test_user.id,
        name="AlreadyStopped", symbol="US30",
        agent_type="potential", broker_name="fake",
        status="stopped",
        risk_config={"risk_per_trade": 0.001},
    )
    db_session.add(agent)
    db_session.commit()

    class MockEngine:
        def is_running(self, agent_id):
            return False
        async def stop_agent(self, agent_id):
            raise AssertionError("should not be called for stopped agent")

    with patch("app.api.agent.get_algo_engine", return_value=MockEngine()):
        r = client_with_broker.delete(f"/api/agents/{agent.id}")
        assert r.status_code == 200

    db_session.refresh(agent)
    assert agent.deleted_at is not None


def test_pause_endpoint_works(client_with_broker, db_session, test_user):
    agent = TradingAgent(
        created_by=test_user.id,
        name="Pausable", symbol="XAUUSD",
        agent_type="potential", broker_name="fake",
        status="running",
        risk_config={"risk_per_trade": 0.001},
    )
    db_session.add(agent)
    db_session.commit()

    class MockEngine:
        async def pause_agent(self, agent_id):
            pass

    with patch("app.api.agent.get_algo_engine", return_value=MockEngine()):
        r = client_with_broker.post(f"/api/agents/{agent.id}/pause")
        assert r.status_code == 200
        assert r.json()["status"] == "paused"

    db_session.refresh(agent)
    assert agent.status == "paused"
