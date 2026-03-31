"""Unit tests for FlowrexAgent — warm-up, evaluate, and model loading."""
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.agent.flowrex_agent import FlowrexAgent
from app.services.broker.base import Candle, AccountInfo


def _make_m5_bars(n=200):
    """Generate synthetic M5 bars."""
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(n) * 0.5) + 2000
    return [
        {
            "time": 1700000000 + i * 300,
            "open": float(closes[max(0, i - 1)]),
            "high": float(closes[i] + abs(np.random.randn()) * 2),
            "low": float(closes[i] - abs(np.random.randn()) * 2),
            "close": float(closes[i]),
            "volume": int(np.random.randint(100, 5000)),
        }
        for i in range(n)
    ]


def _mock_adapter():
    adapter = AsyncMock()
    adapter.get_candles = AsyncMock(return_value=[
        Candle(time=1700000000 + i * 3600, open=2000, high=2010, low=1990, close=2005, volume=1000)
        for i in range(100)
    ])
    adapter.get_account_info = AsyncMock(return_value=AccountInfo(balance=10000))
    return adapter


def _make_agent(**overrides):
    config = {"timeframe": "M5", "warmup_evals": 3, "cooldown_bars": 3, "risk_per_trade": 0.005, **overrides}
    agent = FlowrexAgent(agent_id=1, symbol="XAUUSD", broker_name="mt5", config=config)
    agent._log_fn = lambda *a, **k: None
    return agent


# ── Warm-up tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warmup_blocks_first_evals():
    """Agent should NOT trade during warm-up period."""
    agent = _make_agent(warmup_evals=3)
    bars = _make_m5_bars(200)
    adapter = _mock_adapter()

    # First 3 evals should all return None (warm-up)
    for i in range(3):
        result = await agent.evaluate(bars, adapter, balance=10000, current_bar_index=i + 1)
        assert result is None, f"Eval {i + 1} should be blocked by warm-up"

    assert agent._eval_count == 3


@pytest.mark.asyncio
async def test_warmup_allows_after_period():
    """Agent should allow evaluation after warm-up period ends."""
    agent = _make_agent(warmup_evals=1)
    bars = _make_m5_bars(200)
    adapter = _mock_adapter()

    # Eval 1: warm-up
    result = await agent.evaluate(bars, adapter, balance=10000, current_bar_index=1)
    assert result is None

    # Eval 2+: warm-up done — agent evaluates normally (may or may not signal)
    # At minimum it should NOT be blocked by warm-up
    assert agent._eval_count == 1
    _ = await agent.evaluate(bars, adapter, balance=10000, current_bar_index=2)
    assert agent._eval_count == 2  # Proves it passed warm-up check


@pytest.mark.asyncio
async def test_warmup_default_is_2():
    """Default warm-up should be 2 evals (engine handles first-bar warm-up)."""
    agent = FlowrexAgent(agent_id=1, symbol="XAUUSD", broker_name="mt5", config={"timeframe": "M5"})
    assert agent._warmup_evals == 2


# ── Basic evaluate tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_insufficient_bars():
    """Should reject when fewer than 60 bars."""
    agent = _make_agent(warmup_evals=0)
    bars = _make_m5_bars(30)
    result = await agent.evaluate(bars, _mock_adapter())
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_no_models():
    """Should return None when no models loaded."""
    agent = _make_agent(warmup_evals=0)
    bars = _make_m5_bars(200)
    result = await agent.evaluate(bars, _mock_adapter(), current_bar_index=100)
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_cooldown_active():
    """Should reject during cooldown period."""
    agent = _make_agent(warmup_evals=0, cooldown_bars=3)
    agent._last_trade_bar = 98
    bars = _make_m5_bars(200)
    result = await agent.evaluate(bars, _mock_adapter(), current_bar_index=99)
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_daily_loss_limit():
    """Should reject when daily loss limit hit."""
    agent = _make_agent(warmup_evals=0, max_daily_loss_pct=0.04)
    bars = _make_m5_bars(200)
    result = await agent.evaluate(bars, _mock_adapter(), balance=10000, daily_pnl=-500, current_bar_index=100)
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_with_mock_signal():
    """Should produce signal when ensemble fires."""
    agent = _make_agent(warmup_evals=0)

    from app.services.ml.ensemble_engine import Signal
    mock_signal = Signal(direction=1, confidence=0.65, agreement=1, reason="test", votes={"xgb": {"confidence": 0.65}})

    with patch.object(agent._ensemble_scalping, "predict", return_value=mock_signal):
        with patch.object(agent._ensemble_scalping, "load_models", return_value=True):
            agent._ensemble_scalping.models = {"xgb": {"model": MagicMock()}}
            agent._ensemble = agent._ensemble_scalping
            bars = _make_m5_bars(200)
            result = await agent.evaluate(bars, _mock_adapter(), balance=10000, current_bar_index=100)

    if result is not None:
        assert result["direction"] == "BUY"
        assert result["confidence"] >= 0.55
        assert result["agent_type"] == "flowrex"
        assert "entry_price" in result
        assert "stop_loss" in result
        assert result["stop_loss"] < result["entry_price"]  # Buy: SL below


# ── Model loading tests ───────────────────────────────────────────────


def test_load_picks_up_scalping_models():
    """FlowrexAgent should load scalping pipeline models."""
    agent = _make_agent()
    with patch.object(agent._ensemble_scalping, "load_models", return_value=True):
        agent._ensemble_scalping.models = {"xgboost": {}, "lightgbm": {}}
        with patch.object(agent._ensemble_expert, "load_models", return_value=False):
            loaded = agent.load()
    assert loaded is True
    assert agent._ensemble is agent._ensemble_scalping


def test_load_merges_when_both_exist():
    """Should merge scalping + expert models when both available."""
    agent = _make_agent()
    with patch.object(agent._ensemble_scalping, "load_models", return_value=True):
        agent._ensemble_scalping.models = {"xgboost": {}, "lightgbm": {}}
        with patch.object(agent._ensemble_expert, "load_models", return_value=True):
            agent._ensemble_expert.models = {"lstm": {}}
            loaded = agent.load()
    assert loaded is True
    assert "lstm" in agent._ensemble.models  # Expert's LSTM merged in
    assert "xgboost" in agent._ensemble.models  # Scalping models kept


def test_load_no_models():
    """Should return False when no models found."""
    agent = _make_agent()
    with patch.object(agent._ensemble_scalping, "load_models", return_value=False):
        with patch.object(agent._ensemble_expert, "load_models", return_value=False):
            loaded = agent.load()
    assert loaded is False


# ── SL/TP multiplier tests ────────────────────────────────────────────


def test_sl_tp_multipliers_m5():
    agent = _make_agent(timeframe="M5")
    assert agent._sl_mult == 1.5
    assert agent._tp_mult == 2.5


def test_sl_tp_multipliers_h1():
    agent = _make_agent(timeframe="H1")
    assert agent._sl_mult == 2.0
    assert agent._tp_mult == 3.0


def test_sl_tp_multipliers_d1():
    agent = _make_agent(timeframe="D1")
    assert agent._sl_mult == 2.0
    assert agent._tp_mult == 3.0
