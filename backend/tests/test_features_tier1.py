"""Tests for features_tier1.py: Yang-Zhang vol, Amihud, CVD, MTF features, drawdown, session, DOM."""
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_tier1 import (
    yang_zhang_vol,
    amihud_illiquidity,
    cvd_proxy,
    cvd_cumulative_zscore,
    mtf_divergence_index,
    mtf_momentum_magnitude,
    rolling_max_drawdown,
    session_proximity_features,
    dom_cyclical,
    tod_range_ratio,
    add_tier1_features,
)

# ── Fixtures ───────────────────────────────────────────────────────────

N = 500


@pytest.fixture
def ohlcv():
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 0.5, N))
    close = np.abs(close) + 1
    open_ = close + rng.normal(0, 0.2, N)
    high  = np.maximum(open_, close) + np.abs(rng.normal(0, 0.3, N))
    low   = np.minimum(open_, close) - np.abs(rng.normal(0, 0.3, N))
    vol   = np.abs(rng.normal(100, 20, N)) + 10
    # Unix timestamps: start at 2023-01-02 00:00 UTC, 5-min intervals
    times = np.arange(1672617600, 1672617600 + N * 300, 300, dtype=int)
    return open_, high, low, close, vol, times


# ── Yang-Zhang Volatility ──────────────────────────────────────────────


def test_yang_zhang_shape(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = yang_zhang_vol(o, h, l, c)
    assert result.shape == (N,)


def test_yang_zhang_non_negative(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = yang_zhang_vol(o, h, l, c)
    assert np.all(result >= 0)


def test_yang_zhang_warmup_zeros(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = yang_zhang_vol(o, h, l, c, window=20)
    # First window-1 bars should be zero (pandas rolling warmup is window-1)
    assert np.all(result[:19] == 0)


def test_yang_zhang_positive_after_warmup(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = yang_zhang_vol(o, h, l, c, window=20)
    assert np.any(result[20:] > 0)


def test_yang_zhang_higher_vol_on_volatile_data():
    """More volatile price series should produce higher YZ vol."""
    n = 200
    t = np.arange(n, dtype=float)
    c_flat    = np.ones(n) * 100
    c_volatile = 100 + np.sin(t * 0.3) * 5
    o = c_flat.copy(); h = c_flat + 0.1; l = c_flat - 0.1
    yz_flat = yang_zhang_vol(o, h, l, c_flat, window=20)

    ov = c_volatile.copy(); hv = c_volatile + 2; lv = c_volatile - 2
    yz_vol = yang_zhang_vol(ov, hv, lv, c_volatile, window=20)

    assert np.mean(yz_vol[50:]) > np.mean(yz_flat[50:])


# ── Amihud Illiquidity ─────────────────────────────────────────────────


def test_amihud_shape(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = amihud_illiquidity(c, v)
    assert result.shape == (N,)


def test_amihud_non_negative(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = amihud_illiquidity(c, v)
    assert np.all(result >= 0)


def test_amihud_clipped(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = amihud_illiquidity(c, v)
    assert np.all(result <= 10)


def test_amihud_zero_volume_safe():
    """Should not raise on zero volume."""
    c = np.array([100.0, 101.0, 102.0, 100.5, 99.0] * 10)
    v = np.zeros(50)
    result = amihud_illiquidity(c, v)
    assert not np.any(np.isnan(result))
    assert not np.any(np.isinf(result))


# ── CVD Proxy ──────────────────────────────────────────────────────────


def test_cvd_proxy_shape(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = cvd_proxy(o, h, l, c, v)
    assert result.shape == (N,)


def test_cvd_proxy_zero_hl():
    """Zero high-low range → delta = 0."""
    o = np.array([100.0] * 10)
    h = np.array([100.0] * 10)
    l = np.array([100.0] * 10)
    c = np.array([100.0] * 10)
    v = np.array([50.0] * 10)
    result = cvd_proxy(o, h, l, c, v)
    assert np.all(result == 0)


def test_cvd_bullish_bar_positive():
    """Bullish bar (close > open) → positive CVD delta."""
    o = np.array([100.0])
    c = np.array([102.0])
    h = np.array([103.0])
    l = np.array([99.0])
    v = np.array([200.0])
    result = cvd_proxy(o, h, l, c, v)
    assert result[0] > 0


def test_cvd_bearish_bar_negative():
    """Bearish bar (close < open) → negative CVD delta."""
    o = np.array([102.0])
    c = np.array([100.0])
    h = np.array([103.0])
    l = np.array([99.0])
    v = np.array([200.0])
    result = cvd_proxy(o, h, l, c, v)
    assert result[0] < 0


def test_cvd_zscore_shape(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = cvd_cumulative_zscore(o, h, l, c, v)
    assert result.shape == (N,)


def test_cvd_zscore_bounded(ohlcv):
    """Z-score should not blow up on typical data."""
    o, h, l, c, v, _ = ohlcv
    result = cvd_cumulative_zscore(o, h, l, c, v, window=100)
    finite = result[~np.isnan(result)]
    assert np.all(np.abs(finite) < 100)


# ── MTF Divergence ────────────────────────────────────────────────────


def test_mtf_divergence_aligned():
    """Fully aligned signals → std = 0."""
    sig = np.ones(100)
    result = mtf_divergence_index(sig, sig, sig)
    assert np.allclose(result, 0)


def test_mtf_divergence_max():
    """Max disagreement: (+1, -1, -1) → std ≈ 1.15."""
    h1 = np.ones(100)
    h4 = np.ones(100) * -1
    d1 = np.ones(100) * -1
    result = mtf_divergence_index(h1, h4, d1)
    # std([1, -1, -1]) = std of 3 values
    expected = np.std([1, -1, -1])
    assert np.allclose(result, expected)


def test_mtf_divergence_shape(ohlcv):
    sig = np.ones(N)
    result = mtf_divergence_index(sig, sig * -1, sig)
    assert result.shape == (N,)


# ── MTF Momentum Magnitude ────────────────────────────────────────────


def test_mtf_momentum_shape(ohlcv):
    o, h, l, c, v, _ = ohlcv
    from app.services.backtest.indicators import atr as _atr
    atr_vals = _atr(h, l, c, 14)
    result = mtf_momentum_magnitude(c, atr_vals)
    assert result.shape == (N,)


# ── Rolling Max Drawdown ──────────────────────────────────────────────


def test_rolling_max_drawdown_shape(ohlcv):
    o, h, l, c, v, _ = ohlcv
    result = rolling_max_drawdown(c, 50)
    assert result.shape == (N,)


def test_rolling_max_drawdown_non_positive(ohlcv):
    """Max drawdown is always <= 0 (it's a negative value)."""
    o, h, l, c, v, _ = ohlcv
    result = rolling_max_drawdown(c, 50)
    assert np.all(result <= 0)


def test_rolling_max_drawdown_monotone_series():
    """Strictly increasing series → drawdown = 0."""
    c = np.arange(1.0, 101.0)
    result = rolling_max_drawdown(c, 20)
    assert np.allclose(result[20:], 0)


# ── Session Proximity ─────────────────────────────────────────────────


def test_session_proximity_shape(ohlcv):
    o, h, l, c, v, times = ohlcv
    result = session_proximity_features(times)
    for key, arr in result.items():
        assert arr.shape == (N,), f"{key} shape mismatch"


def test_session_proximity_bounded(ohlcv):
    o, h, l, c, v, times = ohlcv
    result = session_proximity_features(times)
    for key, arr in result.items():
        assert np.all(arr >= 0), f"{key} has negative values"
        assert np.all(arr <= 1), f"{key} exceeds 1.0"


def test_is_last_30min_binary(ohlcv):
    o, h, l, c, v, times = ohlcv
    result = session_proximity_features(times)
    unique = np.unique(result["is_last_30min_ny"])
    assert set(unique.tolist()).issubset({0.0, 1.0})


# ── DOM Cyclical ──────────────────────────────────────────────────────


def test_dom_cyclical_shape(ohlcv):
    o, h, l, c, v, times = ohlcv
    result = dom_cyclical(times)
    assert result["dom_sin"].shape == (N,)
    assert result["dom_cos"].shape == (N,)


def test_dom_cyclical_bounded(ohlcv):
    o, h, l, c, v, times = ohlcv
    result = dom_cyclical(times)
    assert np.all(result["dom_sin"] >= -1)
    assert np.all(result["dom_sin"] <= 1)
    assert np.all(result["dom_cos"] >= -1)
    assert np.all(result["dom_cos"] <= 1)


def test_dom_sin_cos_unit_circle(ohlcv):
    """sin² + cos² should equal 1 for all bars."""
    o, h, l, c, v, times = ohlcv
    result = dom_cyclical(times)
    s2 = result["dom_sin"] ** 2
    c2 = result["dom_cos"] ** 2
    assert np.allclose(s2 + c2, 1.0)


# ── TOD Range Ratio ────────────────────────────────────────────────────


def test_tod_range_ratio_shape(ohlcv):
    o, h, l, c, v, times = ohlcv
    result = tod_range_ratio(h, l, times)
    assert result.shape == (N,)


def test_tod_range_ratio_clipped(ohlcv):
    o, h, l, c, v, times = ohlcv
    result = tod_range_ratio(h, l, times)
    assert np.all(result >= 0)
    assert np.all(result <= 5)


# ── add_tier1_features integration ────────────────────────────────────


def test_add_tier1_features_adds_keys(ohlcv):
    o, h, l, c, v, times = ohlcv
    features = {}
    add_tier1_features(features, o, h, l, c, v, times)

    expected_keys = [
        "yz_vol_20", "amihud_illiq", "cvd_delta", "cvd_zscore",
        "mtf_divergence", "h1_mom_mag", "h4_mom_mag",
        "max_dd_50", "max_dd_200",
        "mins_to_ny_close_norm", "mins_since_lon_open_norm",
        "dom_sin", "dom_cos",
        "tod_range_ratio",
    ]
    for key in expected_keys:
        assert key in features, f"Missing key: {key}"


def test_add_tier1_features_no_nan(ohlcv):
    o, h, l, c, v, times = ohlcv
    features = {}
    add_tier1_features(features, o, h, l, c, v, times)
    for key, arr in features.items():
        assert not np.any(np.isnan(arr)), f"NaN in {key}"
        assert not np.any(np.isinf(arr)), f"Inf in {key}"


def test_add_tier1_with_htf_arrays(ohlcv):
    """Passing HTF trend arrays should populate MTF divergence and momentum."""
    o, h, l, c, v, times = ohlcv
    h1_trend = np.ones(N)
    h4_trend = np.ones(N) * -1
    d1_bias  = np.zeros(N)
    features = {}
    add_tier1_features(features, o, h, l, c, v, times,
                       h1_trend=h1_trend, h4_trend=h4_trend, d1_bias=d1_bias,
                       h1_closes=c, h4_closes=c,
                       h1_atr=h - l, h4_atr=h - l)
    assert "mtf_divergence" in features
    # Should be non-zero since trends disagree
    assert np.any(features["mtf_divergence"] != 0)
