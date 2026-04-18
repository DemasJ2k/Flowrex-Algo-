"""
Tests for config hot-reload completeness (Batch 3 fixes).

These tests would have caught the bugs we found in the audit:
- C2: reload_agent_config didn't reload max_lot_size or sizing_mode
- H1: risk_per_trade default was 0.01 (1%) when DB had 0.001 (0.10%)
- The 2026-04-15 incident where agent config edits had no effect on running agents
"""
import pytest
from unittest.mock import patch, MagicMock

from app.services.agent.engine import AlgoEngine
from app.services.agent.potential_agent import PotentialAgent
from app.services.agent.flowrex_agent_v2 import FlowrexAgentV2


def test_potential_agent_default_risk_is_0_001_not_0_01():
    """
    REGRESSION: H1 — the default for risk_per_trade in PotentialAgent.__init__
    must be 0.001 (0.10%), not 0.01 (1.00%). The old default would silently 10x
    position sizing if config was missing the key.
    """
    config = {}  # empty — should trigger default
    agent = PotentialAgent(agent_id=1, symbol="XAUUSD", broker_name="fake", config=config)
    assert agent.risk_config["risk_per_trade_pct"] == 0.001, \
        f"Expected 0.001 default, got {agent.risk_config['risk_per_trade_pct']}"


def test_potential_agent_uses_config_value_not_default():
    """When risk_per_trade IS in config, use it (not the default)."""
    agent = PotentialAgent(
        agent_id=1, symbol="XAUUSD", broker_name="fake",
        config={"risk_per_trade": 0.005}
    )
    assert agent.risk_config["risk_per_trade_pct"] == 0.005


def test_flowrex_v2_default_risk_is_0_001_not_0_01():
    """REGRESSION: H1 — same bug, same fix on FlowrexAgentV2."""
    agent = FlowrexAgentV2(agent_id=1, symbol="US30", broker_name="fake", config={})
    assert agent.risk_config["risk_per_trade_pct"] == 0.001


def test_flowrex_v2_uses_config_value_not_default():
    agent = FlowrexAgentV2(
        agent_id=1, symbol="US30", broker_name="fake",
        config={"risk_per_trade": 0.0075}
    )
    assert agent.risk_config["risk_per_trade_pct"] == 0.0075


def test_potential_agent_max_lot_size_in_config():
    """max_lot_size is stored in self.config and read at sizing time."""
    agent = PotentialAgent(
        agent_id=1, symbol="XAUUSD", broker_name="fake",
        config={"max_lot_size": 5, "sizing_mode": "risk_pct"}
    )
    assert agent.config.get("max_lot_size") == 5
    assert agent.config.get("sizing_mode") == "risk_pct"


def test_reload_agent_config_updates_in_place():
    """
    REGRESSION: C2 — reload_agent_config must reload risk_per_trade and the
    other risk config fields on a running agent.
    """
    engine = AlgoEngine()

    # Create a runner with a fake agent attached
    from app.services.agent.engine import AgentRunner
    runner = AgentRunner(agent_id=42)
    runner._agent = PotentialAgent(
        agent_id=42, symbol="XAUUSD", broker_name="fake",
        config={"risk_per_trade": 0.01, "max_daily_loss_pct": 0.05}
    )
    engine._runners[42] = runner

    # Mock the DB call inside reload_agent_config
    fake_record = MagicMock()
    fake_record.risk_config = {
        "risk_per_trade": 0.002,
        "max_daily_loss_pct": 0.03,
        "cooldown_bars": 7,
        "sizing_mode": "max_lots",
        "max_lot_size": 3,
    }
    fake_record.timeframe = "M5"

    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = fake_record

    with patch("app.services.agent.engine.SessionLocal", return_value=fake_session):
        ok = engine.reload_agent_config(42)
        assert ok is True

    # Verify the agent picked up the new values
    agent = runner._agent
    assert agent.risk_config["risk_per_trade_pct"] == 0.002
    assert agent.risk_config["daily_loss_limit_pct"] == 0.03
    assert agent.cooldown_bars == 7
    assert agent.config.get("sizing_mode") == "max_lots"
    assert agent.config.get("max_lot_size") == 3


def test_reload_agent_config_returns_false_for_unknown_agent():
    engine = AlgoEngine()
    assert engine.reload_agent_config(99999) is False


def test_reload_models_for_symbol_calls_load_for_v2_agent():
    """
    REGRESSION: C3 — reload_models_for_symbol used to only support legacy
    FlowrexAgent (which had _ensemble_scalping). Now it must call agent.load()
    on FlowrexAgentV2 and PotentialAgent.
    """
    engine = AlgoEngine()
    from app.services.agent.engine import AgentRunner

    # Mock a v2 agent — override load() to track calls
    agent = FlowrexAgentV2(agent_id=1, symbol="US30", broker_name="fake", config={})
    load_call_count = [0]
    def mock_load():
        load_call_count[0] += 1
        return True
    agent.load = mock_load
    agent._log_fn = lambda level, msg, data=None: None

    runner = AgentRunner(agent_id=1)
    runner._agent = agent
    engine._runners[1] = runner

    reloaded = engine.reload_models_for_symbol("US30")
    assert reloaded == 1
    assert load_call_count[0] == 1


def test_reload_models_for_symbol_skips_other_symbols():
    """Only reload models for agents matching the requested symbol."""
    engine = AlgoEngine()
    from app.services.agent.engine import AgentRunner

    us30_agent = FlowrexAgentV2(agent_id=1, symbol="US30", broker_name="fake", config={})
    us30_load_called = []
    us30_agent.load = lambda: (us30_load_called.append(1), True)[1]
    us30_agent._log_fn = lambda *a, **k: None

    btc_agent = FlowrexAgentV2(agent_id=2, symbol="BTCUSD", broker_name="fake", config={})
    btc_load_called = []
    btc_agent.load = lambda: (btc_load_called.append(1), True)[1]
    btc_agent._log_fn = lambda *a, **k: None

    r1 = AgentRunner(agent_id=1); r1._agent = us30_agent
    r2 = AgentRunner(agent_id=2); r2._agent = btc_agent
    engine._runners[1] = r1
    engine._runners[2] = r2

    reloaded = engine.reload_models_for_symbol("US30")
    assert reloaded == 1  # only us30
    assert len(us30_load_called) == 1
    assert len(btc_load_called) == 0
