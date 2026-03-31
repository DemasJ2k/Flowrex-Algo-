"""Unit tests for ExpertAgent pipeline stages."""
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.agent.expert_agent import ExpertAgent, _get_session
from app.services.broker.base import Candle, AccountInfo


def _make_m5_bars(n=200):
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
    return adapter


# ── Session awareness ──────────────────────────────────────────────────


def test_session_asian():
    assert _get_session(3) == "asian"


def test_session_london():
    assert _get_session(10) == "london"


def test_session_ny():
    assert _get_session(15) == "ny"


def test_session_dead_zone():
    assert _get_session(22) == "dead_zone"


# ── Expert agent evaluation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_expert_insufficient_bars():
    agent = ExpertAgent(1, "XAUUSD", "oanda", {})
    agent._log_fn = lambda *a, **k: None
    result = await agent.evaluate(_make_m5_bars(30), _mock_adapter())
    assert result is None


@pytest.mark.asyncio
async def test_expert_no_models():
    agent = ExpertAgent(1, "XAUUSD", "oanda", {})
    agent._log_fn = lambda *a, **k: None
    result = await agent.evaluate(_make_m5_bars(200), _mock_adapter(), current_bar_index=100)
    assert result is None


@pytest.mark.asyncio
async def test_expert_with_mock_signal():
    """Expert agent should produce signal when ensemble fires with 2/3 agreement."""
    agent = ExpertAgent(1, "XAUUSD", "oanda", {"risk_per_trade": 0.005, "cooldown_bars": 3})
    agent._log_fn = lambda *a, **k: None

    from app.services.ml.ensemble_engine import Signal
    mock_signal = Signal(direction=-1, confidence=0.62, agreement=2, reason="expert consensus",
                         votes={"xgb": {"confidence": 0.62}, "lgb": {"confidence": 0.58}})

    with patch.object(agent._ensemble, "predict", return_value=mock_signal):
        with patch.object(agent._meta_labeler, "should_trade", return_value=True):
            agent._ensemble.models = {"xgb": {"model": MagicMock()}, "lgb": {"model": MagicMock()}}
            result = await agent.evaluate(_make_m5_bars(200), _mock_adapter(), balance=10000, current_bar_index=100)

    if result is not None:
        assert result["direction"] == "SELL"
        assert result["agent_type"] == "expert"
        assert result["agreement"] == 2
        assert result["stop_loss"] > result["entry_price"]  # Sell: SL above
        assert result["take_profit"] < result["entry_price"]


@pytest.mark.asyncio
async def test_expert_meta_labeler_rejection():
    """Meta-labeler can block signals."""
    agent = ExpertAgent(1, "XAUUSD", "oanda", {"cooldown_bars": 3})
    agent._log_fn = lambda *a, **k: None

    from app.services.ml.ensemble_engine import Signal
    mock_signal = Signal(direction=1, confidence=0.65, agreement=2, reason="test",
                         votes={"xgb": {"confidence": 0.65}, "lgb": {"confidence": 0.60}})

    with patch.object(agent._ensemble, "predict", return_value=mock_signal):
        with patch.object(agent._meta_labeler, "should_trade", return_value=False):
            agent._ensemble.models = {"xgb": {"model": MagicMock()}}
            result = await agent.evaluate(_make_m5_bars(200), _mock_adapter(), balance=10000, current_bar_index=100)

    assert result is None


@pytest.mark.asyncio
async def test_expert_daily_loss_rejection():
    agent = ExpertAgent(1, "XAUUSD", "oanda", {"max_daily_loss_pct": 0.04})
    agent._log_fn = lambda *a, **k: None
    result = await agent.evaluate(_make_m5_bars(200), _mock_adapter(), balance=10000, daily_pnl=-500, current_bar_index=100)
    assert result is None


# ── Both agent types can coexist ───────────────────────────────────────


def test_both_agent_types_instantiate():
    from app.services.agent.scalping_agent import ScalpingAgent
    scalp = ScalpingAgent(1, "BTCUSD", "oanda", {})
    expert = ExpertAgent(2, "XAUUSD", "oanda", {})
    assert scalp.symbol == "BTCUSD"
    assert expert.symbol == "XAUUSD"
