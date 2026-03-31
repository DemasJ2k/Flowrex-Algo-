"""Tests for features_williams.py: Larry Williams strategy features."""
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_williams import compute_williams_features

# ── Fixtures ──────────────────────────────────────────────────────────────

EXPECTED_KEYS = [
    "lw_stretch_up", "lw_stretch_down", "lw_above_stretch", "lw_below_stretch",
    "lw_stretch_ratio",
    "lw_range_expansion", "lw_nr4", "lw_nr7", "lw_inside_bar",
    "lw_wr_5", "lw_wr_28", "lw_wr_5_slope", "lw_wr_28_slope",
    "lw_wr_aligned_bull", "lw_wr_aligned_bear",
    "lw_wr_bull_divergence", "lw_wr_bear_divergence",
    "lw_smash_bull", "lw_smash_bear", "lw_3bar_mom_bull", "lw_3bar_mom_bear",
    "lw_above_value", "lw_value_slope",
    "lw_oops_bull", "lw_oops_bear",
]


def _make_ohlcv(n: int, seed: int = 42, gaps: bool = False):
    """Generate synthetic OHLCV data using random walk."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    close = np.abs(close) + 1.0
    open_ = close + rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.3, n))
    vol = np.abs(rng.normal(1000, 200, n)) + 100

    if gaps:
        # Inject gaps every 50 bars to trigger oops patterns
        for i in range(50, n, 50):
            if i % 100 == 50:
                # Gap down: open below previous low
                open_[i] = low[i - 1] - rng.uniform(0.5, 2.0)
                low[i] = min(low[i], open_[i] - 0.1)
                # Let high reach back to prev low for oops
                high[i] = max(high[i], low[i - 1] + 0.1)
            else:
                # Gap up: open above previous high
                open_[i] = high[i - 1] + rng.uniform(0.5, 2.0)
                high[i] = max(high[i], open_[i] + 0.1)
                low[i] = min(low[i], high[i - 1] - 0.1)

    times = np.arange(1672617600, 1672617600 + n * 300, 300, dtype=np.int64)
    return open_, high, low, close, vol, times


@pytest.fixture
def small_data():
    return _make_ohlcv(100)


@pytest.fixture
def medium_data():
    return _make_ohlcv(500)


@pytest.fixture
def large_data():
    return _make_ohlcv(5000)


@pytest.fixture
def gapped_data():
    return _make_ohlcv(500, gaps=True)


# ── Tests ─────────────────────────────────────────────────────────────────


def test_returns_dict_with_expected_keys(medium_data):
    """1. Returns dict with ~25 features."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    assert isinstance(feat, dict)
    assert len(feat) == 25
    for key in EXPECTED_KEYS:
        assert key in feat, f"Missing feature: {key}"


def test_all_arrays_same_length(medium_data):
    """2. All arrays same length as input."""
    o, h, l, c, v, t = medium_data
    n = len(c)
    feat = compute_williams_features(o, h, l, c, v, t)
    for key, arr in feat.items():
        assert len(arr) == n, f"{key} has length {len(arr)}, expected {n}"


def test_no_nan_inf(medium_data):
    """3. No NaN/Inf in any output."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    for key, arr in feat.items():
        assert not np.any(np.isnan(arr)), f"NaN found in {key}"
        assert not np.any(np.isinf(arr)), f"Inf found in {key}"


def test_williams_r_range(medium_data):
    """4. Williams %R values in [-100, 0] range."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    for key in ["lw_wr_5", "lw_wr_28"]:
        arr = feat[key]
        assert np.all(arr >= -100), f"{key} has value below -100"
        assert np.all(arr <= 0), f"{key} has value above 0"


def test_nr_binary(medium_data):
    """5. NR4/NR7 are binary (0 or 1)."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    for key in ["lw_nr4", "lw_nr7"]:
        arr = feat[key]
        unique = np.unique(arr)
        assert all(v in [0.0, 1.0] for v in unique), f"{key} has non-binary values: {unique}"


def test_stretch_non_negative(medium_data):
    """6. Stretch values are non-negative."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    assert np.all(feat["lw_stretch_up"] >= 0), "stretch_up has negative values"
    assert np.all(feat["lw_stretch_down"] >= 0), "stretch_down has negative values"


def test_range_expansion_positive(medium_data):
    """7. Range expansion > 0 (after warmup)."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    re = feat["lw_range_expansion"]
    # After ATR warmup (10 bars), values should be positive
    assert np.all(re[10:] > 0), "Range expansion has non-positive values after warmup"


def test_smash_day_signals(medium_data):
    """8. Smash day detection produces signals on volatile data."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    # With 500 bars of random data, expect at least a few smash days
    assert np.sum(feat["lw_smash_bull"]) > 0, "No bullish smash days detected"
    assert np.sum(feat["lw_smash_bear"]) > 0, "No bearish smash days detected"


def test_small_data(small_data):
    """9. Works with small data (100 bars)."""
    o, h, l, c, v, t = small_data
    feat = compute_williams_features(o, h, l, c, v, t)
    assert len(feat) == 25
    for key, arr in feat.items():
        assert len(arr) == 100
        assert not np.any(np.isnan(arr))
        assert not np.any(np.isinf(arr))


def test_large_data(large_data):
    """10. Works with large data (5000 bars)."""
    o, h, l, c, v, t = large_data
    feat = compute_williams_features(o, h, l, c, v, t)
    assert len(feat) == 25
    for key, arr in feat.items():
        assert len(arr) == 5000
        assert arr.dtype == np.float32


def test_oops_pattern_with_gaps(gapped_data):
    """11. Oops pattern detection with gapped data."""
    o, h, l, c, v, t = gapped_data
    feat = compute_williams_features(o, h, l, c, v, t)
    # Gapped data should produce oops signals
    assert np.sum(feat["lw_oops_bull"]) > 0, "No bullish oops patterns detected in gapped data"
    assert np.sum(feat["lw_oops_bear"]) > 0, "No bearish oops patterns detected in gapped data"


def test_all_outputs_float32(medium_data):
    """12. All output arrays are float32."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    for key, arr in feat.items():
        assert arr.dtype == np.float32, f"{key} is {arr.dtype}, expected float32"


def test_binary_features_are_binary(medium_data):
    """13. All binary features only contain 0 and 1."""
    o, h, l, c, v, t = medium_data
    feat = compute_williams_features(o, h, l, c, v, t)
    binary_keys = [
        "lw_above_stretch", "lw_below_stretch",
        "lw_nr4", "lw_nr7", "lw_inside_bar",
        "lw_wr_aligned_bull", "lw_wr_aligned_bear",
        "lw_wr_bull_divergence", "lw_wr_bear_divergence",
        "lw_smash_bull", "lw_smash_bear",
        "lw_3bar_mom_bull", "lw_3bar_mom_bear",
        "lw_above_value",
        "lw_oops_bull", "lw_oops_bear",
    ]
    for key in binary_keys:
        unique = set(np.unique(feat[key]).tolist())
        assert unique <= {0.0, 1.0}, f"{key} has non-binary values: {unique}"


def test_times_optional(medium_data):
    """14. Works when times is None."""
    o, h, l, c, v, _ = medium_data
    feat = compute_williams_features(o, h, l, c, v, times=None)
    assert len(feat) == 25
