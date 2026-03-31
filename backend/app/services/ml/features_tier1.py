"""
Tier-1 feature additions for the ML pipeline.

All heavy computations are vectorised (pandas rolling / numpy) to handle
300k+ bar datasets without looping in Python.

Functions are all (arrays) -> np.ndarray with shape (n,).
NaN/Inf handling is done by the caller (features_mtf.py) via nan_to_num.
"""
import numpy as np
import pandas as pd


def _ts_to_utc_series(times: np.ndarray) -> pd.DatetimeIndex:
    """Convert integer Unix seconds to UTC DatetimeIndex (vectorised)."""
    return pd.to_datetime(times.astype("int64"), unit="s", utc=True)


# ── Yang-Zhang Volatility Estimator ───────────────────────────────────
# Yang-Zhang (2000) — 14× more efficient than close-to-close.
# Implemented with pandas rolling for O(n) performance.

def yang_zhang_vol(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    window: int = 20,
    bars_per_year: float = 252 * 288,
) -> np.ndarray:
    """
    Rolling Yang-Zhang volatility.  Returns annualised vol, same shape as closes.
    Uses pandas rolling for O(n) computation on large datasets.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        prev_close = pd.Series(closes).shift(1).values
        safe_prev  = np.where(prev_close > 0, prev_close, 1.0)
        safe_open  = np.where(opens > 0, opens, 1.0)

        # Overnight log-return (open vs previous close)
        o = np.zeros(len(closes))
        o[1:] = np.log(opens[1:] / safe_prev[1:])

        # Intraday log ratios (Rogers-Satchell components)
        u = np.log(highs  / safe_open)
        d = np.log(lows   / safe_open)
        c = np.log(closes / safe_open)

    k = 0.34 / (1.34 + (window + 1) / max(window - 1, 1))

    s = pd.Series

    # Rolling variance of overnight and close components
    var_overnight = s(o).rolling(window, min_periods=window).var().values
    var_oc        = s(c).rolling(window, min_periods=window).var().values

    # Rogers-Satchell: E[u*(u-c) + d*(d-c)]  (no drift assumption)
    rs = u * (u - c) + d * (d - c)
    var_rs = s(rs).rolling(window, min_periods=window).mean().values

    var_yz = var_overnight + k * var_oc + (1 - k) * var_rs
    var_yz = np.where(np.isnan(var_yz) | (var_yz < 0), 0.0, var_yz)
    return np.sqrt(var_yz * bars_per_year)


# ── Amihud Illiquidity Ratio ────────────────────────────────────────────

def amihud_illiquidity(
    closes: np.ndarray,
    volumes: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    """
    Rolling Amihud illiquidity: |log_return| / volume, normalised by rolling mean.
    Vectorised via pandas rolling.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        prev = np.concatenate([[closes[0]], closes[:-1]])
        safe_prev = np.where(prev > 0, prev, 1.0)
        abs_log_ret = np.abs(np.log(closes / safe_prev))
        safe_vol    = np.where(volumes > 0, volumes, 1e-8)
        raw = abs_log_ret / safe_vol

    # Rolling mean normalisation
    rolling_mean = pd.Series(raw).rolling(window, min_periods=1).mean().values
    norm = np.where(rolling_mean > 1e-12, raw / rolling_mean, 0.0)
    return np.clip(norm, 0, 10)


# ── Cumulative Volume Delta Proxy ──────────────────────────────────────

def cvd_proxy(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
) -> np.ndarray:
    """Per-bar CVD proxy (vectorised)."""
    hl = highs - lows
    co = closes - opens
    safe_hl = np.where(hl > 0, hl, 1.0)
    delta = np.where(hl > 0, co / safe_hl * volumes, 0.0)
    return delta


def cvd_cumulative_zscore(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    window: int = 100,
) -> np.ndarray:
    """Rolling z-score of cumulative CVD proxy (vectorised)."""
    delta   = cvd_proxy(opens, highs, lows, closes, volumes)
    cvd_cum = np.cumsum(delta)
    s       = pd.Series(cvd_cum)
    mu      = s.rolling(window, min_periods=window).mean()
    sigma   = s.rolling(window, min_periods=window).std()
    z       = ((s - mu) / sigma.clip(lower=1e-10)).fillna(0).clip(-5, 5)
    return z.values


# ── MTF Divergence Index ───────────────────────────────────────────────

def mtf_divergence_index(
    h1_trend: np.ndarray,
    h4_trend: np.ndarray,
    d1_bias: np.ndarray,
) -> np.ndarray:
    """Element-wise std of three HTF trend signals (vectorised)."""
    stack = np.stack([h1_trend, h4_trend, d1_bias], axis=1)
    return np.std(stack, axis=1)


# ── MTF Momentum Magnitude ─────────────────────────────────────────────

def mtf_momentum_magnitude(
    htf_closes: np.ndarray,
    htf_atr: np.ndarray,
    ema_period: int = 50,
) -> np.ndarray:
    """(close - EMA50) / ATR at HTF level (vectorised)."""
    from app.services.backtest.indicators import ema as _ema
    ema50    = _ema(htf_closes, ema_period)
    safe_atr = np.where(htf_atr > 0, htf_atr, 1e-8)
    return (htf_closes - ema50) / safe_atr


# ── Rolling Max Drawdown ───────────────────────────────────────────────

def rolling_max_drawdown(closes: np.ndarray, window: int) -> np.ndarray:
    """
    Rolling max drawdown (vectorised via pandas).
    Returns fraction (0 = flat, -0.10 = -10% peak-to-trough within window).
    """
    s    = pd.Series(closes)
    roll = s.rolling(window, min_periods=window)
    # Rolling peak within each window
    peak_in_window = roll.max()
    # Drawdown relative to peak
    dd = (s - peak_in_window) / peak_in_window.clip(lower=1e-12)
    # Min (most negative = max drawdown) — use rolling min on the drawdown series
    result = pd.Series(dd).rolling(window, min_periods=window).min()
    return result.fillna(0).values


# ── Continuous Session Proximity ──────────────────────────────────────

def session_proximity_features(times: np.ndarray) -> dict[str, np.ndarray]:
    """
    Compute continuous session proximity features (vectorised).
    Converts all timestamps to UTC minutes-since-midnight using pandas.
    """
    dti      = _ts_to_utc_series(times)
    utc_mins = (dti.hour * 60 + dti.minute).values.astype(float)

    NY_CLOSE  = 21 * 60   # 1260
    LON_OPEN  = 8  * 60   # 480
    LON_CLOSE = 16 * 60   # 960
    NY_OPEN   = 13 * 60   # 780
    MINS_DAY  = 24 * 60   # 1440
    SCALE     = 480.0

    m2nyc  = (NY_CLOSE  - utc_mins) % MINS_DAY
    ms_lon = (utc_mins  - LON_OPEN) % MINS_DAY
    m2lonc = (LON_CLOSE - utc_mins) % MINS_DAY
    m2nyo  = (NY_OPEN   - utc_mins) % MINS_DAY

    is_last30 = ((utc_mins >= NY_CLOSE - 30) & (utc_mins < NY_CLOSE)).astype(float)

    return {
        "mins_to_ny_close_norm":    np.clip(m2nyc  / SCALE, 0, 1),
        "mins_since_lon_open_norm": np.clip(ms_lon / SCALE, 0, 1),
        "mins_to_lon_close_norm":   np.clip(m2lonc / SCALE, 0, 1),
        "mins_to_ny_open_norm":     np.clip(m2nyo  / SCALE, 0, 1),
        "is_last_30min_ny":         is_last30,
    }


# ── DOM Cyclical Encoding ──────────────────────────────────────────────

def dom_cyclical(times: np.ndarray) -> dict[str, np.ndarray]:
    """sin/cos encoding of day-of-month (vectorised)."""
    dti = _ts_to_utc_series(times)
    dom = dti.day.values.astype(float)
    return {
        "dom_sin": np.sin(2 * np.pi * dom / 31),
        "dom_cos": np.cos(2 * np.pi * dom / 31),
    }


# ── Time-of-Day Range Ratio ────────────────────────────────────────────

def tod_range_ratio(
    highs: np.ndarray,
    lows: np.ndarray,
    times: np.ndarray,
    lookback_days: int = 20,
    bars_per_hour: int = 12,
) -> np.ndarray:
    """
    Ratio of current bar HL range to mean HL range at same hour-of-day
    over past `lookback_days` days.  Vectorised via pandas groupby + rolling.
    """
    dti    = _ts_to_utc_series(times)
    hours  = dti.hour.values
    hl     = highs - lows
    n      = len(hl)

    # For each hour bucket, compute rolling mean over lookback_days occurrences
    # bars_per_hour occurrences per day per bucket → lookback window = lookback_days
    result = np.ones(n)

    df = pd.DataFrame({"hl": hl, "hour": hours})
    # Rolling mean per hour group — compute as: for each row, mean of the
    # previous lookback_days rows in the same hour group.
    # Efficient approach: sort by hour then apply rolling, then restore order.
    mean_by_hour = (
        df.groupby("hour")["hl"]
        .transform(lambda s: s.shift(1).rolling(lookback_days * bars_per_hour, min_periods=1).mean())
    )
    safe_mean = mean_by_hour.clip(lower=1e-8)
    result = (df["hl"] / safe_mean).clip(0, 5).values
    return np.where(np.isnan(result), 1.0, result)


# ── Convenience: add_tier1_features ────────────────────────────────────

def add_tier1_features(
    features: dict,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    times: np.ndarray,
    h1_trend: np.ndarray | None = None,
    h4_trend: np.ndarray | None = None,
    d1_bias:  np.ndarray | None = None,
    h1_closes: np.ndarray | None = None,
    h4_closes: np.ndarray | None = None,
    h1_atr:    np.ndarray | None = None,
    h4_atr:    np.ndarray | None = None,
    bars_per_year: float = 252 * 288,
) -> dict:
    """
    Compute all Tier-1 features and add them to `features` dict in-place.
    All computations are vectorised.
    """
    n = len(closes)

    features["yz_vol_20"]   = yang_zhang_vol(opens, highs, lows, closes, window=20, bars_per_year=bars_per_year)
    features["amihud_illiq"] = amihud_illiquidity(closes, volumes, window=20)
    features["cvd_delta"]   = cvd_proxy(opens, highs, lows, closes, volumes)
    features["cvd_zscore"]  = cvd_cumulative_zscore(opens, highs, lows, closes, volumes, window=100)

    if h1_trend is not None and h4_trend is not None and d1_bias is not None:
        features["mtf_divergence"] = mtf_divergence_index(h1_trend, h4_trend, d1_bias)
    else:
        features["mtf_divergence"] = np.zeros(n)

    if h1_closes is not None and h1_atr is not None:
        features["h1_mom_mag"] = mtf_momentum_magnitude(h1_closes, h1_atr, 50)
    else:
        features["h1_mom_mag"] = np.zeros(n)

    if h4_closes is not None and h4_atr is not None:
        features["h4_mom_mag"] = mtf_momentum_magnitude(h4_closes, h4_atr, 50)
    else:
        features["h4_mom_mag"] = np.zeros(n)

    features["max_dd_50"]  = rolling_max_drawdown(closes, 50)
    features["max_dd_200"] = rolling_max_drawdown(closes, 200)

    features.update(session_proximity_features(times))
    features.update(dom_cyclical(times))

    features["tod_range_ratio"] = tod_range_ratio(highs, lows, times)

    return features
