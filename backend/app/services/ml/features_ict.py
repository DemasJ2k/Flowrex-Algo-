"""
ICT (Inner Circle Trader) feature computation.
Extends the base SMC features with advanced ICT concepts:
  - Liquidity sweep detection (equal highs/lows, session sweeps, wick analysis)
  - Enhanced FVG (consequent encroachment, discount/premium, OB overlap, killzone)
  - Enhanced Order Blocks (size, volume, displacement strength, FVG confluence)
  - Kill zones & Silver Bullet windows
  - Market Structure Shift (MSS) with displacement
  - ICT Confluence composite score
"""
import numpy as np
import pandas as pd


def compute_ict_features(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    times: np.ndarray,
    swing_window: int = 10,
    atr_values: np.ndarray = None,
) -> dict[str, np.ndarray]:
    """
    Compute 20 ICT-specific features from M5 OHLCV data.
    Returns dict of feature_name -> numpy array (length n).
    """
    n = len(closes)
    features = {}

    # Fallback ATR if not provided
    if atr_values is None or len(atr_values) != n:
        atr_values = _simple_atr(highs, lows, closes, 14)

    # Precompute common arrays
    hours = _extract_utc_hours(times)
    vol_sma20 = _rolling_mean(volumes, 20)
    body_sizes = np.abs(closes - opens)
    avg_body20 = _rolling_mean(body_sizes, 20)

    # Detect swings (reuse same logic as smc_features)
    swing_highs, swing_lows = _detect_swings(highs, lows, swing_window)

    # ── Liquidity Sweep Detection (4 features) ──────────────────────

    # 1. Equal highs/lows count in lookback
    eq_count = np.zeros(n)
    tol_mult = 0.1  # within ATR*0.1
    for i in range(20, n):
        tol = atr_values[i] * tol_mult if atr_values[i] > 0 else 0.001
        recent_sh = swing_highs[i-20:i]
        active_sh = recent_sh[recent_sh > 0]
        if len(active_sh) >= 2:
            # Count pairs within tolerance
            pairs = 0
            for j in range(len(active_sh)):
                for k in range(j+1, len(active_sh)):
                    if abs(active_sh[j] - active_sh[k]) < tol:
                        pairs += 1
            eq_count[i] = min(pairs, 5)  # cap at 5
        recent_sl = swing_lows[i-20:i]
        active_sl = recent_sl[recent_sl > 0]
        if len(active_sl) >= 2:
            pairs = 0
            for j in range(len(active_sl)):
                for k in range(j+1, len(active_sl)):
                    if abs(active_sl[j] - active_sl[k]) < tol:
                        pairs += 1
            eq_count[i] += min(pairs, 5)
    features["ict_equal_hl_count"] = eq_count / 10.0  # normalize to [0, 1]

    # 2. Session high/low swept (stop hunt detection)
    session_swept = np.zeros(n)
    rolling_high = pd.Series(highs).rolling(96, min_periods=20).max().values  # ~8h window
    rolling_low = pd.Series(lows).rolling(96, min_periods=20).min().values
    for i in range(2, n):
        # High swept: price exceeded rolling high then closed back below
        if highs[i] > rolling_high[i-1] and closes[i] < rolling_high[i-1]:
            session_swept[i] = -1.0  # bearish sweep (trapped longs)
        # Low swept: price dipped below rolling low then closed back above
        elif lows[i] < rolling_low[i-1] and closes[i] > rolling_low[i-1]:
            session_swept[i] = 1.0  # bullish sweep (trapped shorts)
    features["ict_session_hl_swept"] = session_swept

    # 3. Sweep wick ratio (how much rejection on sweep bar)
    sweep_wick = np.zeros(n)
    candle_range = highs - lows
    upper_wick = highs - np.maximum(opens, closes)
    lower_wick = np.minimum(opens, closes) - lows
    for i in range(n):
        if candle_range[i] > 0:
            if session_swept[i] == -1.0:  # bearish sweep -> upper wick matters
                sweep_wick[i] = upper_wick[i] / candle_range[i]
            elif session_swept[i] == 1.0:  # bullish sweep -> lower wick matters
                sweep_wick[i] = lower_wick[i] / candle_range[i]
    features["ict_sweep_wick_ratio"] = sweep_wick

    # 4. Sweep volume spike
    sweep_vol = np.zeros(n)
    for i in range(n):
        if session_swept[i] != 0 and vol_sma20[i] > 0:
            sweep_vol[i] = volumes[i] / vol_sma20[i]
    features["ict_sweep_volume_spike"] = np.clip(sweep_vol, 0, 5)

    # ── Enhanced Fair Value Gaps (5 features) ────────────────────────

    # Track active FVGs: (top, bottom, bar_index, direction)
    fvg_ce = np.zeros(n)       # distance to consequent encroachment
    fvg_size = np.zeros(n)     # FVG size / ATR
    fvg_in_disc = np.zeros(n)  # FVG in discount/premium zone
    fvg_ob_overlap = np.zeros(n)  # FVG overlaps with OB
    fvg_in_kz = np.zeros(n)   # FVG created during kill zone

    # Track last active FVG state
    last_fvg_top = 0.0
    last_fvg_bot = 0.0
    last_fvg_dir = 0  # +1 bullish, -1 bearish
    last_fvg_kz = 0

    # Also track last active OB for overlap detection
    last_ob_top = 0.0
    last_ob_bot = 0.0

    # Premium/discount zone (rolling 50-bar range)
    pd_zone = np.zeros(n)
    for i in range(50, n):
        rh = np.max(highs[i-50:i+1])
        rl = np.min(lows[i-50:i+1])
        rng = rh - rl
        if rng > 0:
            pd_zone[i] = (closes[i] - rl) / rng

    for i in range(2, n):
        # Detect bullish FVG
        if lows[i] > highs[i-2]:
            last_fvg_top = lows[i]
            last_fvg_bot = highs[i-2]
            last_fvg_dir = 1
            last_fvg_kz = 1 if _is_killzone_hour(hours[i]) else 0
        # Detect bearish FVG
        elif highs[i] < lows[i-2]:
            last_fvg_top = lows[i-2]
            last_fvg_bot = highs[i]
            last_fvg_dir = -1
            last_fvg_kz = 1 if _is_killzone_hour(hours[i]) else 0

        # Detect OB (for overlap)
        body_prev = closes[i-1] - opens[i-1]
        body_curr = closes[i] - opens[i]
        move_size = abs(closes[i] - closes[i-2])
        avg_rng = np.mean(highs[max(0,i-20):i] - lows[max(0,i-20):i]) if i > 0 else 1
        if avg_rng > 0 and move_size > 1.5 * avg_rng:
            if body_curr > 0 and body_prev < 0:
                last_ob_top = highs[i-1]
                last_ob_bot = lows[i-1]
            elif body_curr < 0 and body_prev > 0:
                last_ob_top = highs[i-1]
                last_ob_bot = lows[i-1]

        # Compute FVG features (forward-filled from last detection)
        if last_fvg_top > 0 and last_fvg_bot > 0:
            ce_level = (last_fvg_top + last_fvg_bot) / 2  # consequent encroachment
            atr_i = max(atr_values[i], 1e-10)
            fvg_ce[i] = (closes[i] - ce_level) / atr_i
            fvg_size[i] = (last_fvg_top - last_fvg_bot) / atr_i

            # FVG in correct zone? (bullish FVG should be in discount)
            if last_fvg_dir == 1 and pd_zone[i] < 0.5:
                fvg_in_disc[i] = 1.0
            elif last_fvg_dir == -1 and pd_zone[i] > 0.5:
                fvg_in_disc[i] = 1.0

            # FVG overlaps OB?
            if last_ob_top > 0 and last_ob_bot > 0:
                overlap = max(0, min(last_fvg_top, last_ob_top) - max(last_fvg_bot, last_ob_bot))
                fvg_ob_overlap[i] = 1.0 if overlap > 0 else 0.0

            fvg_in_kz[i] = float(last_fvg_kz)

            # Check if FVG has been filled (mitigated)
            if last_fvg_dir == 1 and closes[i] < last_fvg_bot:
                last_fvg_top = 0.0; last_fvg_bot = 0.0  # FVG filled
            elif last_fvg_dir == -1 and closes[i] > last_fvg_top:
                last_fvg_top = 0.0; last_fvg_bot = 0.0

    features["ict_fvg_ce_level"] = np.clip(fvg_ce, -5, 5)
    features["ict_fvg_size_atr"] = np.clip(fvg_size, 0, 5)
    features["ict_fvg_in_discount"] = fvg_in_disc
    features["ict_fvg_overlaps_ob"] = fvg_ob_overlap
    features["ict_fvg_in_killzone"] = fvg_in_kz

    # ── Enhanced Order Blocks (5 features) ───────────────────────────

    ob_size = np.zeros(n)
    ob_vol_ratio = np.zeros(n)
    ob_disp_str = np.zeros(n)
    ob_fvg_conf = np.zeros(n)
    ob_dist = np.zeros(n)

    last_ob_mid = 0.0
    last_ob_sz = 0.0
    last_ob_vr = 0.0
    last_ob_ds = 0.0
    last_ob_fc = 0.0

    for i in range(2, n):
        body_prev = closes[i-1] - opens[i-1]
        body_curr = closes[i] - opens[i]
        move_size = abs(closes[i] - closes[i-2])
        avg_rng = np.mean(highs[max(0,i-20):i] - lows[max(0,i-20):i]) if i > 0 else 1
        atr_i = max(atr_values[i], 1e-10)

        if avg_rng > 0 and move_size > 1.5 * avg_rng:
            if (body_curr > 0 and body_prev < 0) or (body_curr < 0 and body_prev > 0):
                ob_range = highs[i-1] - lows[i-1]
                last_ob_mid = (highs[i-1] + lows[i-1]) / 2
                last_ob_sz = ob_range / atr_i
                last_ob_vr = volumes[i-1] / max(vol_sma20[i], 1e-10)
                last_ob_ds = abs(body_curr) / atr_i  # displacement strength
                # Check if FVG was created with this move
                has_fvg = (lows[i] > highs[i-2]) or (highs[i] < lows[i-2]) if i >= 2 else False
                last_ob_fc = 1.0 if has_fvg else 0.0

        ob_size[i] = last_ob_sz
        ob_vol_ratio[i] = last_ob_vr
        ob_disp_str[i] = last_ob_ds
        ob_fvg_conf[i] = last_ob_fc
        if last_ob_mid > 0 and atr_i > 0:
            ob_dist[i] = (closes[i] - last_ob_mid) / atr_i

    features["ict_ob_size_atr"] = np.clip(ob_size, 0, 5)
    features["ict_ob_volume_ratio"] = np.clip(ob_vol_ratio, 0, 5)
    features["ict_ob_displacement"] = np.clip(ob_disp_str, 0, 10)
    features["ict_ob_fvg_confluence"] = ob_fvg_conf
    features["ict_ob_distance"] = np.clip(ob_dist, -10, 10)

    # ── Kill Zones & Silver Bullet (3 features) ─────────────────────

    # Silver Bullet windows (UTC): 8-9 (London), 15-16 (NY AM), 19-20 (NY PM)
    sb = np.zeros(n)
    for i in range(n):
        h = hours[i]
        if h in (8, 15, 19):
            sb[i] = 1.0
    features["ict_silver_bullet"] = sb

    # Asian session range (UTC 22-06) / ATR
    asian_range = np.zeros(n)
    asian_high = 0.0
    asian_low = 1e18
    last_day = -1
    for i in range(n):
        day = int(times[i]) // 86400
        h = hours[i]
        if day != last_day:
            if last_day >= 0 and asian_high > 0 and asian_low < 1e18:
                # Forward-fill yesterday's Asian range
                pass
            asian_high = 0.0
            asian_low = 1e18
            last_day = day
        if h >= 22 or h < 6:  # Asian session
            asian_high = max(asian_high, highs[i])
            asian_low = min(asian_low, lows[i])
        atr_i = max(atr_values[i], 1e-10)
        if asian_high > 0 and asian_low < 1e18:
            asian_range[i] = (asian_high - asian_low) / atr_i
    features["ict_asian_range_atr"] = np.clip(asian_range, 0, 5)

    # Asian high/low swept during London/NY
    asian_swept = np.zeros(n)
    day_asian_high = 0.0
    day_asian_low = 1e18
    curr_day = -1
    for i in range(n):
        day = int(times[i]) // 86400
        h = hours[i]
        if day != curr_day:
            day_asian_high = 0.0
            day_asian_low = 1e18
            curr_day = day
        if h >= 22 or h < 6:
            day_asian_high = max(day_asian_high, highs[i])
            day_asian_low = min(day_asian_low, lows[i])
        elif h >= 6 and day_asian_high > 0 and day_asian_low < 1e18:
            if highs[i] > day_asian_high:
                asian_swept[i] = 1.0  # Asian high swept
            elif lows[i] < day_asian_low:
                asian_swept[i] = -1.0  # Asian low swept
    features["ict_asian_swept"] = asian_swept

    # ── Market Structure Shift (2 features) ──────────────────────────

    mss = np.zeros(n)
    bars_since_struct = np.zeros(n)
    last_struct_bar = 0
    trend_dir = 0
    for i in range(2, n):
        # Detect BOS
        is_bos = False
        if swing_highs[i] > 0 or swing_lows[i] > 0:
            pass  # swing detected but not BOS
        # Simple BOS: close breaks recent swing
        sh = swing_highs[max(0,i-20):i]
        active_sh = sh[sh > 0]
        sl = swing_lows[max(0,i-20):i]
        active_sl = sl[sl > 0]

        bos_dir = 0
        if len(active_sh) > 0 and closes[i] > np.max(active_sh):
            bos_dir = 1
        elif len(active_sl) > 0 and closes[i] < np.min(active_sl):
            bos_dir = -1

        if bos_dir != 0:
            last_struct_bar = i
            # Check for displacement (strong body)
            has_displacement = body_sizes[i] > 1.5 * avg_body20[i] if avg_body20[i] > 0 else False
            # MSS = structure break + displacement + direction change
            if bos_dir != trend_dir and has_displacement:
                mss[i] = float(bos_dir)
            trend_dir = bos_dir

        bars_since_struct[i] = min((i - last_struct_bar) / 100.0, 1.0) if last_struct_bar > 0 else 1.0

    features["ict_mss_displacement"] = mss
    features["ict_bars_since_structure"] = bars_since_struct

    # ── ICT Confluence Score (1 feature) ─────────────────────────────
    # Weighted sum of active ICT conditions at each bar (0-7 scale)
    confluence = np.zeros(n)
    is_kz = np.zeros(n)
    for i in range(n):
        if _is_killzone_hour(hours[i]):
            is_kz[i] = 1.0

    confluence += is_kz                                              # in kill zone
    confluence += (np.abs(session_swept) > 0).astype(float)          # liquidity swept
    confluence += (np.abs(ob_dist) < 1.0).astype(float)              # near order block
    confluence += (np.abs(fvg_ce) < 1.0).astype(float)               # near FVG
    confluence += fvg_in_disc                                         # FVG in correct zone
    confluence += fvg_ob_overlap                                      # FVG + OB overlap
    confluence += sb                                                  # Silver Bullet window
    features["ict_confluence"] = confluence / 7.0  # normalize to [0, 1]

    return features


# ── Helpers ──────────────────────────────────────────────────────────────

def _extract_utc_hours(times: np.ndarray) -> np.ndarray:
    """Extract UTC hour from Unix timestamps."""
    return ((times.astype(np.int64) % 86400) // 3600).astype(np.int32)


def _is_killzone_hour(h: int) -> bool:
    """Kill zones: London (7-10 UTC), NY (13-16 UTC), London close (15-17 UTC)."""
    return h in (7, 8, 9, 13, 14, 15, 16)


def _detect_swings(highs, lows, window=10):
    """Detect swing highs and lows (same as smc_features)."""
    n = len(highs)
    sh = np.zeros(n)
    sl = np.zeros(n)
    for i in range(window, n - window):
        if highs[i] == np.max(highs[i-window:i+window+1]):
            sh[i] = highs[i]
        if lows[i] == np.min(lows[i-window:i+window+1]):
            sl[i] = lows[i]
    return sh, sl


def _rolling_mean(arr, window):
    """Simple rolling mean with NaN fill."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=1).mean().values


def _simple_atr(highs, lows, closes, period=14):
    """Simple ATR computation as fallback."""
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr_out = np.zeros(n)
    atr_out[period-1] = np.mean(tr[:period])
    for i in range(period, n):
        atr_out[i] = (atr_out[i-1] * (period - 1) + tr[i]) / period
    return atr_out
