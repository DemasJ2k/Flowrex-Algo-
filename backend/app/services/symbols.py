"""
Unified symbol configuration — merges ML config + instrument specs.
Single source of truth for all per-symbol parameters.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class SymbolConfig:
    # Identity
    symbol: str
    asset_class: str  # "index", "commodity", "crypto", "forex"
    description: str = ""

    # ML/Training params
    label_atr_mult: float = 1.2
    label_forward_bars: int = 10
    prime_hours_utc: tuple = (0, 24)

    # Execution costs
    spread_pips: float = 1.0
    cost_bps: float = 1.0
    slippage_bps: float = 0.5
    tp_atr_mult: float = 1.5
    sl_atr_mult: float = 1.0
    bars_per_day: int = 288
    hold_bars: int = 10
    trend_filter: bool = False

    # Instrument specs (for Oanda)
    pip_size: float = 1.0
    pip_value: float = 1.0
    min_lot: int = 1
    oanda_price_decimals: int = 1


# All symbols in one place
SYMBOLS = {
    "US30": SymbolConfig(
        symbol="US30", asset_class="index",
        description="Dow Jones — macro/earnings driven",
        spread_pips=2.0, cost_bps=1.0, slippage_bps=0.5,
        tp_atr_mult=1.5, sl_atr_mult=1.0,
        bars_per_day=102, prime_hours_utc=(13, 21),
        pip_size=1.0, oanda_price_decimals=0,
    ),
    "BTCUSD": SymbolConfig(
        symbol="BTCUSD", asset_class="crypto",
        description="Bitcoin — momentum-driven, 24/7",
        label_atr_mult=2.0, spread_pips=50.0, cost_bps=5.0, slippage_bps=2.0,
        tp_atr_mult=1.5, sl_atr_mult=1.0,
        bars_per_day=288, trend_filter=False,
        pip_size=0.01, oanda_price_decimals=1,
    ),
    "XAUUSD": SymbolConfig(
        symbol="XAUUSD", asset_class="commodity",
        description="Gold — safe haven, Fed policy driven",
        label_atr_mult=1.5, spread_pips=3.0, cost_bps=3.0, slippage_bps=1.0,
        tp_atr_mult=1.5, sl_atr_mult=1.0,
        bars_per_day=264, prime_hours_utc=(8, 21),
        pip_size=0.01, oanda_price_decimals=2,
    ),
    "ES": SymbolConfig(
        symbol="ES", asset_class="index",
        description="S&P 500 E-mini — most liquid futures",
        spread_pips=0.25, cost_bps=0.5, slippage_bps=0.3,
        tp_atr_mult=1.5, sl_atr_mult=1.0,
        bars_per_day=102, prime_hours_utc=(13, 21), trend_filter=False,
        pip_size=0.1, oanda_price_decimals=1,
    ),
    "NAS100": SymbolConfig(
        symbol="NAS100", asset_class="index",
        description="Nasdaq 100 E-mini — tech-heavy",
        spread_pips=0.5, cost_bps=0.5, slippage_bps=0.3,
        tp_atr_mult=1.5, sl_atr_mult=1.0,
        bars_per_day=102, prime_hours_utc=(13, 21), trend_filter=False,
        pip_size=1.0, oanda_price_decimals=1,
    ),
    "EURUSD": SymbolConfig(
        symbol="EURUSD", asset_class="forex",
        description="Euro/USD — most liquid FX pair",
        spread_pips=0.3, cost_bps=0.5, slippage_bps=0.2,
        pip_size=0.0001, oanda_price_decimals=5,
    ),
    "GBPUSD": SymbolConfig(
        symbol="GBPUSD", asset_class="forex",
        description="Pound/USD — London session dominant",
        spread_pips=0.5, cost_bps=0.5, slippage_bps=0.3,
        pip_size=0.0001, oanda_price_decimals=5,
    ),
}


def get_symbol(symbol: str) -> SymbolConfig:
    """Get unified config for a symbol."""
    return SYMBOLS.get(symbol, SymbolConfig(symbol=symbol, asset_class="unknown"))


def get_all_symbols() -> list[str]:
    return list(SYMBOLS.keys())
