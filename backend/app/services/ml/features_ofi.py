"""
Order Flow Imbalance (OFI) feature module — 15 features.

Computes order-flow, VPIN, volume-profile, and microstructure features
from OHLCV data. Uses proxy estimation when tick-level data is unavailable.
"""

import numpy as np
import pandas as pd

FEATURE_PREFIX = "ofi_"
EXPECTED_FEATURE_COUNT = 15


def _safe_divide(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Element-wise division, returning 0 where denominator is 0."""
    out = np.zeros_like(a, dtype=np.float32)
    mask = b != 0
    out[mask] = a[mask] / b[mask]
    return out


def _rolling_sum(x: np.ndarray, w: int) -> np.ndarray:
    s = pd.Series(x).rolling(w, min_periods=1).sum().values.astype(np.float32)
    return s


def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(x).rolling(w, min_periods=1).mean().values.astype(np.float32)


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(x).rolling(w, min_periods=1).std().values.astype(np.float32)


def _rolling_max(x: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(x).rolling(w, min_periods=1).max().values.astype(np.float32)


def _rolling_corr(a: np.ndarray, b: np.ndarray, w: int) -> np.ndarray:
    return (
        pd.Series(a)
        .rolling(w, min_periods=2)
        .corr(pd.Series(b))
        .fillna(0.0)
        .values.astype(np.float32)
    )


def compute_ofi_features(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    tick_buy_volume: np.ndarray | None = None,
    tick_sell_volume: np.ndarray | None = None,
    tick_count: np.ndarray | None = None,
    large_trade_count: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Compute 15 OFI features. Returns dict of float32 arrays, no NaN/Inf."""
    n = len(closes)
    opens = opens.astype(np.float64)
    highs = highs.astype(np.float64)
    lows = lows.astype(np.float64)
    closes = closes.astype(np.float64)
    volumes = volumes.astype(np.float64)

    use_tick = tick_buy_volume is not None and tick_sell_volume is not None
    hl_range = highs - lows
    hl_range_safe = np.where(hl_range == 0, 1.0, hl_range)
    vol_avg = _rolling_mean(volumes, 20)
    vol_avg_safe = np.where(vol_avg == 0, 1.0, vol_avg)

    # --- Buy / sell volume (real or proxy) ---
    if use_tick:
        buy_vol = tick_buy_volume.astype(np.float64)
        sell_vol = tick_sell_volume.astype(np.float64)
    else:
        ratio = (closes - opens) / hl_range_safe
        buy_vol = (0.5 + 0.5 * ratio) * volumes
        sell_vol = volumes - buy_vol

    total_vol = buy_vol + sell_vol
    total_vol_safe = np.where(total_vol == 0, 1.0, total_vol)

    # ===== Order Flow Imbalance (4) =====
    if use_tick:
        imbalance = _safe_divide(
            (buy_vol - sell_vol).astype(np.float32), total_vol_safe.astype(np.float32)
        )
    else:
        raw = (closes - opens) / hl_range_safe * (volumes / vol_avg_safe)
        imbalance = np.clip(raw, -5, 5).astype(np.float32)

    imbalance_5 = _rolling_sum(imbalance, 5)
    imbalance_20 = _rolling_sum(imbalance, 20)

    mu = _rolling_mean(imbalance, 50)
    sigma = _rolling_std(imbalance, 50)
    sigma_safe = np.where(sigma == 0, 1.0, sigma)
    zscore = ((imbalance - mu) / sigma_safe).astype(np.float32)

    # ===== VPIN (3) =====
    if use_tick:
        raw_vpin = _safe_divide(
            np.abs(buy_vol - sell_vol).astype(np.float32),
            total_vol_safe.astype(np.float32),
        )
    else:
        raw_vpin = (np.abs(closes - opens) / hl_range_safe).astype(np.float32)

    vpin = _rolling_mean(raw_vpin, 20)
    vpin = np.clip(vpin, 0.0, 1.0)

    pctile_75 = (
        pd.Series(vpin)
        .rolling(100, min_periods=1)
        .quantile(0.75)
        .values.astype(np.float32)
    )
    vpin_regime = (vpin > pctile_75).astype(np.float32)

    vpin_lag5 = np.roll(vpin, 5)
    vpin_lag5[:5] = vpin[:5]
    vpin_lag5_safe = np.where(vpin_lag5 == 0, 1e-8, vpin_lag5)
    vpin_change = ((vpin - vpin_lag5) / vpin_lag5_safe).astype(np.float32)

    # ===== Volume (4) =====
    if large_trade_count is not None and tick_count is not None:
        tc_safe = np.where(tick_count == 0, 1.0, tick_count).astype(np.float64)
        large_trade_ratio = (large_trade_count.astype(np.float64) / tc_safe).astype(
            np.float32
        )
    else:
        large_trade_ratio = (volumes > 2.0 * vol_avg_safe).astype(np.float32)

    vol_max_20 = _rolling_max(volumes, 20).astype(np.float64)
    vol_mean_20 = _rolling_mean(volumes, 20).astype(np.float64)
    vol_mean_20_safe = np.where(vol_mean_20 == 0, 1.0, vol_mean_20)
    vol_concentration = (vol_max_20 / vol_mean_20_safe).astype(np.float32)

    up = (closes > opens).astype(np.float64)
    dn = (closes < opens).astype(np.float64)
    up_vol = _rolling_sum((up * volumes).astype(np.float32), 20).astype(np.float64)
    dn_vol = _rolling_sum((dn * volumes).astype(np.float32), 20).astype(np.float64)
    dn_vol_safe = np.where(dn_vol == 0, 1.0, dn_vol)
    vol_asymmetry = (up_vol / dn_vol_safe).astype(np.float32)

    if tick_count is not None:
        tc = tick_count.astype(np.float64)
        tc_avg = _rolling_mean(tc, 20).astype(np.float64)
        tc_avg_safe = np.where(tc_avg == 0, 1.0, tc_avg)
        tick_intensity = (tc / tc_avg_safe).astype(np.float32)
    else:
        tick_intensity = (volumes / vol_avg_safe).astype(np.float32)

    # ===== Microstructure (4) =====
    # bid_ask_bounce: count of close alternating near high vs low in 10-bar window
    near_high = (closes - lows) / hl_range_safe  # 1 = near high, 0 = near low
    near_label = (near_high > 0.5).astype(np.float64)
    alternates = np.zeros(n, dtype=np.float64)
    alternates[1:] = (np.diff(near_label) != 0).astype(np.float64)
    bid_ask_bounce = _rolling_sum(alternates.astype(np.float32), 10)

    # tick_direction_run: max consecutive same-sign returns in 10-bar window
    rets = np.zeros(n, dtype=np.float64)
    rets[1:] = closes[1:] - closes[:-1]
    sign = np.sign(rets)
    runs = np.ones(n, dtype=np.float64)
    for i in range(1, n):
        if sign[i] != 0 and sign[i] == sign[i - 1]:
            runs[i] = runs[i - 1] + 1
    tick_dir_run = (
        pd.Series(runs).rolling(10, min_periods=1).max().values.astype(np.float32)
    )

    # return_vol_corr: 20-bar rolling corr(|return|, volume)
    abs_rets = np.abs(rets).astype(np.float32)
    return_vol_corr = _rolling_corr(abs_rets, volumes.astype(np.float32), 20)

    # net_pressure: 50-bar cumulative OFI / cumulative volume
    cum_ofi = _rolling_sum(imbalance, 50)
    cum_vol = _rolling_sum(volumes.astype(np.float32), 50)
    cum_vol_safe = np.where(cum_vol == 0, 1.0, cum_vol)
    net_pressure = (cum_ofi / cum_vol_safe).astype(np.float32)

    # ===== Assemble & sanitize =====
    features: dict[str, np.ndarray] = {
        "ofi_imbalance": imbalance,
        "ofi_imbalance_5": imbalance_5,
        "ofi_imbalance_20": imbalance_20,
        "ofi_zscore": zscore,
        "ofi_vpin": vpin,
        "ofi_vpin_regime": vpin_regime,
        "ofi_vpin_change": vpin_change,
        "ofi_large_trade_ratio": large_trade_ratio,
        "ofi_vol_concentration": vol_concentration,
        "ofi_vol_asymmetry": vol_asymmetry,
        "ofi_tick_intensity": tick_intensity,
        "ofi_bid_ask_bounce": bid_ask_bounce,
        "ofi_tick_direction_run": tick_dir_run,
        "ofi_return_vol_corr": return_vol_corr,
        "ofi_net_pressure": net_pressure,
    }

    for k, v in features.items():
        v = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
        features[k] = v

    return features
