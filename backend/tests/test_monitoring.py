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
