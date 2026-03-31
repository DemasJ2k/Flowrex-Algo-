"""Unit tests for feature engineering."""
import numpy as np
import pandas as pd
from app.services.ml.features_mtf import compute_expert_features


def _make_bars(n=500):
    """Generate synthetic M5 bars for testing."""
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(n) * 0.5) + 2000
    highs = closes + np.abs(np.random.randn(n) * 2)
    lows = closes - np.abs(np.random.randn(n) * 2)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    times = np.arange(1700000000, 1700000000 + n * 300, 300)[:n]
    volumes = np.random.randint(100, 5000, n)
    return pd.DataFrame({
        "time": times, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": volumes,
    })


def test_feature_count_minimum_80():
    """Should produce at least 80 features."""
    bars = _make_bars(500)
    feature_names, X = compute_expert_features(bars)
    assert len(feature_names) >= 80, f"Only {len(feature_names)} features"
    assert X.shape == (500, len(feature_names))


def test_no_nan_in_output():
    """Output should have no NaN values (replaced with 0)."""
    bars = _make_bars(500)
    feature_names, X = compute_expert_features(bars)
    assert not np.any(np.isnan(X)), "Found NaN in feature matrix"
    assert not np.any(np.isinf(X)), "Found Inf in feature matrix"


def test_feature_names_unique():
    """All feature names should be unique."""
    bars = _make_bars(500)
    feature_names, _ = compute_expert_features(bars)
    assert len(feature_names) == len(set(feature_names)), "Duplicate feature names"


def test_with_htf_data():
    """Should work with higher-timeframe data provided."""
    m5 = _make_bars(1000)
    h1 = _make_bars(100)
    feature_names, X = compute_expert_features(m5, h1)
    assert X.shape[0] == 1000
    assert "h1_trend" in feature_names


def test_without_htf_data():
    """Should still produce HTF features (zero-filled) without HTF data."""
    bars = _make_bars(500)
    feature_names, X = compute_expert_features(bars)
    assert "h1_trend" in feature_names
    assert "d1_bias" in feature_names
    # HTF features should be zero when no HTF data
    h1_idx = feature_names.index("h1_trend")
    assert np.all(X[:, h1_idx] == 0)


def test_session_features_valid():
    """Session features should be in valid ranges."""
    bars = _make_bars(500)
    feature_names, X = compute_expert_features(bars)
    sin_idx = feature_names.index("hour_sin")
    cos_idx = feature_names.index("hour_cos")
    assert np.all(X[:, sin_idx] >= -1) and np.all(X[:, sin_idx] <= 1)
    assert np.all(X[:, cos_idx] >= -1) and np.all(X[:, cos_idx] <= 1)
