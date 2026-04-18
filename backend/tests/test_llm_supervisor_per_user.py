"""
Tests for the LLM supervisor per-user session refactor (Batch 4, audit C23).

Pre-fix, LLMSupervisor was a module-level singleton with shared
self._conversation. User A's chat history would leak into User B's prompts.
"""
import pytest

from app.services.llm.supervisor import LLMSupervisor, get_supervisor


def test_sessions_are_isolated_per_user():
    """REGRESSION: C23 — user A and user B must not share conversation state."""
    sup = LLMSupervisor()
    sup.configure(user_id=1, api_key="key-A", model="haiku", enabled=True)
    sup.configure(user_id=2, api_key="key-B", model="sonnet", enabled=True)

    sess_a = sup.get_session(1)
    sess_b = sup.get_session(2)

    assert sess_a is not sess_b
    assert sess_a.api_key == "key-A"
    assert sess_b.api_key == "key-B"
    assert sess_a.model != sess_b.model
    assert sess_a.conversation is not sess_b.conversation


def test_clear_history_only_clears_one_user():
    sup = LLMSupervisor()
    sup.configure(user_id=1, api_key="k1", enabled=True)
    sup.configure(user_id=2, api_key="k2", enabled=True)

    sup._sessions[1].conversation.append({"role": "user", "content": "hi from A"})
    sup._sessions[2].conversation.append({"role": "user", "content": "hi from B"})

    sup.clear_history(1)
    assert sup._sessions[1].conversation == []
    assert sup._sessions[2].conversation != []


def test_is_enabled_for_isolation():
    sup = LLMSupervisor()
    sup.configure(user_id=1, api_key="k1", enabled=True)
    sup.configure(user_id=2, api_key="", enabled=True)  # no key — disabled

    assert sup.is_enabled_for(1) is True
    assert sup.is_enabled_for(2) is False
    assert sup.is_enabled_for(999) is False  # nonexistent user


def test_consecutive_losses_per_user():
    """Loss tracking is per-user-per-agent."""
    sup = LLMSupervisor()
    sup.configure(user_id=1, api_key="k1", enabled=True)
    sup.configure(user_id=2, api_key="k2", enabled=True)

    sup.get_session(1).consecutive_losses[100] = 3
    sup.get_session(2).consecutive_losses[100] = 0

    assert sup.get_session(1).consecutive_losses[100] == 3
    assert sup.get_session(2).consecutive_losses[100] == 0


def test_parse_actions_requires_autonomous_flag():
    sup = LLMSupervisor()
    sup.configure(user_id=1, api_key="k1", enabled=True, autonomous=False)

    response = '{"action": "PAUSE_AGENT", "agent_id": 5, "reason": "test"}'
    actions = sup.parse_actions(1, response)
    assert actions == [], "actions must not be parsed when autonomous=False"


def test_parse_actions_enforces_risk_bounds():
    """REGRESSION: C27 — risk_per_trade out of bounds must be rejected."""
    sup = LLMSupervisor()
    sup.configure(user_id=1, api_key="k1", enabled=True, autonomous=True)

    # Way too high
    actions = sup.parse_actions(
        1,
        '{"action": "ADJUST_RISK", "agent_id": 5, "risk_per_trade": 5.0}'
    )
    assert actions == [], "risk_per_trade=5.0 must be rejected"

    # Way too low
    actions = sup.parse_actions(
        1,
        '{"action": "ADJUST_RISK", "agent_id": 5, "risk_per_trade": 0.0001}'
    )
    assert actions == [], "risk_per_trade=0.0001 must be rejected"

    # Valid
    actions = sup.parse_actions(
        1,
        '{"action": "ADJUST_RISK", "agent_id": 5, "risk_per_trade": 0.005}'
    )
    assert len(actions) == 1
    assert actions[0]["risk_per_trade"] == 0.005


def test_parse_actions_max_one_per_response():
    """REGRESSION: C27 — at most 1 action per response."""
    sup = LLMSupervisor()
    sup.configure(user_id=1, api_key="k1", enabled=True, autonomous=True)

    multi = (
        '{"action": "PAUSE_AGENT", "agent_id": 1, "reason": "x"} '
        '{"action": "PAUSE_AGENT", "agent_id": 2, "reason": "y"} '
        '{"action": "PAUSE_AGENT", "agent_id": 3, "reason": "z"}'
    )
    actions = sup.parse_actions(1, multi)
    assert len(actions) == 1


def test_get_supervisor_returns_singleton():
    """The module-level singleton wraps multiple per-user sessions."""
    s1 = get_supervisor()
    s2 = get_supervisor()
    assert s1 is s2
