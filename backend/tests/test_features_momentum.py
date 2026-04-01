"""Tests for momentum feature module."""

import numpy as np
import pytest

from app.services.ml.features_momentum import compute_momentum_features


@pytest.fixture
def random_walk_data():
    """Generate cumsum random walk data with seed 42."""
    rng = np.random.RandomState(42)
    n = 1000
    rets = rng.randn(n) * 0.01
    closes = 100.0 + np.cumsum(rets)
    closes = np.maximum(closes, 1.0)
    highs = closes + np.abs(rng.randn(n)) * 0.5
    lows = closes - np.abs(rng.randn(n)) * 0.5
    opens = closes + rng.randn(n) * 0.2
    volumes = np.abs(rng.randn(n)) * 1000 + 500
    return opens, highs, lows, closes, volumes


def test_feature_count(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    assert len(result) == 20


def test_all_prefixed_mom(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    for key in result:
        assert key.startswith("mom_"), f"Key {key} missing mom_ prefix"


def test_array_lengths(random_walk_data):
    n = len(random_walk_data[0])
    result = compute_momentum_features(*random_walk_data)
    for key, arr in result.items():
        assert len(arr) == n, f"{key} has length {len(arr)} != {n}"


def test_no_nan(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    for key, arr in result.items():
        assert not np.any(np.isnan(arr)), f"{key} contains NaN"


def test_no_inf(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    for key, arr in result.items():
        assert not np.any(np.isinf(arr)), f"{key} contains Inf"


def test_float32(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    for key, arr in result.items():
        assert arr.dtype == np.float32, f"{key} is {arr.dtype} not float32"


def test_vol_breakout_binary(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    vals = np.unique(result["mom_vol_breakout"])
    assert set(vals).issubset({0.0, 1.0})


def test_consistency_range(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    for key in ["mom_consistency_12", "mom_consistency_48"]:
        assert np.all(result[key] >= 0.0), f"{key} below 0"
        assert np.all(result[key] <= 1.0), f"{key} above 1"


def test_divergence_binary(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    for key in ["mom_price_rsi_div", "mom_price_vol_div", "mom_macd_div"]:
        vals = np.unique(result[key])
        assert set(vals).issubset({0.0, 1.0}), f"{key} not binary: {vals}"


def test_small_data():
    n = 5
    closes = np.array([100.0, 101.0, 99.5, 102.0, 100.5])
    highs = closes + 0.5
    lows = closes - 0.5
    opens = closes + 0.1
    volumes = np.ones(n) * 1000
    result = compute_momentum_features(opens, highs, lows, closes, volumes)
    assert len(result) == 20
    for key, arr in result.items():
        assert len(arr) == n
        assert not np.any(np.isnan(arr))
        assert not np.any(np.isinf(arr))


def test_large_data():
    rng = np.random.RandomState(42)
    n = 50_000
    closes = 100.0 + np.cumsum(rng.randn(n) * 0.01)
    closes = np.maximum(closes, 1.0)
    highs = closes + 0.5
    lows = closes - 0.5
    opens = closes + 0.1
    volumes = np.abs(rng.randn(n)) * 1000 + 500
    result = compute_momentum_features(opens, highs, lows, closes, volumes)
    assert len(result) == 20
    for key, arr in result.items():
        assert len(arr) == n
        assert arr.dtype == np.float32


def test_roc_cascade_range(random_walk_data):
    result = compute_momentum_features(*random_walk_data)
    cascade = result["mom_roc_cascade"]
    assert np.all(cascade >= -3.0)
    assert np.all(cascade <= 3.0)
