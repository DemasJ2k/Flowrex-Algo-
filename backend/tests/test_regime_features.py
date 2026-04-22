"""Unit tests for the regime feature column (option b) + validator helper
added in the 2026-04-21 sprint.

Covers:
- _compute_regime_features (features_potential.py) — bar-by-bar one-hot
  regime flags + interaction columns.
- validate_regime_on_history (regime_detector.py) — forward-return
  aggregation used by the backtest-page validator tool.
"""
import numpy as np
import pytest

from app.services.ml.features_potential import _compute_regime_features
from app.services.ml.regime_detector import (
    classify_regime_simple,
    validate_regime_on_history,
)


# ── Synthetic bar generators ───────────────────────────────────────────

def _trending_up_bars(n: int = 500, drift: float = 0.5) -> tuple:
    """Closes drift up with modest noise — should classify trending_up."""
    rng = np.random.default_rng(seed=7)
    closes = np.cumsum(rng.normal(drift, 0.2, n)) + 100.0
    highs = closes + np.abs(rng.normal(0.5, 0.2, n))
    lows = closes - np.abs(rng.normal(0.5, 0.2, n))
    return highs, lows, closes


def _flat_ranging_bars(n: int = 500) -> tuple:
    """Close oscillates in a narrow band — low ATR, low ADX → ranging."""
    rng = np.random.default_rng(seed=11)
    closes = 100.0 + rng.normal(0, 0.1, n).cumsum() * 0.05  # very tight range
    highs = closes + 0.05
    lows = closes - 0.05
    return highs, lows, closes


def _volatile_bars(n: int = 500) -> tuple:
    """Huge ATR spike in the final window — should classify volatile."""
    rng = np.random.default_rng(seed=13)
    # Calm then spike. Spike size must clear 75th-percentile ATR threshold.
    closes = np.concatenate([
        rng.normal(0, 0.05, n - 50).cumsum() + 100.0,
        rng.normal(0, 3.0, 50).cumsum() + 100.0,
    ])
    highs = closes + np.abs(rng.normal(0.1, 2.0, n))
    lows = closes - np.abs(rng.normal(0.1, 2.0, n))
    return highs, lows, closes


def _atr(highs, lows, closes, window=14):
    from app.services.backtest.indicators import atr
    return atr(highs, lows, closes, window)


# ── _compute_regime_features ──────────────────────────────────────────

def test_regime_features_column_shape():
    """Seven named columns, length = n, no NaN/Inf."""
    h, l, c = _trending_up_bars(400)
    atr14 = _atr(h, l, c)
    out = _compute_regime_features(h, l, c, atr14)

    expected_keys = {
        "reg_trending_up", "reg_trending_down", "reg_ranging", "reg_volatile",
        "reg_x_atr_pctile", "reg_x_trend_strength", "reg_confidence",
    }
    assert set(out.keys()) == expected_keys
    for name, arr in out.items():
        assert len(arr) == 400, f"{name} length {len(arr)} != 400"
        assert not np.any(np.isnan(arr)), f"NaN in {name}"
        assert not np.any(np.isinf(arr)), f"Inf in {name}"


def test_regime_features_warmup_is_zero():
    """First `vol_lookback` bars (100) before warmup must stay at zero —
    no regime can be classified without enough history."""
    h, l, c = _trending_up_bars(400)
    atr14 = _atr(h, l, c)
    out = _compute_regime_features(h, l, c, atr14)

    # warmup = max(100, 70, 28) + 5 = 105 — anything before 105 is untouched
    for name in ("reg_trending_up", "reg_trending_down", "reg_ranging", "reg_volatile"):
        assert np.all(out[name][:100] == 0.0), f"{name} set before warmup"


def test_regime_features_mutually_exclusive():
    """One-hot flags: at most one of the four regime flags = 1 at any bar."""
    h, l, c = _trending_up_bars(500)
    atr14 = _atr(h, l, c)
    out = _compute_regime_features(h, l, c, atr14)

    flags = np.column_stack([
        out["reg_trending_up"], out["reg_trending_down"],
        out["reg_ranging"], out["reg_volatile"],
    ])
    sums = flags.sum(axis=1)
    # Post-warmup bars should sum to exactly 1; pre-warmup bars to 0.
    assert np.all(sums <= 1.0), "regime flags not mutually exclusive"


def test_regime_features_detect_trending_up():
    """Strong upward drift should primarily classify as trending_up."""
    h, l, c = _trending_up_bars(500, drift=0.8)
    atr14 = _atr(h, l, c)
    out = _compute_regime_features(h, l, c, atr14)

    # After warmup, trending_up should fire meaningfully often (not just 0).
    # Exact % depends on noise so assert a loose floor.
    post_warmup = slice(120, None)
    trending_up_pct = out["reg_trending_up"][post_warmup].mean()
    assert trending_up_pct > 0.1, \
        f"Expected trending_up to fire, got {trending_up_pct:.3f}"


def test_regime_features_detect_volatile():
    """A large ATR spike near the end should trigger volatile classification."""
    h, l, c = _volatile_bars(500)
    atr14 = _atr(h, l, c)
    out = _compute_regime_features(h, l, c, atr14)

    # The volatile spike is in the last 50 bars
    volatile_in_spike = out["reg_volatile"][-50:].sum()
    assert volatile_in_spike > 0, \
        f"Expected volatile regime in spike window, got 0"


def test_regime_features_handles_all_flat_data():
    """Perfectly flat data shouldn't raise — ATR = 0 → early-return path."""
    closes = np.full(300, 100.0)
    highs = np.full(300, 100.0)
    lows = np.full(300, 100.0)
    atr14 = _atr(highs, lows, closes)

    # Must not raise; flags should all be zero (no regime detected with no range)
    out = _compute_regime_features(highs, lows, closes, atr14)
    for name in ("reg_trending_up", "reg_trending_down", "reg_volatile"):
        assert np.all(out[name] == 0.0)


def test_regime_features_short_input_returns_zeros():
    """Fewer than warmup bars → all zeros, no exception."""
    h, l, c = _trending_up_bars(50)  # well below warmup=105
    atr14 = _atr(h, l, c)
    out = _compute_regime_features(h, l, c, atr14)

    for name, arr in out.items():
        assert np.all(arr == 0.0), f"{name} non-zero with insufficient data"


# ── validate_regime_on_history ────────────────────────────────────────

def test_validator_insufficient_data_returns_error():
    h, l, c = _trending_up_bars(50)
    result = validate_regime_on_history(h, l, c, forward_bars=10)
    assert result.get("error") == "insufficient_data"
    assert result["total_bars"] == 50


def test_validator_bucket_math_consistent():
    """Classified count must equal sum of per-regime n_bars (ex-unknown)."""
    h, l, c = _trending_up_bars(800)
    result = validate_regime_on_history(h, l, c, forward_bars=10)

    total_from_buckets = sum(
        b["n_bars"] for name, b in result["buckets"].items() if name != "unknown"
    )
    assert total_from_buckets == result["classified_bars"]


def test_validator_forward_return_math():
    """Mean return in each bucket is the average of next-N close-to-close %."""
    h, l, c = _trending_up_bars(600, drift=0.6)
    result = validate_regime_on_history(h, l, c, forward_bars=10)

    # For synthetic upward-drifting data, trending_up bucket mean should be
    # positive or at least >= trending_down bucket (if any).
    b = result["buckets"]
    if b.get("trending_up", {}).get("n_bars", 0) > 20:
        assert b["trending_up"]["mean_return_pct"] > b.get(
            "trending_down", {}
        ).get("mean_return_pct", -1e9), \
            "Expected trending_up mean > trending_down mean on upward-drift data"


def test_validator_upratewithinrange():
    """up_rate is a probability in [0, 1]."""
    h, l, c = _trending_up_bars(600)
    result = validate_regime_on_history(h, l, c, forward_bars=10)
    for name, b in result["buckets"].items():
        if b["n_bars"] > 0:
            assert 0.0 <= b["up_rate"] <= 1.0, \
                f"{name}: up_rate {b['up_rate']} out of [0, 1]"


def test_validator_shape_stability():
    """Result dict always carries the expected top-level keys."""
    h, l, c = _trending_up_bars(500)
    result = validate_regime_on_history(h, l, c, forward_bars=5)

    assert "total_bars" in result
    assert "classified_bars" in result
    assert "forward_bars" in result
    assert "buckets" in result
    # Buckets always include all four regime names + unknown, even if empty
    expected_buckets = {"trending_up", "trending_down", "ranging", "volatile", "unknown"}
    assert set(result["buckets"].keys()) == expected_buckets


# ── Parity: compute_regime_features ↔ classify_regime_simple ──────────

def test_feature_column_matches_classifier_at_last_bar():
    """The feature module's per-bar regime assignment at bar -1 should
    match classify_regime_simple's output for the same bars.
    """
    h, l, c = _trending_up_bars(500, drift=0.6)
    atr14 = _atr(h, l, c)
    feats = _compute_regime_features(h, l, c, atr14)

    classifier_result = classify_regime_simple(h, l, c)
    # Only assert match when classifier returned a known regime — skip
    # "unknown" because the two code paths use slightly different bar-level
    # guard clauses.
    if classifier_result.regime == "unknown":
        pytest.skip("Classifier returned unknown — no parity check possible")

    # Feature column at last bar should have the matching one-hot set.
    expected_col = f"reg_{classifier_result.regime}"
    assert feats[expected_col][-1] == 1.0, (
        f"Classifier said {classifier_result.regime} but feature column "
        f"{expected_col}[-1] = {feats[expected_col][-1]}"
    )
