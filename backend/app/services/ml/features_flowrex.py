"""
Flowrex Agent v2 — 120 curated ML features.

Combines the best features from:
  - Potential Agent v2 (VWAP, VP, ADX, ORB, EMA, RSI, MACD, Volatility) — 30
  - ICT/SMC (BOS/CHOCH, liquidity, OB, FVG, PD, OTE, displacement) — 20
  - Larry Williams (stretch, %R, smash day, value line) — 15
  - Donchian/Quant (channels, z-scores, momentum, Hurst) — 15
Plus new:
  - 4-layer HTF alignment (D1/H4/H1 EMA200/trend/RSI/MACD/momentum) — 20
  - Session/time cyclic encoding — 10
  - Microstructure (spread, absorption, CVD, delta divergence) — 10

All features prefixed with 'fx_'.
Entry: compute_flowrex_features(m5_bars, h1_bars, h4_bars, d1_bars, symbol)
Returns: (feature_names, X_matrix) — shape (n_bars, 120), float32, no NaN/Inf.
"""
import numpy as np
import pandas as pd

from app.services.backtest.indicators import (
    ema, sma, rsi, atr, macd, bollinger_bands, adx,
)
from app.services.ml.features_potential import (
    _to_arrays, _slope, _crossover, _rolling_max, _rolling_min,
    _align_htf_ohlcv, _anchored_vwap, _session_vwap, _volume_profile,
    _opening_range,
)
from app.services.ml.features_ict import compute_ict_features
from app.services.ml.features_williams import compute_williams_features
from app.services.ml.features_quant import compute_quant_features


# ── Cherry-pick lists ────────────────────────────────────────────────────

_ICT_PICKS = [
    "ict_trend", "ict_choch_recent", "ict_bos_momentum", "ict_bars_since_choch",
    "ict_sweep_bull", "ict_sweep_bear", "ict_dist_to_liq_high", "ict_dist_to_liq_low",
    "ict_ob_bull_active", "ict_ob_bear_active", "ict_ob_in_ote",
    "ict_fvg_bull_count", "ict_fvg_nearest_dist", "ict_fvg_ce_touch",
    "ict_pd_position", "ict_in_discount_bull",
    "ict_in_ote_zone", "ict_ote_dist_705",
    "ict_disp_strength", "ict_confluence_score",
]

_LW_PICKS = [
    "lw_stretch_up", "lw_above_stretch", "lw_stretch_ratio",
    "lw_range_expansion", "lw_nr7", "lw_inside_bar",
    "lw_wr_5", "lw_wr_28", "lw_wr_aligned_bull", "lw_wr_aligned_bear",
    "lw_wr_bull_divergence",
    "lw_smash_bull", "lw_smash_bear",
    "lw_above_value", "lw_value_slope",
]

_QUANT_PICKS = [
    "donch_20_position", "donch_55_position", "donch_20_breakout",
    "donch_squeeze", "donch_width_roc",
    "zscore_24", "zscore_96", "return_autocorr",
    "tsmom_48", "tsmom_96", "volscaled_mom",
    "hurst_100", "hurst_regime",
    "dist_prev_day_high_atr", "dist_prev_day_low_atr",
]


def compute_flowrex_features(
    m5_bars,
    h1_bars=None, h4_bars=None, d1_bars=None,
    symbol: str = "US30",
) -> tuple[list[str], np.ndarray]:
    """Compute 120 curated features for Flowrex Agent v2."""
    times, opens, highs, lows, closes, volumes = _to_arrays(m5_bars)
    n = len(closes)
    features: dict[str, np.ndarray] = {}

    # ── Base indicators ──────────────────────────────────────────────────
    atr_14 = atr(highs, lows, closes, 14)
    atr_safe = np.where((atr_14 > 0) & ~np.isnan(atr_14), atr_14, 1.0)
    hl_range = highs - lows

    # ==================================================================
    # GROUP 1: VWAP (6)
    # ==================================================================
    vwap_sess = _session_vwap(closes, highs, lows, volumes, times)
    vwap_week = _anchored_vwap(closes, highs, lows, volumes, times, "weekly")
    vwap_month = _anchored_vwap(closes, highs, lows, volumes, times, "monthly")

    features["fx_vwap_dist_atr"] = (closes - vwap_sess) / atr_safe
    vwap_std = pd.Series(closes - vwap_sess).rolling(20, min_periods=1).std().fillna(1).values
    features["fx_vwap_zscore"] = np.where(vwap_std > 0, (closes - vwap_sess) / vwap_std, 0)
    features["fx_vwap_above"] = (closes > vwap_sess).astype(float)
    features["fx_vwap_weekly_dist_atr"] = (closes - vwap_week) / atr_safe
    features["fx_vwap_monthly_dist_atr"] = (closes - vwap_month) / atr_safe
    features["fx_vwap_cross"] = _crossover(closes, vwap_sess)

    # ==================================================================
    # GROUP 2: Volume Profile (5)
    # ==================================================================
    poc, vah, val = _volume_profile(highs, lows, volumes)
    features["fx_poc_dist_atr"] = (closes - poc) / atr_safe
    features["fx_vah_dist_atr"] = (vah - closes) / atr_safe
    features["fx_val_dist_atr"] = (closes - val) / atr_safe
    va_range = vah - val
    features["fx_value_area_pos"] = np.where(va_range > 0, (closes - val) / va_range, 0.5)
    features["fx_value_area_width_atr"] = va_range / atr_safe

    # ==================================================================
    # GROUP 3: ADX (4)
    # ==================================================================
    adx_val, plus_di, minus_di = adx(highs, lows, closes, 14)
    adx_val = np.nan_to_num(adx_val, 0)
    plus_di = np.nan_to_num(plus_di, 0)
    minus_di = np.nan_to_num(minus_di, 0)
    features["fx_adx"] = adx_val
    features["fx_plus_di"] = plus_di
    features["fx_minus_di"] = minus_di
    features["fx_di_cross"] = _crossover(plus_di, minus_di)

    # ==================================================================
    # GROUP 4: ORB (3)
    # ==================================================================
    orb_high, orb_low = _opening_range(highs, lows, closes, times)
    orb_range = orb_high - orb_low
    features["fx_orb_break_up"] = (closes > orb_high).astype(float)
    features["fx_orb_position"] = np.where(orb_range > 0, (closes - orb_low) / orb_range, 0.5)
    features["fx_orb_range_atr"] = orb_range / atr_safe

    # ==================================================================
    # GROUP 5: EMA Structure (5)
    # ==================================================================
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    features["fx_ema_9_dist_atr"] = (closes - ema9) / atr_safe
    features["fx_ema_21_dist_atr"] = (closes - ema21) / atr_safe
    features["fx_ema_50_dist_atr"] = (closes - ema50) / atr_safe
    features["fx_ema_200_dist_atr"] = (closes - ema200) / atr_safe
    bull_fan = (ema9 > ema21) & (ema21 > ema50) & (ema50 > ema200)
    bear_fan = (ema9 < ema21) & (ema21 < ema50) & (ema50 < ema200)
    features["fx_ema_fan"] = np.where(bull_fan, 1.0, np.where(bear_fan, -1.0, 0.0))

    # ==================================================================
    # GROUP 6: RSI + MACD (5)
    # ==================================================================
    rsi_14 = rsi(closes, 14)
    features["fx_rsi_14"] = rsi_14
    features["fx_rsi_divergence"] = _slope(rsi_14, 10) - _slope(closes, 10)
    features["fx_rsi_extreme"] = np.where(rsi_14 > 70, 1.0, np.where(rsi_14 < 30, -1.0, 0.0))
    macd_line, macd_sig, macd_hist = macd(closes, 12, 26, 9)
    features["fx_macd_hist_atr"] = macd_hist / atr_safe
    features["fx_macd_cross"] = _crossover(macd_line, macd_sig)

    # ==================================================================
    # GROUP 7: Volatility (2)
    # ==================================================================
    _, _, _, bb_pctb, _ = bollinger_bands(closes, 20, 2.0)
    features["fx_bb_pctb"] = bb_pctb
    atr_50 = atr(highs, lows, closes, 50)
    features["fx_atr_14_50_ratio"] = np.where(atr_50 > 0, atr_14 / atr_50, 1.0)

    # ==================================================================
    # GROUP 8: ICT/SMC — cherry-pick 20 (20)
    # ==================================================================
    h4_h, h4_l, h4_c = None, None, None
    if h4_bars is not None:
        _, _, h4_h, h4_l, h4_c, _ = _to_arrays(h4_bars)

    ict_all = compute_ict_features(
        opens, highs, lows, closes, volumes,
        h4_highs=h4_h, h4_lows=h4_l, h4_closes=h4_c,
        swing_window=10, times=times,
    )
    for key in _ICT_PICKS:
        if key in ict_all:
            features[f"fx_{key}"] = ict_all[key]

    # ==================================================================
    # GROUP 9: Williams — cherry-pick 15 (15)
    # ==================================================================
    lw_all = compute_williams_features(opens, highs, lows, closes, volumes, times)
    for key in _LW_PICKS:
        if key in lw_all:
            features[f"fx_{key}"] = lw_all[key]

    # ==================================================================
    # GROUP 10: Quant — all 15 (15)
    # ==================================================================
    quant_all = compute_quant_features(
        opens, highs, lows, closes, volumes,
        h4_highs=h4_h, h4_lows=h4_l, h4_closes=h4_c,
    )
    for key in _QUANT_PICKS:
        if key in quant_all:
            features[f"fx_{key}"] = quant_all[key]

    # ==================================================================
    # GROUP 11: 4-Layer HTF Alignment (20)
    # ==================================================================
    h1_data = _align_htf_ohlcv(h1_bars, times, n)
    h4_data = _align_htf_ohlcv(h4_bars, times, n)
    d1_data = _align_htf_ohlcv(d1_bars, times, n)

    for prefix, htf in [("h1", h1_data), ("h4", h4_data), ("d1", d1_data)]:
        if htf is not None:
            c = htf["close"]
            htf_ema21 = ema(c, 21)
            htf_ema200_arr = ema(c, 200)
            htf_rsi_arr = rsi(c, 14)
            _, _, htf_macd_hist = macd(c, 12, 26, 9)
            features[f"fx_{prefix}_ema200_dist_atr"] = (c - htf_ema200_arr) / atr_safe
            features[f"fx_{prefix}_trend_dir"] = np.where(c > htf_ema21, 1.0, -1.0)
            features[f"fx_{prefix}_rsi"] = htf_rsi_arr
            features[f"fx_{prefix}_macd_hist_atr"] = htf_macd_hist / atr_safe
            features[f"fx_{prefix}_momentum"] = _slope(c, 5)
        else:
            features[f"fx_{prefix}_ema200_dist_atr"] = np.zeros(n)
            features[f"fx_{prefix}_trend_dir"] = np.zeros(n)
            features[f"fx_{prefix}_rsi"] = np.full(n, 50.0)
            features[f"fx_{prefix}_macd_hist_atr"] = np.zeros(n)
            features[f"fx_{prefix}_momentum"] = np.zeros(n)

    # Composites (5)
    h1_bull = (features["fx_h1_trend_dir"] > 0).astype(float)
    h4_bull = (features["fx_h4_trend_dir"] > 0).astype(float)
    d1_bull = (features["fx_d1_trend_dir"] > 0).astype(float)
    features["fx_htf_trend_alignment"] = (h1_bull + h4_bull + d1_bull) / 3.0

    h1_mom_up = (features["fx_h1_momentum"] > 0).astype(float)
    h4_mom_up = (features["fx_h4_momentum"] > 0).astype(float)
    d1_mom_up = (features["fx_d1_momentum"] > 0).astype(float)
    features["fx_htf_momentum_alignment"] = (h1_mom_up + h4_mom_up + d1_mom_up) / 3.0

    features["fx_d1_bias"] = features["fx_d1_trend_dir"].copy()
    features["fx_h4_h1_agree"] = (
        features["fx_h4_trend_dir"] == features["fx_h1_trend_dir"]
    ).astype(float)
    features["fx_htf_rsi_divergence"] = features["fx_d1_rsi"] - features["fx_h1_rsi"]

    # ==================================================================
    # GROUP 12: Session / Time (10)
    # ==================================================================
    hours = np.array([
        (int(t) % 86400) // 3600 if isinstance(t, (int, float, np.integer)) else 0
        for t in times
    ], dtype=float)
    minutes_in_day = np.array([
        (int(t) % 86400) // 60 if isinstance(t, (int, float, np.integer)) else 0
        for t in times
    ], dtype=float)

    features["fx_hour_sin"] = np.sin(2 * np.pi * hours / 24)
    features["fx_hour_cos"] = np.cos(2 * np.pi * hours / 24)

    dow = np.array([
        ((int(t) // 86400) + 4) % 7 if isinstance(t, (int, float, np.integer)) else 0
        for t in times
    ], dtype=float)
    features["fx_dow_sin"] = np.sin(2 * np.pi * dow / 7)
    features["fx_dow_cos"] = np.cos(2 * np.pi * dow / 7)

    features["fx_london_session"] = ((hours >= 7) & (hours < 16)).astype(float)
    features["fx_ny_session"] = ((hours >= 13) & (hours < 21)).astype(float)
    features["fx_asian_session"] = ((hours >= 0) & (hours < 7)).astype(float)
    features["fx_london_ny_overlap"] = ((hours >= 13) & (hours < 16)).astype(float)

    cash_open_min = 13 * 60 + 30
    features["fx_minutes_since_open"] = np.clip(minutes_in_day - cash_open_min, 0, 480) / 480.0
    features["fx_pre_close"] = ((hours >= 20) & (hours < 21)).astype(float)

    # ==================================================================
    # GROUP 13: Microstructure (10)
    # ==================================================================
    features["fx_spread_proxy"] = np.where(closes > 0, hl_range / closes, 0)

    vol_sma5 = sma(volumes, 5)
    vol_sma20 = sma(volumes, 20)
    features["fx_vol_ratio_5_20"] = np.where(vol_sma20 > 0, vol_sma5 / vol_sma20, 1.0)
    features["fx_vol_ratio_1_20"] = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)

    body_ratio = np.where(hl_range > 0, np.abs(closes - opens) / hl_range, 0)
    features["fx_body_ratio"] = body_ratio
    features["fx_upper_wick_ratio"] = np.where(
        hl_range > 0, (highs - np.maximum(opens, closes)) / hl_range, 0,
    )
    features["fx_lower_wick_ratio"] = np.where(
        hl_range > 0, (np.minimum(opens, closes) - lows) / hl_range, 0,
    )
    features["fx_absorption"] = (
        np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0) * (1 - body_ratio)
    )

    cvd_delta = volumes * np.sign(closes - opens)
    cvd = np.cumsum(cvd_delta)
    features["fx_cvd_slope"] = _slope(cvd, 10)

    price_10_max = pd.Series(closes).rolling(10, min_periods=1).max().values
    cvd_10_max = pd.Series(cvd).rolling(10, min_periods=1).max().values
    price_at_high = (closes >= price_10_max * 0.999).astype(float)
    cvd_at_high = (cvd >= cvd_10_max * 0.999).astype(float)
    features["fx_delta_divergence"] = price_at_high - cvd_at_high

    rel_vol = np.ones(n, dtype=float)
    vol_ser = pd.Series(volumes)
    hour_ser = pd.Series(hours)
    for h_val in range(24):
        mask = hour_ser == h_val
        if mask.sum() > 0:
            hour_mean = vol_ser.where(mask).ffill().rolling(60, min_periods=1).mean()
            rel_vol = np.where(mask & (hour_mean > 0), volumes / hour_mean.values, rel_vol)
    features["fx_relative_volume"] = rel_vol

    # ==================================================================
    # FINALIZE — float32, no NaN/Inf
    # ==================================================================
    names = list(features.keys())
    cols = [np.asarray(features[k], dtype=np.float64).copy() for k in names]
    X = np.column_stack(cols).astype(np.float32)
    np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0, copy=False)
    return names, X
