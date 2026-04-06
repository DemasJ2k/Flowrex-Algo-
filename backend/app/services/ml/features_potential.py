"""
Potential Agent v2 feature engineering — ~85 institutional features.

v2 changes from v1:
  - ATR-normalized all distance features (kills ATR SHAP dominance)
  - Added anchored VWAPs (weekly + monthly reset)
  - Added delta divergence (CVD vs price)
  - Added H1/H4 RSI + MACD as explicit features
  - Added relative volume (vs same-hour-of-day average)
  - Dropped LSTM (0.5% SHAP, not worth compute)
  - Momentum features normalized by ATR

All features prefixed with 'pot_'.
Entry: compute_potential_features(m5_bars, h1_bars, h4_bars, d1_bars, symbol)
Returns: (feature_names, X_matrix) — shape (n_bars, ~85), float32, no NaN/Inf.
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
    s = pd.Series(arr)
    return s.rolling(window, min_periods=window).max().values.copy()


def _rolling_min(arr, window):
    s = pd.Series(arr)
    return s.rolling(window, min_periods=window).min().values.copy()


def _safe_div(num, den, fill=0.0):
    """Element-wise num/den, returning fill where den is 0 or NaN."""
    return np.where((den > 0) & ~np.isnan(den), num / den, fill)


def _align_htf_ohlcv(htf_bars, m5_times, n):
    """Forward-fill HTF OHLCV to M5 length. Returns dict of arrays or None."""
    if htf_bars is None:
        return None
    t, o, h, l, c, v = _to_arrays(htf_bars)
    out = {k: np.full(n, np.nan, dtype=float) for k in ["close", "high", "low", "volume"]}
    j = 0
    for i in range(n):
        while j < len(t) - 1 and t[j + 1] <= m5_times[i]:
            j += 1
        if t[j] <= m5_times[i]:
            out["close"][i] = c[j]
            out["high"][i] = h[j]
            out["low"][i] = l[j]
            out["volume"][i] = v[j]
    for k in out:
        arr = out[k]
        for i in range(1, n):
            if np.isnan(arr[i]):
                arr[i] = arr[i - 1]
        out[k] = np.nan_to_num(arr, 0)
    return out


def _anchored_vwap(closes, highs, lows, volumes, times, reset_period="weekly"):
    """Anchored VWAP — resets at week or month boundary."""
    n = len(closes)
    typical = (highs + lows + closes) / 3.0
    vp = typical * volumes

    if n == 0:
        return np.zeros(0, dtype=float)

    ts = np.array(times, dtype=np.int64)

    if reset_period == "weekly":
        # Reset on Monday 00:00 UTC (day_of_week = 0)
        day_of_week = ((ts // 86400) + 4) % 7  # epoch was Thursday, so +4 -> Monday=0
        is_reset = np.zeros(n, dtype=bool)
        is_reset[0] = True
        is_reset[1:] = (day_of_week[1:] == 0) & (day_of_week[:-1] != 0)
    else:
        # Reset on 1st of month
        days = pd.to_datetime(ts, unit="s", utc=True)
        day_num = days.day
        is_reset = np.zeros(n, dtype=bool)
        is_reset[0] = True
        is_reset[1:] = (day_num[1:] == 1) & (day_num[:-1] != 1)

    session_id = np.cumsum(is_reset)
    df = pd.DataFrame({"sid": session_id, "vp": vp, "vol": volumes})
    cum_vp = df.groupby("sid")["vp"].cumsum().values
    cum_vol = df.groupby("sid")["vol"].cumsum().values
    return np.where(cum_vol > 0, cum_vp / cum_vol, closes)


def _session_vwap(closes, highs, lows, volumes, times, session_start_hour=13, session_start_min=30):
    """Session VWAP — resets at session_start_hour:session_start_min UTC each day."""
    n = len(closes)
    typical = (highs + lows + closes) / 3.0
    vp = typical * volumes
    if n == 0:
        return np.zeros(0, dtype=float)
    ts = np.array(times, dtype=np.int64)
    seconds_in_day = ts % 86400
    reset_sec = session_start_hour * 3600 + session_start_min * 60
    is_reset = seconds_in_day == reset_sec
    session_id = np.cumsum(is_reset)
    df = pd.DataFrame({"sid": session_id, "vp": vp, "vol": volumes})
    cum_vp = df.groupby("sid")["vp"].cumsum().values
    cum_vol = df.groupby("sid")["vol"].cumsum().values
    return np.where(cum_vol > 0, cum_vp / cum_vol, closes)


def _volume_profile(highs, lows, volumes, n_bins=30, window=288, step=12):
    """Rolling volume profile — computed every `step` bars, forward-filled."""
    n = len(highs)
    poc = np.zeros(n, dtype=float)
    vah = np.zeros(n, dtype=float)
    val = np.zeros(n, dtype=float)
    last_poc = highs[0] if n > 0 else 0
    last_vah = highs[0] if n > 0 else 0
    last_val = lows[0] if n > 0 else 0

    for i in range(n):
        if i < window - 1 or (i - (window - 1)) % step != 0:
            poc[i] = last_poc; vah[i] = last_vah; val[i] = last_val
            continue
        start = i - window + 1
        h_s, l_s, v_s = highs[start:i+1], lows[start:i+1], volumes[start:i+1]
        pmin, pmax = l_s.min(), h_s.max()
        if pmax <= pmin:
            poc[i]=highs[i]; vah[i]=highs[i]; val[i]=lows[i]
            last_poc,last_vah,last_val = poc[i],vah[i],val[i]; continue
        bs = (pmax - pmin) / n_bins
        mid_bins = np.clip(((((l_s+h_s)/2) - pmin) / bs).astype(int), 0, n_bins-1)
        bv = np.bincount(mid_bins, weights=v_s, minlength=n_bins)
        pb = np.argmax(bv)
        be = pmin + np.arange(n_bins) * bs
        poc[i] = be[pb] + bs/2
        tv, tgt = bv.sum(), bv.sum()*0.70
        va, lo_i, hi_i = bv[pb], pb, pb
        while va < tgt and (lo_i > 0 or hi_i < n_bins-1):
            el = bv[lo_i-1] if lo_i > 0 else 0
            eh = bv[hi_i+1] if hi_i < n_bins-1 else 0
            if el >= eh and lo_i > 0: lo_i -= 1; va += bv[lo_i]
            elif hi_i < n_bins-1: hi_i += 1; va += bv[hi_i]
            else: lo_i -= 1; va += bv[lo_i]
        vah[i] = be[min(hi_i+1, n_bins-1)] + bs
        val[i] = be[lo_i]
        last_poc,last_vah,last_val = poc[i],vah[i],val[i]
    return poc, vah, val


def _opening_range(highs, lows, closes, times, session_hour=13, session_min=30, n_bars=6):
    """ORB: high/low of first n_bars after session open."""
    n = len(closes)
    orb_high = np.zeros(n, dtype=float)
    orb_low = np.zeros(n, dtype=float)
    cur_h, cur_l, bars_since = np.nan, np.nan, -1
    for i in range(n):
        ts = times[i]
        if isinstance(ts, (np.integer, int, float)):
            hour = int(ts % 86400) // 3600
            minute = int(ts % 3600) // 60
        else:
            hour, minute = 0, 0
        if hour == session_hour and minute == session_min:
            bars_since = 0; cur_h = highs[i]; cur_l = lows[i]
        if 0 <= bars_since < n_bars:
            cur_h = max(cur_h, highs[i]) if not np.isnan(cur_h) else highs[i]
            cur_l = min(cur_l, lows[i]) if not np.isnan(cur_l) else lows[i]
            bars_since += 1
        orb_high[i] = cur_h if not np.isnan(cur_h) else highs[i]
        orb_low[i] = cur_l if not np.isnan(cur_l) else lows[i]
    return orb_high, orb_low


def compute_potential_features(
    m5_bars,
    h1_bars=None, h4_bars=None, d1_bars=None,
    symbol: str = "US30",
) -> tuple[list[str], np.ndarray]:
    """Compute ~85 institutional features for Potential Agent v2."""
    times, opens, highs, lows, closes, volumes = _to_arrays(m5_bars)
    n = len(closes)
    features = {}

    atr_14 = atr(highs, lows, closes, 14)
    atr_safe = np.where((atr_14 > 0) & ~np.isnan(atr_14), atr_14, 1.0)  # for division
    hl_range = highs - lows

    # ── 1. VWAP features (9) — session + weekly + monthly anchored ────
    vwap_sess = _session_vwap(closes, highs, lows, volumes, times)
    vwap_week = _anchored_vwap(closes, highs, lows, volumes, times, "weekly")
    vwap_month = _anchored_vwap(closes, highs, lows, volumes, times, "monthly")

    features["pot_vwap_dist_atr"] = (closes - vwap_sess) / atr_safe
    features["pot_vwap_cross"] = _crossover(closes, vwap_sess)
    vwap_std = pd.Series(closes - vwap_sess).rolling(20, min_periods=1).std().fillna(1).values
    features["pot_vwap_zscore"] = np.where(vwap_std > 0, (closes - vwap_sess) / vwap_std, 0)
    features["pot_vwap_above"] = (closes > vwap_sess).astype(float)
    features["pot_vwap_touch"] = (np.abs(closes - vwap_sess) < atr_safe * 0.15).astype(float)
    features["pot_vwap_reversion_atr"] = (vwap_sess - closes) / atr_safe
    # Anchored VWAPs
    features["pot_vwap_weekly_dist_atr"] = (closes - vwap_week) / atr_safe
    features["pot_vwap_monthly_dist_atr"] = (closes - vwap_month) / atr_safe
    features["pot_vwap_weekly_cross"] = _crossover(closes, vwap_week)

    # ── 2. Volume Profile features (7) ────────────────────────────────
    poc, vah, val = _volume_profile(highs, lows, volumes)
    features["pot_poc_dist_atr"] = (closes - poc) / atr_safe
    features["pot_vah_dist_atr"] = (vah - closes) / atr_safe
    features["pot_val_dist_atr"] = (closes - val) / atr_safe
    va_range = vah - val
    features["pot_value_area_pos"] = np.where(va_range > 0, (closes - val) / va_range, 0.5)
    features["pot_value_area_width_atr"] = va_range / atr_safe
    features["pot_poc_above"] = (closes > poc).astype(float)
    features["pot_inside_value_area"] = ((closes >= val) & (closes <= vah)).astype(float)

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

    # ── 4. EMA Structure features (8) — distances in ATR units ────────
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    features["pot_ema_9_dist_atr"] = (closes - ema9) / atr_safe
    features["pot_ema_21_dist_atr"] = (closes - ema21) / atr_safe
    features["pot_ema_50_dist_atr"] = (closes - ema50) / atr_safe
    features["pot_ema_200_dist_atr"] = (closes - ema200) / atr_safe
    features["pot_ema_9_21_cross"] = _crossover(ema9, ema21)
    features["pot_200_cross"] = _crossover(closes, ema200)
    bull_fan = (ema9 > ema21) & (ema21 > ema50) & (ema50 > ema200)
    bear_fan = (ema9 < ema21) & (ema21 < ema50) & (ema50 < ema200)
    features["pot_ema_fan"] = np.where(bull_fan, 1.0, np.where(bear_fan, -1.0, 0.0))
    features["pot_ema_21_slope"] = _slope(ema21, 5)

    # ── 5. ORB features (5) — distances in ATR units ─────────────────
    orb_high, orb_low = _opening_range(highs, lows, closes, times)
    orb_range = orb_high - orb_low
    features["pot_orb_break_up"] = (closes > orb_high).astype(float)
    features["pot_orb_break_down"] = (closes < orb_low).astype(float)
    features["pot_orb_position"] = np.where(orb_range > 0, (closes - orb_low) / orb_range, 0.5)
    features["pot_orb_range_atr"] = orb_range / atr_safe
    features["pot_orb_dist_atr"] = (closes - (orb_high + orb_low) / 2) / atr_safe

    # ── 6. RSI features (5) ───────────────────────────────────────────
    rsi_14 = rsi(closes, 14)
    rsi_7 = rsi(closes, 7)
    features["pot_rsi_14"] = rsi_14
    features["pot_rsi_7"] = rsi_7
    features["pot_rsi_divergence"] = _slope(rsi_14, 10) - _slope(closes, 10)
    features["pot_rsi_extreme"] = np.where(rsi_14 > 70, 1.0, np.where(rsi_14 < 30, -1.0, 0.0))
    features["pot_rsi_slope"] = _slope(rsi_14, 5)

    # ── 7. MACD/Momentum features (5) — momentum in ATR units ────────
    macd_line, macd_sig, macd_hist = macd(closes, 12, 26, 9)
    features["pot_macd_hist_atr"] = macd_hist / atr_safe
    features["pot_macd_cross"] = _crossover(macd_line, macd_sig)
    features["pot_macd_slope"] = _slope(macd_hist, 3)
    mom10 = closes - np.roll(closes, 10)
    mom20 = closes - np.roll(closes, 20)
    features["pot_momentum_10_atr"] = mom10 / atr_safe
    features["pot_momentum_20_atr"] = mom20 / atr_safe

    # ── 8. Volatility features (5) — reduced, no raw ATR ─────────────
    features["pot_atr_ratio"] = np.where(closes > 0, atr_14 / closes, 0)
    _, _, _, bb_pctb, bb_bw = bollinger_bands(closes, 20, 2.0)
    features["pot_bb_pctb"] = bb_pctb
    features["pot_bb_bandwidth"] = bb_bw
    features["pot_atr_expansion"] = _slope(atr_14, 5)
    atr_50 = atr(highs, lows, closes, 50)
    features["pot_atr_14_50_ratio"] = np.where(atr_50 > 0, atr_14 / atr_50, 1.0)

    # ── 9. CVD/Flow + Delta Divergence (7) ────────────────────────────
    cvd_delta = volumes * np.sign(closes - opens)
    cvd = np.cumsum(cvd_delta)
    features["pot_cvd_slope"] = _slope(cvd, 10)
    features["pot_vol_imbalance"] = np.sign(closes - opens)
    vol_sma20 = sma(volumes, 20)
    features["pot_vol_spike"] = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    body_ratio = np.where(hl_range > 0, np.abs(closes - opens) / hl_range, 0)
    vol_ratio = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    features["pot_absorption"] = vol_ratio * (1 - body_ratio)
    features["pot_obv_slope"] = _slope(obv(closes, volumes), 10)
    # Delta divergence: price making new high but CVD not confirming
    price_10_max = pd.Series(closes).rolling(10, min_periods=1).max().values
    cvd_10_max = pd.Series(cvd).rolling(10, min_periods=1).max().values
    price_at_high = (closes >= price_10_max * 0.999).astype(float)
    cvd_at_high = (cvd >= cvd_10_max * 0.999).astype(float)
    features["pot_delta_divergence"] = price_at_high - cvd_at_high  # +1 = bearish divergence
    # Relative volume: volume vs same-hour average (rolling 5-day)
    hours = np.array([(int(t) % 86400) // 3600 if isinstance(t, (int, float, np.integer)) else 0 for t in times])
    rel_vol = np.ones(n, dtype=float)
    # Group by hour, compute rolling mean
    vol_ser = pd.Series(volumes)
    hour_ser = pd.Series(hours)
    for h in range(24):
        mask = hour_ser == h
        if mask.sum() > 0:
            hour_mean = vol_ser.where(mask).ffill().rolling(60, min_periods=1).mean()
            rel_vol = np.where(mask & (hour_mean > 0), volumes / hour_mean.values, rel_vol)
    features["pot_relative_volume"] = rel_vol

    # ── 10. Session/Time features (5) ─────────────────────────────────
    features["pot_hour_sin"] = np.sin(2 * np.pi * hours / 24)
    features["pot_hour_cos"] = np.cos(2 * np.pi * hours / 24)
    dow = np.array([(int(t) // 86400) % 7 if isinstance(t, (int, float, np.integer)) else 0 for t in times], dtype=float)
    features["pot_dow_sin"] = np.sin(2 * np.pi * dow / 7)
    features["pot_cash_open"] = ((hours >= 13) & (hours < 16)).astype(float)
    features["pot_power_hour"] = ((hours >= 19) & (hours < 21)).astype(float)

    # ── 11. Price Structure features (5) — normalized ─────────────────
    ret1 = np.zeros(n); ret1[1:] = (closes[1:] - closes[:-1]) / np.where(closes[:-1] > 0, closes[:-1], 1)
    features["pot_return_1"] = ret1
    ret5 = np.zeros(n)
    for i in range(5, n):
        ret5[i] = (closes[i] - closes[i-5]) / closes[i-5] if closes[i-5] > 0 else 0
    features["pot_return_5"] = ret5
    features["pot_body_ratio"] = body_ratio
    features["pot_upper_wick"] = np.where(hl_range > 0, (highs - np.maximum(opens, closes)) / hl_range, 0)
    features["pot_range_expansion"] = hl_range / atr_safe

    # ── 12. Donchian/Breakout features (5) ────────────────────────────
    donch_high_20 = _rolling_max(highs, 20)
    donch_low_20 = _rolling_min(lows, 20)
    donch_high_55 = _rolling_max(highs, 55)
    donch_low_55 = _rolling_min(lows, 55)
    donch_range_20 = np.nan_to_num(donch_high_20 - donch_low_20, 0)
    features["pot_donch_position_20"] = np.where(donch_range_20 > 0, (closes - np.nan_to_num(donch_low_20, 0)) / donch_range_20, 0.5)
    features["pot_donch_break_up"] = (closes >= np.nan_to_num(donch_high_55, np.inf)).astype(float)
    features["pot_donch_break_down"] = (closes <= np.nan_to_num(donch_low_55, -np.inf)).astype(float)
    features["pot_donch_squeeze_atr"] = donch_range_20 / atr_safe
    # Breakout retest
    was_breakout = np.zeros(n, dtype=float)
    dh20 = np.nan_to_num(donch_high_20, 0)
    for i in range(5, n):
        if np.any(closes[i-5:i] >= dh20[i-5:i]):
            was_breakout[i] = 1.0
    features["pot_retest"] = was_breakout * (closes < dh20).astype(float)

    # ── 13. HTF Alignment + RSI/MACD (13) ─────────────────────────────
    h1_data = _align_htf_ohlcv(h1_bars, times, n)
    h4_data = _align_htf_ohlcv(h4_bars, times, n)
    d1_data = _align_htf_ohlcv(d1_bars, times, n)

    for prefix, htf in [("h1", h1_data), ("h4", h4_data), ("d1", d1_data)]:
        if htf is not None:
            c = htf["close"]
            h = htf["high"]
            l = htf["low"]
            htf_ema21 = ema(c, 21)
            features[f"pot_{prefix}_trend_atr"] = (c - htf_ema21) / atr_safe
            features[f"pot_{prefix}_rsi"] = rsi(c, 14)
            _, _, mhist = macd(c, 12, 26, 9)
            features[f"pot_{prefix}_macd_hist_atr"] = mhist / atr_safe
            features[f"pot_{prefix}_momentum"] = _slope(c, 5)
        else:
            features[f"pot_{prefix}_trend_atr"] = np.zeros(n)
            features[f"pot_{prefix}_rsi"] = np.full(n, 50.0)
            features[f"pot_{prefix}_macd_hist_atr"] = np.zeros(n)
            features[f"pot_{prefix}_momentum"] = np.zeros(n)

    # HTF alignment score
    h1_bull = (features["pot_h1_trend_atr"] > 0).astype(float)
    h4_bull = (features["pot_h4_trend_atr"] > 0).astype(float)
    d1_bull = (features["pot_d1_trend_atr"] > 0).astype(float)
    features["pot_htf_alignment"] = (h1_bull + h4_bull + d1_bull) / 3.0

    # ── Finalize ──────────────────────────────────────────────────────
    names = list(features.keys())
    cols = [np.asarray(features[k], dtype=np.float64).copy() for k in names]
    X = np.column_stack(cols).astype(np.float32)
    np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0, copy=False)
    return names, X
