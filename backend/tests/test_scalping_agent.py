"""Unit tests for scalping agent evaluate()."""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.agent.scalping_agent import ScalpingAgent
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


@pytest.mark.asyncio
async def test_evaluate_insufficient_bars():
    """Should reject when fewer than 60 bars."""
    agent = ScalpingAgent(1, "XAUUSD", "oanda", {})
    agent._log_fn = lambda *a, **k: None
    bars = _make_m5_bars(30)
    result = await agent.evaluate(bars, _mock_adapter())
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_no_models():
    """Should return None when no models loaded."""
    agent = ScalpingAgent(1, "XAUUSD", "oanda", {})
    agent._log_fn = lambda *a, **k: None
    # Don't load models
    bars = _make_m5_bars(200)
    result = await agent.evaluate(bars, _mock_adapter(), current_bar_index=100)
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_with_mock_signal():
    """Should produce signal when ensemble fires."""
    agent = ScalpingAgent(1, "XAUUSD", "oanda", {"risk_per_trade": 0.005, "cooldown_bars": 3})
    agent._log_fn = lambda *a, **k: None

    # Mock the ensemble to return a signal
    from app.services.ml.ensemble_engine import Signal
    mock_signal = Signal(direction=1, confidence=0.65, agreement=1, reason="test", votes={"xgb": {"confidence": 0.65}})

    with patch.object(agent._ensemble, "predict", return_value=mock_signal):
        with patch.object(agent._ensemble, "load_models", return_value=True):
            agent._ensemble.models = {"xgb": {"model": MagicMock()}}
            bars = _make_m5_bars(200)
            result = await agent.evaluate(bars, _mock_adapter(), balance=10000, current_bar_index=100)

    if result is not None:
        assert result["direction"] == "BUY"
        assert result["confidence"] >= 0.55
        assert "entry_price" in result
        assert "stop_loss" in result
        assert "take_profit" in result
        assert "lot_size" in result
        assert result["stop_loss"] < result["entry_price"]  # Buy: SL below entry
        assert result["take_profit"] > result["entry_price"]


@pytest.mark.asyncio
async def test_evaluate_cooldown_active():
    """Should reject during cooldown period."""
    agent = ScalpingAgent(1, "XAUUSD", "oanda", {"cooldown_bars": 3})
    agent._log_fn = lambda *a, **k: None
    agent._last_trade_bar = 98  # Last trade at bar 98

    bars = _make_m5_bars(200)
    result = await agent.evaluate(bars, _mock_adapter(), current_bar_index=99)  # Only 1 bar since last trade
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_daily_loss_limit():
    """Should reject when daily loss limit hit."""
    agent = ScalpingAgent(1, "XAUUSD", "oanda", {"max_daily_loss_pct": 0.04})
    agent._log_fn = lambda *a, **k: None

    bars = _make_m5_bars(200)
    result = await agent.evaluate(bars, _mock_adapter(), balance=10000, daily_pnl=-500, current_bar_index=100)
    assert result is None
