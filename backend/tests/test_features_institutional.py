"""Tests for institutional feature computation (VWAP, S/D Zones, Wyckoff)."""
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


def test_inst_returns_dict():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_institutional_features(o, h, l, c, v, t)
    assert isinstance(result, dict)


def test_inst_feature_count():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_institutional_features(o, h, l, c, v, t)
    assert len(result) >= 18, f"Expected >= 18 features, got {len(result)}"


def test_inst_array_lengths():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv(n=400)
    result = compute_institutional_features(o, h, l, c, v, t)
    for name, arr in result.items():
        assert len(arr) == 400, f"{name} length {len(arr)} != 400"


def test_inst_no_nan():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_institutional_features(o, h, l, c, v, t)
    for name, arr in result.items():
        assert not np.any(np.isnan(arr)), f"NaN in {name}"


def test_vwap_features_present():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_institutional_features(o, h, l, c, v, t)
    vwap_keys = ["inst_vwap_dist_atr", "inst_vwap_band_pos", "inst_vwap_slope", "inst_vwap_cross"]
    for k in vwap_keys:
        assert k in result, f"Missing VWAP feature: {k}"


def test_sd_zone_features_present():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_institutional_features(o, h, l, c, v, t)
    sd_keys = ["inst_sd_nearest_dist", "inst_sd_zone_freshness", "inst_sd_zone_strength",
               "inst_sd_zone_type", "inst_at_demand", "inst_at_supply"]
    for k in sd_keys:
        assert k in result, f"Missing S/D feature: {k}"


def test_wyckoff_features_present():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_institutional_features(o, h, l, c, v, t)
    wk_keys = ["inst_effort_vs_result", "inst_spring_upthrust", "inst_volume_climax", "inst_atr_compression"]
    for k in wk_keys:
        assert k in result, f"Missing Wyckoff feature: {k}"


def test_va_position_bounded():
    from app.services.ml.features_institutional import compute_institutional_features
    o, h, l, c, v, t = _make_ohlcv()
    result = compute_institutional_features(o, h, l, c, v, t)
    assert np.all(result["inst_va_position"] >= 0)
    assert np.all(result["inst_va_position"] <= 1.0)
