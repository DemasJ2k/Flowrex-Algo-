"""Unit tests for risk manager."""
from app.services.agent.risk_manager import RiskManager, PROP_FIRM_CONFIG


# ===================================================================
# Legacy tests (unchanged)
# ===================================================================

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


# ===================================================================
# New prop-firm tests
# ===================================================================

def _make_rm(**overrides) -> RiskManager:
    """Create a RiskManager with prop firm defaults and optional overrides."""
    return RiskManager(overrides)


# 1. Daily DD yellow tier reduces risk
def test_daily_dd_yellow_reduces_risk():
    rm = _make_rm()
    # Push daily P&L to -1.5% of 10k = -150
    rm._daily_pnl = -150.0
    rm._account_size = 10_000

    approved, risk_pct, reason = rm.approve_trade(
        symbol="US30", direction="long", confidence=0.7,
        atr=50.0, current_price=40000.0, hour_utc=14.0,
    )
    assert approved is True
    # Base 0.75% * 0.5 (yellow) = 0.375%
    assert risk_pct < PROP_FIRM_CONFIG["base_risk_per_trade_pct"]
    assert reason == "Trade approved"


# 2. Daily DD red tier blocks new trades
def test_daily_dd_red_blocks_trades():
    rm = _make_rm()
    rm._daily_pnl = -250.0  # -2.5%
    rm._account_size = 10_000

    approved, risk_pct, reason = rm.approve_trade(
        symbol="US30", direction="long", confidence=0.7,
        atr=50.0, current_price=40000.0, hour_utc=14.0,
    )
    assert approved is False
    assert "red" in reason.lower() or "DD" in reason


# 3. Daily DD hard stop triggers close-all
def test_daily_dd_hard_stop_close_all():
    rm = _make_rm()
    rm._daily_pnl = -300.0  # -3.0%
    rm._account_size = 10_000

    assert rm.should_close_all() is True

    approved, _, reason = rm.approve_trade(
        symbol="BTCUSD", direction="long", confidence=0.9,
        atr=500.0, current_price=60000.0, hour_utc=12.0,
    )
    assert approved is False
    assert "hard stop" in reason.lower()


# 4. Total DD tiers reduce risk progressively
def test_total_dd_tiers_reduce_risk():
    rm = _make_rm()
    rm._account_size = 10_000

    # Green -- full risk
    rm._total_pnl = 0.0
    rm._daily_pnl = 0.0
    _, risk_green, _ = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)

    # Caution (-2%) -- 0.67x
    rm._total_pnl = -200.0
    _, risk_caution, _ = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert risk_caution < risk_green

    # Warning (-4%) -- 0.50x
    rm._total_pnl = -400.0
    _, risk_warning, _ = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert risk_warning < risk_caution

    # Critical (-6%) -- 0.33x
    rm._total_pnl = -600.0
    _, risk_critical, _ = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert risk_critical < risk_warning

    # Emergency (-8%) -- blocked
    rm._total_pnl = -800.0
    approved, _, reason = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert approved is False
    assert "emergency" in reason.lower()


# 5. Anti-martingale reduces after 2 consecutive losses
def test_anti_martingale_reduces_after_2_losses():
    rm = _make_rm()
    rm.record_trade_result(-50.0)
    rm.record_trade_result(-50.0)  # 2 consecutive losses

    _, risk_after_2, _ = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)

    rm2 = _make_rm()
    _, risk_fresh, _ = rm2.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)

    assert risk_after_2 < risk_fresh


# 6. Anti-martingale resets on win
def test_anti_martingale_resets_on_win():
    rm = _make_rm()
    rm.record_trade_result(-50.0)
    rm.record_trade_result(-50.0)
    assert rm._consecutive_losses == 2

    rm.record_trade_result(100.0)  # win
    assert rm._consecutive_losses == 0

    mult = rm._anti_martingale_multiplier()
    assert mult == 1.0


# 7. Session window blocks US30 outside allowed windows
def test_session_blocks_us30_outside_window():
    rm = _make_rm()

    # Outside both windows
    approved, _, reason = rm.approve_trade("US30", "long", 0.7, 50, 40000, 10.0)
    assert approved is False
    assert "session" in reason.lower()

    # Inside primary window (14:00 UTC)
    approved, _, _ = rm.approve_trade("US30", "long", 0.7, 50, 40000, 14.0)
    assert approved is True

    # Inside secondary window (19:30 UTC)
    approved, _, _ = rm.approve_trade("US30", "long", 0.7, 50, 40000, 19.5)
    assert approved is True


# 8. Session window allows BTCUSD anytime
def test_session_allows_btcusd_anytime():
    rm = _make_rm()
    for hour in [0.0, 6.0, 12.0, 18.0, 23.5]:
        approved, _, _ = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, hour)
        assert approved is True, f"BTCUSD should be allowed at {hour} UTC"


# 9. Max trades per day blocks after 5 trades
def test_max_trades_per_day_blocks():
    rm = _make_rm()
    for _ in range(5):
        rm.record_trade_result(10.0)

    approved, _, reason = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert approved is False
    assert "Max trades" in reason


# 10. Max concurrent positions blocks at 2
def test_max_concurrent_positions_blocks():
    rm = _make_rm()
    rm.open_position()
    rm.open_position()

    approved, _, reason = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert approved is False
    assert "concurrent" in reason.lower()

    # Close one and it should allow again
    rm.close_position()
    approved, _, _ = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert approved is True


# 11. Position size calculation
def test_position_size_calculation():
    rm = _make_rm()
    # $10,000 balance, 0.75% risk = $75
    # SL = 50 points, point_value = $10/lot
    # lots = 75 / (50 * 10) = 0.15
    lots = rm.get_position_size(
        account_balance=10_000,
        risk_pct=0.0075,
        stop_loss_points=50,
        point_value=10,
    )
    assert abs(lots - 0.15) < 1e-9

    # Edge: zero SL
    assert rm.get_position_size(10_000, 0.0075, 0, 10) == 0.0
    # Edge: zero point value
    assert rm.get_position_size(10_000, 0.0075, 50, 0) == 0.0


# 12. Daily reset clears counters
def test_daily_reset_clears_counters():
    rm = _make_rm()
    rm.record_trade_result(-50.0)
    rm.record_trade_result(-50.0)
    rm.record_trade_result(-50.0)
    rm.open_position()

    assert rm._trades_today == 3
    assert rm._consecutive_losses == 3
    assert rm._daily_pnl < 0

    rm.daily_reset()

    assert rm._trades_today == 0
    assert rm._consecutive_losses == 0
    assert rm._daily_pnl == 0.0
    assert rm._daily_peak_pnl == 0.0
    # total_pnl should NOT reset
    assert rm._total_pnl < 0
    # concurrent_positions should NOT reset
    assert rm._concurrent_positions == 1


# 13. Daily profit protection
def test_daily_profit_protection():
    rm = _make_rm()

    # Build up profits above trail activation ($100 = 1%)
    rm.record_trade_result(120.0)  # daily_pnl = 120, peak = 120
    assert rm._daily_peak_pnl == 120.0

    # Trail floor = 120 * (1 - 0.50) = 60
    # Now record a loss that brings P&L below the floor
    rm.record_trade_result(-80.0)  # daily_pnl = 40, which is < 60

    approved, _, reason = rm.approve_trade("BTCUSD", "long", 0.7, 500, 60000, 12.0)
    assert approved is False
    assert "profit protection" in reason.lower()


# 14. Get status returns expected keys
def test_get_status_keys():
    rm = _make_rm()
    status = rm.get_status()
    expected_keys = {
        "daily_pnl", "daily_peak_pnl", "daily_dd_pct", "total_pnl",
        "total_dd_pct", "consecutive_losses", "trades_today",
        "concurrent_positions", "daily_tier", "total_tier",
        "anti_martingale_mult",
    }
    assert expected_keys.issubset(set(status.keys()))
    assert status["daily_tier"] == "GREEN"
    assert status["total_tier"] == "GREEN"


# 15. XAUUSD session enforcement
def test_session_xauusd():
    rm = _make_rm()
    # Outside both windows (3 AM UTC)
    approved, _, reason = rm.approve_trade("XAUUSD", "long", 0.7, 5, 2000, 3.0)
    assert approved is False
    assert "session" in reason.lower()

    # Inside primary (8 AM UTC)
    approved, _, _ = rm.approve_trade("XAUUSD", "long", 0.7, 5, 2000, 8.0)
    assert approved is True

    # Inside secondary (14 UTC)
    approved, _, _ = rm.approve_trade("XAUUSD", "long", 0.7, 5, 2000, 14.0)
    assert approved is True
