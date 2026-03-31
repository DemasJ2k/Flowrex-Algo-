"""Unit tests for risk manager."""
from app.services.agent.risk_manager import RiskManager


def test_trade_approved_normal():
    rm = RiskManager({"risk_per_trade": 0.005, "max_daily_loss_pct": 0.04, "cooldown_bars": 3})
    check = rm.check_trade(balance=10000, daily_pnl=0, daily_trade_count=0, bars_since_last_trade=10)
    assert check.approved is True
    assert check.risk_amount == 50.0  # 10000 * 0.005


def test_trade_rejected_daily_loss():
    rm = RiskManager({"max_daily_loss_pct": 0.04})
    check = rm.check_trade(balance=10000, daily_pnl=-500, daily_trade_count=0, bars_since_last_trade=10)
    assert check.approved is False
    assert "Daily loss limit" in check.reason


def test_trade_rejected_cooldown():
    rm = RiskManager({"cooldown_bars": 3})
    check = rm.check_trade(balance=10000, daily_pnl=0, daily_trade_count=0, bars_since_last_trade=1)
    assert check.approved is False
    assert "Cooldown" in check.reason


def test_trade_rejected_trade_count():
    rm = RiskManager({"max_trades_per_day": 5})
    check = rm.check_trade(balance=10000, daily_pnl=0, daily_trade_count=5, bars_since_last_trade=10)
    assert check.approved is False
    assert "trade limit" in check.reason


def test_trade_rejected_zero_balance():
    rm = RiskManager()
    check = rm.check_trade(balance=0, daily_pnl=0, daily_trade_count=0, bars_since_last_trade=10)
    assert check.approved is False


def test_session_multiplier_applied():
    rm = RiskManager({"risk_per_trade": 0.01})
    check = rm.check_trade(balance=10000, daily_pnl=0, daily_trade_count=0, bars_since_last_trade=10, session_multiplier=0.5)
    assert check.approved is True
    assert check.risk_amount == 50.0  # 10000 * 0.01 * 0.5
