"""Tests for app.services.llm.monitoring.

Covers:
- execute_autonomous_actions enforces agent ownership + risk bounds
- Rate-limit dedup for errors and alerts
- Bounded rate-limit dict size
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm import monitoring


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Reset module-level rate-limit state before each test."""
    monitoring._error_rate_limit.clear()
    monitoring._alert_rate_limit.clear()
    yield
    monitoring._error_rate_limit.clear()
    monitoring._alert_rate_limit.clear()


class TestAlertDedup:
    def test_first_alert_not_rate_limited(self):
        assert monitoring._alert_rate_limited(1, 10, "loss_streak") is False

    def test_second_alert_same_kind_is_limited(self):
        monitoring._alert_rate_limited(1, 10, "loss_streak")
        assert monitoring._alert_rate_limited(1, 10, "loss_streak") is True

    def test_different_kind_not_limited(self):
        monitoring._alert_rate_limited(1, 10, "loss_streak")
        assert monitoring._alert_rate_limited(1, 10, "error_flood") is False

    def test_different_agent_not_limited(self):
        monitoring._alert_rate_limited(1, 10, "loss_streak")
        assert monitoring._alert_rate_limited(1, 11, "loss_streak") is False


class TestRateLimitBoundedSize:
    def test_cap_enforced(self):
        d = {}
        for i in range(monitoring._RATE_LIMIT_MAX_SIZE + 50):
            monitoring._rate_limit_set(d, (i,), datetime.now(timezone.utc))
        assert len(d) <= monitoring._RATE_LIMIT_MAX_SIZE

    def test_oldest_evicted_first(self):
        d = {}
        # Fill to just over cap with ascending timestamps
        base = datetime.now(timezone.utc)
        for i in range(monitoring._RATE_LIMIT_MAX_SIZE + 1):
            monitoring._rate_limit_set(d, (i,), base + timedelta(seconds=i))
        # Oldest key (0) should be evicted; newest (cap) should remain
        assert (0,) not in d
        assert (monitoring._RATE_LIMIT_MAX_SIZE,) in d

    def test_existing_key_update_doesnt_evict(self):
        d = {}
        base = datetime.now(timezone.utc)
        for i in range(monitoring._RATE_LIMIT_MAX_SIZE):
            monitoring._rate_limit_set(d, (i,), base + timedelta(seconds=i))
        # Update existing key — should not trigger eviction
        monitoring._rate_limit_set(d, (0,), base + timedelta(seconds=9999))
        assert (0,) in d
        assert len(d) == monitoring._RATE_LIMIT_MAX_SIZE


class TestExecuteAutonomousActions:
    @pytest.mark.asyncio
    async def test_no_actions_returns_empty(self):
        """If supervisor.parse_actions returns nothing, function returns []."""
        with patch.object(monitoring, "get_supervisor") as mock_get_sup:
            mock_sup = MagicMock()
            mock_sup.parse_actions.return_value = []
            mock_get_sup.return_value = mock_sup
            result = await monitoring.execute_autonomous_actions(user_id=1, response="no actions here")
            assert result == []

    @pytest.mark.asyncio
    async def test_action_for_other_users_agent_skipped(self):
        """Security: user 1 can't pause user 2's agent via AI."""
        action = {"action": "PAUSE_AGENT", "agent_id": 999, "reason": "test"}
        with patch.object(monitoring, "get_supervisor") as mock_get_sup:
            mock_sup = MagicMock()
            mock_sup.parse_actions.return_value = [action]
            mock_get_sup.return_value = mock_sup

            # Mock SessionLocal to return DB where agent 999 is owned by user 2, not user 1
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = None  # not found for user 1
            with patch.object(monitoring, "SessionLocal", return_value=mock_db):
                result = await monitoring.execute_autonomous_actions(user_id=1, response="{}")
                assert result == []  # skipped silently

    @pytest.mark.asyncio
    async def test_adjust_risk_out_of_bounds_rejected(self):
        """RiskManager enforces 0.001-0.02 — parse_actions should reject higher."""
        from app.services.llm.supervisor import LLMSupervisor, UserSession
        sup = LLMSupervisor()
        sess = sup.get_session(1)
        sess.autonomous = True
        sess.api_key = "test"
        # AI tries to set risk=0.5 (50%!) — out of bounds
        response = 'Recommending {"action":"ADJUST_RISK","agent_id":5,"risk_per_trade":0.5,"reason":"test"}'
        actions = sup.parse_actions(1, response)
        assert actions == []  # rejected

    @pytest.mark.asyncio
    async def test_adjust_risk_in_bounds_accepted(self):
        from app.services.llm.supervisor import LLMSupervisor
        sup = LLMSupervisor()
        sess = sup.get_session(1)
        sess.autonomous = True
        sess.api_key = "test"
        response = 'Recommending {"action":"ADJUST_RISK","agent_id":5,"risk_per_trade":0.005,"reason":"losses"}'
        actions = sup.parse_actions(1, response)
        assert len(actions) == 1
        assert actions[0]["action"] == "ADJUST_RISK"
        assert actions[0]["risk_per_trade"] == 0.005

    @pytest.mark.asyncio
    async def test_autonomous_disabled_returns_no_actions(self):
        """If user hasn't enabled autonomous mode, no actions are executed."""
        from app.services.llm.supervisor import LLMSupervisor
        sup = LLMSupervisor()
        sess = sup.get_session(1)
        sess.autonomous = False  # explicitly disabled
        sess.api_key = "test"
        response = '{"action":"PAUSE_AGENT","agent_id":5}'
        actions = sup.parse_actions(1, response)
        assert actions == []


class TestQuietHours:
    def test_inside_range_same_day(self):
        now = datetime(2026, 4, 20, 9, 30)  # 09:30 local
        assert monitoring._is_within_quiet_hours(now, "09:00", "10:00") is True

    def test_outside_range_same_day(self):
        now = datetime(2026, 4, 20, 11, 00)
        assert monitoring._is_within_quiet_hours(now, "09:00", "10:00") is False

    def test_wraps_midnight_midnight_hour(self):
        # 22:00 → 07:00 wrap; 00:30 should be inside
        now = datetime(2026, 4, 20, 0, 30)
        assert monitoring._is_within_quiet_hours(now, "22:00", "07:00") is True

    def test_wraps_midnight_evening_hour(self):
        now = datetime(2026, 4, 20, 23, 30)
        assert monitoring._is_within_quiet_hours(now, "22:00", "07:00") is True

    def test_wraps_midnight_outside_range(self):
        now = datetime(2026, 4, 20, 12, 00)
        assert monitoring._is_within_quiet_hours(now, "22:00", "07:00") is False

    def test_empty_strings_disabled(self):
        now = datetime(2026, 4, 20, 12, 00)
        assert monitoring._is_within_quiet_hours(now, None, None) is False
        assert monitoring._is_within_quiet_hours(now, "", "") is False


class TestFrequencyMinutes:
    def test_known_presets(self):
        assert monitoring._frequency_minutes("1h") == 60
        assert monitoring._frequency_minutes("4h") == 240
        assert monitoring._frequency_minutes("12h") == 720
        assert monitoring._frequency_minutes("daily") == 1440

    def test_off_returns_none(self):
        assert monitoring._frequency_minutes("off") is None

    def test_unknown_defaults_to_60(self):
        assert monitoring._frequency_minutes("never-heard-of") == 60


class TestStateHash:
    def test_identical_ctx_identical_hash(self):
        ctx = {
            "daily_summary": {"total_pnl": 12.345, "trade_count": 3},
            "open_positions": 1,
            "agents": [{"status": "running"}, {"status": "stopped"}],
            "recent_trades": [{"id": 99}],
        }
        assert monitoring._compute_state_hash(ctx) == monitoring._compute_state_hash(ctx)

    def test_pnl_rounding_stable_within_penny(self):
        a = {
            "daily_summary": {"total_pnl": 12.341, "trade_count": 3},
            "open_positions": 1, "agents": [], "recent_trades": [],
        }
        b = dict(a, daily_summary={"total_pnl": 12.344, "trade_count": 3})
        # Both round to 12.34 — hash stable within sub-penny noise.
        assert monitoring._compute_state_hash(a) == monitoring._compute_state_hash(b)

    def test_new_trade_changes_hash(self):
        a = {
            "daily_summary": {"total_pnl": 0, "trade_count": 3},
            "open_positions": 1, "agents": [], "recent_trades": [{"id": 1}],
        }
        b = dict(a, recent_trades=[{"id": 2}])
        assert monitoring._compute_state_hash(a) != monitoring._compute_state_hash(b)


class TestMonitoringConfigDefaults:
    def test_empty_gets_defaults(self):
        cfg = monitoring._load_monitoring_config({})
        assert cfg["enabled"] is True
        assert cfg["frequency"] == "1h"
        assert cfg["skip_when_markets_closed"] is True
        assert cfg["skip_when_unchanged"] is True
        assert cfg["quiet_hours_start"] is None

    def test_override_merges(self):
        cfg = monitoring._load_monitoring_config(
            {"monitoring": {"frequency": "4h", "quiet_hours_start": "22:00"}}
        )
        assert cfg["frequency"] == "4h"
        assert cfg["quiet_hours_start"] == "22:00"
        # unchanged keys retain defaults
        assert cfg["skip_when_unchanged"] is True
