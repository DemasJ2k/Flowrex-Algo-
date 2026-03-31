"""
Smart Money Concepts (SMC) feature computation.
Implements: Order Blocks, Fair Value Gaps, Break of Structure, Liquidity Levels.
No external dependency — pure numpy implementation.
"""
import numpy as np


def compute_smc_features(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    swing_window: int = 10,
) -> dict[str, np.ndarray]:
    """
    Compute Smart Money Concepts features from OHLC data.
    Returns dict of feature_name -> numpy array (same length as input).
    """
    n = len(closes)
    features = {}

    # ── Swing Highs/Lows (foundation for all SMC) ─────────────────
    swing_highs, swing_lows = _detect_swings(highs, lows, swing_window)

    # ── Break of Structure (BOS) ──────────────────────────────────
    # BOS = price breaks previous swing high (bullish) or swing low (bearish)
    bos = np.zeros(n)
    last_swing_high = highs[0]
    last_swing_low = lows[0]
    for i in range(1, n):
        if swing_highs[i] > 0:
            last_swing_high = swing_highs[i]
        if swing_lows[i] > 0:
            last_swing_low = swing_lows[i]
        if highs[i] > last_swing_high and last_swing_high > 0:
            bos[i] = 1.0  # bullish BOS
        elif lows[i] < last_swing_low and last_swing_low > 0:
            bos[i] = -1.0  # bearish BOS
    features["smc_bos"] = bos

    # ── Change of Character (CHoCH) ───────────────────────────────
    # CHoCH = trend reversal signal (first break against prevailing trend)
    choch = np.zeros(n)
    trend = 0  # 1=bullish, -1=bearish
    for i in range(1, n):
        if bos[i] == 1.0:
            if trend == -1:
                choch[i] = 1.0  # bullish CHoCH (was bearish, now breaks up)
            trend = 1
        elif bos[i] == -1.0:
            if trend == 1:
                choch[i] = -1.0  # bearish CHoCH (was bullish, now breaks down)
            trend = -1
    features["smc_choch"] = choch

    # ── Order Blocks (OB) ─────────────────────────────────────────
    # Bullish OB: last bearish candle before a strong bullish move
    # Bearish OB: last bullish candle before a strong bearish move
    ob_bull_top = np.zeros(n)
    ob_bull_bot = np.zeros(n)
    ob_bear_top = np.zeros(n)
    ob_bear_bot = np.zeros(n)

    for i in range(2, n):
        body_prev = closes[i-1] - opens[i-1]
        body_curr = closes[i] - opens[i]
        move_size = abs(closes[i] - closes[i-2])
        avg_range = np.mean(highs[max(0,i-20):i] - lows[max(0,i-20):i])

        if avg_range == 0:
            continue

        # Strong move = > 1.5x average range
        if move_size > 1.5 * avg_range:
            if body_curr > 0 and body_prev < 0:
                # Bullish OB: last bearish candle before bullish move
                ob_bull_top[i] = highs[i-1]
                ob_bull_bot[i] = lows[i-1]
            elif body_curr < 0 and body_prev > 0:
                # Bearish OB: last bullish candle before bearish move
                ob_bear_top[i] = highs[i-1]
                ob_bear_bot[i] = lows[i-1]

    # Forward-fill OB levels + compute proximity
    features["smc_ob_bull_proximity"] = _proximity_to_zone(closes, ob_bull_bot, ob_bull_top)
    features["smc_ob_bear_proximity"] = _proximity_to_zone(closes, ob_bear_bot, ob_bear_top)

    # ── Fair Value Gaps (FVG) ─────────────────────────────────────
    # Bullish FVG: gap between bar[i-2].high and bar[i].low (price skipped)
    # Bearish FVG: gap between bar[i].high and bar[i-2].low
    fvg_bull = np.zeros(n)
    fvg_bear = np.zeros(n)

    for i in range(2, n):
        # Bullish FVG: bar[i].low > bar[i-2].high → gap up
        if lows[i] > highs[i-2]:
            gap_size = lows[i] - highs[i-2]
            if closes[i] > 0:
                fvg_bull[i] = gap_size / closes[i]  # normalized
        # Bearish FVG: bar[i].high < bar[i-2].low → gap down
        if highs[i] < lows[i-2]:
            gap_size = lows[i-2] - highs[i]
            if closes[i] > 0:
                fvg_bear[i] = gap_size / closes[i]

    features["smc_fvg_bull"] = fvg_bull
    features["smc_fvg_bear"] = fvg_bear

    # FVG proximity: distance to nearest unfilled FVG
    features["smc_fvg_bull_proximity"] = _nearest_fvg_proximity(closes, highs, lows, fvg_bull, direction=1)
    features["smc_fvg_bear_proximity"] = _nearest_fvg_proximity(closes, highs, lows, fvg_bear, direction=-1)

    # ── Liquidity Levels ──────────────────────────────────────────
    # Equal highs/lows = liquidity pools (stop losses cluster there)
    features["smc_liquidity_above"] = _liquidity_distance(closes, highs, swing_highs, direction=1)
    features["smc_liquidity_below"] = _liquidity_distance(closes, lows, swing_lows, direction=-1)

    # ── Premium/Discount Zone ─────────────────────────────────────
    # Price position within recent swing range (>0.5 = premium, <0.5 = discount)
    pd_zone = np.zeros(n)
    for i in range(swing_window, n):
        recent_high = np.max(highs[i-swing_window:i+1])
        recent_low = np.min(lows[i-swing_window:i+1])
        rng = recent_high - recent_low
        if rng > 0:
            pd_zone[i] = (closes[i] - recent_low) / rng
    features["smc_premium_discount"] = pd_zone

    # ── Displacement Detection ────────────────────────────────────
    # Large-body candles indicating institutional momentum
    body_sizes = np.abs(closes - opens)
    avg_body = np.zeros(n)
    for i in range(20, n):
        avg_body[i] = np.mean(body_sizes[i-20:i])
    with np.errstate(divide="ignore", invalid="ignore"):
        displacement = np.where(avg_body > 0, body_sizes / avg_body, 0)
    displacement = np.nan_to_num(displacement, nan=0.0, posinf=0.0, neginf=0.0)
    features["smc_displacement"] = displacement

    return features


def _detect_swings(highs: np.ndarray, lows: np.ndarray, window: int = 10):
    """Detect swing highs and lows."""
    n = len(highs)
    swing_highs = np.zeros(n)
    swing_lows = np.zeros(n)

    for i in range(window, n - window):
        if highs[i] == np.max(highs[i-window:i+window+1]):
            swing_highs[i] = highs[i]
        if lows[i] == np.min(lows[i-window:i+window+1]):
            swing_lows[i] = lows[i]

    return swing_highs, swing_lows


def _proximity_to_zone(closes, zone_bot, zone_top):
    """Compute normalized distance from price to nearest active zone."""
    n = len(closes)
    proximity = np.zeros(n)
    active_bot = 0.0
    active_top = 0.0

    for i in range(n):
        if zone_bot[i] > 0:
            active_bot = zone_bot[i]
            active_top = zone_top[i]
        if active_bot > 0 and active_top > 0 and closes[i] > 0:
            zone_mid = (active_bot + active_top) / 2
            proximity[i] = (closes[i] - zone_mid) / closes[i]
    return proximity


def _nearest_fvg_proximity(closes, highs, lows, fvg_signal, direction):
    """Distance to nearest unfilled FVG."""
    n = len(closes)
    proximity = np.zeros(n)
    last_fvg_level = 0.0

    for i in range(n):
        if fvg_signal[i] > 0:
            if direction == 1:
                last_fvg_level = highs[i-2] if i >= 2 else 0  # bottom of bullish FVG
            else:
                last_fvg_level = lows[i-2] if i >= 2 else 0  # top of bearish FVG
        if last_fvg_level > 0 and closes[i] > 0:
            proximity[i] = (closes[i] - last_fvg_level) / closes[i]
    return proximity


def _liquidity_distance(closes, price_array, swing_levels, direction):
    """Distance to nearest liquidity pool (equal highs/lows cluster)."""
    n = len(closes)
    distance = np.zeros(n)

    for i in range(20, n):
        # Find swing levels in recent window
        recent_swings = swing_levels[i-20:i]
        active_swings = recent_swings[recent_swings > 0]

        if len(active_swings) == 0 or closes[i] == 0:
            continue

        if direction == 1:  # liquidity above
            above = active_swings[active_swings > closes[i]]
            if len(above) > 0:
                distance[i] = (np.min(above) - closes[i]) / closes[i]
        else:  # liquidity below
            below = active_swings[active_swings < closes[i]]
            if len(below) > 0:
                distance[i] = (closes[i] - np.max(below)) / closes[i]

    return distance
