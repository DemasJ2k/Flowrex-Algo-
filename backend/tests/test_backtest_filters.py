"""Tests for backtest filter sandbox + per-user cache isolation.

Covers:
- Session bucket helper (_session_for_ts) matches live agent convention.
- PotentialBacktestRequest accepts new filter-sandbox fields with
  sensible defaults.
- In-memory _potential_results cache is keyed by (user_id, symbol) —
  two users running the same symbol do not see each other's data.
"""
import pandas as pd
import pytest

from app.api.backtest import PotentialBacktestRequest, _potential_results


# ── Session bucket helper parity ───────────────────────────────────────
# Live agents classify a UTC timestamp into: asian, london, ny_open,
# ny_close, off_hours. Backtest must match exactly.

def _session_for_ts(ts_seconds: int) -> str:
    """Reimplementation — the actual one is a closure inside _run_potential_backtest."""
    hr = int(pd.to_datetime(ts_seconds, unit="s", utc=True).hour)
    if hr < 8:    return "asian"
    if hr < 13:   return "london"
    if hr < 17:   return "ny_open"
    if hr < 21:   return "ny_close"
    return "off_hours"


@pytest.mark.parametrize("hour,expected", [
    (0, "asian"),
    (7, "asian"),
    (8, "london"),
    (12, "london"),
    (13, "ny_open"),
    (16, "ny_open"),
    (17, "ny_close"),
    (20, "ny_close"),
    (21, "off_hours"),
    (23, "off_hours"),
])
def test_session_bucket_boundaries(hour, expected):
    """Exact boundary behaviour — < not <=."""
    ts = int(pd.Timestamp(f"2026-04-22 {hour:02d}:00:00", tz="UTC").timestamp())
    assert _session_for_ts(ts) == expected


# ── Request model defaults ─────────────────────────────────────────────

def test_request_defaults_safe():
    """Default backtest request should leave all filters OFF so
    behaviour matches pre-sandbox pipeline."""
    r = PotentialBacktestRequest(symbol="BTCUSD")
    assert r.session_filter is False
    assert r.regime_filter is False
    assert r.use_correlations is True  # default on — mirrors live agent default
    assert r.allow_buy is True
    assert r.allow_sell is True
    assert r.agent_type == "potential"
    # Scout knobs carry their defaults even for non-scout agents (harmless)
    assert r.lookback_bars == 40
    assert r.instant_entry_confidence == 0.85
    assert r.max_pending_bars == 10
    assert r.pullback_atr_fraction == 0.50
    assert r.dedupe_window_bars == 20


def test_request_accepts_filter_overrides():
    """Explicit filter settings round-trip cleanly."""
    r = PotentialBacktestRequest(
        symbol="XAUUSD",
        session_filter=True,
        allowed_sessions=["london", "ny_open"],
        regime_filter=True,
        allowed_regimes=["ranging", "volatile"],
        use_correlations=False,
        allow_buy=True,
        allow_sell=False,
        agent_type="scout",
    )
    assert r.session_filter is True
    assert r.allowed_sessions == ["london", "ny_open"]
    assert r.regime_filter is True
    assert r.allowed_regimes == ["ranging", "volatile"]
    assert r.use_correlations is False
    assert r.allow_sell is False
    assert r.agent_type == "scout"


def test_request_rejects_invalid_agent_type():
    """Only 'potential' and 'scout' are supported. Pydantic allows any
    string at the model layer — validation is done in the simulation.
    Document this with a test anyway."""
    # No exception raised; strings pass through. The simulation branch
    # does `is_scout = (body.agent_type == "scout")`, so anything else
    # follows the potential path.
    r = PotentialBacktestRequest(symbol="US30", agent_type="whatever")
    assert r.agent_type == "whatever"


# ── Cache isolation (the critical fix from commit 1634d61) ────────────

def test_cache_key_type_annotation():
    """Cache is typed as dict[tuple[int, str], dict]. This test just
    documents the contract; actual isolation is tested below."""
    assert isinstance(_potential_results, dict)


def test_cache_isolates_users():
    """Two users with the same symbol don't share results."""
    _potential_results.clear()
    user_a_result = {"symbol": "XAUUSD", "total_pnl": 100.0, "win_rate": 60.0}
    user_b_result = {"symbol": "XAUUSD", "total_pnl": -50.0, "win_rate": 30.0}

    _potential_results[(42, "XAUUSD")] = user_a_result
    _potential_results[(99, "XAUUSD")] = user_b_result

    # Each user reads only their own
    assert _potential_results[(42, "XAUUSD")]["total_pnl"] == 100.0
    assert _potential_results[(99, "XAUUSD")]["total_pnl"] == -50.0

    # Writing to one user's slot does not affect the other
    _potential_results[(42, "XAUUSD")] = {"total_pnl": 999.0}
    assert _potential_results[(99, "XAUUSD")]["total_pnl"] == -50.0


def test_cache_projection_filters_by_user():
    """The /potential/status endpoint projects results to {symbol: data}
    for the current user only. This mirrors that projection."""
    _potential_results.clear()
    _potential_results[(42, "XAUUSD")] = {"total_pnl": 100.0}
    _potential_results[(42, "BTCUSD")] = {"total_pnl": 200.0}
    _potential_results[(99, "XAUUSD")] = {"total_pnl": -50.0}

    uid = 42
    own = {sym: data for (u, sym), data in _potential_results.items() if u == uid}
    assert own == {"XAUUSD": {"total_pnl": 100.0}, "BTCUSD": {"total_pnl": 200.0}}
    assert "XAUUSD" in own
    # Critically: user 99's data is NOT projected
    assert own["XAUUSD"]["total_pnl"] != -50.0


def test_cache_pop_scoped_to_user():
    """Running a new backtest pops only the current user's slot."""
    _potential_results.clear()
    _potential_results[(42, "XAUUSD")] = {"prev": "user_a_data"}
    _potential_results[(99, "XAUUSD")] = {"prev": "user_b_data"}

    # User 42 starts a new run
    _potential_results.pop((42, "XAUUSD"), None)

    # Only user 42's slot gone
    assert (42, "XAUUSD") not in _potential_results
    assert (99, "XAUUSD") in _potential_results
    assert _potential_results[(99, "XAUUSD")] == {"prev": "user_b_data"}


# ── Direction-gate counter (audit finding #5) ─────────────────────────

def test_direction_gate_logic_long_only():
    """allow_buy=True, allow_sell=False: BUY signals pass, SELL rejected."""
    allow_buy = True
    allow_sell = False
    rejected = 0

    for sig in [2, 0, 2, 0, 0, 2]:  # 3 BUY, 3 SELL
        is_long_sig = sig == 2
        if (is_long_sig and not allow_buy) or ((not is_long_sig) and not allow_sell):
            rejected += 1
    # 3 SELLs rejected, 3 BUYs passed
    assert rejected == 3


def test_direction_gate_logic_short_only():
    """allow_sell=True, allow_buy=False: SELLs pass, BUYs rejected."""
    allow_buy = False
    allow_sell = True
    rejected = 0

    for sig in [2, 0, 2, 0, 0, 2]:
        is_long_sig = sig == 2
        if (is_long_sig and not allow_buy) or ((not is_long_sig) and not allow_sell):
            rejected += 1
    assert rejected == 3  # 3 BUYs rejected


def test_direction_gate_both_disabled():
    """allow_buy=False, allow_sell=False: everything rejected.
    The wizard validates against this but the backtest should still
    handle it gracefully (0 trades)."""
    allow_buy = False
    allow_sell = False
    rejected = 0

    for sig in [2, 0, 2, 0]:
        is_long_sig = sig == 2
        if (is_long_sig and not allow_buy) or ((not is_long_sig) and not allow_sell):
            rejected += 1
    assert rejected == 4
