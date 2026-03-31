"""Tests for ICT/SMC comprehensive features."""
import numpy as np
import pytest
from app.services.ml.features_ict import compute_ict_features


EXPECTED_FEATURES = 30


def _make_ohlcv(n=500, seed=42):
    """Generate synthetic OHLCV data (random walk + noise)."""
    np.random.seed(seed)
    closes = np.cumsum(np.random.randn(n) * 0.5) + 2000
    highs = closes + np.abs(np.random.randn(n) * 2)
    lows = closes - np.abs(np.random.randn(n) * 2)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = np.random.randint(100, 5000, n).astype(float)
    return opens, highs, lows, closes, volumes


def _make_trending_reverting(n=1000, seed=42):
    """Generate data with clear trends and reversals for sweep detection."""
    np.random.seed(seed)
    t = np.arange(n)
    # Sawtooth with noise — creates trends + reversals
    base = 2000 + 50 * np.sin(2 * np.pi * t / 200) + np.cumsum(np.random.randn(n) * 0.3)
    closes = base
    highs = closes + np.abs(np.random.randn(n) * 3)
    lows = closes - np.abs(np.random.randn(n) * 3)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = np.random.randint(100, 5000, n).astype(float)
    return opens, highs, lows, closes, volumes


# ── Test 1: Returns dict with ~30 features ────────────────────────
def test_returns_dict_with_expected_features():
    result = compute_ict_features(*_make_ohlcv())
    assert isinstance(result, dict)
    assert len(result) >= EXPECTED_FEATURES, (
        f"Expected ~{EXPECTED_FEATURES} features, got {len(result)}: {sorted(result.keys())}"
    )


# ── Test 2: All arrays same length as input ───────────────────────
def test_all_arrays_same_length():
    n = 500
    result = compute_ict_features(*_make_ohlcv(n))
    for key, arr in result.items():
        assert len(arr) == n, f"{key} has length {len(arr)}, expected {n}"


# ── Test 3: No NaN/Inf values ─────────────────────────────────────
def test_no_nan_inf():
    result = compute_ict_features(*_make_ohlcv())
    for key, arr in result.items():
        assert not np.any(np.isnan(arr)), f"{key} contains NaN"
        assert not np.any(np.isinf(arr)), f"{key} contains Inf"


# ── Test 4: BOS/CHOCH values are valid ────────────────────────────
def test_bos_choch_values():
    result = compute_ict_features(*_make_ohlcv())
    trend = result["ict_trend"]
    assert set(np.unique(trend)).issubset({-1.0, 0.0, 1.0})

    choch_recent = result["ict_choch_recent"]
    assert set(np.unique(choch_recent)).issubset({0.0, 1.0})


# ── Test 5: PD position is 0-1 range ──────────────────────────────
def test_pd_position_range():
    result = compute_ict_features(*_make_ohlcv())
    pd_pos = result["ict_pd_position"]
    valid = pd_pos[pd_pos != 0]
    if len(valid) > 0:
        assert np.all(valid >= 0.0) and np.all(valid <= 1.0), (
            f"PD position out of range: min={valid.min()}, max={valid.max()}"
        )


# ── Test 6: Confluence score is 0-10 range ─────────────────────────
def test_confluence_score_range():
    result = compute_ict_features(*_make_ohlcv())
    score = result["ict_confluence_score"]
    assert np.all(score >= 0.0) and np.all(score <= 10.0), (
        f"Confluence score out of range: min={score.min()}, max={score.max()}"
    )
    grade = result["ict_setup_grade"]
    assert set(np.unique(grade)).issubset({0.0, 1.0, 2.0, 3.0})


# ── Test 7: Works with small data (100 bars) ──────────────────────
def test_small_data():
    result = compute_ict_features(*_make_ohlcv(100))
    assert isinstance(result, dict)
    assert len(result) >= EXPECTED_FEATURES
    for key, arr in result.items():
        assert len(arr) == 100, f"{key} wrong length"
        assert not np.any(np.isnan(arr)), f"{key} has NaN"


# ── Test 8: Works with large data (5000 bars) ─────────────────────
def test_large_data():
    result = compute_ict_features(*_make_ohlcv(5000))
    assert isinstance(result, dict)
    assert len(result) >= EXPECTED_FEATURES
    for key, arr in result.items():
        assert len(arr) == 5000, f"{key} wrong length"
        assert not np.any(np.isnan(arr)), f"{key} has NaN"
        assert arr.dtype == np.float32, f"{key} is {arr.dtype}, expected float32"


# ── Test 9: Sweep detection produces signals on trending+reverting data
def test_sweep_detection_signals():
    result = compute_ict_features(*_make_trending_reverting(1000))
    sweep_bull = result["ict_sweep_bull"]
    sweep_bear = result["ict_sweep_bear"]
    # Should have at least some sweeps on trending/reverting data
    total_sweeps = np.sum(sweep_bull) + np.sum(sweep_bear)
    assert total_sweeps > 0, "No sweeps detected on trending+reverting data"


# ── Test 10: H4 data integration works when provided ──────────────
def test_h4_integration():
    opens, highs, lows, closes, volumes = _make_ohlcv(960)
    # Create H4 data (960 M5 bars / 48 = 20 H4 bars)
    np.random.seed(99)
    n_h4 = 20
    h4_closes = np.cumsum(np.random.randn(n_h4) * 2) + 2000
    h4_highs = h4_closes + np.abs(np.random.randn(n_h4) * 5)
    h4_lows = h4_closes - np.abs(np.random.randn(n_h4) * 5)

    result = compute_ict_features(
        opens, highs, lows, closes, volumes,
        h4_highs=h4_highs, h4_lows=h4_lows, h4_closes=h4_closes,
    )
    pd_h4 = result["ict_pd_h4_position"]
    assert len(pd_h4) == 960
    # H4 PD should have some non-zero values
    assert np.sum(pd_h4 != 0) > 0, "H4 PD position is all zeros"
    # Should be in 0-1 range
    valid = pd_h4[pd_h4 != 0]
    assert np.all(valid >= 0.0) and np.all(valid <= 1.0)


# ── Additional: all feature names start with ict_ ──────────────────
def test_feature_naming_convention():
    result = compute_ict_features(*_make_ohlcv())
    for key in result:
        assert key.startswith("ict_"), f"Feature '{key}' does not start with 'ict_'"


# ── Additional: float32 output ─────────────────────────────────────
def test_float32_output():
    result = compute_ict_features(*_make_ohlcv())
    for key, arr in result.items():
        assert arr.dtype == np.float32, f"{key} is {arr.dtype}, expected float32"
