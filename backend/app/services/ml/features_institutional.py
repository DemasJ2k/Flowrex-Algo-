"""
Institutional trading features: VWAP, Volume Profile, Supply/Demand Zones, Wyckoff events.
These capture what institutional desks and prop firm traders actually use.
"""
import numpy as np
import pandas as pd


def compute_institutional_features(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    times: np.ndarray,
    atr_values: np.ndarray = None,
) -> dict[str, np.ndarray]:
    """
    Compute 18 institutional-style features from M5 OHLCV.
    Returns dict of feature_name -> numpy array (length n).
    """
    n = len(closes)
    features = {}

    if atr_values is None or len(atr_values) != n:
        atr_values = _simple_atr(highs, lows, closes, 14)

    atr_safe = np.where(atr_values > 0, atr_values, 1e-10)
    vol_sma20 = pd.Series(volumes).rolling(20, min_periods=1).mean().values

    # ── VWAP with Daily Reset (4 features) ───────────────────────────

    typical_price = (highs + lows + closes) / 3.0
    day_ids = times.astype(np.int64) // 86400

    df_vwap = pd.DataFrame({
        "day": day_ids, "tp_vol": typical_price * volumes, "vol": volumes,
    })
    cum_vol = df_vwap.groupby("day")["vol"].cumsum().values
    cum_tpvol = df_vwap.groupby("day")["tp_vol"].cumsum().values
    vwap = np.where(cum_vol > 0, cum_tpvol / cum_vol, closes)

    # VWAP standard deviation bands (rolling within day)
    vwap_sq = np.where(cum_vol > 0,
        df_vwap.groupby("day").apply(
            lambda g: ((g["tp_vol"] / g["vol"].clip(lower=1e-10) -
                        (g["tp_vol"].cumsum() / g["vol"].cumsum().clip(lower=1e-10)))**2).cumsum()
        ).reset_index(drop=True).values if len(df_vwap) > 0 else np.zeros(n),
        np.zeros(n))
    # Simplified: use rolling std of (close - vwap)
    vwap_dev = pd.Series(closes - vwap).rolling(50, min_periods=5).std().fillna(0).values
    vwap_dev_safe = np.where(vwap_dev > 0, vwap_dev, 1e-10)

    features["inst_vwap_dist_atr"] = np.clip((closes - vwap) / atr_safe, -5, 5)
    features["inst_vwap_band_pos"] = np.clip((closes - vwap) / vwap_dev_safe, -3, 3) / 3.0
    features["inst_vwap_slope"] = _slope(vwap, 5)
    # VWAP cross
    vwap_cross = np.zeros(n)
    for i in range(1, n):
        if closes[i] > vwap[i] and closes[i-1] <= vwap[i-1]:
            vwap_cross[i] = 1.0
        elif closes[i] < vwap[i] and closes[i-1] >= vwap[i-1]:
            vwap_cross[i] = -1.0
    features["inst_vwap_cross"] = vwap_cross

    # ── Volume Profile Proxy (3 features) ────────────────────────────
    # Compute every 12 bars (hourly stride) for performance, forward-fill

    poc_dist = np.zeros(n)
    va_pos = np.zeros(n)
    hvn_lvn = np.zeros(n)

    window = 288  # 1 day of M5 bars
    stride = 12   # compute every hour
    n_bins = 20

    for start in range(window, n, stride):
        i_end = min(start + stride, n)
        lo_slice = lows[start-window:start]
        hi_slice = highs[start-window:start]
        cl_slice = closes[start-window:start]
        vo_slice = volumes[start-window:start]

        price_low = np.min(lo_slice)
        price_high = np.max(hi_slice)
        if price_high <= price_low:
            continue

        # Build volume histogram
        bin_edges = np.linspace(price_low, price_high, n_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        bin_vol = np.zeros(n_bins)
        for j in range(len(cl_slice)):
            idx = min(int((cl_slice[j] - price_low) / (price_high - price_low) * n_bins), n_bins - 1)
            idx = max(idx, 0)
            bin_vol[idx] += vo_slice[j]

        total_vol = bin_vol.sum()
        if total_vol <= 0:
            continue

        # POC: bin with highest volume
        poc_idx = np.argmax(bin_vol)
        poc_price = bin_centers[poc_idx]

        # Value Area: expand from POC until 70% volume captured
        va_lo_idx = poc_idx
        va_hi_idx = poc_idx
        captured = bin_vol[poc_idx]
        while captured < 0.70 * total_vol and (va_lo_idx > 0 or va_hi_idx < n_bins - 1):
            expand_lo = bin_vol[va_lo_idx - 1] if va_lo_idx > 0 else 0
            expand_hi = bin_vol[va_hi_idx + 1] if va_hi_idx < n_bins - 1 else 0
            if expand_lo >= expand_hi and va_lo_idx > 0:
                va_lo_idx -= 1
                captured += expand_lo
            elif va_hi_idx < n_bins - 1:
                va_hi_idx += 1
                captured += expand_hi
            else:
                break

        va_low = bin_edges[va_lo_idx]
        va_high = bin_edges[va_hi_idx + 1]

        # Fill for stride bars
        for j in range(start, i_end):
            atr_j = max(atr_safe[j], 1e-10)
            poc_dist[j] = (closes[j] - poc_price) / atr_j
            if va_high > va_low:
                va_pos[j] = np.clip((closes[j] - va_low) / (va_high - va_low), 0, 1)
            # HVN/LVN: check if price is at high or low volume node
            p_idx = min(int((closes[j] - price_low) / (price_high - price_low) * n_bins), n_bins - 1)
            p_idx = max(p_idx, 0)
            vol_pctile = bin_vol[p_idx] / max(np.max(bin_vol), 1e-10)
            hvn_lvn[j] = 1.0 if vol_pctile > 0.7 else (-1.0 if vol_pctile < 0.3 else 0.0)

    features["inst_poc_distance"] = np.clip(poc_dist, -5, 5)
    features["inst_va_position"] = va_pos
    features["inst_hvn_lvn"] = hvn_lvn

    # ── Supply/Demand Zones (6 features) ─────────────────────────────

    sd_nearest = np.zeros(n)
    sd_fresh = np.zeros(n)
    sd_strength = np.zeros(n)
    sd_type = np.zeros(n)
    at_demand = np.zeros(n)
    at_supply = np.zeros(n)

    # Zone: (top, bot, type, impulse_strength, touch_count, birth_bar)
    zones = []
    MAX_ZONES = 20

    body = closes - opens
    avg_body = pd.Series(np.abs(body)).rolling(20, min_periods=1).mean().values

    for i in range(5, n):
        # Detect base: 2-5 small-body bars
        base_start = -1
        for k in range(max(0, i-5), i):
            if abs(body[k]) < 0.5 * avg_body[k] if avg_body[k] > 0 else True:
                if base_start < 0:
                    base_start = k
            else:
                base_start = -1

        if base_start >= 0 and (i - base_start) >= 2:
            # Check for impulse before and after base
            pre_impulse = abs(closes[base_start] - closes[max(0, base_start-3)]) if base_start >= 3 else 0
            post_impulse = abs(closes[i] - closes[base_start]) if i > base_start else 0
            avg_imp = avg_body[i] * 3 if avg_body[i] > 0 else 0

            if pre_impulse > avg_imp or post_impulse > avg_imp:
                zone_top = np.max(highs[base_start:i+1])
                zone_bot = np.min(lows[base_start:i+1])
                pre_dir = 1 if closes[base_start] > closes[max(0, base_start-3)] else -1
                post_dir = 1 if closes[i] > closes[base_start] else -1
                imp_str = max(pre_impulse, post_impulse) / max(atr_safe[i], 1e-10)

                # Classify: RBR=1, DBD=-1, RBD=0.5, DBR=-0.5
                if pre_dir > 0 and post_dir > 0:
                    z_type = 1.0    # RBR (demand continuation)
                elif pre_dir < 0 and post_dir < 0:
                    z_type = -1.0   # DBD (supply continuation)
                elif pre_dir > 0 and post_dir < 0:
                    z_type = 0.5    # RBD (supply reversal)
                else:
                    z_type = -0.5   # DBR (demand reversal)

                if len(zones) >= MAX_ZONES:
                    zones.pop(0)
                zones.append({"top": zone_top, "bot": zone_bot, "type": z_type,
                              "imp": imp_str, "touches": 0, "birth": i})

        # Update zone touches and compute features
        best_dist = 1e10
        best_zone = None
        expired = []
        for zi, z in enumerate(zones):
            mid = (z["top"] + z["bot"]) / 2
            dist = abs(closes[i] - mid) / max(atr_safe[i], 1e-10)

            # Touch detection
            if closes[i] >= z["bot"] and closes[i] <= z["top"]:
                z["touches"] += 1

            # Expire after 5 touches or 500 bars
            if z["touches"] >= 5 or (i - z["birth"]) > 500:
                expired.append(zi)
                continue

            if dist < best_dist:
                best_dist = dist
                best_zone = z

        for zi in reversed(expired):
            zones.pop(zi)

        if best_zone:
            sd_nearest[i] = min(best_dist, 10)
            sd_fresh[i] = min(best_zone["touches"], 5) / 5.0
            sd_strength[i] = best_zone["imp"] / (1 + best_zone["touches"])
            sd_type[i] = best_zone["type"]

            prox = abs(closes[i] - (best_zone["top"] + best_zone["bot"]) / 2) / max(atr_safe[i], 1e-10)
            if best_zone["type"] < 0 and prox < 0.5:  # supply zone
                at_supply[i] = 1.0
            elif best_zone["type"] > 0 and prox < 0.5:  # demand zone (RBR/DBR)
                at_demand[i] = 1.0

    features["inst_sd_nearest_dist"] = np.clip(sd_nearest, 0, 10) / 10.0
    features["inst_sd_zone_freshness"] = sd_fresh
    features["inst_sd_zone_strength"] = np.clip(sd_strength, 0, 5)
    features["inst_sd_zone_type"] = sd_type
    features["inst_at_demand"] = at_demand
    features["inst_at_supply"] = at_supply

    # ── Wyckoff Events (4 features) ──────────────────────────────────

    # 1. Effort vs Result: volume/price_move ratio
    price_move = np.abs(closes - opens)
    price_move_safe = np.where(price_move > 0, price_move, 1e-10)
    evr_raw = volumes / price_move_safe
    evr_mean = pd.Series(evr_raw).rolling(20, min_periods=1).mean().values
    evr_safe = np.where(evr_mean > 0, evr_mean, 1e-10)
    features["inst_effort_vs_result"] = np.clip(evr_raw / evr_safe, 0, 5)

    # 2. Spring/Upthrust: false breakout of 50-bar range
    spring = np.zeros(n)
    roll_low50 = pd.Series(lows).rolling(50, min_periods=20).min().values
    roll_high50 = pd.Series(highs).rolling(50, min_periods=20).max().values
    for i in range(2, n):
        # Spring: dip below range low, close back above
        if lows[i] < roll_low50[i-1] and closes[i] > roll_low50[i-1]:
            spring[i] = 1.0
        # Upthrust: push above range high, close back below
        elif highs[i] > roll_high50[i-1] and closes[i] < roll_high50[i-1]:
            spring[i] = -1.0
    features["inst_spring_upthrust"] = spring

    # 3. Volume climax: extreme volume + extreme range
    candle_range = highs - lows
    avg_range20 = pd.Series(candle_range).rolling(20, min_periods=1).mean().values
    climax = np.zeros(n)
    for i in range(20, n):
        vol_extreme = volumes[i] > 3.0 * vol_sma20[i] if vol_sma20[i] > 0 else False
        range_extreme = candle_range[i] > 2.0 * avg_range20[i] if avg_range20[i] > 0 else False
        if vol_extreme and range_extreme:
            climax[i] = 1.0
    features["inst_volume_climax"] = climax

    # 4. ATR compression: ATR(14) / ATR(50), low = squeeze
    atr50 = _simple_atr(highs, lows, closes, 50)
    atr50_safe = np.where(atr50 > 0, atr50, 1e-10)
    features["inst_atr_compression"] = np.clip(atr_values / atr50_safe, 0, 3)

    # ── Absorption (1 feature) ───────────────────────────────────────
    # High wicks + high volume = large limit orders absorbing aggression
    upper_wick = highs - np.maximum(opens, closes)
    lower_wick = np.minimum(opens, closes) - lows
    candle_range_safe = np.where(candle_range > 0, candle_range, 1e-10)
    wick_ratio = (upper_wick + lower_wick) / candle_range_safe
    vol_ratio = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    features["inst_absorption"] = np.clip(wick_ratio * vol_ratio, 0, 5)

    return features


# ── Helpers ──────────────────────────────────────────────────────────────

def _slope(values, period=5):
    """Normalised rolling slope (same as features_mtf._slope but simplified)."""
    n = len(values)
    result = np.zeros(n)
    for i in range(period, n):
        segment = values[i-period+1:i+1]
        if abs(values[i]) > 0:
            result[i] = (segment[-1] - segment[0]) / (abs(values[i]) * period)
    return np.clip(result, -1, 1)


def _simple_atr(highs, lows, closes, period=14):
    """Simple ATR fallback."""
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr_out = np.zeros(n)
    if period <= n:
        atr_out[period-1] = np.mean(tr[:period])
        for i in range(period, n):
            atr_out[i] = (atr_out[i-1] * (period - 1) + tr[i]) / period
    return atr_out
