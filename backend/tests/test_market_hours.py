"""Tests for app.services.market_hours.

Uses a fixed `now` datetime to make weekend / weekday / maintenance-halt
behavior deterministic across test runs.
"""
from datetime import datetime, timezone, timedelta

import pytest

from app.services.market_hours import (
    is_market_open,
    next_open,
    seconds_until_open,
    ASSET_CLASS,
)


# Fixed reference points
# 2026-04-17 (Fri) 20:00 UTC — NY session still active for indices/forex
FRIDAY_OPEN = datetime(2026, 4, 17, 20, 0, tzinfo=timezone.utc)
# 2026-04-17 (Fri) 22:00 UTC — forex close, index close already at 21:00
FRIDAY_CLOSE = datetime(2026, 4, 17, 22, 0, tzinfo=timezone.utc)
# 2026-04-18 (Sat) 12:00 UTC — weekend middle
SATURDAY = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
# 2026-04-19 (Sun) 12:00 UTC — weekend end (still closed)
SUNDAY_DAY = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
# 2026-04-19 (Sun) 22:30 UTC — after Sunday open for forex/futures
SUNDAY_OPEN = datetime(2026, 4, 19, 22, 30, tzinfo=timezone.utc)
# 2026-04-20 (Mon) 12:00 UTC — normal trading day
MONDAY = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
# 2026-04-22 (Wed) 21:30 UTC — CME maintenance halt window
WED_HALT = datetime(2026, 4, 22, 21, 30, tzinfo=timezone.utc)


class TestCrypto:
    """Crypto = 24/7."""

    @pytest.mark.parametrize("now", [FRIDAY_CLOSE, SATURDAY, SUNDAY_DAY, MONDAY, WED_HALT])
    def test_crypto_always_open(self, now):
        for sym in ["BTCUSD", "ETHUSD"]:
            open_, _ = is_market_open(sym, now)
            assert open_, f"{sym} should be open 24/7 at {now}"


class TestForex:
    """Forex / metals — Sun 22:00 UTC → Fri 22:00 UTC."""

    def test_open_during_week(self):
        for sym in ["XAUUSD", "XAGUSD", "EURUSD"]:
            open_, _ = is_market_open(sym, MONDAY)
            assert open_, f"{sym} should be open Mon 12:00 UTC"

    def test_closed_saturday(self):
        open_, reason = is_market_open("XAUUSD", SATURDAY)
        assert not open_
        assert "weekend" in reason.lower()

    def test_closed_sunday_before_open(self):
        open_, _ = is_market_open("XAUUSD", SUNDAY_DAY)
        assert not open_

    def test_open_sunday_22_30(self):
        open_, _ = is_market_open("XAUUSD", SUNDAY_OPEN)
        assert open_

    def test_closed_friday_after_22_00(self):
        open_, _ = is_market_open("XAUUSD", FRIDAY_CLOSE)
        assert not open_

    def test_open_friday_before_22_00(self):
        open_, _ = is_market_open("XAUUSD", FRIDAY_OPEN)
        assert open_


class TestUSIndices:
    """Futures — same weekend hours + 21:00-22:00 daily maintenance halt."""

    def test_closed_during_halt(self):
        open_, reason = is_market_open("US30", WED_HALT)
        assert not open_
        assert "maintenance" in reason.lower() or "halt" in reason.lower()

    def test_open_normal_trading(self):
        for sym in ["US30", "NAS100", "ES"]:
            open_, _ = is_market_open(sym, MONDAY)
            assert open_

    def test_closed_weekend(self):
        for sym in ["US30", "NAS100", "ES"]:
            open_, _ = is_market_open(sym, SATURDAY)
            assert not open_


class TestNextOpen:
    def test_crypto_returns_none(self):
        assert next_open("BTCUSD", SATURDAY) is None
        assert next_open("ETHUSD", MONDAY) is None

    def test_forex_next_open_from_saturday(self):
        nxt = next_open("XAUUSD", SATURDAY)
        assert nxt is not None
        # Should be Sunday 22:00 UTC (or thereabouts)
        assert nxt.weekday() == 6  # Sunday
        assert nxt.hour >= 22

    def test_seconds_until_open_zero_when_open(self):
        assert seconds_until_open("XAUUSD", MONDAY) == 0
        assert seconds_until_open("BTCUSD", SATURDAY) == 0

    def test_seconds_until_open_positive_when_closed(self):
        secs = seconds_until_open("XAUUSD", SATURDAY)
        assert secs > 0
        assert secs < 3 * 24 * 3600  # less than 3 days


class TestAssetClassDefault:
    def test_unknown_symbol_defaults_to_forex(self):
        # Should apply forex hours (closed on weekends)
        open_, _ = is_market_open("FAKESYMBOL", SATURDAY)
        assert not open_
        open_, _ = is_market_open("FAKESYMBOL", MONDAY)
        assert open_

    def test_all_known_symbols_have_class(self):
        # Sanity: every mapped symbol has a valid asset class
        valid_classes = {"crypto", "forex", "us_index", "asia_index"}
        for sym, cls in ASSET_CLASS.items():
            assert cls in valid_classes, f"{sym} has invalid class {cls}"
