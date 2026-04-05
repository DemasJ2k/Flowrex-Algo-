"""Tests for ICT feature computation."""
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
    times = np.arange(n) * 300 + 1609459200  # 5-min intervals from 2021-01-01
    return opn, high, low, close, vol, times


def test_ict_returns_dict():
    from app.services.ml.features_ict import compute_ict_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_ict_features(o, h, l, c, v, t)
    assert isinstance(result, dict)


def test_ict_feature_count():
    from app.services.ml.features_ict import compute_ict_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_ict_features(o, h, l, c, v, t)
    assert len(result) >= 20, f"Expected >= 20 features, got {len(result)}"


def test_ict_array_lengths():
    from app.services.ml.features_ict import compute_ict_features
    o, h, l, c, v, t = _make_ohlcv(n=300)
    result = compute_ict_features(o, h, l, c, v, t)
    for name, arr in result.items():
        assert len(arr) == 300, f"{name} length {len(arr)} != 300"


def test_ict_no_nan():
    from app.services.ml.features_ict import compute_ict_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_ict_features(o, h, l, c, v, t)
    for name, arr in result.items():
        assert not np.any(np.isnan(arr)), f"NaN in {name}"


def test_ict_confluence_bounded():
    from app.services.ml.features_ict import compute_ict_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_ict_features(o, h, l, c, v, t)
    assert np.all(result["ict_confluence"] >= 0)
    assert np.all(result["ict_confluence"] <= 1.0)


def test_ict_silver_bullet_binary():
    from app.services.ml.features_ict import compute_ict_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_ict_features(o, h, l, c, v, t)
    unique = set(np.unique(result["ict_silver_bullet"]))
    assert unique.issubset({0.0, 1.0})


def test_ict_feature_names():
    from app.services.ml.features_ict import compute_ict_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_ict_features(o, h, l, c, v, t)
    expected_prefixes = ["ict_equal", "ict_session", "ict_sweep", "ict_fvg", "ict_ob",
                         "ict_silver", "ict_asian", "ict_mss", "ict_bars", "ict_confluence"]
    for prefix in expected_prefixes:
        matches = [k for k in result if k.startswith(prefix)]
        assert len(matches) >= 1, f"No feature starting with '{prefix}'"


def test_ict_integration_with_compute_expert():
    """ICT features integrate into the full pipeline without errors."""
    import warnings
    warnings.filterwarnings("ignore")
    from app.services.ml.features_mtf import compute_expert_features
    import pandas as pd

    rng = np.random.RandomState(42)
    n = 600
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    m5 = pd.DataFrame({
        "time": np.arange(n) * 300 + 1609459200,
        "open": close + rng.randn(n) * 0.3,
        "high": close + rng.uniform(0.1, 1.0, n),
        "low": close - rng.uniform(0.1, 1.0, n),
        "close": close,
        "volume": rng.uniform(100, 10000, n),
    })
    names, X = compute_expert_features(m5, symbol="US30", include_external=False)
    # Should have base (~130) + ICT (~20) + institutional (~18) + divergence (~15) = ~183+
    assert len(names) >= 180, f"Expected >= 180 features, got {len(names)}"
    assert X.shape == (n, len(names))
    assert not np.any(np.isnan(X))
