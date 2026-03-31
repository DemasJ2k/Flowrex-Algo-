"""
Calendar / event-driven features for the ML pipeline.

All features are binary or ordinal signals derived purely from the
bar timestamp — no external HTTP calls at inference time.

Key references:
  - Pre-FOMC drift: NY Fed Staff Report SR512
  - BTC halving: block-reward schedule (deterministic)
  - Option expiry (OPEX): third Friday of each month
  - Quad-witching: March, June, September, December OPEX
  - Gold seasonality: empirical monthly bias table
  - Buyback blackout: SEC Rule 10b-18 quiet period ~5 weeks around earnings

Usage:
    from app.services.ml.features_calendar import add_calendar_features
    add_calendar_features(features, times, symbol="BTCUSD")
"""
import numpy as np
from datetime import datetime, timezone, date


# ── FOMC Meeting Dates (UTC) ────────────────────────────────────────────
# Pre-FOMC 24h window (17:00 ET day-before to 17:00 ET meeting day).
# 17:00 ET = 21:00 or 22:00 UTC depending on DST; we use 22:00 UTC.
# Source: federalreserve.gov/monetarypolicy/fomccalendars.htm
# Covers training window 2020-2025.

_FOMC_DATES: list[date] = [
    # 2020
    date(2020, 1, 29), date(2020, 3, 3), date(2020, 3, 15),
    date(2020, 4, 29), date(2020, 6, 10), date(2020, 7, 29),
    date(2020, 9, 16), date(2020, 11, 5), date(2020, 12, 16),
    # 2021
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28),
    date(2021, 6, 16), date(2021, 7, 28), date(2021, 9, 22),
    date(2021, 11, 3), date(2021, 12, 15),
    # 2022
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4),
    date(2022, 6, 15), date(2022, 7, 27), date(2022, 9, 21),
    date(2022, 11, 2), date(2022, 12, 14),
    # 2023
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3),
    date(2023, 6, 14), date(2023, 7, 26), date(2023, 9, 20),
    date(2023, 11, 1), date(2023, 12, 13),
    # 2024
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),
    date(2024, 6, 12), date(2024, 7, 31), date(2024, 9, 18),
    date(2024, 11, 7), date(2024, 12, 18),
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),
    date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
    date(2025, 11, 5), date(2025, 12, 10),
]

_FOMC_SET: set[date] = set(_FOMC_DATES)


def _third_friday(year: int, month: int) -> date:
    """Return the third Friday of the given year/month."""
    # First day of the month
    d = date(year, month, 1)
    # Days until first Friday (weekday 4)
    days_to_friday = (4 - d.weekday()) % 7
    first_friday = d.replace(day=1 + days_to_friday)
    return first_friday.replace(day=first_friday.day + 14)


def _build_opex_set(year_start: int = 2019, year_end: int = 2026) -> set[date]:
    opex = set()
    for y in range(year_start, year_end + 1):
        for m in range(1, 13):
            opex.add(_third_friday(y, m))
    return opex


_OPEX_SET: set[date] = _build_opex_set()

_QUAD_MONTHS = {3, 6, 9, 12}


# ── BTC Halving Dates (UTC) ────────────────────────────────────────────
# Block rewards halve every ~4 years; dates are deterministic post-mining.
_BTC_HALVINGS: list[date] = [
    date(2012, 11, 28),
    date(2016, 7, 9),
    date(2020, 5, 11),
    date(2024, 4, 19),
    date(2028, 3, 15),  # estimated
]

# Cycle duration ≈ 1460 days
_BTC_CYCLE_DAYS = 1460


# ── Gold Seasonality (monthly bias) ────────────────────────────────────
# Empirical average monthly return (1975-2023 LBMA data, sign only).
# Source: World Gold Council annual analysis.
_GOLD_SEASONAL: dict[int, float] = {
    1:  0.8,   # Jan: strong
    2:  0.2,
    3: -0.3,
    4:  0.1,
    5: -0.2,
    6:  0.0,
    7:  0.5,
    8:  1.2,   # Aug: strong
    9:  1.5,   # Sep: strongest
    10: 0.0,
    11:-0.4,
    12: 0.1,
}

# ── US30 Buyback Blackout Proxy ─────────────────────────────────────────
# Companies may not repurchase shares in the 5 weeks before earnings release.
# Buyback support is absent → higher vol, less persistent upside.
# Approximate: weeks 5-8 of each quarter (i.e. mid-month 2 and month 3 of each quarter).
_BUYBACK_BLACKOUT_MONTHS = {2, 3, 5, 6, 8, 9, 11, 12}  # months with typical quiet periods


# ── Crypto OPEX (Deribit BTC options) ─────────────────────────────────
# Last Friday of each month is BTC options expiry on Deribit.
def _last_friday(year: int, month: int) -> date:
    """Return the last Friday of the given year/month."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month.replace(day=1) if next_month.day == 1 else next_month
    # Walk back from first day of next month
    from datetime import timedelta
    d = next_month - timedelta(days=1)
    while d.weekday() != 4:  # 4 = Friday
        d -= timedelta(days=1)
    return d


def _build_crypto_opex_set(year_start: int = 2019, year_end: int = 2026) -> set[date]:
    opex = set()
    for y in range(year_start, year_end + 1):
        for m in range(1, 13):
            try:
                opex.add(_last_friday(y, m))
            except ValueError:
                pass
    return opex


_CRYPTO_OPEX_SET: set[date] = _build_crypto_opex_set()


# ── Futures Roll Dates ─────────────────────────────────────────────────
# CME gold / ES futures roll quarterly (around 8th-15th of roll month).
# We use the 10th of roll month ± 3 days as a ±3 day window.
_FUTURES_ROLL_MONTHS = {3, 6, 9, 12}


def _is_futures_roll_week(d: date) -> bool:
    return d.month in _FUTURES_ROLL_MONTHS and 7 <= d.day <= 17


# ── Sorted date arrays for vectorised lookups ──────────────────────────

def _dates_to_ordinals(date_set: set) -> np.ndarray:
    """Convert a set of dates to a sorted numpy int array (ordinal days)."""
    return np.sort(np.array([d.toordinal() for d in date_set], dtype=np.int32))


def _days_to_next(bar_ordinals: np.ndarray, event_ordinals: np.ndarray, cap: int) -> np.ndarray:
    """
    For each bar ordinal, find the minimum number of days to the next event.
    Uses searchsorted for O(n log m) performance.
    """
    idx = np.searchsorted(event_ordinals, bar_ordinals, side="left")
    idx = np.clip(idx, 0, len(event_ordinals) - 1)
    days = event_ordinals[idx] - bar_ordinals
    # If idx points to past event, try idx+1
    past_mask = days < 0
    idx2 = np.clip(idx + 1, 0, len(event_ordinals) - 1)
    days = np.where(past_mask, event_ordinals[idx2] - bar_ordinals, days)
    return np.clip(days, 0, cap).astype(float)


_FOMC_ORDINALS:        np.ndarray = _dates_to_ordinals(_FOMC_SET)
_OPEX_ORDINALS:        np.ndarray = _dates_to_ordinals(_OPEX_SET)
_CRYPTO_OPEX_ORDINALS: np.ndarray = _dates_to_ordinals(_CRYPTO_OPEX_SET)
_HALVING_ORDINALS:     np.ndarray = np.sort(
    np.array([d.toordinal() for d in _BTC_HALVINGS], dtype=np.int32)
)


# ── Main feature computation ───────────────────────────────────────────


def add_calendar_features(
    features: dict,
    times: np.ndarray,
    symbol: str = "BTCUSD",
) -> dict:
    """
    Compute calendar/event features and add them to `features` dict in-place.
    Fully vectorised — no per-bar Python loops.

    Symbol-specific features:
      BTCUSD:  halving_cycle_phase, crypto_opex_flag, days_to_crypto_opex
      XAUUSD:  gold_seasonal_bias, days_to_futures_roll, fomc_drift_flag
      US30:    fomc_drift_flag, opex_week_flag, quad_witching_flag,
               days_to_opex, buyback_blackout_flag
      All:     fomc_drift_flag (macro regime relevant for all)
    """
    import pandas as pd

    # ── Vectorised timestamp → date attributes ─────────────────────────
    dti      = pd.to_datetime(times.astype("int64"), unit="s", utc=True)
    months   = dti.month.values.astype(int)
    days_arr = dti.day.values.astype(int)
    years    = dti.year.values.astype(int)
    n        = len(times)

    # Gregorian ordinal = days since 0001-01-01 (same as Python date.toordinal())
    # Vectorised via: ordinal ≈ Unix-days + offset from epoch to ordinal epoch
    # Unix day 0 = 1970-01-01, ordinal of 1970-01-01 = 719163
    _UNIX_EPOCH_ORDINAL = 719163
    ordinals = (times.astype("int64") // 86400 + _UNIX_EPOCH_ORDINAL).astype(np.int32)

    # ── Pre-FOMC 24h drift flag (all symbols) ─────────────────────────
    # A bar is in the pre-FOMC window if today OR tomorrow is an FOMC day.
    fomc_flag      = np.isin(ordinals, _FOMC_ORDINALS).astype(float)
    fomc_tomorrow  = np.isin(ordinals + 1, _FOMC_ORDINALS).astype(float)
    features["fomc_drift_flag"] = np.clip(fomc_flag + fomc_tomorrow, 0, 1)

    # ── OPEX features ─────────────────────────────────────────────────
    days_opex  = _days_to_next(ordinals, _OPEX_ORDINALS, cap=30)
    opex_week  = (days_opex <= 5).astype(float)
    quad_witch = (opex_week * np.isin(months, list(_QUAD_MONTHS))).astype(float)

    features["opex_week_flag"]     = opex_week
    features["days_to_opex_norm"]  = days_opex / 30.0
    features["quad_witching_flag"] = quad_witch

    # ── Symbol-specific ───────────────────────────────────────────────

    if symbol == "BTCUSD":
        # BTC halving cycle phase (0-1 over 4-year cycle)
        # Find most recent past halving via searchsorted
        idx_past = np.searchsorted(_HALVING_ORDINALS, ordinals, side="right") - 1
        idx_past = np.clip(idx_past, 0, len(_HALVING_ORDINALS) - 1)
        days_since = ordinals - _HALVING_ORDINALS[idx_past]
        days_since = np.clip(days_since, 0, None)
        features["halving_cycle_phase"]       = (days_since % _BTC_CYCLE_DAYS) / _BTC_CYCLE_DAYS

        # Days to next halving
        days_to_next_halving = _days_to_next(ordinals, _HALVING_ORDINALS, cap=365)
        features["halving_days_to_next_norm"] = days_to_next_halving / 365.0

        # Crypto OPEX
        days_crypto_opex = _days_to_next(ordinals, _CRYPTO_OPEX_ORDINALS, cap=30)
        features["crypto_opex_flag"]          = (days_crypto_opex <= 3).astype(float)
        features["days_to_crypto_opex_norm"]  = days_crypto_opex / 30.0

    elif symbol == "XAUUSD":
        # Gold seasonality (monthly bias)
        max_val = max(abs(v) for v in _GOLD_SEASONAL.values())
        seasonal_arr = np.array([_GOLD_SEASONAL.get(m, 0.0) for m in months])
        features["gold_seasonal_bias"] = seasonal_arr / max_val

        # Futures roll window: month in {3,6,9,12} and day 7-17
        in_roll_month = np.isin(months, list(_FUTURES_ROLL_MONTHS))
        in_roll_days  = (days_arr >= 7) & (days_arr <= 17)
        features["futures_roll_flag"] = (in_roll_month & in_roll_days).astype(float)

        # Days to nearest futures roll (quarterly — use day 10 of roll months)
        # Build a sorted ordinal array for roll-day dates across years
        roll_dates = []
        for y in range(2018, 2027):
            for m in _FUTURES_ROLL_MONTHS:
                roll_dates.append(date(y, m, 10).toordinal())
        roll_ordinals = np.sort(np.array(roll_dates, dtype=np.int32))
        days_to_roll  = _days_to_next(ordinals, roll_ordinals, cap=45)
        features["days_to_roll_norm"] = days_to_roll / 45.0

    elif symbol == "US30":
        # Buyback blackout proxy (vectorised)
        in_blackout_month = np.isin(months, list(_BUYBACK_BLACKOUT_MONTHS))
        in_blackout_days  = days_arr >= 15
        features["buyback_blackout_flag"] = (in_blackout_month & in_blackout_days).astype(float)

    return features
