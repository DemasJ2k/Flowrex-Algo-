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


def _session_vwap(closes, highs, lows, volumes, times, session_start_hour=13, session_start_min=30, is_24_7=False):
    """Session VWAP.

    - For session-based symbols (indices, XAUUSD): resets at session_start_hour:session_start_min UTC each day.
    - For 24/7 symbols (BTCUSD, ETHUSD): resets at UTC 00:00 each day (pass is_24_7=True).
    """
    n = len(closes)
    typical = (highs + lows + closes) / 3.0
    vp = typical * volumes
    if n == 0:
        return np.zeros(0, dtype=float)
    ts = np.array(times, dtype=np.int64)
    seconds_in_day = ts % 86400
    if is_24_7:
        # UTC midnight rollover — any bar with 0 <= seconds_in_day < 300 (5 min) is the new session
        is_reset = seconds_in_day < 300
    else:
        reset_sec = session_start_hour * 3600 + session_start_min * 60
        # Use >= and range rather than exact equality — survives bar-minute drift
        is_reset = (seconds_in_day >= reset_sec) & (seconds_in_day < reset_sec + 300)
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


def _opening_range(highs, lows, closes, times, session_hour=13, session_min=30, n_bars=6, is_24_7=False):
    """ORB: high/low of first n_bars after session open.

    For 24/7 symbols (BTCUSD, ETHUSD), use UTC 00:00 as the 'session open' since
    there's no real cash-open. For session-based symbols, use the passed hour:min.

    Fix over prior version: uses a 5-minute window to detect session open instead
    of exact minute equality — survives bar-timestamp drift and holiday schedules.
    """
    n = len(closes)
    orb_high = np.zeros(n, dtype=float)
    orb_low = np.zeros(n, dtype=float)
    cur_h, cur_l, bars_since = np.nan, np.nan, -1
    reset_start_sec = 0 if is_24_7 else (session_hour * 3600 + session_min * 60)
    for i in range(n):
        ts = times[i]
        try:
            sec_in_day = int(ts) % 86400
        except (TypeError, ValueError):
            sec_in_day = 0
        in_reset_window = (reset_start_sec <= sec_in_day < reset_start_sec + 300)
        if in_reset_window:
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

    # Symbol-aware session windowing
    try:
        from app.services.ml.symbol_config import SYMBOL_CONFIGS
        cfg = SYMBOL_CONFIGS.get(symbol, {})
    except Exception:
        cfg = {}
    asset_class = cfg.get("asset_class", "index")
    is_24_7 = asset_class in ("crypto",)  # BTCUSD, ETHUSD
    # Session start — if prime_hours_utc is set, use its low; else default to 13:30 (NYSE open)
    prime = cfg.get("prime_hours_utc", (13, 21))
    session_start_hour = prime[0] if isinstance(prime, (tuple, list)) else 13
    session_start_min = 30 if session_start_hour == 13 else 0

    atr_14 = atr(highs, lows, closes, 14)
    atr_safe = np.where((atr_14 > 0) & ~np.isnan(atr_14), atr_14, 1.0)  # for division
    hl_range = highs - lows

    # ── 1. VWAP features (9) — session + weekly + monthly anchored ────
    vwap_sess = _session_vwap(
        closes, highs, lows, volumes, times,
        session_start_hour=session_start_hour,
        session_start_min=session_start_min,
        is_24_7=is_24_7,
    )
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
    orb_high, orb_low = _opening_range(
        highs, lows, closes, times,
        session_hour=session_start_hour,
        session_min=session_start_min,
        is_24_7=is_24_7,
    )
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
    # Momentum = current close minus close N bars ago.
    # np.roll is circular (bar 0 wraps to bar N-1), which created spurious values
    # for the first N rows. Use a proper backward-looking diff with leading zeros.
    mom10 = np.concatenate([np.zeros(10), closes[10:] - closes[:-10]])
    mom20 = np.concatenate([np.zeros(20), closes[20:] - closes[:-20]])
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
    # CVD (cumulative volume delta): use ROLLING window sum, not unbounded cumsum.
    # Old impl used np.cumsum(cvd_delta) which grew from bar 0 → bar N. In backtest
    # this produced values on the scale of N*avg_volume; in live with only 500 loaded
    # bars, values were 1000× smaller. Model learned patterns on large-scale CVD and
    # produced wrong predictions in live. Rolling(100) keeps the scale consistent
    # between backtest and live. (Identical fix as features_flowrex.py line 311.)
    cvd_delta = volumes * np.sign(closes - opens)
    cvd = pd.Series(cvd_delta).rolling(100, min_periods=20).sum().fillna(0).values
    features["pot_cvd_slope"] = _slope(cvd, 10)
    features["pot_vol_imbalance"] = np.sign(closes - opens)
    vol_sma20 = sma(volumes, 20)
    features["pot_vol_spike"] = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    body_ratio = np.where(hl_range > 0, np.abs(closes - opens) / hl_range, 0)
    vol_ratio = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    features["pot_absorption"] = vol_ratio * (1 - body_ratio)
    features["pot_obv_slope"] = _slope(obv(closes, volumes), 10)
    # Delta divergence: price making new high but CVD not confirming.
    # Using bounded CVD (above) so divergence signal is comparable live vs backtest.
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

    # ── Regime features (option b — 2026-04-21) ───────────────────────
    # Four one-hot regime columns + a few interaction columns. Appended at
    # the end of the feature vector so models trained before this block
    # existed still get their expected 85-col input when the inference path
    # trims down to the model's saved feature_count. New retrains pick the
    # regime columns up automatically.
    try:
        features.update(_compute_regime_features(highs, lows, closes, atr_14))
    except Exception:
        # Failing features compute should never block training or inference —
        # fall back to zeros so the column shape stays stable.
        zeros = np.zeros(n, dtype=np.float64)
        for k in ("reg_trending_up", "reg_trending_down", "reg_ranging",
                 "reg_volatile", "reg_x_atr_pctile", "reg_x_trend_strength",
                 "reg_confidence"):
            features[k] = zeros

    # ── Finalize ──────────────────────────────────────────────────────
    names = list(features.keys())
    cols = [np.asarray(features[k], dtype=np.float64).copy() for k in names]
    X = np.column_stack(cols).astype(np.float32)
    np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0, copy=False)
    return names, X


def _compute_regime_features(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, atr14: np.ndarray,
) -> dict[str, np.ndarray]:
    """Bar-by-bar regime one-hot + interaction features.

    Mirrors `classify_regime_simple` decision tree using precomputed indicator
    arrays so the cost is O(n) instead of O(n²). Columns:
      - `reg_trending_up`  / `reg_trending_down` / `reg_ranging` / `reg_volatile`
        — mutually exclusive binary flags (0/1).
      - `reg_x_atr_pctile` — current ATR percentile (0-1) over last 100 bars.
        Pairs with regime flags for trees to split on (e.g. ranging + low
        ATR pctile vs volatile regime separately).
      - `reg_x_trend_strength` — |EMA50 slope over last 20 bars| / ATR,
        signed by direction. Captures trend intensity inside trending states.
      - `reg_confidence` — heuristic 0-1 confidence analogous to the runtime
        classifier's confidence output.
    """
    from app.services.backtest.indicators import adx as adx_fn
    from app.services.backtest.indicators import ema as ema_fn

    n = len(closes)
    adx_val, _, _ = adx_fn(highs, lows, closes, 14)
    ema50 = ema_fn(closes, 50)

    out = {
        "reg_trending_up": np.zeros(n, dtype=np.float64),
        "reg_trending_down": np.zeros(n, dtype=np.float64),
        "reg_ranging": np.zeros(n, dtype=np.float64),
        "reg_volatile": np.zeros(n, dtype=np.float64),
        "reg_x_atr_pctile": np.zeros(n, dtype=np.float64),
        "reg_x_trend_strength": np.zeros(n, dtype=np.float64),
        "reg_confidence": np.zeros(n, dtype=np.float64),
    }

    vol_lookback = 100
    slope_lookback = 20
    warmup = max(vol_lookback, 50 + slope_lookback, 30)
    atr_vol_pctile = 75.0
    adx_range = 20.0

    for i in range(warmup, n):
        ca = atr14[i]
        if np.isnan(ca):
            continue
        recent = atr14[max(0, i - vol_lookback + 1): i + 1]
        recent = recent[~np.isnan(recent)]
        if len(recent) < 20:
            continue
        # ATR percentile — used as an interaction signal even when not volatile.
        pctile = float(np.searchsorted(np.sort(recent), ca) / max(len(recent), 1))
        out["reg_x_atr_pctile"][i] = pctile
        thresh = float(np.percentile(recent, atr_vol_pctile))
        if thresh > 0 and ca >= thresh:
            out["reg_volatile"][i] = 1.0
            out["reg_confidence"][i] = min(1.0, 0.6 + (ca - thresh) / max(thresh, 1e-9))
            continue
        cax = adx_val[i]
        if np.isnan(cax):
            cax = 0.0
        if cax < adx_range:
            out["reg_ranging"][i] = 1.0
            out["reg_confidence"][i] = min(1.0, (adx_range - cax) / adx_range + 0.5)
            continue
        if i - slope_lookback < 0 or np.isnan(ema50[i]) or np.isnan(ema50[i - slope_lookback]):
            continue
        slope = (ema50[i] - ema50[i - slope_lookback]) / max(float(ca), 1e-9)
        if slope > 0:
            out["reg_trending_up"][i] = 1.0
        else:
            out["reg_trending_down"][i] = 1.0
        out["reg_x_trend_strength"][i] = float(slope)
        out["reg_confidence"][i] = min(1.0, abs(slope) / 2.0 + 0.5)

    return out
