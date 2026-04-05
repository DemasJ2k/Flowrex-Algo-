"""
Divergence & Breakout-Retest features.
  - RSI regular/hidden divergence (reversal + continuation signals)
  - MACD histogram divergence
  - Short-term RSI (2-bar, 6-bar mean reversion)
  - Breakout detection (consolidation, volume confirmation)
  - Retest detection (return to breakout level, rejection analysis)
"""
import numpy as np
import pandas as pd


def compute_divergence_features(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    times: np.ndarray,
    atr_values: np.ndarray = None,
) -> dict[str, np.ndarray]:
    """
    Compute 15 divergence and breakout-retest features.
    Returns dict of feature_name -> numpy array (length n).
    """
    n = len(closes)
    features = {}

    if atr_values is None or len(atr_values) != n:
        atr_values = _simple_atr(highs, lows, closes, 14)

    atr_safe = np.where(atr_values > 0, atr_values, 1e-10)
    vol_sma20 = pd.Series(volumes).rolling(20, min_periods=1).mean().values

    # ── Precompute indicators ────────────────────────────────────────
    rsi_14 = _rsi(closes, 14)
    macd_hist = _macd_hist(closes)

    # Detect swing highs/lows on price and RSI
    price_sh, price_sl = _detect_swings(highs, lows, window=10)
    rsi_sh, rsi_sl = _detect_swings_1d(rsi_14, window=10)

    # ── RSI Divergence (5 features) ──────────────────────────────────

    reg_bull = np.zeros(n)
    reg_bear = np.zeros(n)
    hid_bull = np.zeros(n)
    hid_bear = np.zeros(n)
    div_strength = np.zeros(n)

    lookback = 30

    # Collect swing indices for efficient lookup
    for i in range(lookback + 10, n):
        # Find last 2 price swing lows and corresponding RSI lows
        p_lows = []
        r_lows = []
        for j in range(i - lookback, i):
            if price_sl[j] > 0:
                p_lows.append((j, price_sl[j]))
            if rsi_sl[j] > 0:
                r_lows.append((j, rsi_sl[j]))

        # Regular Bullish: price lower low, RSI higher low
        if len(p_lows) >= 2 and len(r_lows) >= 2:
            p1, p2 = p_lows[-2], p_lows[-1]
            r1, r2 = r_lows[-2], r_lows[-1]
            if p2[1] < p1[1] and r2[1] > r1[1]:
                reg_bull[i] = 1.0
                div_strength[i] = abs(p2[1] - p1[1]) / max(atr_safe[i], 1e-10)
            # Hidden Bullish: price higher low, RSI lower low
            elif p2[1] > p1[1] and r2[1] < r1[1]:
                hid_bull[i] = 1.0

        # Find swing highs
        p_highs = []
        r_highs = []
        for j in range(i - lookback, i):
            if price_sh[j] > 0:
                p_highs.append((j, price_sh[j]))
            if rsi_sh[j] > 0:
                r_highs.append((j, rsi_sh[j]))

        # Regular Bearish: price higher high, RSI lower high
        if len(p_highs) >= 2 and len(r_highs) >= 2:
            p1, p2 = p_highs[-2], p_highs[-1]
            r1, r2 = r_highs[-2], r_highs[-1]
            if p2[1] > p1[1] and r2[1] < r1[1]:
                reg_bear[i] = 1.0
                div_strength[i] = max(div_strength[i],
                    abs(p2[1] - p1[1]) / max(atr_safe[i], 1e-10))
            # Hidden Bearish: price lower high, RSI higher high
            elif p2[1] < p1[1] and r2[1] > r1[1]:
                hid_bear[i] = 1.0

    features["div_rsi_regular_bull"] = reg_bull
    features["div_rsi_regular_bear"] = reg_bear
    features["div_rsi_hidden_bull"] = hid_bull
    features["div_rsi_hidden_bear"] = hid_bear
    features["div_rsi_strength"] = np.clip(div_strength, 0, 5)

    # ── Short-term RSI (2 features) ──────────────────────────────────
    features["div_rsi_2bar"] = _rsi(closes, 2) / 100.0   # normalize to [0, 1]
    features["div_rsi_6bar"] = _rsi(closes, 6) / 100.0

    # ── MACD Divergence (1 feature) ──────────────────────────────────
    macd_div = np.zeros(n)
    macd_sh, macd_sl = _detect_swings_1d(macd_hist, window=8)

    for i in range(lookback + 10, n):
        p_highs = [(j, price_sh[j]) for j in range(i-lookback, i) if price_sh[j] > 0]
        m_highs = [(j, macd_sh[j]) for j in range(i-lookback, i) if macd_sh[j] != 0]

        if len(p_highs) >= 2 and len(m_highs) >= 2:
            if p_highs[-1][1] > p_highs[-2][1] and m_highs[-1][1] < m_highs[-2][1]:
                macd_div[i] = -1.0  # bearish MACD divergence

        p_lows = [(j, price_sl[j]) for j in range(i-lookback, i) if price_sl[j] > 0]
        m_lows = [(j, macd_sl[j]) for j in range(i-lookback, i) if macd_sl[j] != 0]

        if len(p_lows) >= 2 and len(m_lows) >= 2:
            if p_lows[-1][1] < p_lows[-2][1] and m_lows[-1][1] > m_lows[-2][1]:
                macd_div[i] = 1.0  # bullish MACD divergence

    features["div_macd_divergence"] = macd_div

    # ── Breakout Detection (4 features) ──────────────────────────────

    consol_bars = np.zeros(n)
    range_atr = np.zeros(n)
    breakout_body = np.zeros(n)
    breakout_vol = np.zeros(n)

    # Track consolidation state
    consol_start = 0
    consol_high = highs[0]
    consol_low = lows[0]
    in_consol = False

    for i in range(10, n):
        # Rolling 10-bar range
        r10_high = np.max(highs[i-9:i+1])
        r10_low = np.min(lows[i-9:i+1])
        r10_range = r10_high - r10_low

        # Check if in consolidation (range < 0.5 * ATR normalized over 10 bars)
        if r10_range < atr_safe[i] * 0.5:
            if not in_consol:
                consol_start = i - 9
                consol_high = r10_high
                consol_low = r10_low
                in_consol = True
            else:
                consol_high = max(consol_high, r10_high)
                consol_low = min(consol_low, r10_low)
            consol_bars[i] = min((i - consol_start) / 50.0, 1.0)
            range_atr[i] = r10_range / max(atr_safe[i], 1e-10)
        else:
            # Breakout? Check if we were in consolidation and price broke out
            if in_consol and (closes[i] > consol_high or closes[i] < consol_low):
                cr = highs[i] - lows[i]
                body = abs(closes[i] - opens[i])
                breakout_body[i] = body / max(cr, 1e-10)  # body ratio
                breakout_vol[i] = volumes[i] / max(vol_sma20[i], 1e-10)
            in_consol = False

    features["div_consolidation_bars"] = consol_bars
    features["div_range_atr"] = np.clip(range_atr, 0, 2)
    features["div_breakout_body_ratio"] = np.clip(breakout_body, 0, 1)
    features["div_breakout_volume"] = np.clip(breakout_vol, 0, 5)

    # ── Retest Detection (3 features) ────────────────────────────────

    retest_flag = np.zeros(n)
    retest_vol = np.zeros(n)
    retest_rej = np.zeros(n)

    # Track breakout levels
    last_bo_level = 0.0
    last_bo_dir = 0     # +1 bullish, -1 bearish
    last_bo_bar = 0
    last_bo_vol = 0.0

    for i in range(10, n):
        # Detect breakout
        if breakout_body[i] > 0:
            if closes[i] > opens[i]:
                last_bo_level = consol_high if in_consol else highs[i-1]
                last_bo_dir = 1
            else:
                last_bo_level = consol_low if in_consol else lows[i-1]
                last_bo_dir = -1
            last_bo_bar = i
            last_bo_vol = volumes[i]

        # Detect retest (price returns to breakout level within 20 bars)
        if last_bo_level > 0 and 0 < (i - last_bo_bar) <= 20:
            dist_to_level = abs(closes[i] - last_bo_level) / max(atr_safe[i], 1e-10)
            if dist_to_level < 0.3:  # within 0.3 ATR of breakout level
                retest_flag[i] = 1.0
                retest_vol[i] = volumes[i] / max(last_bo_vol, 1e-10)
                # Rejection: wick ratio in the right direction
                cr = highs[i] - lows[i]
                if cr > 0:
                    if last_bo_dir == 1:  # bullish breakout, retest = support
                        retest_rej[i] = (np.minimum(opens[i], closes[i]) - lows[i]) / cr
                    else:  # bearish breakout, retest = resistance
                        retest_rej[i] = (highs[i] - np.maximum(opens[i], closes[i])) / cr

    features["div_retest_flag"] = retest_flag
    features["div_retest_volume_ratio"] = np.clip(retest_vol, 0, 3)
    features["div_retest_rejection"] = np.clip(retest_rej, 0, 1)

    return features


# ── Indicator helpers ────────────────────────────────────────────────────

def _rsi(closes, period=14):
    """RSI computation (Wilder's smoothing)."""
    n = len(closes)
    result = np.full(n, 50.0)
    deltas = np.diff(closes, prepend=closes[0])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.zeros(n)
    avg_loss = np.zeros(n)
    if period < n:
        avg_gain[period] = np.mean(gains[1:period+1])
        avg_loss[period] = np.mean(losses[1:period+1])
        for i in range(period + 1, n):
            avg_gain[i] = (avg_gain[i-1] * (period-1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i-1] * (period-1) + losses[i]) / period
        for i in range(period, n):
            if avg_loss[i] > 0:
                rs = avg_gain[i] / avg_loss[i]
                result[i] = 100 - (100 / (1 + rs))
            elif avg_gain[i] > 0:
                result[i] = 100.0
    return result


def _macd_hist(closes, fast=12, slow=26, signal=9):
    """MACD histogram."""
    n = len(closes)
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    return macd_line - signal_line


def _ema(values, period):
    """Exponential moving average."""
    n = len(values)
    result = np.zeros(n)
    alpha = 2.0 / (period + 1)
    result[0] = values[0]
    for i in range(1, n):
        result[i] = alpha * values[i] + (1 - alpha) * result[i-1]
    return result


def _detect_swings(highs, lows, window=10):
    """Detect swing highs/lows on OHLC."""
    n = len(highs)
    sh = np.zeros(n)
    sl = np.zeros(n)
    for i in range(window, n - window):
        if highs[i] == np.max(highs[i-window:i+window+1]):
            sh[i] = highs[i]
        if lows[i] == np.min(lows[i-window:i+window+1]):
            sl[i] = lows[i]
    return sh, sl


def _detect_swings_1d(values, window=10):
    """Detect swing highs/lows on a 1D array (RSI, MACD, etc.)."""
    n = len(values)
    sh = np.zeros(n)
    sl = np.zeros(n)
    for i in range(window, n - window):
        segment = values[i-window:i+window+1]
        if values[i] == np.max(segment):
            sh[i] = values[i]
        if values[i] == np.min(segment):
            sl[i] = values[i]
    return sh, sl


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
