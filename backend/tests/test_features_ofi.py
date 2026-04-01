"""Tests for OFI feature module."""

import numpy as np
import pytest

from app.services.ml.features_ofi import (
    EXPECTED_FEATURE_COUNT,
    compute_ofi_features,
)


def _make_ohlcv(n: int, seed: int = 42):
    rng = np.random.RandomState(seed)
    closes = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    opens = closes + rng.randn(n) * 0.2
    highs = np.maximum(opens, closes) + np.abs(rng.randn(n) * 0.3)
    lows = np.minimum(opens, closes) - np.abs(rng.randn(n) * 0.3)
    volumes = (rng.exponential(1000, n)).astype(np.float64)
    return opens, highs, lows, closes, volumes


def _make_tick_data(n: int, seed: int = 42):
    rng = np.random.RandomState(seed)
    buy = rng.exponential(500, n).astype(np.float64)
    sell = rng.exponential(500, n).astype(np.float64)
    tick_count = rng.poisson(200, n).astype(np.float64)
    large_trades = rng.poisson(10, n).astype(np.float64)
    return buy, sell, tick_count, large_trades


class TestOFIFeaturesProxy:
    """Tests using proxy mode (no tick data)."""

    def test_feature_count(self):
        o, h, l, c, v = _make_ohlcv(200)
        feats = compute_ofi_features(o, h, l, c, v)
        assert len(feats) == EXPECTED_FEATURE_COUNT

    def test_array_lengths(self):
        n = 200
        o, h, l, c, v = _make_ohlcv(n)
        feats = compute_ofi_features(o, h, l, c, v)
        for name, arr in feats.items():
            assert len(arr) == n, f"{name} has wrong length {len(arr)}"

    def test_no_nan_inf(self):
        o, h, l, c, v = _make_ohlcv(300)
        feats = compute_ofi_features(o, h, l, c, v)
        for name, arr in feats.items():
            assert not np.any(np.isnan(arr)), f"{name} contains NaN"
            assert not np.any(np.isinf(arr)), f"{name} contains Inf"

    def test_float32(self):
        o, h, l, c, v = _make_ohlcv(100)
        feats = compute_ofi_features(o, h, l, c, v)
        for name, arr in feats.items():
            assert arr.dtype == np.float32, f"{name} dtype is {arr.dtype}"

    def test_naming_convention(self):
        o, h, l, c, v = _make_ohlcv(100)
        feats = compute_ofi_features(o, h, l, c, v)
        for name in feats:
            assert name.startswith("ofi_"), f"{name} missing ofi_ prefix"

    def test_vpin_in_range(self):
        o, h, l, c, v = _make_ohlcv(300)
        feats = compute_ofi_features(o, h, l, c, v)
        assert np.all(feats["ofi_vpin"] >= 0.0)
        assert np.all(feats["ofi_vpin"] <= 1.0)

    def test_zscore_centered(self):
        o, h, l, c, v = _make_ohlcv(500)
        feats = compute_ofi_features(o, h, l, c, v)
        zs = feats["ofi_zscore"]
        # After warm-up, z-score mean should be near 0
        assert abs(float(np.mean(zs[60:]))) < 1.5

    def test_small_data(self):
        o, h, l, c, v = _make_ohlcv(5)
        feats = compute_ofi_features(o, h, l, c, v)
        assert len(feats) == EXPECTED_FEATURE_COUNT
        for name, arr in feats.items():
            assert len(arr) == 5
            assert not np.any(np.isnan(arr))

    def test_large_data(self):
        o, h, l, c, v = _make_ohlcv(10000)
        feats = compute_ofi_features(o, h, l, c, v)
        assert len(feats) == EXPECTED_FEATURE_COUNT
        for arr in feats.values():
            assert len(arr) == 10000


class TestOFIFeaturesTickData:
    """Tests using real tick data."""

    def test_with_tick_data(self):
        n = 200
        o, h, l, c, v = _make_ohlcv(n)
        buy, sell, tc, lt = _make_tick_data(n)
        feats = compute_ofi_features(
            o, h, l, c, v,
            tick_buy_volume=buy,
            tick_sell_volume=sell,
            tick_count=tc,
            large_trade_count=lt,
        )
        assert len(feats) == EXPECTED_FEATURE_COUNT
        for name, arr in feats.items():
            assert len(arr) == n, f"{name} wrong length"
            assert arr.dtype == np.float32, f"{name} wrong dtype"
            assert not np.any(np.isnan(arr)), f"{name} has NaN"
            assert not np.any(np.isinf(arr)), f"{name} has Inf"

    def test_tick_vs_proxy_differ(self):
        n = 200
        o, h, l, c, v = _make_ohlcv(n)
        buy, sell, tc, lt = _make_tick_data(n)
        proxy = compute_ofi_features(o, h, l, c, v)
        tick = compute_ofi_features(
            o, h, l, c, v,
            tick_buy_volume=buy,
            tick_sell_volume=sell,
            tick_count=tc,
            large_trade_count=lt,
        )
        # At least the imbalance should differ between proxy and tick modes
        assert not np.allclose(
            proxy["ofi_imbalance"], tick["ofi_imbalance"], atol=1e-6
        ), "Tick and proxy imbalance should differ"
