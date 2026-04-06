"""
Potential Agent feature engineering — ~80 institutional trading features.
Strategies: VWAP, Volume Profile, ADX/Trend, EMA Structure, ORB, Breakout,
RSI divergence, MACD, Volatility, CVD/Flow, Session/Time, Structure, Donchian, HTF.
All features prefixed with 'pot_'.

Entry: compute_potential_features(m5_bars, h1_bars, h4_bars, d1_bars, symbol)
Returns: (feature_names, X_matrix) — shape (n_bars, ~80), float32, no NaN/Inf.
"""
import numpy as np
import pandas as pd
from app.services.backtest.indicators import (
    ema, sma, rsi, atr, macd, bollinger_bands, obv, roc, adx,
)


def _to_arrays(bars):
    if isinstance(bars, pd.DataFrame):
        df = bars
    else:
        df = pd.DataFrame(bars)
    return (
        df["time"].values if "time" in df.columns else np.arange(len(df)),
        df["open"].values.astype(float),
        df["high"].values.astype(float),
        df["low"].values.astype(float),
        df["close"].values.astype(float),
        df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(df)),
    )


def _slope(arr, period=5):
    out = np.zeros_like(arr, dtype=float)
    for i in range(period, len(arr)):
        if arr[i - period] != 0:
            out[i] = (arr[i] - arr[i - period]) / abs(arr[i - period])
    return out


def _crossover(fast, slow):
    out = np.zeros(len(fast), dtype=float)
    for i in range(1, len(fast)):
        if np.isnan(fast[i]) or np.isnan(slow[i]) or np.isnan(fast[i-1]) or np.isnan(slow[i-1]):
            continue
        if fast[i] > slow[i] and fast[i-1] <= slow[i-1]:
            out[i] = 1.0
        elif fast[i] < slow[i] and fast[i-1] >= slow[i-1]:
            out[i] = -1.0
    return out


def _rolling_max(arr, window):
    out = np.full_like(arr, np.nan, dtype=float)
    for i in range(window - 1, len(arr)):
        out[i] = np.nanmax(arr[i - window + 1:i + 1])
    return out


def _rolling_min(arr, window):
    out = np.full_like(arr, np.nan, dtype=float)
    for i in range(window - 1, len(arr)):
        out[i] = np.nanmin(arr[i - window + 1:i + 1])
    return out


def _align_htf(htf_bars, m5_times, n):
    """Forward-fill HTF data to M5 length."""
    if htf_bars is None:
        return None
    t, o, h, l, c, v = _to_arrays(htf_bars)
    aligned = np.full(n, np.nan, dtype=float)
    j = 0
    for i in range(n):
        while j < len(t) - 1 and t[j + 1] <= m5_times[i]:
            j += 1
        if t[j] <= m5_times[i]:
            aligned[i] = c[j]
    # forward fill remaining NaN
    for i in range(1, n):
        if np.isnan(aligned[i]):
            aligned[i] = aligned[i - 1]
    return aligned


def _session_vwap(closes, highs, lows, volumes, times, session_start_hour=13, session_start_min=30):
    """Session VWAP — resets at session_start_hour:session_start_min UTC each day.
    Vectorized: detect resets via timestamp modular arithmetic."""
    n = len(closes)
    typical = (highs + lows + closes) / 3.0
    vp = typical * volumes

    # Detect session boundaries
    if n == 0:
        return np.zeros(0, dtype=float)
    ts = times.astype(np.int64) if hasattr(times, 'astype') else np.array(times, dtype=np.int64)
    seconds_in_day = ts % 86400
    reset_sec = session_start_hour * 3600 + session_start_min * 60
    is_reset = seconds_in_day == reset_sec

    # Build session groups
    session_id = np.cumsum(is_reset)

    # Cumulative within each session via pandas groupby trick
    df = pd.DataFrame({"sid": session_id, "vp": vp, "vol": volumes})
    cum_vp = df.groupby("sid")["vp"].cumsum().values
    cum_vol = df.groupby("sid")["vol"].cumsum().values

    vwap = np.where(cum_vol > 0, cum_vp / cum_vol, closes)
    return vwap


def _volume_profile(highs, lows, volumes, n_bins=30, window=288, step=12):
    """Rolling volume profile — computed every `step` bars, forward-filled.
    Returns POC, VAH, VAL. step=12 means one computation per hour of M5 data."""
    n = len(highs)
    poc = np.zeros(n, dtype=float)
    vah = np.zeros(n, dtype=float)
    val = np.zeros(n, dtype=float)

    last_poc = highs[0] if n > 0 else 0
    last_vah = highs[0] if n > 0 else 0
    last_val = lows[0] if n > 0 else 0

    for i in range(n):
        if i < window - 1 or (i - (window - 1)) % step != 0:
            poc[i] = last_poc
            vah[i] = last_vah
            val[i] = last_val
            continue

        start = i - window + 1
        h_slice = highs[start:i + 1]
        l_slice = lows[start:i + 1]
        v_slice = volumes[start:i + 1]

        price_min = l_slice.min()
        price_max = h_slice.max()
        if price_max <= price_min:
            poc[i] = highs[i]
            vah[i] = highs[i]
            val[i] = lows[i]
            last_poc, last_vah, last_val = poc[i], vah[i], val[i]
            continue

        # Vectorized bin assignment
        bin_size = (price_max - price_min) / n_bins
        low_bins = np.clip(((l_slice - price_min) / bin_size).astype(int), 0, n_bins - 1)
        high_bins = np.clip(((h_slice - price_min) / bin_size).astype(int), 0, n_bins - 1)

        # Fast: assign all volume to midpoint bin (approximation)
        mid_bins = (low_bins + high_bins) // 2
        bin_vol = np.bincount(mid_bins, weights=v_slice, minlength=n_bins)

        poc_bin = np.argmax(bin_vol)
        bin_edges_lo = price_min + np.arange(n_bins) * bin_size
        poc[i] = bin_edges_lo[poc_bin] + bin_size / 2

        # Value area: 70% of total volume around POC
        total_vol = bin_vol.sum()
        target_vol = total_vol * 0.70
        va_vol = bin_vol[poc_bin]
        lo_idx, hi_idx = poc_bin, poc_bin
        while va_vol < target_vol and (lo_idx > 0 or hi_idx < n_bins - 1):
            expand_lo = bin_vol[lo_idx - 1] if lo_idx > 0 else 0
            expand_hi = bin_vol[hi_idx + 1] if hi_idx < n_bins - 1 else 0
            if expand_lo >= expand_hi and lo_idx > 0:
                lo_idx -= 1
                va_vol += bin_vol[lo_idx]
            elif hi_idx < n_bins - 1:
                hi_idx += 1
                va_vol += bin_vol[hi_idx]
            else:
                lo_idx -= 1
                va_vol += bin_vol[lo_idx]

        vah[i] = bin_edges_lo[min(hi_idx + 1, n_bins - 1)] + bin_size
        val[i] = bin_edges_lo[lo_idx]
        last_poc, last_vah, last_val = poc[i], vah[i], val[i]

    return poc, vah, val


def _opening_range(highs, lows, closes, times, session_hour=13, session_min=30, n_bars=6):
    """ORB: high/low of first n_bars after session open. Returns orb_high, orb_low."""
    n = len(closes)
    orb_high = np.zeros(n, dtype=float)
    orb_low = np.zeros(n, dtype=float)
    current_orb_h = np.nan
    current_orb_l = np.nan
    bars_since_open = -1

    for i in range(n):
        ts = times[i]
        try:
            if isinstance(ts, (np.integer, int, float)):
                hour = int(ts % 86400) // 3600
                minute = int(ts % 3600) // 60
            else:
                hour = int(pd.Timestamp(ts).hour)
                minute = int(pd.Timestamp(ts).minute)
        except Exception:
            hour, minute = 0, 0

        if hour == session_hour and minute == session_min:
            bars_since_open = 0
            current_orb_h = highs[i]
            current_orb_l = lows[i]

        if 0 <= bars_since_open < n_bars:
            current_orb_h = max(current_orb_h, highs[i]) if not np.isnan(current_orb_h) else highs[i]
            current_orb_l = min(current_orb_l, lows[i]) if not np.isnan(current_orb_l) else lows[i]
            bars_since_open += 1

        orb_high[i] = current_orb_h if not np.isnan(current_orb_h) else highs[i]
        orb_low[i] = current_orb_l if not np.isnan(current_orb_l) else lows[i]

    return orb_high, orb_low


def compute_potential_features(
    m5_bars,
    h1_bars=None, h4_bars=None, d1_bars=None,
    symbol: str = "US30",
) -> tuple[list[str], np.ndarray]:
    """Compute ~80 institutional features for the Potential Agent."""
    times, opens, highs, lows, closes, volumes = _to_arrays(m5_bars)
    n = len(closes)
    features = {}

    atr_14 = atr(highs, lows, closes, 14)
    hl_range = highs - lows

    # ── 1. VWAP features (7) ──────────────────────────────────────────
    vwap = _session_vwap(closes, highs, lows, volumes, times)
    features["pot_vwap_dist"] = np.where(closes > 0, (closes - vwap) / closes, 0)
    features["pot_vwap_cross"] = _crossover(closes, vwap)
    features["pot_vwap_slope"] = _slope(vwap, 5)
    vwap_std = pd.Series(closes - vwap).rolling(20, min_periods=1).std().fillna(0).values
    features["pot_vwap_zscore"] = np.where(vwap_std > 0, (closes - vwap) / vwap_std, 0)
    features["pot_vwap_above"] = (closes > vwap).astype(float)
    features["pot_vwap_touch"] = (np.abs(closes - vwap) < atr_14 * 0.1).astype(float)
    features["pot_vwap_reversion"] = np.where(atr_14 > 0, (vwap - closes) / atr_14, 0)

    # ── 2. Volume Profile features (6) ────────────────────────────────
    poc, vah, val = _volume_profile(highs, lows, volumes)
    features["pot_poc_dist"] = np.where(closes > 0, (closes - poc) / closes, 0)
    features["pot_vah_dist"] = np.where(closes > 0, (vah - closes) / closes, 0)
    features["pot_val_dist"] = np.where(closes > 0, (closes - val) / closes, 0)
    va_range = vah - val
    features["pot_value_area_pos"] = np.where(va_range > 0, (closes - val) / va_range, 0.5)
    features["pot_value_area_width"] = np.where(closes > 0, va_range / closes, 0)
    features["pot_poc_above"] = (closes > poc).astype(float)

    # ── 3. ADX/Trend features (6) ─────────────────────────────────────
    adx_val, plus_di, minus_di = adx(highs, lows, closes, 14)
    adx_val = np.nan_to_num(adx_val, 0)
    plus_di = np.nan_to_num(plus_di, 0)
    minus_di = np.nan_to_num(minus_di, 0)
    features["pot_adx"] = adx_val
    features["pot_plus_di"] = plus_di
    features["pot_minus_di"] = minus_di
    features["pot_adx_strong"] = (adx_val > 25).astype(float)
    features["pot_di_cross"] = _crossover(plus_di, minus_di)
    features["pot_adx_slope"] = _slope(adx_val, 5)

    # ── 4. EMA Structure features (8) ─────────────────────────────────
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    features["pot_ema_9_dist"] = np.where(closes > 0, (closes - ema9) / closes, 0)
    features["pot_ema_21_dist"] = np.where(closes > 0, (closes - ema21) / closes, 0)
    features["pot_ema_50_dist"] = np.where(closes > 0, (closes - ema50) / closes, 0)
    features["pot_ema_200_dist"] = np.where(closes > 0, (closes - ema200) / closes, 0)
    features["pot_ema_9_21_cross"] = _crossover(ema9, ema21)
    features["pot_200_cross"] = _crossover(closes, ema200)
    # EMA fan: all EMAs in order (bullish=1, bearish=-1, mixed=0)
    bull_fan = (ema9 > ema21) & (ema21 > ema50) & (ema50 > ema200)
    bear_fan = (ema9 < ema21) & (ema21 < ema50) & (ema50 < ema200)
    features["pot_ema_fan"] = np.where(bull_fan, 1.0, np.where(bear_fan, -1.0, 0.0))
    features["pot_ema_21_slope"] = _slope(ema21, 5)

    # ── 5. ORB features (5) ───────────────────────────────────────────
    orb_high, orb_low = _opening_range(highs, lows, closes, times)
    orb_range = orb_high - orb_low
    features["pot_orb_break_up"] = (closes > orb_high).astype(float)
    features["pot_orb_break_down"] = (closes < orb_low).astype(float)
    features["pot_orb_position"] = np.where(orb_range > 0, (closes - orb_low) / orb_range, 0.5)
    features["pot_orb_range_atr"] = np.where(atr_14 > 0, orb_range / atr_14, 0)
    features["pot_orb_dist"] = np.where(closes > 0, (closes - (orb_high + orb_low) / 2) / closes, 0)

    # ── 6. RSI features (5) ───────────────────────────────────────────
    rsi_14 = rsi(closes, 14)
    rsi_7 = rsi(closes, 7)
    features["pot_rsi_14"] = rsi_14
    features["pot_rsi_7"] = rsi_7
    features["pot_rsi_divergence"] = _slope(rsi_14, 10) - _slope(closes, 10)
    features["pot_rsi_extreme"] = np.where(rsi_14 > 70, 1.0, np.where(rsi_14 < 30, -1.0, 0.0))
    features["pot_rsi_slope"] = _slope(rsi_14, 5)

    # ── 7. MACD/Momentum features (5) ─────────────────────────────────
    macd_line, macd_sig, macd_hist = macd(closes, 12, 26, 9)
    features["pot_macd_hist"] = macd_hist
    features["pot_macd_cross"] = _crossover(macd_line, macd_sig)
    features["pot_macd_slope"] = _slope(macd_hist, 3)
    features["pot_momentum_10"] = closes - np.roll(closes, 10)
    features["pot_momentum_20"] = closes - np.roll(closes, 20)

    # ── 8. Volatility features (6) ────────────────────────────────────
    features["pot_atr_14"] = atr_14
    features["pot_atr_ratio"] = np.where(closes > 0, atr_14 / closes, 0)
    _, _, _, bb_pctb, bb_bw = bollinger_bands(closes, 20, 2.0)
    features["pot_bb_pctb"] = bb_pctb
    features["pot_bb_bandwidth"] = bb_bw
    features["pot_atr_expansion"] = _slope(atr_14, 5)
    atr_50 = atr(highs, lows, closes, 50)
    features["pot_atr_14_50_ratio"] = np.where(atr_50 > 0, atr_14 / atr_50, 1.0)

    # ── 9. CVD/Order Flow proxy features (5) ──────────────────────────
    # CVD proxy: volume * sign of close-open
    cvd_delta = volumes * np.sign(closes - opens)
    cvd = np.cumsum(cvd_delta)
    features["pot_cvd_slope"] = _slope(cvd, 10)
    features["pot_vol_imbalance"] = np.where(
        volumes > 0,
        (volumes * (closes > opens).astype(float) - volumes * (closes < opens).astype(float)) / volumes,
        0
    )
    vol_sma20 = sma(volumes, 20)
    features["pot_vol_spike"] = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    # Absorption: high volume + small body = absorption
    body_ratio = np.where(hl_range > 0, np.abs(closes - opens) / hl_range, 0)
    vol_ratio = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    features["pot_absorption"] = vol_ratio * (1 - body_ratio)
    features["pot_obv_slope"] = _slope(obv(closes, volumes), 10)

    # ── 10. Session/Time features (5) ─────────────────────────────────
    hours = np.array([(int(t) % 86400) // 3600 if isinstance(t, (int, float, np.integer)) else 0 for t in times], dtype=float)
    features["pot_hour_sin"] = np.sin(2 * np.pi * hours / 24)
    features["pot_hour_cos"] = np.cos(2 * np.pi * hours / 24)
    dow = np.array([(int(t) // 86400) % 7 if isinstance(t, (int, float, np.integer)) else 0 for t in times], dtype=float)
    features["pot_dow_sin"] = np.sin(2 * np.pi * dow / 7)
    features["pot_cash_open"] = ((hours >= 13) & (hours < 16)).astype(float)

    # ── 11. Price Structure features (6) ──────────────────────────────
    features["pot_return_1"] = np.zeros(n)
    features["pot_return_1"][1:] = (closes[1:] - closes[:-1]) / np.where(closes[:-1] > 0, closes[:-1], 1)
    features["pot_return_5"] = np.zeros(n)
    for i in range(5, n):
        features["pot_return_5"][i] = (closes[i] - closes[i-5]) / closes[i-5] if closes[i-5] > 0 else 0
    features["pot_body_ratio"] = body_ratio
    features["pot_upper_wick"] = np.where(hl_range > 0, (highs - np.maximum(opens, closes)) / hl_range, 0)
    gap = np.zeros(n)
    gap[1:] = opens[1:] - closes[:-1]
    features["pot_gap"] = gap
    features["pot_range_expansion"] = np.where(atr_14 > 0, hl_range / atr_14, 0)

    # ── 12. Donchian/Breakout features (5) ────────────────────────────
    donch_high_20 = _rolling_max(highs, 20)
    donch_low_20 = _rolling_min(lows, 20)
    donch_high_55 = _rolling_max(highs, 55)
    donch_low_55 = _rolling_min(lows, 55)
    donch_range_20 = donch_high_20 - donch_low_20
    features["pot_donch_position_20"] = np.where(donch_range_20 > 0, (closes - donch_low_20) / donch_range_20, 0.5)
    features["pot_donch_break_up"] = (closes >= donch_high_55).astype(float)
    features["pot_donch_break_down"] = (closes <= donch_low_55).astype(float)
    # Breakout + retest: was at high recently but pulled back
    was_breakout = np.zeros(n, dtype=float)
    for i in range(5, n):
        if np.any(closes[i-5:i] >= donch_high_20[i-5:i]):
            was_breakout[i] = 1.0
    features["pot_retest"] = was_breakout * (closes < donch_high_20).astype(float)
    features["pot_donch_squeeze"] = np.where(closes > 0, donch_range_20 / closes, 0)

    # ── 13. HTF Alignment features (7) ────────────────────────────────
    h1_close = _align_htf(h1_bars, times, n)
    h4_close = _align_htf(h4_bars, times, n)
    d1_close = _align_htf(d1_bars, times, n)

    if h1_close is not None:
        h1_ema21 = ema(h1_close, 21)
        features["pot_h1_trend"] = np.where(h1_close > 0, (h1_close - h1_ema21) / h1_close, 0)
        features["pot_h1_momentum"] = _slope(h1_close, 5)
    else:
        features["pot_h1_trend"] = np.zeros(n)
        features["pot_h1_momentum"] = np.zeros(n)

    if h4_close is not None:
        h4_ema21 = ema(h4_close, 21)
        features["pot_h4_trend"] = np.where(h4_close > 0, (h4_close - h4_ema21) / h4_close, 0)
        features["pot_h4_momentum"] = _slope(h4_close, 5)
    else:
        features["pot_h4_trend"] = np.zeros(n)
        features["pot_h4_momentum"] = np.zeros(n)

    if d1_close is not None:
        d1_ema50 = ema(d1_close, 50)
        features["pot_d1_trend"] = np.where(d1_close > 0, (d1_close - d1_ema50) / d1_close, 0)
        features["pot_d1_above_ema50"] = (d1_close > d1_ema50).astype(float)
    else:
        features["pot_d1_trend"] = np.zeros(n)
        features["pot_d1_above_ema50"] = np.zeros(n)

    # HTF alignment score: how many HTFs agree on direction
    h1_bull = (features["pot_h1_trend"] > 0).astype(float)
    h4_bull = (features["pot_h4_trend"] > 0).astype(float)
    d1_bull = features["pot_d1_above_ema50"]
    features["pot_htf_alignment"] = (h1_bull + h4_bull + d1_bull) / 3.0

    # ── Finalize ──────────────────────────────────────────────────────
    names = list(features.keys())
    X = np.column_stack([features[k] for k in names]).astype(np.float32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return names, X
