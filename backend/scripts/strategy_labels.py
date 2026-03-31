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
    exit_types[:] = ""
    hold_bars_out = np.zeros(n, dtype=np.int64)
    pnl_pcts = np.zeros(n, dtype=np.float64)

    # --- Triple-barrier scan (LONG direction) ---
    # For each bar, we check both LONG and SHORT and pick the one that
    # triggers first (or the better outcome on timeout).
    for i in range(n):
        max_h = int(hold_arr[i])
        if i + 1 >= n:
            # Not enough forward data
            exit_types[i] = "timeout"
            continue

        entry = closes[i]
        tp_d = tp_dist[i]
        sl_d = sl_dist[i]

        # -- LONG --
        long_tp = entry + tp_d
        long_sl = entry - sl_d
        long_exit_type = "timeout"
        long_exit_bar = min(i + max_h, n - 1)
        long_pnl = 0.0

        for j in range(i + 1, min(i + max_h + 1, n)):
            if lows[j] <= long_sl:
                long_exit_type = "sl"
                long_exit_bar = j
                long_pnl = -(sl_d / entry)
                break
            if highs[j] >= long_tp:
                long_exit_type = "tp"
                long_exit_bar = j
                long_pnl = tp_d / entry
                break
        else:
            # timeout
            c_exit = closes[min(i + max_h, n - 1)]
            long_pnl = (c_exit - entry) / entry

        # -- SHORT --
        short_tp = entry - tp_d
        short_sl = entry + sl_d
        short_exit_type = "timeout"
        short_exit_bar = min(i + max_h, n - 1)
        short_pnl = 0.0

        for j in range(i + 1, min(i + max_h + 1, n)):
            if highs[j] >= short_sl:
                short_exit_type = "sl"
                short_exit_bar = j
                short_pnl = -(sl_d / entry)
                break
            if lows[j] <= short_tp:
                short_exit_type = "tp"
                short_exit_bar = j
                short_pnl = tp_d / entry
                break
        else:
            c_exit = closes[min(i + max_h, n - 1)]
            short_pnl = (entry - c_exit) / entry

        # --- Determine label ---
        # Pick the direction whose TP was hit first; if neither, use timeout P&L
        if long_exit_type == "tp" and short_exit_type == "tp":
            # Both TPs hit — pick whichever hit first
            if long_exit_bar <= short_exit_bar:
                label = 1
                chosen_exit_type = "tp"
                chosen_exit_bar = long_exit_bar
                chosen_pnl = long_pnl
                chosen_tp = long_tp
                chosen_sl = long_sl
            else:
                label = -1
                chosen_exit_type = "tp"
                chosen_exit_bar = short_exit_bar
                chosen_pnl = short_pnl
                chosen_tp = short_tp
                chosen_sl = short_sl
        elif long_exit_type == "tp":
            label = 1
            chosen_exit_type = "tp"
            chosen_exit_bar = long_exit_bar
            chosen_pnl = long_pnl
            chosen_tp = long_tp
            chosen_sl = long_sl
        elif short_exit_type == "tp":
            label = -1
            chosen_exit_type = "tp"
            chosen_exit_bar = short_exit_bar
            chosen_pnl = short_pnl
            chosen_tp = short_tp
            chosen_sl = short_sl
        else:
            # Neither TP hit — both SL or timeout
            # Use sign of better P&L (or 0 if negligible)
            if long_pnl > short_pnl and long_pnl > 0:
                label = 1
                chosen_exit_type = long_exit_type
                chosen_exit_bar = long_exit_bar
                chosen_pnl = long_pnl
                chosen_tp = long_tp
                chosen_sl = long_sl
            elif short_pnl > long_pnl and short_pnl > 0:
                label = -1
                chosen_exit_type = short_exit_type
                chosen_exit_bar = short_exit_bar
                chosen_pnl = short_pnl
                chosen_tp = short_tp
                chosen_sl = short_sl
            else:
                # Both negative or zero — label 0 (no good trade here)
                label = 0
                chosen_exit_type = "timeout"
                chosen_exit_bar = min(i + max_h, n - 1)
                chosen_pnl = max(long_pnl, short_pnl)
                chosen_tp = long_tp
                chosen_sl = long_sl

        labels[i] = label
        tp_prices[i] = chosen_tp
        sl_prices[i] = chosen_sl
        exit_bars[i] = chosen_exit_bar
        exit_types[i] = chosen_exit_type
        hold_bars_out[i] = chosen_exit_bar - i
        pnl_pcts[i] = chosen_pnl

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
