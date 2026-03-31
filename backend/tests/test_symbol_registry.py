"""Tests for the centralized symbol registry."""
from app.services.broker.symbol_registry import SymbolRegistry


def _fresh_registry():
    """Create a fresh registry (not the singleton) for isolated tests."""
    return SymbolRegistry()


# ── Default mappings ───────────────────────────────────────────────────


def test_default_oanda_mappings():
    reg = _fresh_registry()
    assert reg.to_broker("XAUUSD", "oanda") == "XAU_USD"
    assert reg.to_broker("BTCUSD", "oanda") == "BTC_USD"
    assert reg.to_broker("US30", "oanda") == "US30_USD"
    assert reg.to_broker("EURUSD", "oanda") == "EUR_USD"


def test_default_ctrader_mappings():
    reg = _fresh_registry()
    assert reg.to_broker("XAUUSD", "ctrader") == "XAUUSD"
    assert reg.to_broker("US30", "ctrader") == "US30"


def test_default_mt5_mappings():
    reg = _fresh_registry()
    assert reg.to_broker("XAUUSD", "mt5") == "XAUUSD"
    assert reg.to_broker("EURUSD", "mt5") == "EURUSD"


# ── Reverse lookups ───────────────────────────────────────────────────


def test_reverse_oanda():
    reg = _fresh_registry()
    assert reg.to_canonical("XAU_USD", "oanda") == "XAUUSD"
    assert reg.to_canonical("BTC_USD", "oanda") == "BTCUSD"
    assert reg.to_canonical("US30_USD", "oanda") == "US30"


def test_reverse_ctrader():
    reg = _fresh_registry()
    assert reg.to_canonical("XAUUSD", "ctrader") == "XAUUSD"
    assert reg.to_canonical("US30", "ctrader") == "US30"


# ── Roundtrip ──────────────────────────────────────────────────────────


def test_roundtrip_all_brokers():
    reg = _fresh_registry()
    for sym in ["XAUUSD", "BTCUSD", "US30", "EURUSD", "GBPJPY"]:
        for broker in ["oanda", "ctrader", "mt5"]:
            broker_sym = reg.to_broker(sym, broker)
            canonical = reg.to_canonical(broker_sym, broker)
            assert canonical == sym, f"Roundtrip failed: {sym} -> {broker_sym} -> {canonical} ({broker})"


# ── Fallback for unknown symbols ──────────────────────────────────────


def test_unknown_symbol_returns_as_is():
    reg = _fresh_registry()
    assert reg.to_broker("SOMETHINGWEIRD", "oanda") == "SOMETHINGWEIRD"


def test_unknown_broker_symbol_cleaned():
    reg = _fresh_registry()
    # Unknown symbol gets underscores stripped
    result = reg.to_canonical("UNKNOWN_PAIR", "oanda")
    assert result == "UNKNOWNPAIR"


# ── Auto-discovery ─────────────────────────────────────────────────────


def test_auto_discover_gold_variant():
    """GOLD should map to XAUUSD."""
    reg = _fresh_registry()
    reg.auto_discover("some_broker", ["GOLD", "SILVER", "DJ30"])
    assert reg.to_canonical("GOLD", "some_broker") == "XAUUSD"
    assert reg.to_canonical("SILVER", "some_broker") == "XAGUSD"
    assert reg.to_canonical("DJ30", "some_broker") == "US30"


def test_auto_discover_suffixed_symbols():
    """XAUUSDm (micro account suffix) should match XAUUSD."""
    reg = _fresh_registry()
    reg.auto_discover("mt5_micro", ["XAUUSDm", "EURUSDm", "BTCUSDm"])
    assert reg.to_canonical("XAUUSDm", "mt5_micro") == "XAUUSD"
    assert reg.to_canonical("EURUSDm", "mt5_micro") == "EURUSD"
    assert reg.to_canonical("BTCUSDm", "mt5_micro") == "BTCUSD"


def test_auto_discover_cash_symbols():
    """US30.cash / NAS100.cash variants."""
    reg = _fresh_registry()
    reg.auto_discover("some_broker", ["US30.cash", "NAS100.cash"])
    assert reg.to_canonical("US30.cash", "some_broker") == "US30"
    assert reg.to_canonical("NAS100.cash", "some_broker") == "NAS100"


def test_auto_discover_does_not_overwrite_existing():
    """If a broker already has a mapping, auto-discover should not replace it."""
    reg = _fresh_registry()
    # oanda already has XAUUSD -> XAU_USD
    reg.auto_discover("oanda", ["GOLD"])
    # Should still use the existing mapping
    assert reg.to_broker("XAUUSD", "oanda") == "XAU_USD"


def test_auto_discover_nasdaq_variants():
    reg = _fresh_registry()
    reg.auto_discover("broker_x", ["USTEC", "NASDAQ"])
    assert reg.to_canonical("USTEC", "broker_x") == "NAS100"


# ── get_broker_symbols ─────────────────────────────────────────────────


def test_get_broker_symbols():
    reg = _fresh_registry()
    oanda_syms = reg.get_broker_symbols("oanda")
    assert "XAUUSD" in oanda_syms
    assert oanda_syms["XAUUSD"] == "XAU_USD"
    assert "US30" in oanda_syms
