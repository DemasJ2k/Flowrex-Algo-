"""Tests for divergence and breakout-retest feature computation."""
import pytest
import numpy as np
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_ohlcv(n=500, seed=42):
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    opn = close + rng.randn(n) * 0.3
    vol = rng.uniform(100, 10000, n)
    times = np.arange(n) * 300 + 1609459200
    return opn, high, low, close, vol, times


def test_div_returns_dict():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    assert isinstance(result, dict)


def test_div_feature_count():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    assert len(result) >= 15, f"Expected >= 15 features, got {len(result)}"


def test_div_array_lengths():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv(n=350)
    result = compute_divergence_features(o, h, l, c, v, t)
    for name, arr in result.items():
        assert len(arr) == 350, f"{name} length {len(arr)} != 350"


def test_div_no_nan():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    for name, arr in result.items():
        assert not np.any(np.isnan(arr)), f"NaN in {name}"


def test_rsi_divergence_binary():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    for key in ["div_rsi_regular_bull", "div_rsi_regular_bear",
                "div_rsi_hidden_bull", "div_rsi_hidden_bear"]:
        unique = set(np.unique(result[key]))
        assert unique.issubset({0.0, 1.0}), f"{key} not binary: {unique}"


def test_rsi_short_term_bounded():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    assert np.all(result["div_rsi_2bar"] >= 0)
    assert np.all(result["div_rsi_2bar"] <= 1.0)
    assert np.all(result["div_rsi_6bar"] >= 0)
    assert np.all(result["div_rsi_6bar"] <= 1.0)


def test_breakout_body_ratio_bounded():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    assert np.all(result["div_breakout_body_ratio"] >= 0)
    assert np.all(result["div_breakout_body_ratio"] <= 1.0)


def test_retest_flag_binary():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    unique = set(np.unique(result["div_retest_flag"]))
    assert unique.issubset({0.0, 1.0})


def test_macd_divergence_values():
    from app.services.ml.features_divergence import compute_divergence_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_divergence_features(o, h, l, c, v, t)
    unique = set(np.unique(result["div_macd_divergence"]))
    assert unique.issubset({-1.0, 0.0, 1.0})
