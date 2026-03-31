"""Unit tests for technical indicators — compare against known values."""
import numpy as np
from app.services.backtest.indicators import (
    ema, sma, rsi, atr, macd, bollinger_bands, stochastic, cci, williams_r, obv, roc,
)


def test_sma_basic():
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float)
    result = sma(values, 3)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert result[2] == 2.0  # (1+2+3)/3
    assert result[3] == 3.0  # (2+3+4)/3
    assert result[4] == 4.0  # (3+4+5)/3


def test_ema_basic():
    values = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0], dtype=float)
    result = ema(values, 3)
    assert np.isnan(result[0])
    assert not np.isnan(result[2])
    # EMA should be responsive to recent values
    assert result[-1] > result[-2]


def test_rsi_range():
    """RSI should always be between 0 and 100."""
    np.random.seed(42)
    values = np.cumsum(np.random.randn(200)) + 100
    result = rsi(values, 14)
    valid = result[~np.isnan(result)]
    assert len(valid) > 0
    assert np.all(valid >= 0)
    assert np.all(valid <= 100)


def test_atr_positive():
    """ATR should always be positive."""
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(100)) + 100
    highs = closes + np.abs(np.random.randn(100))
    lows = closes - np.abs(np.random.randn(100))
    result = atr(highs, lows, closes, 14)
    valid = result[~np.isnan(result)]
    assert len(valid) > 0
    assert np.all(valid >= 0)


def test_macd_components():
    np.random.seed(42)
    values = np.cumsum(np.random.randn(100)) + 100
    line, signal, hist = macd(values, 12, 26, 9)
    assert len(line) == len(values)
    # Histogram should equal line - signal
    valid_mask = ~np.isnan(line) & ~np.isnan(signal)
    np.testing.assert_allclose(hist[valid_mask], (line - signal)[valid_mask], atol=1e-10)


def test_bollinger_bands_relationship():
    """Upper should be above lower, middle between them."""
    np.random.seed(42)
    values = np.cumsum(np.random.randn(100)) + 100
    upper, lower, middle, pct_b, bw = bollinger_bands(values, 20, 2.0)
    valid = ~np.isnan(upper) & ~np.isnan(lower) & ~np.isnan(middle)
    assert np.all(upper[valid] >= middle[valid])
    assert np.all(middle[valid] >= lower[valid])


def test_stochastic_range():
    """Stochastic %K should be 0-100."""
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(100)) + 100
    highs = closes + np.abs(np.random.randn(100))
    lows = closes - np.abs(np.random.randn(100))
    k, d = stochastic(highs, lows, closes, 14, 3)
    valid_k = k[~np.isnan(k)]
    assert np.all(valid_k >= 0)
    assert np.all(valid_k <= 100)


def test_obv_direction():
    """OBV should increase when price goes up."""
    closes = np.array([10, 11, 12, 11, 13], dtype=float)
    volumes = np.array([100, 200, 300, 200, 400], dtype=float)
    result = obv(closes, volumes)
    assert result[1] > result[0]  # price up -> OBV up
    assert result[3] < result[2]  # price down -> OBV down


def test_roc_basic():
    values = np.array([100, 105, 110, 108, 112, 115], dtype=float)
    result = roc(values, 2)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert abs(result[2] - 10.0) < 0.01  # (110-100)/100 * 100 = 10%
