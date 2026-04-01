"""
ICT / Smart Money Concepts — comprehensive feature module (~30 features).

Implements: Enhanced BOS/CHOCH, liquidity sweeps, order blocks, FVGs,
premium/discount, OTE zones, displacement, breaker blocks, confluence scoring.

All pure numpy — no external dependencies.  Single entry point:
    compute_ict_features(...) -> dict[str, np.ndarray]
"""
import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rolling_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 period: int = 14) -> np.ndarray:
    """Simple rolling ATR (true range SMA)."""
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
    atr = np.empty(n)
    atr[:period] = np.cumsum(tr[:period]) / np.arange(1, period + 1)
    for i in range(period, n):
        atr[i] = atr[i - 1] + (tr[i] - tr[i - period]) / period
    # clamp to avoid division by zero
    atr = np.maximum(atr, 1e-10)
    return atr


def _detect_swings(highs: np.ndarray, lows: np.ndarray,
                   window: int) -> tuple[np.ndarray, np.ndarray]:
    """Return arrays of swing-high/swing-low prices (0 where none)."""
    n = len(highs)
    sh = np.zeros(n)
    sl = np.zeros(n)
    for i in range(window, n - window):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        if highs[i] == np.max(highs[lo:hi]):
            sh[i] = highs[i]
        if lows[i] == np.min(lows[lo:hi]):
            sl[i] = lows[i]
    return sh, sl


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_ict_features(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    h4_highs: np.ndarray | None = None,
    h4_lows: np.ndarray | None = None,
    h4_closes: np.ndarray | None = None,
    swing_window: int = 10,
) -> dict[str, np.ndarray]:
    """Compute ~30 ICT/SMC features. All outputs are float32, same length as input."""

    n = len(closes)
    atr = _rolling_atr(highs, lows, closes)
    body = closes - opens
    abs_body = np.abs(body)

    swing_highs, swing_lows = _detect_swings(highs, lows, swing_window)

    # ===================================================================
    # 1. Enhanced Market Structure (4)
    # ===================================================================
    bos = np.zeros(n)       # +1 bullish, -1 bearish
    choch = np.zeros(n)
    trend = np.zeros(n)     # running trend

    last_sh = closes[0]
    last_sl = closes[0]
    cur_trend = 0

    for i in range(1, n):
        if swing_highs[i] > 0:
            last_sh = swing_highs[i]
        if swing_lows[i] > 0:
            last_sl = swing_lows[i]
        # close-based BOS (not wicks)
        if closes[i] > last_sh and last_sh > 0:
            if cur_trend == -1:
                choch[i] = 1.0
            bos[i] = 1.0
            cur_trend = 1
        elif closes[i] < last_sl and last_sl > 0:
            if cur_trend == 1:
                choch[i] = -1.0
            bos[i] = -1.0
            cur_trend = -1
        trend[i] = cur_trend

    features: dict[str, np.ndarray] = {}
    features["ict_trend"] = trend

    # choch_recent: 1 if CHOCH in last 5 bars
    choch_recent = np.zeros(n)
    for i in range(n):
        lo = max(0, i - 4)
        if np.any(choch[lo:i + 1] != 0):
            choch_recent[i] = 1.0
    features["ict_choch_recent"] = choch_recent

    # bos_momentum: rolling bullish-bearish BOS count (20 bars)
    bos_mom = np.zeros(n)
    bull_cum = np.cumsum(bos == 1.0)
    bear_cum = np.cumsum(bos == -1.0)
    for i in range(n):
        lo = max(0, i - 19)
        bos_mom[i] = (bull_cum[i] - bull_cum[lo] + (1 if bos[lo] == 1.0 else 0)
                       - (bear_cum[i] - bear_cum[lo] + (1 if bos[lo] == -1.0 else 0)))
    features["ict_bos_momentum"] = bos_mom

    # bars_since_choch
    bars_choch = np.zeros(n)
    last_c = -200
    for i in range(n):
        if choch[i] != 0:
            last_c = i
        bars_choch[i] = min(i - last_c, 100) / 100.0 if last_c >= 0 else 1.0
    features["ict_bars_since_choch"] = bars_choch

    # ===================================================================
    # 2. Liquidity Features (6)
    # ===================================================================
    sweep_bull = np.zeros(n)
    sweep_bear = np.zeros(n)

    for i in range(1, n):
        # nearest swing high above close[i-1]
        lk = max(0, i - 40)
        sh_slice = swing_highs[lk:i]
        above = sh_slice[sh_slice > closes[i - 1]]
        if len(above) > 0:
            nearest_sh = np.min(above)
            if highs[i] > nearest_sh and closes[i] < nearest_sh:
                sweep_bull[i] = 1.0
        sl_slice = swing_lows[lk:i]
        below = sl_slice[(sl_slice > 0) & (sl_slice < closes[i - 1])]
        if len(below) > 0:
            nearest_sl = np.max(below)
            if lows[i] < nearest_sl and closes[i] > nearest_sl:
                sweep_bear[i] = 1.0

    features["ict_sweep_bull"] = sweep_bull
    features["ict_sweep_bear"] = sweep_bear

    # equal highs / equal lows (within 0.1% tolerance, 20-bar lookback)
    eq_highs = np.zeros(n)
    eq_lows = np.zeros(n)
    for i in range(20, n):
        sh_w = swing_highs[i - 20:i + 1]
        active_sh = sh_w[sh_w > 0]
        if len(active_sh) >= 2:
            tol = 0.001 * np.mean(active_sh)
            # count clusters: pairwise within tolerance
            diffs = np.abs(active_sh[:, None] - active_sh[None, :])
            np.fill_diagonal(diffs, np.inf)
            eq_highs[i] = float(np.sum(diffs < tol)) / 2.0

        sl_w = swing_lows[i - 20:i + 1]
        active_sl = sl_w[sl_w > 0]
        if len(active_sl) >= 2:
            tol = 0.001 * np.mean(active_sl)
            diffs = np.abs(active_sl[:, None] - active_sl[None, :])
            np.fill_diagonal(diffs, np.inf)
            eq_lows[i] = float(np.sum(diffs < tol)) / 2.0

    features["ict_equal_highs"] = eq_highs
    features["ict_equal_lows"] = eq_lows

    # distance to liquidity
    dist_liq_high = np.zeros(n)
    dist_liq_low = np.zeros(n)
    for i in range(swing_window, n):
        sh_w = swing_highs[max(0, i - 40):i]
        above = sh_w[sh_w > closes[i]]
        if len(above) > 0:
            dist_liq_high[i] = (np.min(above) - closes[i]) / atr[i]

        sl_w = swing_lows[max(0, i - 40):i]
        below = sl_w[(sl_w > 0) & (sl_w < closes[i])]
        if len(below) > 0:
            dist_liq_low[i] = (closes[i] - np.max(below)) / atr[i]

    features["ict_dist_to_liq_high"] = dist_liq_high
    features["ict_dist_to_liq_low"] = dist_liq_low

    # ===================================================================
    # 3. Order Block Features (4)
    # ===================================================================
    # Track OBs as list of (top, bottom, direction, bar_idx, mitigated)
    MAX_OBS = 50
    ob_tops = np.zeros(MAX_OBS)
    ob_bots = np.zeros(MAX_OBS)
    ob_dirs = np.zeros(MAX_OBS)  # +1 bull, -1 bear
    ob_mit = np.zeros(MAX_OBS, dtype=bool)  # mitigated
    ob_idx = np.zeros(MAX_OBS, dtype=int)
    ob_ptr = 0  # ring buffer pointer

    ob_bull_active = np.zeros(n)
    ob_bear_active = np.zeros(n)

    # Also track FVG zones for the ob_with_fvg feature
    fvg_zones: list[tuple[float, float, int]] = []  # (top, bot, direction)

    for i in range(2, n):
        avg_range = np.mean(highs[max(0, i - 20):i] - lows[max(0, i - 20):i])
        if avg_range == 0:
            avg_range = 1e-10
        move = abs(closes[i] - closes[i - 2])

        # Detect new OB
        if move > 1.5 * avg_range:
            if body[i] > 0 and body[i - 1] < 0:
                # Bullish OB = last bearish candle
                slot = ob_ptr % MAX_OBS
                ob_tops[slot] = highs[i - 1]
                ob_bots[slot] = lows[i - 1]
                ob_dirs[slot] = 1.0
                ob_mit[slot] = False
                ob_idx[slot] = i
                ob_ptr += 1
            elif body[i] < 0 and body[i - 1] > 0:
                slot = ob_ptr % MAX_OBS
                ob_tops[slot] = highs[i - 1]
                ob_bots[slot] = lows[i - 1]
                ob_dirs[slot] = -1.0
                ob_mit[slot] = False
                ob_idx[slot] = i
                ob_ptr += 1

        # Track FVGs
        if lows[i] > highs[i - 2]:
            fvg_zones.append((lows[i], highs[i - 2], 1))  # bull FVG
        if highs[i] < lows[i - 2]:
            fvg_zones.append((lows[i - 2], highs[i], -1))  # bear FVG

        # Mark mitigated OBs and check active
        for j in range(min(ob_ptr, MAX_OBS)):
            if ob_mit[j]:
                continue
            if ob_dirs[j] == 1.0:
                if lows[i] < ob_bots[j]:
                    ob_mit[j] = True
                elif closes[i] >= ob_bots[j] and closes[i] <= ob_tops[j] + 0.5 * atr[i]:
                    ob_bull_active[i] = 1.0
            elif ob_dirs[j] == -1.0:
                if highs[i] > ob_tops[j]:
                    ob_mit[j] = True
                elif closes[i] <= ob_tops[j] and closes[i] >= ob_bots[j] - 0.5 * atr[i]:
                    ob_bear_active[i] = 1.0

    features["ict_ob_bull_active"] = ob_bull_active
    features["ict_ob_bear_active"] = ob_bear_active

    # OB in OTE zone and OB+FVG overlap — computed in a second pass
    ob_in_ote = np.zeros(n)
    ob_with_fvg = np.zeros(n)

    # We need swing info for OTE. Find last significant swing leg.
    # Re-use swing_highs/swing_lows
    last_swing_hi_val = 0.0
    last_swing_lo_val = 0.0
    for i in range(n):
        if swing_highs[i] > 0:
            last_swing_hi_val = swing_highs[i]
        if swing_lows[i] > 0:
            last_swing_lo_val = swing_lows[i]

        if last_swing_hi_val > 0 and last_swing_lo_val > 0:
            rng = last_swing_hi_val - last_swing_lo_val
            if rng > 0:
                # OTE zone (62-79% retracement)
                if trend[i] >= 0:  # bullish — retracement down from high
                    ote_top = last_swing_hi_val - 0.62 * rng
                    ote_bot = last_swing_hi_val - 0.79 * rng
                else:
                    ote_top = last_swing_lo_val + 0.79 * rng
                    ote_bot = last_swing_lo_val + 0.62 * rng

                # Check active (unmitigated) OBs
                for j in range(min(ob_ptr, MAX_OBS)):
                    if ob_mit[j]:
                        continue
                    ob_mid = (ob_tops[j] + ob_bots[j]) / 2.0
                    if ote_bot <= ob_mid <= ote_top:
                        ob_in_ote[i] = 1.0
                        break

        # OB with overlapping FVG
        for j in range(min(ob_ptr, MAX_OBS)):
            if ob_mit[j]:
                continue
            for fvg_top, fvg_bot, _ in fvg_zones[-30:]:  # last 30 FVGs
                if ob_bots[j] < fvg_top and ob_tops[j] > fvg_bot:
                    ob_with_fvg[i] = 1.0
                    break
            if ob_with_fvg[i] > 0:
                break

    features["ict_ob_in_ote"] = ob_in_ote
    features["ict_ob_with_fvg"] = ob_with_fvg

    # ===================================================================
    # 4. Fair Value Gap Features (4)
    # ===================================================================
    # Track unfilled FVGs as list of (top, bot, direction, filled)
    fvg_list_top: list[float] = []
    fvg_list_bot: list[float] = []
    fvg_list_dir: list[int] = []
    fvg_list_filled: list[bool] = []

    fvg_bull_count = np.zeros(n)
    fvg_bear_count = np.zeros(n)
    fvg_nearest_dist = np.zeros(n)
    fvg_ce_touch = np.zeros(n)

    MAX_FVGS = 500  # Cap FVG list to prevent O(n*k) blowup on large datasets

    for i in range(2, n):
        # Detect new FVGs
        if lows[i] > highs[i - 2]:
            fvg_list_top.append(lows[i])
            fvg_list_bot.append(highs[i - 2])
            fvg_list_dir.append(1)
            fvg_list_filled.append(False)
        if highs[i] < lows[i - 2]:
            fvg_list_top.append(lows[i - 2])
            fvg_list_bot.append(highs[i])
            fvg_list_dir.append(-1)
            fvg_list_filled.append(False)

        # Trim old FVGs to prevent unbounded growth
        if len(fvg_list_top) > MAX_FVGS:
            trim = len(fvg_list_top) - MAX_FVGS
            fvg_list_top = fvg_list_top[trim:]
            fvg_list_bot = fvg_list_bot[trim:]
            fvg_list_dir = fvg_list_dir[trim:]
            fvg_list_filled = fvg_list_filled[trim:]

        # Check fills and count
        bull_cnt = 0
        bear_cnt = 0
        nearest = 1e10
        nearest_signed = 0.0

        for k in range(len(fvg_list_top)):
            if fvg_list_filled[k]:
                continue
            ft, fb, fd = fvg_list_top[k], fvg_list_bot[k], fvg_list_dir[k]
            # Check fill
            if fd == 1 and lows[i] <= fb:
                fvg_list_filled[k] = True
                continue
            if fd == -1 and highs[i] >= ft:
                fvg_list_filled[k] = True
                continue

            mid = (ft + fb) / 2.0
            dist = abs(closes[i] - mid)

            if fd == 1 and mid < closes[i]:
                bull_cnt += 1
                if dist < nearest:
                    nearest = dist
                    nearest_signed = -(dist / atr[i])  # below = negative
            elif fd == -1 and mid > closes[i]:
                bear_cnt += 1
                if dist < nearest:
                    nearest = dist
                    nearest_signed = dist / atr[i]  # above = positive

            # CE touch: price at 50% of FVG
            ce = (ft + fb) / 2.0
            if abs(closes[i] - ce) < 0.3 * atr[i]:
                fvg_ce_touch[i] = 1.0

        fvg_bull_count[i] = bull_cnt
        fvg_bear_count[i] = bear_cnt
        if nearest < 1e9:
            fvg_nearest_dist[i] = nearest_signed

    features["ict_fvg_bull_count"] = fvg_bull_count
    features["ict_fvg_bear_count"] = fvg_bear_count
    features["ict_fvg_nearest_dist"] = fvg_nearest_dist
    features["ict_fvg_ce_touch"] = fvg_ce_touch

    # ===================================================================
    # 5. Premium / Discount (3)
    # ===================================================================
    pd_lookback = 50
    pd_position = np.zeros(n)
    for i in range(pd_lookback, n):
        hi = np.max(highs[i - pd_lookback:i + 1])
        lo = np.min(lows[i - pd_lookback:i + 1])
        rng = hi - lo
        if rng > 0:
            pd_position[i] = (closes[i] - lo) / rng
    features["ict_pd_position"] = pd_position

    # H4 premium/discount
    pd_h4 = np.zeros(n)
    if h4_highs is not None and h4_lows is not None and h4_closes is not None:
        n_h4 = len(h4_closes)
        h4_pd = np.zeros(n_h4)
        h4_lb = min(12, n_h4)  # ~12 H4 bars = ~2 days
        for j in range(h4_lb, n_h4):
            hi = np.max(h4_highs[j - h4_lb:j + 1])
            lo = np.min(h4_lows[j - h4_lb:j + 1])
            rng = hi - lo
            if rng > 0:
                h4_pd[j] = (h4_closes[j] - lo) / rng
        # Forward-fill to M5 length (each H4 bar = 48 M5 bars)
        ratio = max(1, n // max(n_h4, 1))
        for j in range(n_h4):
            start = j * ratio
            end = min((j + 1) * ratio, n)
            pd_h4[start:end] = h4_pd[j]
    features["ict_pd_h4_position"] = pd_h4

    # Discount + bullish alignment
    in_disc_bull = np.zeros(n)
    for i in range(n):
        if pd_position[i] < 0.5 and trend[i] > 0:
            in_disc_bull[i] = 1.0
    features["ict_in_discount_bull"] = in_disc_bull

    # ===================================================================
    # 6. OTE Zone (2)
    # ===================================================================
    in_ote = np.zeros(n)
    ote_dist_705 = np.zeros(n)

    last_sh_val = 0.0
    last_sl_val = 0.0
    for i in range(n):
        if swing_highs[i] > 0:
            last_sh_val = swing_highs[i]
        if swing_lows[i] > 0:
            last_sl_val = swing_lows[i]
        if last_sh_val > 0 and last_sl_val > 0:
            rng = last_sh_val - last_sl_val
            if rng > 0:
                if trend[i] >= 0:
                    ote_top = last_sh_val - 0.62 * rng
                    ote_bot = last_sh_val - 0.79 * rng
                    level_705 = last_sh_val - 0.705 * rng
                else:
                    ote_bot = last_sl_val + 0.62 * rng
                    ote_top = last_sl_val + 0.79 * rng
                    level_705 = last_sl_val + 0.705 * rng
                if ote_bot <= closes[i] <= ote_top:
                    in_ote[i] = 1.0
                ote_dist_705[i] = (closes[i] - level_705) / atr[i]

    features["ict_in_ote_zone"] = in_ote
    features["ict_ote_dist_705"] = ote_dist_705

    # ===================================================================
    # 7. Displacement (3)
    # ===================================================================
    disp_threshold = 1.5  # body > 1.5x ATR = displacement
    is_disp = abs_body > (disp_threshold * atr)
    disp_dir_raw = np.where(body > 0, 1.0, -1.0) * is_disp

    disp_strength = np.zeros(n)
    disp_direction = np.zeros(n)
    for i in range(n):
        lo = max(0, i - 9)
        window = slice(lo, i + 1)
        ratios = abs_body[window] / atr[window]
        mask = is_disp[window]
        if np.any(mask):
            disp_strength[i] = np.max(ratios[mask])
            # direction of strongest
            idx_max = np.argmax(ratios * mask)
            disp_direction[i] = disp_dir_raw[lo + idx_max]
    features["ict_disp_strength"] = disp_strength
    features["ict_disp_direction"] = disp_direction

    # displacement imbalance over 20 bars
    bull_disp = (disp_dir_raw == 1.0).astype(float)
    bear_disp = (disp_dir_raw == -1.0).astype(float)
    bull_cum2 = np.cumsum(bull_disp)
    bear_cum2 = np.cumsum(bear_disp)
    disp_imb = np.zeros(n)
    for i in range(n):
        lo = max(0, i - 19)
        disp_imb[i] = ((bull_cum2[i] - (bull_cum2[lo - 1] if lo > 0 else 0))
                        - (bear_cum2[i] - (bear_cum2[lo - 1] if lo > 0 else 0)))
    features["ict_disp_imbalance"] = disp_imb

    # ===================================================================
    # 8. Breaker Blocks (2)
    # ===================================================================
    # A breaker = a failed OB. Bullish breaker = bearish OB that got mitigated
    # (price broke above it), now acts as support.
    breaker_bull = np.zeros(n)
    breaker_bear = np.zeros(n)

    # Track breakers from OB mitigation events
    breaker_zones: list[tuple[float, float, int, bool]] = []  # top, bot, dir, active

    # Re-scan OBs for breaker detection
    ob2_tops: list[float] = []
    ob2_bots: list[float] = []
    ob2_dirs: list[int] = []  # +1 bull, -1 bear
    ob2_active: list[bool] = []

    for i in range(2, n):
        avg_range = np.mean(highs[max(0, i - 20):i] - lows[max(0, i - 20):i])
        if avg_range == 0:
            avg_range = 1e-10
        move = abs(closes[i] - closes[i - 2])
        if move > 1.5 * avg_range:
            if body[i] > 0 and body[i - 1] < 0:
                ob2_tops.append(highs[i - 1])
                ob2_bots.append(lows[i - 1])
                ob2_dirs.append(1)
                ob2_active.append(True)
            elif body[i] < 0 and body[i - 1] > 0:
                ob2_tops.append(highs[i - 1])
                ob2_bots.append(lows[i - 1])
                ob2_dirs.append(-1)
                ob2_active.append(True)

        # Check OBs for mitigation -> becomes breaker
        for k in range(len(ob2_tops)):
            if not ob2_active[k]:
                continue
            if ob2_dirs[k] == 1 and lows[i] < ob2_bots[k]:
                # Bullish OB failed -> bearish breaker? No.
                # Failed bullish OB (support broke) -> bearish breaker
                ob2_active[k] = False
                breaker_zones.append((ob2_tops[k], ob2_bots[k], -1, True))
            elif ob2_dirs[k] == -1 and highs[i] > ob2_tops[k]:
                # Failed bearish OB (resistance broke) -> bullish breaker
                ob2_active[k] = False
                breaker_zones.append((ob2_tops[k], ob2_bots[k], 1, True))

        # Check if price is near active breakers
        for k in range(len(breaker_zones)):
            t, b, d, act = breaker_zones[k]
            if not act:
                continue
            # Invalidate if price breaks through breaker opposite side
            if d == 1 and lows[i] < b:
                breaker_zones[k] = (t, b, d, False)
                continue
            if d == -1 and highs[i] > t:
                breaker_zones[k] = (t, b, d, False)
                continue
            if d == 1 and closes[i] >= b and closes[i] <= t + 0.3 * atr[i]:
                breaker_bull[i] = 1.0
            elif d == -1 and closes[i] <= t and closes[i] >= b - 0.3 * atr[i]:
                breaker_bear[i] = 1.0

    features["ict_breaker_bull"] = breaker_bull
    features["ict_breaker_bear"] = breaker_bear

    # ===================================================================
    # 9. Confluence Score (2)
    # ===================================================================
    score = np.zeros(n)
    for i in range(n):
        s = 0.0
        # 1. In OTE zone
        s += in_ote[i]
        # 2. OB active (bull or bear)
        s += ob_bull_active[i] + ob_bear_active[i]
        # 3. OB in OTE
        s += ob_in_ote[i]
        # 4. FVG CE touch
        s += fvg_ce_touch[i]
        # 5. Liquidity sweep
        s += sweep_bull[i] + sweep_bear[i]
        # 6. Displacement present
        s += 1.0 if disp_strength[i] > 0 else 0.0
        # 7. Breaker active
        s += breaker_bull[i] + breaker_bear[i]
        # 8. Correct PD alignment
        s += in_disc_bull[i]
        score[i] = min(s, 10.0)

    features["ict_confluence_score"] = score

    # setup grade: 0=none, 1=C (2-3), 2=B (4-5), 3=A (6+)
    grade = np.zeros(n)
    grade[score >= 2] = 1.0
    grade[score >= 4] = 2.0
    grade[score >= 6] = 3.0
    features["ict_setup_grade"] = grade

    # ===================================================================
    # Final cleanup: cast to float32, remove NaN/Inf
    # ===================================================================
    for k in features:
        arr = np.nan_to_num(features[k], nan=0.0, posinf=0.0, neginf=0.0)
        features[k] = arr.astype(np.float32)

    return features
