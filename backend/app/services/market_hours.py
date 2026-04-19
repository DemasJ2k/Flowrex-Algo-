"""
Market hours awareness for each asset class.

Returns (is_open, reason, next_open_utc) for any symbol so the engine can
skip polling / trading when markets are closed instead of hammering the
broker with orders that will be rejected.

Asset class hours (UTC):
  - crypto (BTCUSD, ETHUSD): 24/7
  - forex / metals (XAUUSD, XAGUSD, EURUSD, etc.): 24/5
      open Sunday 22:00 UTC → close Friday 22:00 UTC
  - US indices (US30, NAS100, ES, SPX): follows CME / NYSE futures
      open Sunday 22:00 UTC → close Friday 21:00 UTC with daily 1hr halt
  - AUS200: Sunday 22:50 UTC → Friday 21:00 UTC (Asian session-heavy)

These are approximate — exchanges have holiday calendars and daily halts.
The agent also falls back on broker-side MARKET_CLOSED errors for correctness.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional


# asset class mapping
ASSET_CLASS = {
    "BTCUSD": "crypto",
    "ETHUSD": "crypto",

    "XAUUSD": "forex",
    "XAGUSD": "forex",
    "EURUSD": "forex",
    "GBPUSD": "forex",
    "USDJPY": "forex",

    "US30": "us_index",
    "NAS100": "us_index",
    "ES": "us_index",
    "SPX": "us_index",

    "AUS200": "asia_index",
}


def _asset_class(symbol: str) -> str:
    return ASSET_CLASS.get(symbol.upper(), "forex")  # default forex hours


def is_market_open(symbol: str, now: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Return (is_open, reason).
    reason is either "open" or a short description of why closed.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cls = _asset_class(symbol)

    if cls == "crypto":
        return True, "open"

    weekday = now.weekday()  # Mon=0, Sun=6
    hour = now.hour + now.minute / 60.0

    if cls == "forex":
        # Open: Sunday 22:00 UTC → Friday 22:00 UTC
        if weekday == 5:  # Saturday
            return False, "Weekend (forex closed until Sunday 22:00 UTC)"
        if weekday == 6 and hour < 22:  # Sunday before open
            return False, f"Weekend (forex opens Sunday 22:00 UTC, currently {hour:.1f})"
        if weekday == 4 and hour >= 22:  # Friday after close
            return False, "Weekend started (forex closed)"
        return True, "open"

    if cls in ("us_index", "asia_index"):
        # Approx same as forex with slight variation
        if weekday == 5:
            return False, "Weekend (futures closed until Sunday 22:00 UTC)"
        if weekday == 6 and hour < 22:
            return False, f"Weekend (futures open Sunday 22:00 UTC)"
        if weekday == 4 and hour >= 21:
            return False, "Weekend started (futures closed)"
        # Daily maintenance halt: 21:00-22:00 UTC (for CME products)
        if cls == "us_index" and 21 <= hour < 22:
            return False, "Daily CME maintenance halt (21:00-22:00 UTC)"
        return True, "open"

    return True, "open"


def next_open(symbol: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """
    Return the UTC datetime of the next market open (rough estimate).
    Returns None for always-open (crypto).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cls = _asset_class(symbol)
    if cls == "crypto":
        return None

    # Roll forward hour-by-hour until market is open. Max 7 days of hours (168)
    # covers any realistic gap including long weekends.
    candidate = now
    for _ in range(168):
        open_, _ = is_market_open(symbol, candidate)
        if open_:
            return candidate
        candidate += timedelta(hours=1)
    return candidate


def seconds_until_open(symbol: str, now: Optional[datetime] = None) -> int:
    """Return seconds until the next market open, or 0 if already open."""
    if now is None:
        now = datetime.now(timezone.utc)
    open_now, _ = is_market_open(symbol, now)
    if open_now:
        return 0
    nxt = next_open(symbol, now)
    if not nxt:
        return 0
    delta = (nxt - now).total_seconds()
    return max(0, int(delta))


_ASSET_CLASS_SAMPLE = {
    "crypto":     "BTCUSD",
    "forex":      "EURUSD",
    "us_index":   "US30",
    "asia_index": "AUS200",
}


def get_asset_class_status(now: Optional[datetime] = None) -> dict[str, dict]:
    """
    Return a short human-readable open/closed summary keyed by asset class.
    Consumed by the AI supervisor prompt so the model stops hallucinating
    "system failure" when forex is closed but crypto is open.

    Shape: {"crypto": {"open": True, "reason": "open"}, "forex": {...}, ...}
    """
    if now is None:
        now = datetime.now(timezone.utc)
    status = {}
    for cls, sample in _ASSET_CLASS_SAMPLE.items():
        is_open, reason = is_market_open(sample, now)
        status[cls] = {"open": is_open, "reason": reason}
    return status


def any_market_open_for_symbols(symbols: list[str],
                                now: Optional[datetime] = None) -> bool:
    """True if at least one of the given symbols' markets is currently open."""
    if not symbols:
        return False
    for sym in symbols:
        open_, _ = is_market_open(sym, now)
        if open_:
            return True
    return False
