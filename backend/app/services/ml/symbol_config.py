"""
Per-symbol configuration for ML training and feature engineering.
Controls: labeling thresholds, prime trading hours, feature parameters.
"""

SYMBOL_CONFIGS: dict[str, dict] = {
    "XAUUSD": {
        "asset_class": "commodity",
        "label_atr_mult": 1.5,
        "label_forward_bars": 8,
        "prime_hours_utc": (8, 21),
        "spread_pips": 3.0,
        # Execution realism params
        "cost_bps": 3.0,        # round-trip spread (0.03%)
        "slippage_bps": 1.0,    # slippage per trade (0.01%)
        "tp_atr_mult": 1.5,     # take-profit at entry ± ATR×1.5
        "sl_atr_mult": 1.0,     # stop-loss at entry ∓ ATR×1.0
        "bars_per_day": 264,    # ~22h market hours
        "description": "Gold — driven by USD strength, safe haven flows, Fed policy",
    },
    "BTCUSD": {
        "asset_class": "crypto",
        "label_atr_mult": 2.0,  # Require 2×ATR move for labeling (fewer, higher-quality trades)
        "label_forward_bars": 10,
        "hold_bars": 12,        # Allow extra bars for 2×ATR target to develop
        "prime_hours_utc": (0, 24),
        "spread_pips": 50.0,
        "cost_bps": 5.0,        # round-trip spread+commission (~0.05%)
        "slippage_bps": 2.0,    # slippage (wider due to crypto volatility)
        "tp_atr_mult": 2.0,     # TP matches label_atr_mult: risk 0.8 to make 2.0 (28.6% break-even)
        "sl_atr_mult": 0.8,     # SL tighter than TP — good risk/reward
        "bars_per_day": 288,    # 24h
        "trend_filter": False,  # BTC reverses too fast for 20-day EMA; model features handle regime
        "description": "Bitcoin — momentum-driven, 24/7, high volatility",
    },
    "US30": {
        "asset_class": "index",
        "label_atr_mult": 1.2,
        "label_forward_bars": 10,
        "prime_hours_utc": (13, 21),
        "spread_pips": 2.0,
        "cost_bps": 1.0,        # tight spread (~2 pts on 40k = 0.005%) × 2
        "slippage_bps": 0.5,    # minimal slippage (highly liquid)
        "tp_atr_mult": 1.2,     # matches label_atr_mult
        "sl_atr_mult": 0.8,
        "bars_per_day": 102,    # ~8.5h session
        "description": "Dow Jones — macro/earnings driven, NY session dominant",
    },
    "ES": {
        "asset_class": "index",
        "label_atr_mult": 1.2,
        "label_forward_bars": 10,
        "prime_hours_utc": (13, 21),
        "spread_pips": 0.25,        # ES tick = 0.25 pts, spread typically 0.25-0.50
        "cost_bps": 0.5,            # very tight spread (~0.25 pts on 5500 = 0.005%)
        "slippage_bps": 0.3,        # most liquid futures contract in the world
        "tp_atr_mult": 1.2,
        "sl_atr_mult": 0.8,
        "bars_per_day": 102,        # ~8.5h primary session (same as US30)
        "hold_bars": 10,
        "trend_filter": False,      # ES mean-reverts frequently
        "description": "S&P 500 E-mini — most liquid futures, macro-driven",
    },
    "NAS100": {
        "asset_class": "index",
        "label_atr_mult": 1.2,
        "label_forward_bars": 10,
        "prime_hours_utc": (13, 21),
        "spread_pips": 0.5,
        "cost_bps": 0.5,
        "slippage_bps": 0.3,
        "tp_atr_mult": 1.2,
        "sl_atr_mult": 0.8,
        "bars_per_day": 102,
        "hold_bars": 10,
        "trend_filter": False,
        "description": "Nasdaq 100 E-mini — tech-heavy, high beta, momentum-driven",
    },
    "EURUSD": {
        "asset_class": "forex",
        "label_atr_mult": 1.0,
        "label_forward_bars": 10,
        "prime_hours_utc": (8, 17),
        "spread_pips": 0.3,
        "description": "Euro/USD — most liquid FX pair, ECB/Fed driven",
    },
    "GBPUSD": {
        "asset_class": "forex",
        "label_atr_mult": 1.0,
        "label_forward_bars": 10,
        "prime_hours_utc": (8, 17),
        "spread_pips": 0.5,
        "description": "Pound/USD — London session dominant, BOE driven",
    },
}


def get_symbol_config(symbol: str) -> dict:
    """Get config for a symbol with defaults."""
    default = {
        "asset_class": "unknown",
        "label_atr_mult": 1.0,
        "label_forward_bars": 10,
        "prime_hours_utc": (0, 24),
        "spread_pips": 1.0,
        "description": "",
    }
    return SYMBOL_CONFIGS.get(symbol, default)


def get_all_symbols() -> list[str]:
    return list(SYMBOL_CONFIGS.keys())


# Backward compatibility — delegates to unified symbols.py
from app.services.symbols import get_symbol as _get_unified
# get_symbol_config still works as before (returns dict, not dataclass)
