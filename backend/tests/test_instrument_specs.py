"""Unit tests for instrument specs and lot sizing."""
from app.services.agent.instrument_specs import (
    calc_lot_size, calc_sl_tp, get_session_multiplier, get_spec,
)


def test_get_spec_known():
    spec = get_spec("XAUUSD")
    assert spec.symbol == "XAUUSD"
    assert spec.pip_size == 0.01


def test_get_spec_unknown_fallback():
    spec = get_spec("UNKNOWN")
    assert spec.min_lot == 0.01


def test_calc_lot_size_xauusd():
    """Risk $50 with 10-pip SL on gold."""
    size = calc_lot_size("XAUUSD", risk_amount=50.0, sl_distance=10.0, broker_name="oanda")
    assert size > 0
    assert size >= 0.01  # min lot


def test_calc_lot_size_btcusd():
    size = calc_lot_size("BTCUSD", risk_amount=100.0, sl_distance=500.0, broker_name="oanda")
    assert size > 0


def test_calc_lot_size_zero_sl():
    """Zero SL distance should return min lot (1 unit for Oanda CFDs)."""
    size = calc_lot_size("XAUUSD", risk_amount=50.0, sl_distance=0.0)
    assert size == 1  # Oanda uses integer units; 1 is the minimum safe fallback


def test_calc_sl_tp_buy():
    sl, tp = calc_sl_tp(entry_price=2000.0, direction=1, atr_value=10.0)
    assert sl < 2000.0  # SL below entry for buy
    assert tp > 2000.0  # TP above entry for buy
    assert sl == 2000.0 - 15.0  # 1.5 * ATR
    assert tp == 2000.0 + 25.0  # 2.5 * ATR


def test_calc_sl_tp_sell():
    sl, tp = calc_sl_tp(entry_price=2000.0, direction=-1, atr_value=10.0)
    assert sl > 2000.0  # SL above entry for sell
    assert tp < 2000.0  # TP below entry for sell


def test_session_multiplier_asian_gold():
    assert get_session_multiplier(3, "XAUUSD") == 0.5  # Asian session, gold


def test_session_multiplier_london_gold():
    assert get_session_multiplier(10, "XAUUSD") == 1.0  # London session


def test_session_multiplier_asian_crypto():
    assert get_session_multiplier(3, "BTCUSD") == 1.0  # Crypto: no reduction
