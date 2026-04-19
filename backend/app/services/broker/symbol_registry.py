"""
Centralized symbol normalization layer.

Our system uses canonical names: XAUUSD, BTCUSD, US30, EURUSD, etc.
Each broker uses different names for the same instruments.

This registry provides:
1. Default known mappings per broker
2. Auto-discovery: fuzzy-match broker symbols to canonical names on connect
3. User-override via JSON config file
"""
import json
import os
import re
from typing import Optional

# ── Default known mappings ─────────────────────────────────────────────
# canonical_name -> {broker_name: broker_symbol}

_DEFAULT_MAPPINGS: dict[str, dict[str, str]] = {
    "XAUUSD": {
        "oanda": "XAU_USD",
        "ctrader": "XAUUSD",
        "mt5": "XAUUSD",
        "tradovate": "GCZ6",
        "interactive_brokers": "XAUUSD",
    },
    "XAGUSD": {
        "oanda": "XAG_USD",
        "ctrader": "XAGUSD",
        "mt5": "XAGUSD",
        "tradovate": "SIZ6",
        "interactive_brokers": "XAGUSD",
    },
    "BTCUSD": {
        "oanda": "BTC_USD",
        "ctrader": "BTCUSD",
        "mt5": "BTCUSD",
        "tradovate": "BTCZ6",
        "interactive_brokers": "BTC",
    },
    "ETHUSD": {
        "oanda": "ETH_USD",
        "ctrader": "ETHUSD",
        "mt5": "ETHUSD",
        "tradovate": "ETHZ6",
        "interactive_brokers": "ETH",
    },
    "US30": {
        "oanda": "US30_USD",
        "ctrader": "US30",
        "mt5": "US30",
        "tradovate": "YMZ6",
        "interactive_brokers": "YM",
    },
    "NAS100": {
        "oanda": "NAS100_USD",
        "ctrader": "NAS100",
        "mt5": "NAS100",
        "tradovate": "NQZ6",
        "interactive_brokers": "NQ",
    },
    "ES": {
        "oanda": "SPX500_USD",  # Same as SPX500 — both map to S&P 500 CFD on Oanda
        "ctrader": "US500",
        "mt5": "US500",
        "tradovate": "ESZ6",
        "interactive_brokers": "ES",
    },
    "SPX500": {
        "oanda": "SPX500_USD",
        "ctrader": "US500",
        "mt5": "US500",
        "tradovate": "ESZ6",
        "interactive_brokers": "ES",
    },
    "EURUSD": {
        "oanda": "EUR_USD",
        "ctrader": "EURUSD",
        "mt5": "EURUSD",
        "interactive_brokers": "EUR",
    },
    "GBPUSD": {
        "oanda": "GBP_USD",
        "ctrader": "GBPUSD",
        "mt5": "GBPUSD",
        "interactive_brokers": "GBP",
    },
    "USDJPY": {
        "oanda": "USD_JPY",
        "ctrader": "USDJPY",
        "mt5": "USDJPY",
        "interactive_brokers": "USD.JPY",
    },
    "AUDUSD": {
        "oanda": "AUD_USD",
        "ctrader": "AUDUSD",
        "mt5": "AUDUSD",
        "interactive_brokers": "AUD",
    },
    "USDCAD": {
        "oanda": "USD_CAD",
        "ctrader": "USDCAD",
        "mt5": "USDCAD",
        "interactive_brokers": "USD.CAD",
    },
    "USDCHF": {
        "oanda": "USD_CHF",
        "ctrader": "USDCHF",
        "mt5": "USDCHF",
        "interactive_brokers": "USD.CHF",
    },
    "NZDUSD": {
        "oanda": "NZD_USD",
        "ctrader": "NZDUSD",
        "mt5": "NZDUSD",
        "interactive_brokers": "NZD",
    },
    "EURGBP": {
        "oanda": "EUR_GBP",
        "ctrader": "EURGBP",
        "mt5": "EURGBP",
        "interactive_brokers": "EUR.GBP",
    },
    "EURJPY": {
        "oanda": "EUR_JPY",
        "ctrader": "EURJPY",
        "mt5": "EURJPY",
        "interactive_brokers": "EUR.JPY",
    },
    "GBPJPY": {
        "oanda": "GBP_JPY",
        "ctrader": "GBPJPY",
        "mt5": "GBPJPY",
        "interactive_brokers": "GBP.JPY",
    },
}

# ── Fuzzy matching patterns for auto-discovery ─────────────────────────
# Maps canonical names to regex patterns that catch common broker variants

_FUZZY_PATTERNS: dict[str, re.Pattern] = {
    "XAUUSD": re.compile(r"^(XAU[_/]?USD|GOLD)\.?.*$", re.IGNORECASE),
    "XAGUSD": re.compile(r"^(XAG[_/]?USD|SILVER)\.?.*$", re.IGNORECASE),
    "BTCUSD": re.compile(r"^(BTC[_/]?USD|BITCOIN)\.?.*$", re.IGNORECASE),
    "ETHUSD": re.compile(r"^(ETH[_/]?USD|ETHEREUM)\.?.*$", re.IGNORECASE),
    "US30":   re.compile(r"^(US30|DJ30|DJI|USTEC30|US30[_.]?(USD|cash)|YM[FGHJKMNQUVXZ]\d)\.?.*$", re.IGNORECASE),
    "NAS100": re.compile(r"^(NAS100|USTEC|NDX|NASDAQ|NAS100[_.]?(USD|cash)|NQ[FGHJKMNQUVXZ]\d)\.?.*$", re.IGNORECASE),
    "SPX500": re.compile(r"^(SPX500|US500|SP500|SPX|SPX500[_.]?(USD|cash)|ES[FGHJKMNQUVXZ]\d)\.?.*$", re.IGNORECASE),
    "EURUSD": re.compile(r"^EUR[_/]?USD\.?.*$", re.IGNORECASE),
    "GBPUSD": re.compile(r"^GBP[_/]?USD\.?.*$", re.IGNORECASE),
    "USDJPY": re.compile(r"^USD[_/]?JPY\.?.*$", re.IGNORECASE),
    "AUDUSD": re.compile(r"^AUD[_/]?USD\.?.*$", re.IGNORECASE),
    "USDCAD": re.compile(r"^USD[_/]?CAD\.?.*$", re.IGNORECASE),
    "USDCHF": re.compile(r"^USD[_/]?CHF\.?.*$", re.IGNORECASE),
    "NZDUSD": re.compile(r"^NZD[_/]?USD\.?.*$", re.IGNORECASE),
    "EURGBP": re.compile(r"^EUR[_/]?GBP\.?.*$", re.IGNORECASE),
    "EURJPY": re.compile(r"^EUR[_/]?JPY\.?.*$", re.IGNORECASE),
    "GBPJPY": re.compile(r"^GBP[_/]?JPY\.?.*$", re.IGNORECASE),
}

# Path for user override config
_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "symbol_mappings.json")


class SymbolRegistry:
    """
    Centralized symbol name resolver.

    Usage:
        registry = SymbolRegistry()
        broker_sym = registry.to_broker("XAUUSD", "oanda")   # -> "XAU_USD"
        canonical  = registry.to_canonical("XAU_USD", "oanda") # -> "XAUUSD"
    """

    def __init__(self):
        # canonical -> {broker -> broker_symbol}
        self._mappings: dict[str, dict[str, str]] = {}
        # broker -> {broker_symbol -> canonical}  (reverse index)
        self._reverse: dict[str, dict[str, str]] = {}
        self._load_defaults()
        self._load_user_overrides()

    def _load_defaults(self):
        """Load built-in default mappings."""
        for canonical, brokers in _DEFAULT_MAPPINGS.items():
            self._mappings[canonical] = dict(brokers)
        self._rebuild_reverse()

    def _load_user_overrides(self):
        """Load user overrides from JSON config if it exists."""
        if not os.path.exists(_CONFIG_FILE):
            return
        try:
            with open(_CONFIG_FILE, "r") as f:
                overrides = json.load(f)
            for canonical, brokers in overrides.items():
                canonical = canonical.upper()
                if canonical not in self._mappings:
                    self._mappings[canonical] = {}
                self._mappings[canonical].update(brokers)
            self._rebuild_reverse()
        except (json.JSONDecodeError, OSError):
            pass

    def _rebuild_reverse(self):
        """Rebuild the reverse lookup index."""
        self._reverse.clear()
        for canonical, brokers in self._mappings.items():
            for broker, broker_sym in brokers.items():
                if broker not in self._reverse:
                    self._reverse[broker] = {}
                self._reverse[broker][broker_sym] = canonical

    def to_broker(self, canonical: str, broker: str) -> str:
        """Convert canonical symbol to broker-specific symbol."""
        broker_map = self._mappings.get(canonical.upper(), {})
        result = broker_map.get(broker)
        if result:
            return result
        # Fallback: return canonical as-is (some brokers use standard names)
        return canonical

    def to_canonical(self, broker_symbol: str, broker: str) -> str:
        """Convert broker-specific symbol to canonical name."""
        broker_reverse = self._reverse.get(broker, {})
        result = broker_reverse.get(broker_symbol)
        if result:
            return result
        # Fallback: strip common suffixes and underscores
        cleaned = broker_symbol.replace("_", "").rstrip("m.").split(".")[0]
        return cleaned

    def auto_discover(self, broker: str, broker_symbols: list[str]):
        """
        Fuzzy-match broker symbols to canonical names.
        Call this after connecting to a broker to fill gaps in the mapping.
        """
        for broker_sym in broker_symbols:
            # Skip if already mapped
            if broker in self._reverse and broker_sym in self._reverse.get(broker, {}):
                continue
            # Try fuzzy patterns
            for canonical, pattern in _FUZZY_PATTERNS.items():
                if pattern.match(broker_sym):
                    if canonical not in self._mappings:
                        self._mappings[canonical] = {}
                    # Only set if no existing mapping for this broker+canonical
                    if broker not in self._mappings[canonical]:
                        self._mappings[canonical][broker] = broker_sym
                    break
        self._rebuild_reverse()

    def get_all_canonical(self) -> list[str]:
        """Return all known canonical symbol names."""
        return list(self._mappings.keys())

    def get_broker_symbols(self, broker: str) -> dict[str, str]:
        """Return {canonical: broker_symbol} for a given broker."""
        result = {}
        for canonical, brokers in self._mappings.items():
            if broker in brokers:
                result[canonical] = brokers[broker]
        return result

    def save_user_overrides(self):
        """Save current mappings to user config file (for persistence)."""
        os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
        with open(_CONFIG_FILE, "w") as f:
            json.dump(self._mappings, f, indent=2)


# ── Singleton ──────────────────────────────────────────────────────────

_registry: Optional[SymbolRegistry] = None


def get_symbol_registry() -> SymbolRegistry:
    global _registry
    if _registry is None:
        _registry = SymbolRegistry()
    return _registry
