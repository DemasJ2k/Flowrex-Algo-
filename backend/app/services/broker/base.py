from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional


class BrokerError(Exception):
    """Raised when a broker operation fails."""

    def __init__(self, message: str, code: Optional[str] = None):
        self.message = message
        self.code = code
        super().__init__(message)


# ── Universal data types ───────────────────────────────────────────────


@dataclass
class AccountInfo:
    balance: float = 0.0
    equity: float = 0.0
    margin_used: float = 0.0
    margin_available: float = 0.0
    currency: str = "USD"
    unrealized_pnl: float = 0.0
    account_id: str = ""
    server: str = ""


@dataclass
class Position:
    id: str = ""
    symbol: str = ""
    direction: str = ""  # "BUY" or "SELL"
    size: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    pnl: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None


@dataclass
class Order:
    id: str = ""
    symbol: str = ""
    direction: str = ""
    size: float = 0.0
    order_type: str = ""  # "MARKET", "LIMIT", "STOP"
    price: float = 0.0
    status: str = ""
    sl: Optional[float] = None
    tp: Optional[float] = None


@dataclass
class Candle:
    time: int = 0  # Unix timestamp
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0


@dataclass
class SymbolInfo:
    name: str = ""
    min_lot: float = 0.01
    lot_step: float = 0.01
    pip_size: float = 0.0001
    pip_value: float = 1.0
    digits: int = 5


@dataclass
class Tick:
    symbol: str = ""
    bid: float = 0.0
    ask: float = 0.0
    time: int = 0


@dataclass
class OrderResult:
    success: bool = False
    order_id: str = ""
    message: str = ""
    # Execution quality — populated by brokers that report fills
    fill_price: float = 0.0
    requested_price: float = 0.0


@dataclass
class CloseResult:
    success: bool = False
    pnl: float = 0.0
    message: str = ""


@dataclass
class ModifyResult:
    success: bool = False
    message: str = ""


# ── Timeframe constants ───────────────────────────────────────────────

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]


# ── Abstract Base Class ───────────────────────────────────────────────


class BrokerAdapter(ABC):
    """Abstract interface for all broker adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Broker identifier string (e.g. 'oanda', 'ctrader', 'mt5')."""
        ...

    @abstractmethod
    async def connect(self, credentials: dict) -> bool:
        """Connect to broker with credentials. Returns True on success."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from broker."""
        ...

    @abstractmethod
    async def get_account_info(self) -> AccountInfo:
        """Get account balance, equity, margin."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get open positions."""
        ...

    @abstractmethod
    async def get_orders(self) -> list[Order]:
        """Get pending orders."""
        ...

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> list[Candle]:
        """Get OHLCV candle data."""
        ...

    @abstractmethod
    async def get_symbols(self) -> list[SymbolInfo]:
        """Get available trading instruments."""
        ...

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> OrderResult:
        """Place a trade order."""
        ...

    @abstractmethod
    async def close_position(self, position_id: str) -> CloseResult:
        """Close an open position."""
        ...

    @abstractmethod
    async def modify_order(
        self, order_id: str, sl: Optional[float] = None, tp: Optional[float] = None
    ) -> ModifyResult:
        """Modify an existing order's SL/TP."""
        ...

    @abstractmethod
    async def get_tick(self, symbol: str) -> Tick:
        """Get latest bid/ask for a symbol."""
        ...

    async def subscribe_prices(self, symbol: str) -> AsyncIterator[Tick]:
        """Stream live price ticks. Deferred to Phase 8."""
        raise NotImplementedError("Price streaming deferred to Phase 8 (WebSockets)")
        yield  # pragma: no cover — makes this an async generator
