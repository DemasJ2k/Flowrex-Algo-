"""Tests for the orphan-detection fallback added in commit 8ab8fa5.

The reconciler receives broker positions (whose IDs may be AGGREGATE
identifiers like "XAU_USD:long" for Oanda) and compares them against
DB AgentTrade rows (whose broker_ticket stores per-trade IDs like "393").
Without a fallback, every Oanda reconcile falsely flags its own
position as an orphan.

The fix is two-layer:
  1. Exact broker_ticket match (cTrader / Tradovate per-trade IDs).
  2. (symbol, direction) fallback across ALL agents for this user.

These tests exercise the matching logic at the data-structure level —
they don't instantiate AgentRunner (that needs async + DB + broker).
Instead they verify the set-operations that power the orphan decision.
"""
import pytest


# ── Re-implementation of the matching logic for isolated testing ──────
#
# The engine.py / main.py reconcile logic is structured so that a
# broker position p is NOT an orphan when:
#   ticket = str(p.id)
#   is_exact_match = ticket and ticket in db_tickets
#   is_direction_match = (p.symbol, p.direction.upper()) in db_directions
#   orphan = not (is_exact_match or is_direction_match)
#
# These helpers mirror that directly.

def _is_orphan(position_id: str, position_symbol: str, position_direction: str,
               db_tickets: set, db_directions: set) -> bool:
    """Return True iff the broker position looks orphaned vs DB state."""
    ticket = str(position_id) if position_id else ""
    pos_key = (position_symbol, (position_direction or "").upper())
    if ticket and ticket in db_tickets:
        return False
    if pos_key in db_directions:
        return False
    return True


# ── Exact ticket match (cTrader / Tradovate path) ──────────────────────

def test_exact_ticket_match_not_orphan():
    """Broker returns per-trade ID that exists in DB → not orphan."""
    db_tickets = {"T12345", "T67890"}
    db_directions = {("BTCUSD", "BUY")}
    result = _is_orphan(
        position_id="T12345", position_symbol="BTCUSD",
        position_direction="BUY",
        db_tickets=db_tickets, db_directions=db_directions,
    )
    assert result is False


def test_exact_ticket_miss_but_direction_match_not_orphan():
    """Broker's aggregate ID doesn't match any db_ticket, but sibling
    agent has an open BUY BTCUSD → fallback catches it."""
    db_tickets = {"393", "401"}
    db_directions = {("BTCUSD", "BUY")}
    result = _is_orphan(
        position_id="BTC_USD:long",  # Oanda aggregate format
        position_symbol="BTCUSD",
        position_direction="BUY",
        db_tickets=db_tickets, db_directions=db_directions,
    )
    assert result is False, "Symbol+direction fallback should have matched"


def test_no_match_is_orphan():
    """Both layers miss → genuinely orphaned position."""
    db_tickets = {"393"}
    db_directions = {("XAUUSD", "BUY")}
    result = _is_orphan(
        position_id="T99999", position_symbol="BTCUSD",
        position_direction="BUY",
        db_tickets=db_tickets, db_directions=db_directions,
    )
    assert result is True


def test_direction_case_insensitive():
    """Direction comparison is case-insensitive ('buy' vs 'BUY')."""
    db_tickets = {"393"}
    db_directions = {("XAUUSD", "BUY")}

    # Broker returns lowercase direction
    assert _is_orphan(
        position_id="XAU_USD:long", position_symbol="XAUUSD",
        position_direction="buy",
        db_tickets=db_tickets, db_directions=db_directions,
    ) is False


def test_opposite_direction_is_orphan():
    """BUY in DB + SELL on broker → different direction, flag as orphan."""
    db_tickets = {"393"}
    db_directions = {("XAUUSD", "BUY")}
    assert _is_orphan(
        position_id="XAU_USD:short", position_symbol="XAUUSD",
        position_direction="SELL",
        db_tickets=db_tickets, db_directions=db_directions,
    ) is True


def test_different_symbol_is_orphan():
    """Symbol mismatch → orphan (even if direction matches)."""
    db_tickets = {"393"}
    db_directions = {("XAUUSD", "BUY")}
    assert _is_orphan(
        position_id="BTC_USD:long", position_symbol="BTCUSD",
        position_direction="BUY",
        db_tickets=db_tickets, db_directions=db_directions,
    ) is True


def test_empty_ticket_falls_through_to_direction():
    """Position has no ID — only direction fallback can save it."""
    db_tickets = {"393"}
    db_directions = {("BTCUSD", "BUY")}
    assert _is_orphan(
        position_id="", position_symbol="BTCUSD",
        position_direction="BUY",
        db_tickets=db_tickets, db_directions=db_directions,
    ) is False


def test_empty_db_everything_is_orphan():
    """No open DB trades → every broker position is an orphan."""
    assert _is_orphan(
        position_id="T1", position_symbol="BTCUSD",
        position_direction="BUY",
        db_tickets=set(), db_directions=set(),
    ) is True


# ── Sibling-agent coverage (the specific XAUUSD scenario) ──────────────

def test_sibling_agent_open_position_not_flagged():
    """
    User has 4 XAUUSD agents (scout, potential, flowrex_v2 × 2). Only
    one of them opened a BUY position (ticket 393). The reconciler
    running for the OTHER three agents should NOT flag that position
    as an orphan because db_directions is scoped across the user's
    agents, not just the current agent.

    This is the specific regression that was spamming CRITICAL logs
    on the Trading page.
    """
    # Aggregate db state across all four XAUUSD agents for this user.
    db_tickets = {"393"}  # only one agent opened a trade
    db_directions = {("XAUUSD", "BUY")}

    # For each of the four agents, the fallback hits the same direction set,
    # so none of them should flag orphan.
    for agent_symbol, agent_type in [
        ("XAUUSD", "scout"),
        ("XAUUSD", "potential"),
        ("XAUUSD", "flowrex_v2"),
        ("XAUUSD", "flowrex_v2"),
    ]:
        orphan = _is_orphan(
            position_id="XAU_USD:long", position_symbol="XAUUSD",
            position_direction="BUY",
            db_tickets=db_tickets, db_directions=db_directions,
        )
        assert orphan is False, f"Sibling {agent_symbol}/{agent_type} falsely flagged"


def test_size_not_compared():
    """The fallback intentionally does NOT compare size — broker's
    aggregate size may differ from any single DB trade's size due to
    partial fills. This test documents that.
    """
    # DB trade is 5 lots; broker aggregate is 2 lots (partial close earlier).
    # Direction fallback should still match — size mismatch is silent.
    db_tickets = {"393"}
    db_directions = {("XAUUSD", "BUY")}
    assert _is_orphan(
        position_id="XAU_USD:long", position_symbol="XAUUSD",
        position_direction="BUY",
        db_tickets=db_tickets, db_directions=db_directions,
    ) is False
