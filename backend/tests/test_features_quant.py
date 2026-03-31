"""
Tests for features_quant.py — quantitative feature engineering.
"""
import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_quant import compute_quant_features


def _make_ohlcv(n=500, seed=42):
    """Create synthetic OHLCV arrays using random walk."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.standard_normal(n) * 0.5)
    close = np.maximum(close, 1.0)
    high = close + np.abs(rng.standard_normal(n) * 0.3)
    low = close - np.abs(rng.standard_normal(n) * 0.3)
    opn = close + rng.standard_normal(n) * 0.1
    vol = np.ones(n) * 1000.0
    return opn, high, low, close, vol


# ── 1. Returns dict with ~15 features ─────────────────────────────
def test_returns_expected_feature_count():
    o, h, l, c, v = _make_ohlcv()
    feat = compute_quant_features(o, h, l, c, v)
    assert isinstance(feat, dict)
    assert len(feat) >= 14
    assert len(feat) <= 18


# ── 2. All arrays same length as input ────────────────────────────
def test_all_arrays_same_length():
    n = 500
    o, h, l, c, v = _make_ohlcv(n=n)
    feat = compute_quant_features(o, h, l, c, v)
    for name, arr in feat.items():
        assert len(arr) == n, f"{name} has length {len(arr)}, expected {n}"


# ── 3. No NaN/Inf ─────────────────────────────────────────────────
def test_no_nan_inf():
    o, h, l, c, v = _make_ohlcv()
    feat = compute_quant_features(o, h, l, c, v)
    for name, arr in feat.items():
        assert not np.any(np.isnan(arr)), f"{name} contains NaN"
        assert not np.any(np.isinf(arr)), f"{name} contains Inf"


# ── 4. Donchian position is 0-1 range ─────────────────────────────
def test_donchian_position_range():
    o, h, l, c, v = _make_ohlcv(n=1000)
    feat = compute_quant_features(o, h, l, c, v)
    for key in ["donch_20_position", "donch_55_position"]:
        arr = feat[key]
        # After warmup, values should be in [0, 1]
        assert np.all(arr >= -0.01), f"{key} has values below 0"
        assert np.all(arr <= 1.01), f"{key} has values above 1"


# ── 5. Donchian breakout values in {-1, 0, 1} ─────────────────────
def test_donchian_breakout_values():
    o, h, l, c, v = _make_ohlcv(n=1000)
    feat = compute_quant_features(o, h, l, c, v)
    unique = set(np.unique(feat["donch_20_breakout"]))
    assert unique.issubset({-1.0, 0.0, 1.0})


# ── 6. Z-scores centered around 0 ─────────────────────────────────
def test_zscores_centered():
    o, h, l, c, v = _make_ohlcv(n=2000)
    feat = compute_quant_features(o, h, l, c, v)
    for key in ["zscore_24", "zscore_96"]:
        # After warmup, mean should be near 0
        arr = feat[key][200:]
        assert abs(np.mean(arr)) < 1.0, f"{key} mean {np.mean(arr)} not near 0"


# ── 7. Hurst exponent in valid range (0-1) ────────────────────────
def test_hurst_valid_range():
    o, h, l, c, v = _make_ohlcv(n=1000)
    feat = compute_quant_features(o, h, l, c, v)
    arr = feat["hurst_100"]
    assert np.all(arr >= 0.0), "Hurst below 0"
    assert np.all(arr <= 1.0), "Hurst above 1"


# ── 8. TSMOM returns are reasonable ───────────────────────────────
def test_tsmom_reasonable():
    o, h, l, c, v = _make_ohlcv(n=1000)
    feat = compute_quant_features(o, h, l, c, v)
    for key in ["tsmom_48", "tsmom_96"]:
        arr = feat[key]
        assert np.all(np.abs(arr) < 10.0), f"{key} has extreme values"


# ── 9. Works with small data (200 bars) ───────────────────────────
def test_small_data():
    o, h, l, c, v = _make_ohlcv(n=200)
    feat = compute_quant_features(o, h, l, c, v)
    assert len(feat) >= 14
    for name, arr in feat.items():
        assert len(arr) == 200
        assert not np.any(np.isnan(arr))


# ── 10. Works with large data (5000 bars) ─────────────────────────
def test_large_data():
    o, h, l, c, v = _make_ohlcv(n=5000)
    feat = compute_quant_features(o, h, l, c, v)
    assert len(feat) >= 14
    for name, arr in feat.items():
        assert len(arr) == 5000
        assert not np.any(np.isnan(arr))


# ── 11. H4 data integration works ─────────────────────────────────
def test_h4_data_integration():
    o, h, l, c, v = _make_ohlcv(n=500)
    rng = np.random.default_rng(99)
    h4_c = 100.0 + np.cumsum(rng.standard_normal(500) * 0.5)
    h4_h = h4_c + 0.5
    h4_l = h4_c - 0.5
    feat = compute_quant_features(o, h, l, c, v,
                                  h4_highs=h4_h, h4_lows=h4_l, h4_closes=h4_c)
    assert len(feat) >= 14
    for name, arr in feat.items():
        assert len(arr) == 500
        assert not np.any(np.isnan(arr))


# ── 12. Float32 output ────────────────────────────────────────────
def test_float32_output():
    o, h, l, c, v = _make_ohlcv()
    feat = compute_quant_features(o, h, l, c, v)
    for name, arr in feat.items():
        assert arr.dtype == np.float32, f"{name} is {arr.dtype}, expected float32"


# ── 13. Hurst regime values in {-1, 0, 1} ─────────────────────────
def test_hurst_regime_values():
    o, h, l, c, v = _make_ohlcv(n=1000)
    feat = compute_quant_features(o, h, l, c, v)
    unique = set(np.unique(feat["hurst_regime"]))
    assert unique.issubset({-1.0, 0.0, 1.0})


# ── 14. Donchian squeeze is binary ────────────────────────────────
def test_donchian_squeeze_binary():
    o, h, l, c, v = _make_ohlcv(n=1000)
    feat = compute_quant_features(o, h, l, c, v)
    unique = set(np.unique(feat["donch_squeeze"]))
    assert unique.issubset({0.0, 1.0})


# ── 15. Expected feature names present ────────────────────────────
def test_expected_feature_names():
    o, h, l, c, v = _make_ohlcv()
    feat = compute_quant_features(o, h, l, c, v)
    expected = [
        "donch_20_position", "donch_55_position", "donch_20_breakout",
        "donch_squeeze", "donch_width_roc",
        "zscore_24", "zscore_96", "return_autocorr",
        "tsmom_48", "tsmom_96", "volscaled_mom",
        "hurst_100", "hurst_regime",
        "dist_prev_day_high_atr", "dist_prev_day_low_atr",
    ]
    for name in expected:
        assert name in feat, f"Missing feature: {name}"
