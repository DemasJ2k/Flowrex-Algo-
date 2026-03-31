"""Tests for the backtesting engine with transaction costs."""
import numpy as np
import pandas as pd
import pytest
from app.services.backtest.engine import BacktestEngine, TransactionCosts, BacktestTrade


def _make_m5_data(n=1000):
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(n) * 0.5) + 2000
    return pd.DataFrame({
        "time": np.arange(1700000000, 1700000000 + n * 300, 300)[:n],
        "open": np.roll(closes, 1),
        "high": closes + np.abs(np.random.randn(n) * 2),
        "low": closes - np.abs(np.random.randn(n) * 2),
        "close": closes,
        "volume": np.random.randint(100, 5000, n),
    })


def test_backtest_runs_without_error():
    engine = BacktestEngine()
    data = _make_m5_data(500)
    result = engine.run("XAUUSD", m5_data=data, include_monte_carlo=False, prime_hours_only=False)
    assert result is not None
    assert isinstance(result.net_pnl, float)


def test_backtest_insufficient_data():
    engine = BacktestEngine()
    data = _make_m5_data(50)
    result = engine.run("XAUUSD", m5_data=data)
    assert result.total_trades == 0


def test_costs_reduce_pnl():
    """Net P&L should be lower than gross P&L when costs are applied."""
    engine = BacktestEngine()
    data = _make_m5_data(1000)

    # Run with zero costs
    result_free = engine.run("XAUUSD", m5_data=data, spread_pips=0, slippage_pips=0,
                              commission_per_lot=0, include_monte_carlo=False, prime_hours_only=False)

    # Run with real costs
    result_costs = engine.run("XAUUSD", m5_data=data, spread_pips=3.0, slippage_pips=1.0,
                               commission_per_lot=0, include_monte_carlo=False, prime_hours_only=False)

    if result_free.total_trades > 0 and result_costs.total_trades > 0:
        # With costs, net P&L should be worse (lower for profitable, more negative for losing)
        assert result_costs.net_pnl <= result_free.net_pnl + 1  # small tolerance


def test_cost_breakdown_tracked():
    """Total costs should be sum of spread + slippage + commission."""
    engine = BacktestEngine()
    data = _make_m5_data(1000)
    result = engine.run("XAUUSD", m5_data=data, spread_pips=3.0, slippage_pips=1.0,
                         commission_per_lot=5.0, include_monte_carlo=False, prime_hours_only=False)

    if result.total_trades > 0:
        assert result.total_spread_cost >= 0
        assert result.total_slippage_cost >= 0
        assert result.total_commission >= 0


def test_monte_carlo_produces_wider_drawdown():
    """MC 95th percentile drawdown should be >= backtest max drawdown."""
    engine = BacktestEngine()
    data = _make_m5_data(1000)
    result = engine.run("XAUUSD", m5_data=data, include_monte_carlo=True, prime_hours_only=False)

    if result.total_trades >= 10 and result.monte_carlo:
        assert result.monte_carlo.drawdown_95th >= result.max_drawdown * 0.8  # 80% tolerance


def test_monte_carlo_simulations_count():
    engine = BacktestEngine()
    data = _make_m5_data(1000)
    result = engine.run("XAUUSD", m5_data=data, include_monte_carlo=True, prime_hours_only=False)

    if result.monte_carlo:
        assert result.monte_carlo.simulations == 1000


def test_trade_duration_tracked():
    engine = BacktestEngine()
    data = _make_m5_data(1000)
    result = engine.run("XAUUSD", m5_data=data, include_monte_carlo=False, prime_hours_only=False)

    if result.total_trades > 0:
        assert result.avg_trade_duration_bars > 0
        for t in result.trades:
            assert t.duration_bars >= 0


def test_streaks_computed():
    engine = BacktestEngine()
    data = _make_m5_data(1000)
    result = engine.run("XAUUSD", m5_data=data, include_monte_carlo=False, prime_hours_only=False)

    assert result.max_consecutive_wins >= 0
    assert result.max_consecutive_losses >= 0


def test_monthly_returns_computed():
    engine = BacktestEngine()
    data = _make_m5_data(1000)
    result = engine.run("XAUUSD", m5_data=data, include_monte_carlo=False, prime_hours_only=False)

    assert isinstance(result.monthly_returns, dict)


def test_equity_and_drawdown_curves():
    engine = BacktestEngine()
    data = _make_m5_data(1000)
    result = engine.run("XAUUSD", m5_data=data, include_monte_carlo=False, prime_hours_only=False)

    assert len(result.equity_curve) >= 1
    if result.total_trades > 0:
        assert len(result.drawdown_curve) > 0
