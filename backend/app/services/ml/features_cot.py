"""
COT (Commitment of Traders) feature integration for the ML pipeline.

Loads pre-computed weekly COT features and forward-fills them to M5 bar
timestamps, respecting the CFTC release schedule to avoid lookahead bias.

COT data represents Tuesday positions but is released on Friday afternoon.
To prevent lookahead bias, we only make each week's COT data available
starting from the following Friday at 20:30 UTC (typical CFTC release time).

Features added (8 total):
    cot_comm_net        — Commercial net position
    cot_spec_net        — Large speculator net position
    cot_comm_index_26w  — Williams COT Index (26-week lookback)
    cot_comm_index_52w  — Williams COT Index (52-week lookback)
    cot_comm_pct_oi     — Commercial net as % of open interest
    cot_comm_change     — Week-over-week change in commercial net
    cot_extreme_bull    — Binary: comm_index_52w > 90
    cot_extreme_bear    — Binary: comm_index_52w < 10

Usage:
    from app.services.ml.features_cot import add_cot_features
    add_cot_features(features, times, symbol="US30")
"""
from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Add scripts directory to path so we can import fetch_cot_data
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts")

COT_FEATURE_NAMES = [
    "cot_comm_net",
    "cot_spec_net",
    "cot_comm_index_26w",
    "cot_comm_index_52w",
    "cot_comm_pct_oi",
    "cot_comm_change",
    "cot_extreme_bull",
    "cot_extreme_bear",
]

# CFTC releases COT data on Friday after market close (~20:30 UTC).
# The data represents positions as of Tuesday.
# To avoid lookahead bias, we shift each COT observation's availability
# from Tuesday (report date) to Friday 20:30 UTC of the same week.
_RELEASE_DELAY_DAYS = 3  # Tuesday -> Friday
_RELEASE_HOUR_UTC = 21   # Round up to 21:00 for safety margin


def _load_cot_features(symbol: str) -> pd.DataFrame | None:
    """Load pre-computed COT features from CSV."""
    # Import from scripts
    try:
        sys.path.insert(0, _SCRIPTS_DIR)
        from fetch_cot_data import load_cot_features
        return load_cot_features(symbol)
    except ImportError:
        # Fallback: try loading directly
        data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "cot")
        path = os.path.join(data_dir, f"cot_features_{symbol}.csv")
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")
            return df.sort_index()
        except Exception:
            return None
    finally:
        if _SCRIPTS_DIR in sys.path:
            sys.path.remove(_SCRIPTS_DIR)


def _shift_to_release_time(dates: pd.DatetimeIndex) -> np.ndarray:
    """
    Shift COT report dates (Tuesdays) to their CFTC release time (Friday 21:00 UTC).

    Returns array of unix timestamps (int64 seconds) representing when each
    COT observation becomes available to the market.
    """
    # Each COT date is a Tuesday. The data is released on Friday of the same week.
    # Friday = Tuesday + 3 days, at 21:00 UTC.
    release_times = []
    for dt in dates:
        # Calculate days until Friday (weekday 4)
        current_weekday = dt.weekday()
        # Tuesday = 1, we want Friday = 4, so +3 days
        # But handle any weekday the date might actually be
        days_to_friday = (4 - current_weekday) % 7
        if days_to_friday == 0 and current_weekday != 4:
            days_to_friday = 7
        release_dt = dt + pd.Timedelta(days=days_to_friday)
        release_dt = release_dt.replace(hour=_RELEASE_HOUR_UTC, minute=0, second=0)
        release_times.append(int(release_dt.timestamp()))
    return np.array(release_times, dtype=np.int64)


def _align_cot_to_bars(
    cot_df: pd.DataFrame,
    bar_timestamps: np.ndarray,
) -> dict[str, np.ndarray]:
    """
    Forward-fill weekly COT data to M5 bar timestamps.

    Uses the CFTC release schedule (Friday 21:00 UTC) to prevent lookahead
    bias. Each bar only sees COT data that was publicly available at or
    before that bar's timestamp.
    """
    n_bars = len(bar_timestamps)
    result = {name: np.zeros(n_bars) for name in COT_FEATURE_NAMES}

    if cot_df is None or len(cot_df) == 0:
        return result

    # Shift COT dates to release timestamps
    release_ts = _shift_to_release_time(cot_df.index)

    # For each bar, find the most recent COT observation that was released
    # before or at the bar's timestamp
    bar_ts = bar_timestamps.astype(np.int64)

    # If timestamps look like nanoseconds, convert to seconds
    if len(bar_ts) > 0 and bar_ts[0] > 1e15:
        bar_ts = bar_ts // 10**9

    idx = np.searchsorted(release_ts, bar_ts, side="right") - 1
    valid = idx >= 0

    for col in COT_FEATURE_NAMES:
        if col not in cot_df.columns:
            continue
        vals = cot_df[col].values.astype(float)
        idx_clipped = np.clip(idx, 0, len(vals) - 1)
        col_result = np.zeros(n_bars)
        col_result[valid] = vals[idx_clipped[valid]]
        # Replace any NaN with 0
        col_result = np.where(np.isnan(col_result), 0.0, col_result)
        result[col] = col_result

    return result


def add_cot_features(
    features: dict,
    times: np.ndarray,
    symbol: str,
) -> None:
    """
    Add COT features to the ML feature dictionary.

    Non-fatal: if COT data is not available for the symbol, this function
    returns silently without adding any features.

    Args:
        features: dict of feature_name -> np.ndarray to append to (modified in-place)
        times: array of M5 bar timestamps (unix seconds or nanoseconds)
        symbol: trading symbol ("US30", "XAUUSD")
    """
    try:
        cot_df = _load_cot_features(symbol)
        if cot_df is None:
            return

        aligned = _align_cot_to_bars(cot_df, times)
        features.update(aligned)

    except Exception as e:
        logger.warning(f"COT feature integration failed for {symbol}: {e}")
        # Non-fatal — do not add features, do not raise
