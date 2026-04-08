"""
Per-symbol instrument specifications and position sizing.
"""
from dataclasses import dataclass
import math


@dataclass
class InstrumentSpec:
    symbol: str
    pip_size: float
    pip_value: float  # USD per pip per standard lot
    min_lot: float
    lot_step: float
    contract_size: float = 1.0


# Default specs — pip_value is per 1 Oanda unit for Oanda broker
# Oanda: 1 unit of US30 = $1/point, 1 unit of NAS100 = $1/point,
# 1 unit of XAUUSD = price of 1 oz, 1 unit BTCUSD = 1 BTC
INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    "XAUUSD": InstrumentSpec("XAUUSD", pip_size=0.01, pip_value=1.0, min_lot=1, lot_step=1),
    "XAGUSD": InstrumentSpec("XAGUSD", pip_size=0.001, pip_value=5.0, min_lot=1, lot_step=1),
    "BTCUSD": InstrumentSpec("BTCUSD", pip_size=0.01, pip_value=1.0, min_lot=1, lot_step=1),
    "ETHUSD": InstrumentSpec("ETHUSD", pip_size=0.01, pip_value=1.0, min_lot=1, lot_step=1),
    "US30":   InstrumentSpec("US30", pip_size=1.0, pip_value=1.0, min_lot=1, lot_step=1),
    "NAS100": InstrumentSpec("NAS100", pip_size=1.0, pip_value=1.0, min_lot=1, lot_step=1),
    "SPX500": InstrumentSpec("SPX500", pip_size=0.1, pip_value=1.0, min_lot=1, lot_step=1),
    "ES":     InstrumentSpec("ES", pip_size=0.1, pip_value=1.0, min_lot=1, lot_step=1),
    "EURUSD": InstrumentSpec("EURUSD", pip_size=0.0001, pip_value=1.0, min_lot=1, lot_step=1),
    "GBPUSD": InstrumentSpec("GBPUSD", pip_size=0.0001, pip_value=1.0, min_lot=1, lot_step=1),
    "USDJPY": InstrumentSpec("USDJPY", pip_size=0.01, pip_value=1.0, min_lot=1, lot_step=1),
}

# Oanda requires specific decimal precision per instrument.
# The pip_size-based formula breaks for pip_size=1.0 (gives 1, should be 0)
# and pip_size=0.25 (gives 2, should be 1). Hardcode the correct values.
OANDA_PRICE_DECIMALS: dict[str, int] = {
    "US30": 0,
    "SPX500": 1,
    "ES": 1,
    "NAS100": 1,
    "XAUUSD": 2,
    "XAGUSD": 3,
    "BTCUSD": 1,
    "ETHUSD": 2,
    "EURUSD": 5,
    "GBPUSD": 5,
    "USDJPY": 3,
}


def get_oanda_price_decimals(symbol: str) -> int:
    """Get the number of decimal places Oanda accepts for a symbol's price."""
    if symbol in OANDA_PRICE_DECIMALS:
        return OANDA_PRICE_DECIMALS[symbol]
    # Fallback: derive from pip_size (works for most forex pairs)
    spec = get_spec(symbol)
    if spec.pip_size >= 1.0:
        return 0
    return max(0, len(str(spec.pip_size).rstrip('0').split('.')[-1]))

# Oanda uses units, not standard lots. 1 standard lot = contract_size units.
OANDA_CONTRACT_SIZES = {
    "XAUUSD": 1,      # 1 unit = 1 oz gold
    "BTCUSD": 1,      # 1 unit = 1 BTC
    "US30": 1,        # 1 unit = $1 per point
    "EURUSD": 1,      # 1 unit = 1 EUR
    "GBPUSD": 1,      # 1 unit
    "USDJPY": 1,
}


def get_spec(symbol: str) -> InstrumentSpec:
    """Get instrument spec, with fallback for unknown symbols."""
    return INSTRUMENT_SPECS.get(symbol, InstrumentSpec(
        symbol=symbol, pip_size=0.0001, pip_value=10.0, min_lot=0.01, lot_step=0.01
    ))


def calc_lot_size(
    symbol: str,
    risk_amount: float,
    sl_distance: float,
    broker_name: str = "oanda",
) -> float:
    """
    Calculate position size based on risk amount and SL distance.
    Formula: lot_size = risk_amount / (sl_distance * pip_value_per_lot)
    For Oanda: returns units (not standard lots).
    """
    spec = get_spec(symbol)

    if sl_distance <= 0:
        return spec.min_lot

    # Convert SL distance to pips
    sl_pips = sl_distance / spec.pip_size

    if sl_pips <= 0:
        return spec.min_lot

    # pip_value is per standard lot
    risk_per_pip = risk_amount / sl_pips

    if broker_name == "oanda":
        # Oanda uses units directly
        # For XAUUSD: 1 unit = exposure to 1 oz, pip_value per unit = pip_size
        # lot_size here represents Oanda units
        if spec.pip_value > 0:
            lots = risk_per_pip / spec.pip_value
        else:
            lots = spec.min_lot
    else:
        # Standard lot sizing
        if spec.pip_value > 0:
            lots = risk_per_pip / spec.pip_value
        else:
            lots = spec.min_lot

    # Round to lot step
    if spec.lot_step > 0:
        lots = math.floor(lots / spec.lot_step) * spec.lot_step

    # Clamp to minimum
    lots = max(lots, spec.min_lot)

    # Round to reasonable precision
    lots = round(lots, 8)

    return lots


def calc_sl_tp(
    entry_price: float,
    direction: int,
    atr_value: float,
    sl_multiplier: float = 1.5,
    tp_multiplier: float = 2.5,
    symbol: str = "",
    broker_name: str = "oanda",
) -> tuple[float, float]:
    """
    Calculate SL and TP based on ATR.
    direction: 1=buy, -1=sell
    Returns (stop_loss, take_profit)
    """
    sl_distance = atr_value * sl_multiplier
    tp_distance = atr_value * tp_multiplier

    if direction == 1:  # buy
        sl = entry_price - sl_distance
        tp = entry_price + tp_distance
    else:  # sell
        sl = entry_price + sl_distance
        tp = entry_price - tp_distance

    digits = get_oanda_price_decimals(symbol) if (broker_name == "oanda" and symbol) else 5
    return round(sl, digits), round(tp, digits)


def get_session_multiplier(hour_utc: int, symbol: str) -> float:
    """
    Session multiplier for risk scaling.
    0.5x during Asian session for non-crypto (Gold, Indices).
    """
    is_asian = 0 <= hour_utc < 8
    is_crypto = symbol in ("BTCUSD", "ETHUSD")

    if is_asian and not is_crypto:
        return 0.5
    return 1.0
