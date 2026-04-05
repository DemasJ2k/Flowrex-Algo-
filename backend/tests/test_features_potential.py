"""Tests for features_potential.py — Potential Agent institutional features."""
import numpy as np
import pandas as pd
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.ml.features_potential import compute_potential_features


def _make_bars(n=500, seed=42):
    """Generate synthetic M5 OHLCV bars."""
    rng = np.random.RandomState(seed)
    base = 40000 + np.cumsum(rng.randn(n) * 50)
    highs = base + rng.uniform(10, 100, n)
    lows = base - rng.uniform(10, 100, n)
    opens = base + rng.uniform(-30, 30, n)
    closes = base + rng.uniform(-30, 30, n)
    volumes = rng.uniform(100, 10000, n)
    # Timestamps: M5 bars starting from 2024-01-02 13:30 UTC
    start_ts = int(pd.Timestamp("2024-01-02 13:30", tz="UTC").timestamp())
    times = np.arange(n) * 300 + start_ts
    return pd.DataFrame({
        "time": times, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": volumes,
    })


def _make_htf_bars(n_m5, tf_multiple, seed=99):
    rng = np.random.RandomState(seed)
    n = n_m5 // tf_multiple + 1
    base = 40000 + np.cumsum(rng.randn(n) * 50)
    start_ts = int(pd.Timestamp("2024-01-02 00:00", tz="UTC").timestamp())
    times = np.arange(n) * (300 * tf_multiple) + start_ts
    return pd.DataFrame({
        "time": times, "open": base, "high": base + 50,
        "low": base - 50, "close": base + 10,
        "volume": rng.uniform(1000, 50000, n),
    })


class TestFeatureCount:
    def test_feature_count_range(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        assert 70 <= len(names) <= 90, f"Expected 70-90 features, got {len(names)}"

    def test_feature_names_unique(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        assert len(names) == len(set(names)), "Duplicate feature names"


class TestArrayShape:
    def test_output_shape(self):
        n = 500
        bars = _make_bars(n)
        names, X = compute_potential_features(bars)
        assert X.shape == (n, len(names))

    def test_small_data(self):
        bars = _make_bars(50)
        names, X = compute_potential_features(bars)
        assert X.shape[0] == 50
        assert X.shape[1] == len(names)

    def test_large_data(self):
        bars = _make_bars(2000)
        names, X = compute_potential_features(bars)
        assert X.shape[0] == 2000


class TestDataQuality:
    def test_no_nan(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        assert not np.any(np.isnan(X)), "NaN found in output"

    def test_no_inf(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        assert not np.any(np.isinf(X)), "Inf found in output"

    def test_float32(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        assert X.dtype == np.float32


class TestFeatureGroups:
    def test_vwap_features(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        vwap_feats = [n for n in names if "vwap" in n]
        assert len(vwap_feats) >= 5, f"Expected >= 5 VWAP features, got {len(vwap_feats)}"

    def test_volume_profile_features(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        vp_feats = [n for n in names if "poc" in n or "vah" in n or "val" in n]
        assert len(vp_feats) >= 5, f"Expected >= 5 Volume Profile features, got {len(vp_feats)}"

    def test_adx_features(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        adx_feats = [n for n in names if "adx" in n or "plus_di" in n or "minus_di" in n]
        assert len(adx_feats) >= 4, f"Expected >= 4 ADX features, got {len(adx_feats)}"

    def test_orb_features(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        orb_feats = [n for n in names if "orb" in n]
        assert len(orb_feats) >= 4, f"Expected >= 4 ORB features, got {len(orb_feats)}"

    def test_ema_features(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        ema_feats = [n for n in names if "ema" in n]
        assert len(ema_feats) >= 6, f"Expected >= 6 EMA features, got {len(ema_feats)}"

    def test_htf_features(self):
        n = 500
        bars = _make_bars(n)
        h1 = _make_htf_bars(n, 12)
        h4 = _make_htf_bars(n, 48)
        d1 = _make_htf_bars(n, 288)
        names, X = compute_potential_features(bars, h1, h4, d1)
        htf_feats = [n for n in names if "h1_" in n or "h4_" in n or "d1_" in n or "htf" in n]
        assert len(htf_feats) >= 5, f"Expected >= 5 HTF features, got {len(htf_feats)}"

    def test_all_prefixed(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        for name in names:
            assert name.startswith("pot_"), f"Feature '{name}' missing 'pot_' prefix"


class TestHTFHandling:
    def test_without_htf(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        assert X.shape[0] == 500

    def test_with_htf(self):
        n = 500
        bars = _make_bars(n)
        h1 = _make_htf_bars(n, 12)
        h4 = _make_htf_bars(n, 48)
        d1 = _make_htf_bars(n, 288)
        names, X = compute_potential_features(bars, h1, h4, d1)
        assert X.shape[0] == n
        assert not np.any(np.isnan(X))


class TestValueRanges:
    def test_rsi_range(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        rsi_idx = names.index("pot_rsi_14")
        rsi_vals = X[:, rsi_idx]
        valid = rsi_vals[rsi_vals != 0]
        if len(valid) > 0:
            assert valid.min() >= 0 and valid.max() <= 100

    def test_binary_features(self):
        bars = _make_bars(500)
        names, X = compute_potential_features(bars)
        binary_feats = ["pot_vwap_above", "pot_poc_above", "pot_adx_strong",
                        "pot_orb_break_up", "pot_orb_break_down", "pot_cash_open"]
        for fname in binary_feats:
            if fname in names:
                idx = names.index(fname)
                vals = set(np.unique(X[:, idx]))
                assert vals.issubset({0.0, 1.0}), f"{fname} has non-binary values: {vals}"
