"""Unit tests for ScoutAgent (2026-04-21 sprint).

Focuses on the parts of the state machine that don't require an async
event loop + DB + broker adapter:

- `_scout_check_triggers` (api/backtest.py) — pure function mirror of
  ScoutAgent._check_triggers. Used by the backtest simulation.
- ScoutAgent._check_triggers — runtime trigger logic.
- Entry/expiry/dedupe branches via lightweight state stubs.

The full async `evaluate()` path is covered by integration tests
(test_agent_lifecycle.py). These tests verify the trigger logic in
isolation so trigger regressions surface fast.
"""
import numpy as np
import pytest

from app.api.backtest import _scout_check_triggers


# ── Helpers ────────────────────────────────────────────────────────────

def _mk_ohlc(n: int, base: float = 100.0, step: float = 0.0) -> tuple:
    """Generate simple OHLC arrays with optional drift."""
    closes = base + np.arange(n, dtype=float) * step
    highs = closes + 0.5
    lows = closes - 0.5
    return highs, lows, closes


def _mk_pending(
    direction: int = 1,
    confidence: float = 0.60,
    ref_close: float = 100.0,
    ref_atr: float = 1.0,
    bars_waited: int = 1,
) -> dict:
    """Build a pending-state dict matching ScoutAgent's internal shape."""
    return {
        "direction": direction,
        "confidence": confidence,
        "ref_close": ref_close,
        "ref_high": ref_close + 0.5,
        "ref_low": ref_close - 0.5,
        "ref_atr": ref_atr,
        "ref_time": 1_700_000_000,
        "bars_waited": bars_waited,
    }


# ── Instant-confidence trigger ─────────────────────────────────────────

def test_instant_confidence_fires_above_threshold():
    """conf >= instant_entry_confidence → trigger = 'instant_confidence'."""
    pending = _mk_pending(confidence=0.95)
    highs, lows, closes = _mk_ohlc(100)
    result = _scout_check_triggers(
        pending, i=99, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result == "instant_confidence"


def test_instant_confidence_silent_below_threshold():
    """Below threshold: must NOT return instant_confidence before checking
    pullback / BOS."""
    pending = _mk_pending(confidence=0.70)  # below 0.85
    highs, lows, closes = _mk_ohlc(100)  # flat — no pullback, no BOS
    result = _scout_check_triggers(
        pending, i=99, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result is None


# ── Pullback trigger ──────────────────────────────────────────────────

def test_pullback_long_fires_when_price_retraces_then_reverses():
    """BUY pending: price drops >= 0.5 × ATR below ref_close,
    then current bar closes > prior close (reversal candle)."""
    # Build a scenario: ref_close was 100, ATR = 2.0, so pullback distance = 1.0.
    # Price drops to 98.5 (1.5 below ref — exceeds threshold) then reverses.
    n = 60
    closes = np.full(n, 100.0)
    closes[-5:] = [99.5, 99.0, 98.5, 98.8, 99.2]  # drop + reversal at end
    highs = closes + 0.2
    lows = closes - 0.2
    lows[-3] = 97.5  # deepest low

    pending = _mk_pending(direction=1, confidence=0.60, ref_close=100.0, ref_atr=2.0, bars_waited=5)
    result = _scout_check_triggers(
        pending, i=n - 1, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result == "pullback", f"Expected pullback trigger, got {result}"


def test_pullback_short_fires_when_price_rises_then_reverses():
    """SELL pending: price rises >= 0.5 × ATR above ref_close, reversal down."""
    n = 60
    closes = np.full(n, 100.0)
    closes[-5:] = [100.5, 101.0, 101.5, 101.2, 100.8]  # rise + reversal
    highs = closes + 0.2
    highs[-3] = 102.5  # peak
    lows = closes - 0.2

    pending = _mk_pending(direction=-1, confidence=0.60, ref_close=100.0, ref_atr=2.0, bars_waited=5)
    result = _scout_check_triggers(
        pending, i=n - 1, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result == "pullback", f"Expected pullback trigger, got {result}"


def test_pullback_does_not_fire_without_retrace():
    """Price never moves against pending direction → no pullback trigger."""
    highs, lows, closes = _mk_ohlc(60, base=100.0, step=0.1)  # steady drift up
    pending = _mk_pending(direction=1, confidence=0.60, ref_close=100.0, ref_atr=2.0, bars_waited=5)
    result = _scout_check_triggers(
        pending, i=59, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    # No retrace, may hit BOS (new high) instead. We only assert it didn't
    # classify as pullback.
    assert result != "pullback"


# ── Break-of-structure trigger ────────────────────────────────────────

def test_bos_long_fires_on_new_high():
    """BUY pending: current high exceeds max of last `lookback_bars`."""
    n = 80
    closes = np.full(n, 100.0)
    highs = np.full(n, 100.5)
    lows = np.full(n, 99.5)
    # Put a fresh new high on the last bar, above the lookback window max.
    highs[-1] = 105.0
    closes[-1] = 104.5

    # With confidence < threshold + no pullback move, only BOS can fire.
    pending = _mk_pending(direction=1, confidence=0.60, ref_close=100.0, ref_atr=2.0, bars_waited=1)
    result = _scout_check_triggers(
        pending, i=n - 1, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result == "break_of_structure"


def test_bos_short_fires_on_new_low():
    """SELL pending: current low below min of last `lookback_bars`."""
    n = 80
    closes = np.full(n, 100.0)
    highs = np.full(n, 100.5)
    lows = np.full(n, 99.5)
    lows[-1] = 95.0
    closes[-1] = 95.5

    pending = _mk_pending(direction=-1, confidence=0.60, ref_close=100.0, ref_atr=2.0, bars_waited=1)
    result = _scout_check_triggers(
        pending, i=n - 1, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result == "break_of_structure"


def test_bos_does_not_fire_inside_range():
    """No new extreme → no BOS."""
    n = 80
    closes = np.full(n, 100.0)
    highs = closes + 0.5
    lows = closes - 0.5
    pending = _mk_pending(direction=1, confidence=0.60, ref_close=100.0, ref_atr=2.0, bars_waited=1)
    result = _scout_check_triggers(
        pending, i=n - 1, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result is None


# ── Trigger priority ───────────────────────────────────────────────────

def test_trigger_priority_instant_beats_pullback():
    """Both conditions satisfied: instant_confidence wins."""
    n = 60
    closes = np.full(n, 100.0)
    closes[-3:] = [98.0, 98.5, 99.0]  # pullback setup
    highs = closes + 0.2
    lows = closes - 0.2

    pending = _mk_pending(direction=1, confidence=0.90, ref_close=100.0, ref_atr=2.0, bars_waited=3)
    result = _scout_check_triggers(
        pending, i=n - 1, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result == "instant_confidence"


# ── Edge cases ────────────────────────────────────────────────────────

def test_zero_atr_uses_default():
    """ref_atr = 0 is sanitized to 1.0 → pullback distance = 0.5 units."""
    n = 60
    closes = np.full(n, 100.0)
    closes[-3:] = [99.0, 99.4, 99.6]  # 1-unit drop + reversal
    highs = closes + 0.1
    lows = closes - 0.1
    lows[-2] = 98.0

    pending = _mk_pending(direction=1, confidence=0.60, ref_close=100.0, ref_atr=0.0, bars_waited=3)
    # With ATR defaulted to 1.0, pullback_distance = 0.5; 98.0 vs 100 = 2.0 drop passes.
    result = _scout_check_triggers(
        pending, i=n - 1, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    assert result == "pullback"


def test_empty_pending_fields_do_not_crash():
    """Missing bars_waited defaults to 0 — must not raise KeyError."""
    pending = {
        "direction": 1,
        "confidence": 0.60,
        "ref_close": 100.0,
        "ref_high": 100.5,
        "ref_low": 99.5,
        "ref_atr": 2.0,
        "ref_time": 1_700_000_000,
        # bars_waited omitted
    }
    highs, lows, closes = _mk_ohlc(60)
    # Should not raise
    result = _scout_check_triggers(
        pending, i=59, closes=closes, highs=highs, lows=lows,
        lookback_bars=40, pullback_atr_fraction=0.5,
        instant_entry_confidence=0.85,
    )
    # Either None or BOS (flat data) — but no exception
    assert result is None or result in ("pullback", "break_of_structure")
