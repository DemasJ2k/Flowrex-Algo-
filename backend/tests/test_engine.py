"""Integration tests for AlgoEngine lifecycle."""
import pytest
from app.services.agent.engine import AlgoEngine


@pytest.mark.asyncio
async def test_start_stop_lifecycle():
    engine = AlgoEngine()
    assert engine.get_running_agents() == []

    # Start should add to runners (even if agent doesn't exist in DB — it'll error internally)
    await engine.start_agent(999)
    assert 999 in engine.get_running_agents()

    await engine.stop_agent(999)
    assert 999 not in engine.get_running_agents()


@pytest.mark.asyncio
async def test_pause_resume():
    engine = AlgoEngine()
    await engine.start_agent(998)

    runner = engine._runners.get(998)
    assert runner is not None

    await engine.pause_agent(998)
    assert runner._paused is True

    await engine.resume_agent(998)
    assert runner._paused is False

    await engine.stop_agent(998)


@pytest.mark.asyncio
async def test_stop_all():
    engine = AlgoEngine()
    await engine.start_agent(100)
    await engine.start_agent(101)
    assert len(engine.get_running_agents()) == 2

    await engine.stop_all()
    assert len(engine.get_running_agents()) == 0


@pytest.mark.asyncio
async def test_double_start_idempotent():
    engine = AlgoEngine()
    await engine.start_agent(200)
    await engine.start_agent(200)  # Should not create duplicate
    assert engine.get_running_agents().count(200) == 1
    await engine.stop_all()
