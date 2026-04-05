"""
Pure numpy technical indicator implementations.
No TA-Lib dependency — all calculations done with numpy.
"""
import numpy as np


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    result = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return result
    alpha = 2.0 / (period + 1)
    result[period - 1] = np.mean(values[:period])
    for i in range(period, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    result = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return result
    cumsum = np.cumsum(values)
    cumsum[period:] = cumsum[period:] - cumsum[:-period]
    result[period - 1:] = cumsum[period - 1:] / period
    return result


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    result = np.full_like(values, np.nan, dtype=float)
    if len(values) < period + 1:
        return result
    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return result


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    result = np.full_like(closes, np.nan, dtype=float)
    if len(closes) < period + 1:
        return result
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    # Pad TR to match original length (first bar has no TR)
    tr = np.concatenate([[highs[0] - lows[0]], tr])

    result[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def macd(values: np.ndarray, fast: int = 12, slow: int = 26, signal_period: int = 9):
    """MACD line, signal line, histogram."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(values: np.ndarray, period: int = 20, std_dev: float = 2.0):
    """Bollinger Bands: middle, upper, lower, %B, bandwidth."""
    middle = sma(values, period)
    rolling_std = np.full_like(values, np.nan, dtype=float)
    for i in range(period - 1, len(values)):
        rolling_std[i] = np.std(values[i - period + 1 : i + 1], ddof=0)

    upper = middle + std_dev * rolling_std
    lower = middle - std_dev * rolling_std

    # %B = (price - lower) / (upper - lower)
    band_width = upper - lower
    pct_b = np.where(band_width > 0, (values - lower) / band_width, 0.5)
    bandwidth = np.where(middle > 0, band_width / middle, 0.0)

    return upper, lower, middle, pct_b, bandwidth


def stochastic(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               k_period: int = 14, d_period: int = 3):
    """Stochastic %K and %D."""
    k = np.full_like(closes, np.nan, dtype=float)
    for i in range(k_period - 1, len(closes)):
        highest = np.max(highs[i - k_period + 1 : i + 1])
        lowest = np.min(lows[i - k_period + 1 : i + 1])
        rng = highest - lowest
        k[i] = ((closes[i] - lowest) / rng * 100) if rng > 0 else 50.0
    d = sma(k, d_period)
    return k, d


def cci(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 20) -> np.ndarray:
    """Commodity Channel Index."""
    tp = (highs + lows + closes) / 3.0
    result = np.full_like(closes, np.nan, dtype=float)
    for i in range(period - 1, len(tp)):
        window = tp[i - period + 1 : i + 1]
        mean_val = np.mean(window)
        mean_dev = np.mean(np.abs(window - mean_val))
        if mean_dev > 0:
            result[i] = (tp[i] - mean_val) / (0.015 * mean_dev)
        else:
            result[i] = 0.0
    return result


def williams_r(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               period: int = 14) -> np.ndarray:
    """Williams %R."""
    result = np.full_like(closes, np.nan, dtype=float)
    for i in range(period - 1, len(closes)):
        highest = np.max(highs[i - period + 1 : i + 1])
        lowest = np.min(lows[i - period + 1 : i + 1])
        rng = highest - lowest
        result[i] = ((highest - closes[i]) / rng * -100) if rng > 0 else -50.0
    return result


def obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """On-Balance Volume."""
    result = np.zeros_like(closes, dtype=float)
    result[0] = volumes[0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result[i] = result[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            result[i] = result[i - 1] - volumes[i]
        else:
            result[i] = result[i - 1]
    return result


def roc(values: np.ndarray, period: int = 10) -> np.ndarray:
    """Rate of Change."""
    result = np.full_like(values, np.nan, dtype=float)
    for i in range(period, len(values)):
        if values[i - period] != 0:
            result[i] = (values[i] - values[i - period]) / values[i - period] * 100
    return result


def keltner_channels(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                     ema_period: int = 20, atr_period: int = 14, multiplier: float = 1.5):
    """Keltner Channels: upper, lower, middle."""
    middle = ema(closes, ema_period)
    atr_vals = atr(highs, lows, closes, atr_period)
    upper = middle + multiplier * atr_vals
    lower = middle - multiplier * atr_vals
    return upper, lower, middle


def adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14):
    """Average Directional Index. Returns (adx, plus_di, minus_di)."""
    n = len(closes)
    adx_out = np.full(n, np.nan, dtype=float)
    plus_di_out = np.full(n, np.nan, dtype=float)
    minus_di_out = np.full(n, np.nan, dtype=float)

    if n < period * 2:
        return adx_out, plus_di_out, minus_di_out

    # True Range
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))

    # Directional Movement
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    # Smoothed TR, +DM, -DM using Wilder's smoothing
    atr_s = np.zeros(n)
    plus_dm_s = np.zeros(n)
    minus_dm_s = np.zeros(n)

    atr_s[period] = np.sum(tr[1:period+1])
    plus_dm_s[period] = np.sum(plus_dm[1:period+1])
    minus_dm_s[period] = np.sum(minus_dm[1:period+1])

    for i in range(period + 1, n):
        atr_s[i] = atr_s[i-1] - atr_s[i-1] / period + tr[i]
        plus_dm_s[i] = plus_dm_s[i-1] - plus_dm_s[i-1] / period + plus_dm[i]
        minus_dm_s[i] = minus_dm_s[i-1] - minus_dm_s[i-1] / period + minus_dm[i]

    # +DI and -DI
    for i in range(period, n):
        if atr_s[i] > 0:
            plus_di_out[i] = 100 * plus_dm_s[i] / atr_s[i]
            minus_di_out[i] = 100 * minus_dm_s[i] / atr_s[i]

    # DX and ADX
    dx = np.zeros(n)
    for i in range(period, n):
        di_sum = plus_di_out[i] + minus_di_out[i]
        if di_sum > 0:
            dx[i] = 100 * abs(plus_di_out[i] - minus_di_out[i]) / di_sum

    # First ADX = average of first `period` DX values
    start = period * 2
    if start < n:
        adx_out[start] = np.mean(dx[period+1:start+1])
        for i in range(start + 1, n):
            adx_out[i] = (adx_out[i-1] * (period - 1) + dx[i]) / period

    return adx_out, plus_di_out, minus_di_out
