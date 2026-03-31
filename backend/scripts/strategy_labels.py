"""
Strategy-informed labels: triple-barrier + ICT/SMC quality scoring.

Enhances standard triple-barrier labeling with ICT confluence scores so that
high-quality setups (strong ICT alignment) receive higher sample weights
during ML training, and optionally get wider TP / tighter SL / longer hold.

Entry points:
    compute_strategy_labels(...)   -> pd.DataFrame with enriched labels
    compute_dynamic_barriers(...)  -> (tp_dist, sl_dist, hold_bars) arrays
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


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
    return np.maximum(atr, 1e-10)


def _try_compute_ict_scores(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
) -> np.ndarray:
    """
    Attempt to compute ICT confluence scores.  Falls back to zeros if the
    ICT feature module is unavailable (e.g. during unit tests).
    """
    try:
        from app.services.ml.features_ict import compute_ict_features
        ict = compute_ict_features(opens, highs, lows, closes, volumes)
        return ict.get("ict_confluence_score", np.zeros(len(closes)))
    except Exception:
        return np.zeros(len(closes))


# ---------------------------------------------------------------------------
# Dynamic barriers
# ---------------------------------------------------------------------------

def compute_dynamic_barriers(
    closes: np.ndarray,
    atr: np.ndarray,
    ict_scores: np.ndarray,
    base_tp_mult: float = 2.0,
    base_sl_mult: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute dynamic TP/SL distances and max-hold bars based on ICT confluence.

    Tiering:
        High confluence  (score >= 6): wider TP (2.5x), tighter SL (0.8x), 36 bars
        Medium confluence (4-5):       standard TP/SL, 24 bars
        Low confluence   (< 4):        tighter TP (1.5x), wider SL (1.2x), 12 bars

    Returns
    -------
    tp_distances : np.ndarray   -- absolute TP distance from entry (ATR-scaled)
    sl_distances : np.ndarray   -- absolute SL distance from entry (ATR-scaled)
    max_hold     : np.ndarray   -- per-bar max hold in bars (int-like float)
    """
    n = len(closes)
    tp_distances = np.empty(n)
    sl_distances = np.empty(n)
    max_hold = np.empty(n)

    for i in range(n):
        s = ict_scores[i]
        a = atr[i]
        if s >= 6.0:
            tp_distances[i] = a * base_tp_mult * 1.25   # 2.5x when base=2.0
            sl_distances[i] = a * base_sl_mult * 0.8
            max_hold[i] = 36.0
        elif s >= 4.0:
            tp_distances[i] = a * base_tp_mult
            sl_distances[i] = a * base_sl_mult
            max_hold[i] = 24.0
        else:
            tp_distances[i] = a * base_tp_mult * 0.75   # 1.5x when base=2.0
            sl_distances[i] = a * base_sl_mult * 1.2
            max_hold[i] = 12.0

    return tp_distances, sl_distances, max_hold


# ---------------------------------------------------------------------------
# Main labeling function
# ---------------------------------------------------------------------------

def compute_strategy_labels(
    df: pd.DataFrame,
    symbol: str = "US30",
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 1.0,
    max_hold_bars: int = 24,
    atr_period: int = 14,
    use_dynamic_barriers: bool = False,
) -> pd.DataFrame:
    """
    Compute strategy-informed labels combining triple-barrier with ICT quality.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: open, high, low, close.
        Optionally: volume (defaults to zeros if absent).
    symbol : str
        Trading symbol (for future per-symbol config lookup).
    tp_atr_mult : float
        Base take-profit multiplier (entry +/- ATR * mult).
    sl_atr_mult : float
        Base stop-loss multiplier.
    max_hold_bars : int
        Maximum bars before timeout exit.
    atr_period : int
        ATR lookback window.
    use_dynamic_barriers : bool
        If True, TP/SL/hold are dynamically adjusted per ICT score tier.

    Returns
    -------
    pd.DataFrame with columns:
        label          : int    (-1 short win, 0 timeout, +1 long win)
        label_quality  : float  ICT confluence score at entry (0-10)
        label_weighted : float  label * quality_weight (for sample weighting)
        tp_price       : float  take-profit price level
        sl_price       : float  stop-loss price level
        exit_bar       : int    bar index where trade exited
        exit_type      : str    "tp", "sl", or "timeout"
        hold_bars      : int    bars the trade was held
        pnl_pct        : float  percentage P&L
    """
    opens = df["open"].values.astype(np.float64)
    highs = df["high"].values.astype(np.float64)
    lows = df["low"].values.astype(np.float64)
    closes = df["close"].values.astype(np.float64)
    volumes = df["volume"].values.astype(np.float64) if "volume" in df.columns else np.zeros(len(df))

    n = len(closes)
    atr = _rolling_atr(highs, lows, closes, period=atr_period)

    # --- ICT scores ---
    ict_scores = _try_compute_ict_scores(opens, highs, lows, closes, volumes)

    # --- Dynamic or fixed barriers ---
    if use_dynamic_barriers:
        tp_dist, sl_dist, hold_arr = compute_dynamic_barriers(
            closes, atr, ict_scores, base_tp_mult=tp_atr_mult, base_sl_mult=sl_atr_mult,
        )
    else:
        tp_dist = atr * tp_atr_mult
        sl_dist = atr * sl_atr_mult
        hold_arr = np.full(n, float(max_hold_bars))

    # --- Pre-allocate output arrays ---
    labels = np.zeros(n, dtype=np.int8)
    tp_prices = np.full(n, np.nan)
    sl_prices = np.full(n, np.nan)
    exit_bars = np.full(n, -1, dtype=np.int64)
    exit_types = np.empty(n, dtype=object)
    exit_types[:] = "timeout"
    hold_bars_out = np.zeros(n, dtype=np.int64)
    pnl_pcts = np.zeros(n, dtype=np.float64)

    # --- Vectorised triple-barrier scan ───────────────────────────────
    # For each unique max_hold value, compute forward rolling max/min
    # and determine TP/SL hit using array operations (no per-bar loop).
    unique_holds = np.unique(hold_arr.astype(int))

    for max_h in unique_holds:
        mask = (hold_arr.astype(int) == max_h)
        if not mask.any() or max_h < 1:
            continue

        # Reverse-rolling max/min gives us future max/min from each bar
        highs_s = pd.Series(highs)
        lows_s = pd.Series(lows)
        closes_s = pd.Series(closes)

        # For each bar i, we want max(highs[i+1:i+max_h+1]) and min(lows[i+1:i+max_h+1])
        # Trick: reverse, rolling, reverse, shift(-1)
        rev_h = highs_s.iloc[::-1].reset_index(drop=True)
        rev_l = lows_s.iloc[::-1].reset_index(drop=True)

        future_max_h = (rev_h.rolling(max_h, min_periods=1).max()
                        .iloc[::-1].reset_index(drop=True).shift(-1).values)
        future_min_l = (rev_l.rolling(max_h, min_periods=1).min()
                        .iloc[::-1].reset_index(drop=True).shift(-1).values)

        # Timeout exit close
        timeout_idx = np.minimum(np.arange(n) + max_h, n - 1)
        timeout_close = closes[timeout_idx]

        # For bars in this hold group
        idx = np.where(mask)[0]
        entry = closes[idx]
        tp_d = tp_dist[idx]
        sl_d = sl_dist[idx]

        # LONG: TP hit if future_max >= entry + tp_d, SL if future_min <= entry - sl_d
        long_tp_hit = future_max_h[idx] >= (entry + tp_d)
        long_sl_hit = future_min_l[idx] <= (entry - sl_d)
        long_pnl_tp = tp_d / entry
        long_pnl_sl = -(sl_d / entry)
        long_pnl_timeout = (timeout_close[idx] - entry) / np.maximum(entry, 1e-10)

        # SHORT: TP hit if future_min <= entry - tp_d, SL if future_max >= entry + sl_d
        short_tp_hit = future_min_l[idx] <= (entry - tp_d)
        short_sl_hit = future_max_h[idx] >= (entry + sl_d)
        short_pnl_tp = tp_d / entry
        short_pnl_sl = -(sl_d / entry)
        short_pnl_timeout = (entry - timeout_close[idx]) / np.maximum(entry, 1e-10)

        # Determine best direction per bar
        # Simplified: TP takes priority. If both TPs hit, pick long (bias toward long for index).
        # If only one TP hit, pick that direction. If neither, use timeout P&L.
        for k, i in enumerate(idx):
            if i + 1 >= n:
                continue

            ltp = long_tp_hit[k]
            lsl = long_sl_hit[k]
            stp = short_tp_hit[k]
            ssl = short_sl_hit[k]

            # Determine long outcome
            if ltp and not lsl:
                l_pnl = long_pnl_tp[k]; l_type = "tp"
            elif lsl and not ltp:
                l_pnl = long_pnl_sl[k]; l_type = "sl"
            elif ltp and lsl:
                # Both hit — need to check which first (use simple heuristic: SL first if closer)
                if sl_d[k] < tp_d[k]:
                    l_pnl = long_pnl_sl[k]; l_type = "sl"
                else:
                    l_pnl = long_pnl_tp[k]; l_type = "tp"
            else:
                l_pnl = long_pnl_timeout[k]; l_type = "timeout"

            # Determine short outcome
            if stp and not ssl:
                s_pnl = short_pnl_tp[k]; s_type = "tp"
            elif ssl and not stp:
                s_pnl = short_pnl_sl[k]; s_type = "sl"
            elif stp and ssl:
                if sl_d[k] < tp_d[k]:
                    s_pnl = short_pnl_sl[k]; s_type = "sl"
                else:
                    s_pnl = short_pnl_tp[k]; s_type = "tp"
            else:
                s_pnl = short_pnl_timeout[k]; s_type = "timeout"

            # Pick direction
            if l_type == "tp" and s_type != "tp":
                label, pnl, etype = 1, l_pnl, l_type
                tp_p, sl_p = entry[k] + tp_d[k], entry[k] - sl_d[k]
            elif s_type == "tp" and l_type != "tp":
                label, pnl, etype = -1, s_pnl, s_type
                tp_p, sl_p = entry[k] - tp_d[k], entry[k] + sl_d[k]
            elif l_type == "tp" and s_type == "tp":
                label, pnl, etype = 1, l_pnl, "tp"  # prefer long for index
                tp_p, sl_p = entry[k] + tp_d[k], entry[k] - sl_d[k]
            elif l_pnl > s_pnl and l_pnl > 0:
                label, pnl, etype = 1, l_pnl, l_type
                tp_p, sl_p = entry[k] + tp_d[k], entry[k] - sl_d[k]
            elif s_pnl > l_pnl and s_pnl > 0:
                label, pnl, etype = -1, s_pnl, s_type
                tp_p, sl_p = entry[k] - tp_d[k], entry[k] + sl_d[k]
            else:
                label, pnl, etype = 0, max(l_pnl, s_pnl), "timeout"
                tp_p, sl_p = entry[k] + tp_d[k], entry[k] - sl_d[k]

            labels[i] = label
            tp_prices[i] = tp_p
            sl_prices[i] = sl_p
            exit_bars[i] = min(i + max_h, n - 1)
            exit_types[i] = etype
            hold_bars_out[i] = max_h
            pnl_pcts[i] = pnl

    # --- Quality weighting ---
    quality = np.clip(ict_scores[:n], 0.0, 10.0)
    # weight: 0.5 at score 0, 1.0 at score 10
    quality_weight = 0.5 + 0.5 * (quality / 10.0)
    label_weighted = labels.astype(np.float64) * quality_weight

    result = pd.DataFrame({
        "label": labels,
        "label_quality": quality,
        "label_weighted": label_weighted,
        "tp_price": tp_prices,
        "sl_price": sl_prices,
        "exit_bar": exit_bars,
        "exit_type": exit_types,
        "hold_bars": hold_bars_out,
        "pnl_pct": pnl_pcts,
    }, index=df.index)

    return result
