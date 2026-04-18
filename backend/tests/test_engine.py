"""
Integration tests for AlgoEngine lifecycle.

Fixed in Batch 12 (2026-04-16): these tests previously failed because the
engine's internal SessionLocal() created its own DB connection that bypassed
the test's in-memory SQLite. Now we patch SessionLocal to use the test session.
"""
import pytest
from unittest.mock import patch

from app.services.agent.engine import AlgoEngine


@pytest.fixture()
def patched_engine(db_session):
    """AlgoEngine with SessionLocal patched to use the test's in-memory SQLite."""
    from tests.conftest import TestingSessionLocal

    with patch("app.services.agent.engine.SessionLocal", TestingSessionLocal):
        engine = AlgoEngine()
        yield engine
        # Cleanup: stop all agents so background tasks don't leak
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(engine.stop_all())
        else:
            asyncio.run(engine.stop_all())


@pytest.mark.asyncio
async def test_start_stop_lifecycle(patched_engine):
    engine = patched_engine
    assert engine.get_running_agents() == []

    # Start with a non-existent agent_id — the runner will log an error
    # ("Agent not found in DB") but still appear in the runners dict briefly.
    await engine.start_agent(999)

    # The runner was added (even though the agent didn't exist — it logged an error)
    # It should now be stoppable without crashing.
    await engine.stop_agent(999)
    assert 999 not in engine.get_running_agents()


@pytest.mark.asyncio
async def test_pause_resume(patched_engine):
    engine = patched_engine
    await engine.start_agent(998)

    runner = engine._runners.get(998)
    assert runner is not None

    await engine.pause_agent(998)
    assert runner._paused is True

    await engine.resume_agent(998)
    assert runner._paused is False

    await engine.stop_agent(998)


@pytest.mark.asyncio
async def test_stop_all(patched_engine):
    engine = patched_engine
    await engine.start_agent(100)
    await engine.start_agent(101)
    assert len(engine.get_running_agents()) == 2

    await engine.stop_all()
    assert len(engine.get_running_agents()) == 0


@pytest.mark.asyncio
async def test_double_start_idempotent(patched_engine):
    engine = patched_engine
    await engine.start_agent(200)
    await engine.start_agent(200)  # Should not create duplicate
    assert engine.get_running_agents().count(200) == 1
    await engine.stop_all()
