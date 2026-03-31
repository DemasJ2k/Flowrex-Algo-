"""
Larry Williams strategy features — ~25 features.

Implements: volatility breakout (stretch), range expansion, multi-period %R,
smash day / key reversal, trend filter (value line), oops pattern.

Entry point: compute_williams_features(opens, highs, lows, closes, volumes, times)
"""
import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────

def _rolling_mean(arr: np.ndarray, period: int) -> np.ndarray:
    """Simple rolling mean, zero-filled warmup."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    cs = np.cumsum(arr)
    for i in range(period - 1, n):
        out[i] = (cs[i] - (cs[i - period] if i >= period else 0.0)) / period
    return out


def _rolling_max(arr: np.ndarray, period: int) -> np.ndarray:
    """Rolling max over window, zero-filled warmup."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        start = max(0, i - period + 1)
        out[i] = np.max(arr[start:i + 1]) if i >= period - 1 else 0.0
    return out


def _rolling_min(arr: np.ndarray, period: int) -> np.ndarray:
    """Rolling min over window, zero-filled warmup."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        start = max(0, i - period + 1)
        out[i] = np.min(arr[start:i + 1]) if i >= period - 1 else 0.0
    return out


def _williams_r(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int) -> np.ndarray:
    """Williams %R — returns values in [-100, 0], zero-filled warmup."""
    n = len(closes)
    out = np.zeros(n, dtype=np.float64)
    for i in range(period - 1, n):
        hh = np.max(highs[i - period + 1:i + 1])
        ll = np.min(lows[i - period + 1:i + 1])
        rng = hh - ll
        out[i] = ((hh - closes[i]) / rng * -100.0) if rng > 0 else -50.0
    return out


def _true_range(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """True Range array (length N). First bar uses high-low."""
    tr = np.empty(len(closes), dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    tr[1:] = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
    )
    return tr


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """Average True Range, zero-filled warmup."""
    tr = _true_range(highs, lows, closes)
    out = np.zeros(len(closes), dtype=np.float64)
    if len(closes) < period:
        return out
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, len(closes)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


# ── Main ──────────────────────────────────────────────────────────────────

def compute_williams_features(
    opens: np.ndarray, highs: np.ndarray, lows: np.ndarray,
    closes: np.ndarray, volumes: np.ndarray,
    times: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Compute ~25 Larry Williams strategy features. All outputs float32, same length as input."""
    n = len(closes)
    feat: dict[str, np.ndarray] = {}

    # ── 1. Volatility Breakout / Stretch (5) ──────────────────────────
    buy_stretch_raw = np.abs(opens - lows)
    sell_stretch_raw = np.abs(highs - opens)
    stretch_up = _rolling_mean(buy_stretch_raw, 3)
    stretch_down = _rolling_mean(sell_stretch_raw, 3)

    feat["lw_stretch_up"] = stretch_up
    feat["lw_stretch_down"] = stretch_down
    feat["lw_above_stretch"] = (closes > opens + stretch_up).astype(np.float64)
    feat["lw_below_stretch"] = (closes < opens - stretch_down).astype(np.float64)
    denom = np.where(stretch_down > 0, stretch_down, 1.0)
    feat["lw_stretch_ratio"] = stretch_up / denom

    # ── 2. Range Expansion (4) ────────────────────────────────────────
    tr = _true_range(highs, lows, closes)
    atr10 = _atr(highs, lows, closes, 10)
    atr10_safe = np.where(atr10 > 0, atr10, 1.0)
    feat["lw_range_expansion"] = tr / atr10_safe

    bar_range = highs - lows
    # NR4
    nr4 = np.zeros(n, dtype=np.float64)
    for i in range(3, n):
        if bar_range[i] <= np.min(bar_range[i - 3:i + 1]):
            nr4[i] = 1.0
    feat["lw_nr4"] = nr4

    # NR7
    nr7 = np.zeros(n, dtype=np.float64)
    for i in range(6, n):
        if bar_range[i] <= np.min(bar_range[i - 6:i + 1]):
            nr7[i] = 1.0
    feat["lw_nr7"] = nr7

    # Inside bar
    inside = np.zeros(n, dtype=np.float64)
    inside[1:] = ((highs[1:] < highs[:-1]) & (lows[1:] > lows[:-1])).astype(np.float64)
    feat["lw_inside_bar"] = inside

    # ── 3. Williams %R Multi-Period (8) ───────────────────────────────
    wr5 = _williams_r(highs, lows, closes, 5)
    wr14 = _williams_r(highs, lows, closes, 14)
    wr28 = _williams_r(highs, lows, closes, 28)

    feat["lw_wr_5"] = wr5
    feat["lw_wr_28"] = wr28

    # 3-bar slope (rate of change)
    wr5_slope = np.zeros(n, dtype=np.float64)
    wr5_slope[3:] = wr5[3:] - wr5[:-3]
    feat["lw_wr_5_slope"] = wr5_slope

    wr28_slope = np.zeros(n, dtype=np.float64)
    wr28_slope[3:] = wr28[3:] - wr28[:-3]
    feat["lw_wr_28_slope"] = wr28_slope

    # Aligned signals
    wr5_turning_up = np.zeros(n, dtype=np.float64)
    wr5_turning_up[1:] = (wr5[1:] > wr5[:-1]).astype(np.float64)
    wr5_turning_down = np.zeros(n, dtype=np.float64)
    wr5_turning_down[1:] = (wr5[1:] < wr5[:-1]).astype(np.float64)

    feat["lw_wr_aligned_bull"] = (
        (wr28 > -50) & (wr14 < -80) & (wr5_turning_up > 0)
    ).astype(np.float64)
    feat["lw_wr_aligned_bear"] = (
        (wr28 < -50) & (wr14 > -20) & (wr5_turning_down > 0)
    ).astype(np.float64)

    # Divergence: 20-bar lookback
    div_period = 20
    bull_div = np.zeros(n, dtype=np.float64)
    bear_div = np.zeros(n, dtype=np.float64)
    for i in range(div_period, n):
        window_lo = lows[i - div_period:i + 1]
        window_hi = highs[i - div_period:i + 1]
        window_wr = wr14[i - div_period:i + 1]
        # Bull: price new low but %R higher low
        if closes[i] <= np.min(window_lo):
            prev_low_idx = np.argmin(window_lo[:-1])
            if window_wr[-1] > window_wr[prev_low_idx]:
                bull_div[i] = 1.0
        # Bear: price new high but %R lower high
        if closes[i] >= np.max(window_hi):
            prev_high_idx = np.argmax(window_hi[:-1])
            if window_wr[-1] < window_wr[prev_high_idx]:
                bear_div[i] = 1.0
    feat["lw_wr_bull_divergence"] = bull_div
    feat["lw_wr_bear_divergence"] = bear_div

    # ── 4. Smash Day / Key Reversal (4) ──────────────────────────────
    smash_bull = np.zeros(n, dtype=np.float64)
    smash_bear = np.zeros(n, dtype=np.float64)
    smash_bull[1:] = ((lows[1:] < lows[:-1]) & (closes[1:] > closes[:-1])).astype(np.float64)
    smash_bear[1:] = ((highs[1:] > highs[:-1]) & (closes[1:] < closes[:-1])).astype(np.float64)
    feat["lw_smash_bull"] = smash_bull
    feat["lw_smash_bear"] = smash_bear

    mom3_bull = np.zeros(n, dtype=np.float64)
    mom3_bear = np.zeros(n, dtype=np.float64)
    if n > 3:
        mom3_bull[3:] = (lows[3:] > lows[:-3]).astype(np.float64)
        mom3_bear[3:] = (highs[3:] < highs[:-3]).astype(np.float64)
    feat["lw_3bar_mom_bull"] = mom3_bull
    feat["lw_3bar_mom_bear"] = mom3_bear

    # ── 5. Trend Filter — Value Line (2) ─────────────────────────────
    typical = (highs + lows + closes) / 3.0
    value_line = _rolling_mean(typical, 20)
    feat["lw_above_value"] = (closes > value_line).astype(np.float64)

    value_slope = np.zeros(n, dtype=np.float64)
    if n > 5:
        value_slope[5:] = value_line[5:] - value_line[:-5]
    feat["lw_value_slope"] = value_slope

    # ── 6. Oops Pattern (2) ──────────────────────────────────────────
    oops_bull = np.zeros(n, dtype=np.float64)
    oops_bear = np.zeros(n, dtype=np.float64)
    if n > 1:
        oops_bull[1:] = ((opens[1:] < lows[:-1]) & (highs[1:] >= lows[:-1])).astype(np.float64)
        oops_bear[1:] = ((opens[1:] > highs[:-1]) & (lows[1:] <= highs[:-1])).astype(np.float64)
    feat["lw_oops_bull"] = oops_bull
    feat["lw_oops_bear"] = oops_bear

    # ── Finalize: float32, no NaN/Inf ─────────────────────────────────
    for k in feat:
        feat[k] = np.nan_to_num(feat[k], nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    return feat
