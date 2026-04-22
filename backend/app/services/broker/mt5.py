import asyncio
from typing import Optional
from datetime import datetime, timezone

from app.services.broker.base import (
    BrokerAdapter, BrokerError,
    AccountInfo, Position, Order, Candle, SymbolInfo, Tick,
    OrderResult, CloseResult, ModifyResult,
)
from app.services.broker.symbol_registry import get_symbol_registry

# Conditional import — MT5 only works on Windows with terminal installed
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False


# Timeframe mapping
_TF_MAP = {}
if MT5_AVAILABLE:
    _TF_MAP = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1,
    }


def _require_mt5():
    if not MT5_AVAILABLE:
        raise BrokerError(
            "MetaTrader5 package not available. "
            "MT5 adapter requires Windows with MT5 terminal installed."
        )


def _get_filling_candidates(symbol: str) -> list:
    """Return filling mode candidates in priority order for a symbol.

    Reads the symbol's filling_mode bitmask and returns modes to try:
    - IOC first (works on most market-execution brokers)
    - FOK second
    - RETURN last (doesn't work on Market execution, but worth trying)
    """
    info = mt5.symbol_info(symbol)
    candidates = []

    if info is not None:
        filling = info.filling_mode
        # IOC (bit 1) preferred for market orders on most CFD brokers
        if filling & 2:
            candidates.append(mt5.ORDER_FILLING_IOC)
        if filling & 1:
            candidates.append(mt5.ORDER_FILLING_FOK)

    # Always include all 3 as fallback — we'll try them in order
    for mode in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
        if mode not in candidates:
            candidates.append(mode)

    return candidates


class MT5Adapter(BrokerAdapter):
    """
    MetaTrader 5 adapter. Wraps synchronous MT5 calls in asyncio.to_thread.

    MT5's `mt5.initialize()` is a process-global singleton. Multiple MT5Adapter
    instances share the same terminal connection. A reference counter ensures
    `mt5.shutdown()` is only called when the last adapter disconnects.
    """
    _init_count = 0
    _init_lock = None  # lazily created because asyncio.Lock needs a running loop

    def __init__(self):
        self._connected = False

    @property
    def name(self) -> str:
        return "mt5"

    async def connect(self, credentials: dict) -> bool:
        _require_mt5()
        path = credentials.get("path", "")
        login = credentials.get("login", "")
        password = credentials.get("password", "")
        server = credentials.get("server", "")

        # NO env-var fallback (removed 2026-04-22) — same multi-user leak
        # risk as Oanda. Require explicit per-user credentials.
        if not login or not password or str(login) == "0":
            raise BrokerError(
                "MT5 requires login, password, and server in the connection "
                "credentials. Fill them in Settings → Broker Connections → MT5."
            )

        # Ensure login is numeric
        try:
            login_int = int(str(login).strip())
        except (ValueError, TypeError):
            raise BrokerError(f"MT5 login must be numeric, got: '{login}'")

        kwargs = {}
        if path:
            kwargs["path"] = path
        kwargs["login"] = login_int
        kwargs["password"] = str(password)
        if server:
            kwargs["server"] = str(server).strip()

        # Reference-counted init — only call mt5.initialize if we're the first adapter
        if MT5Adapter._init_lock is None:
            MT5Adapter._init_lock = asyncio.Lock()
        async with MT5Adapter._init_lock:
            if MT5Adapter._init_count == 0:
                ok = await asyncio.to_thread(mt5.initialize, **kwargs)
                if not ok:
                    error = await asyncio.to_thread(mt5.last_error)
                    raise BrokerError(f"MT5 initialization failed: {error}")
            MT5Adapter._init_count += 1

        self._connected = True
        # Auto-discover symbols
        try:
            raw = await asyncio.to_thread(mt5.symbols_get)
            if raw:
                broker_symbols = [s.name for s in raw if s.visible]
                get_symbol_registry().auto_discover("mt5", broker_symbols)
        except Exception:
            pass
        return True

    async def disconnect(self) -> None:
        if MT5_AVAILABLE and self._connected:
            if MT5Adapter._init_lock is None:
                MT5Adapter._init_lock = asyncio.Lock()
            async with MT5Adapter._init_lock:
                MT5Adapter._init_count = max(0, MT5Adapter._init_count - 1)
                if MT5Adapter._init_count == 0:
                    await asyncio.to_thread(mt5.shutdown)
        self._connected = False

    async def get_account_info(self) -> AccountInfo:
        _require_mt5()
        info = await asyncio.to_thread(mt5.account_info)
        if info is None:
            raise BrokerError("Failed to get MT5 account info")
        return AccountInfo(
            balance=info.balance,
            equity=info.equity,
            margin_used=info.margin,
            currency=info.currency,
            unrealized_pnl=info.profit,
            account_id=str(info.login),
            server=str(info.server),
        )

    async def get_positions(self) -> list[Position]:
        _require_mt5()
        raw = await asyncio.to_thread(mt5.positions_get)
        if raw is None:
            return []
        positions = []
        for pos in raw:
            positions.append(Position(
                id=str(pos.ticket),
                symbol=pos.symbol,
                direction="BUY" if pos.type == 0 else "SELL",
                size=pos.volume,
                entry_price=pos.price_open,
                current_price=pos.price_current,
                pnl=pos.profit,
                sl=pos.sl if pos.sl > 0 else None,
                tp=pos.tp if pos.tp > 0 else None,
            ))
        return positions

    async def get_orders(self) -> list[Order]:
        _require_mt5()
        raw = await asyncio.to_thread(mt5.orders_get)
        if raw is None:
            return []
        orders = []
        for o in raw:
            order_types = {0: "BUY", 1: "SELL", 2: "BUY_LIMIT", 3: "SELL_LIMIT",
                           4: "BUY_STOP", 5: "SELL_STOP"}
            otype = order_types.get(o.type, str(o.type))
            direction = "BUY" if "BUY" in otype else "SELL"
            orders.append(Order(
                id=str(o.ticket),
                symbol=o.symbol,
                direction=direction,
                size=o.volume_current,
                order_type="LIMIT" if "LIMIT" in otype else "STOP" if "STOP" in otype else "MARKET",
                price=o.price_open,
                status="PENDING",
                sl=o.sl if o.sl > 0 else None,
                tp=o.tp if o.tp > 0 else None,
            ))
        return orders

    async def get_candles(self, symbol: str, timeframe: str = "M5", count: int = 200) -> list[Candle]:
        _require_mt5()
        tf = _TF_MAP.get(timeframe)
        if tf is None:
            raise BrokerError(f"Unsupported timeframe: {timeframe}")

        rates = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return []
        candles = []
        for r in rates:
            candles.append(Candle(
                time=int(r["time"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=int(r["tick_volume"]),
            ))
        return candles

    async def get_symbols(self) -> list[SymbolInfo]:
        _require_mt5()
        raw = await asyncio.to_thread(mt5.symbols_get)
        if raw is None:
            return []
        symbols = []
        for s in raw:
            if not s.visible:
                continue
            symbols.append(SymbolInfo(
                name=s.name,
                min_lot=s.volume_min,
                lot_step=s.volume_step,
                pip_size=s.point,
                pip_value=getattr(s, "trade_tick_value", 1.0),
                digits=s.digits,
            ))
        return symbols

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
        _require_mt5()

        # Get current tick for market orders
        tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
        if tick is None:
            return OrderResult(success=False, message=f"No tick data for {symbol}")

        action = mt5.TRADE_ACTION_DEAL if order_type.upper() == "MARKET" else mt5.TRADE_ACTION_PENDING
        order_type_mt5 = mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        fill_price = tick.ask if side.upper() == "BUY" else tick.bid

        base_request = {
            "action": action,
            "symbol": symbol,
            "volume": size,
            "type": order_type_mt5,
            "price": price if price else fill_price,
            "deviation": 20,
            "magic": 123456,
            "comment": "flowrex-algo",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if sl is not None:
            base_request["sl"] = sl
        if tp is not None:
            base_request["tp"] = tp

        # Try filling modes in order until one succeeds (error 10030 = invalid fill)
        fill_candidates = await asyncio.to_thread(_get_filling_candidates, symbol)
        fill_names = {mt5.ORDER_FILLING_FOK: "FOK", mt5.ORDER_FILLING_IOC: "IOC", mt5.ORDER_FILLING_RETURN: "RETURN"}
        result = None
        for filling in fill_candidates:
            request = {**base_request, "type_filling": filling}
            result = await asyncio.to_thread(mt5.order_send, request)
            if result is None:
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                return OrderResult(success=True, order_id=str(result.order),
                                   message=f"Order filled (fill={fill_names.get(filling, filling)})")
            if result.retcode != 10030:  # 10030 = unsupported filling mode — try next
                break

        if result is not None:
            msg = getattr(result, "comment", "unknown error")
            code = getattr(result, "retcode", "unknown")
        else:
            msg = "order_send returned None for all filling modes"
            code = "None"
        return OrderResult(success=False, message=f"MT5 error {code}: {msg}")

    async def close_position(self, position_id: str) -> CloseResult:
        _require_mt5()

        # Find the position to get its details
        positions = await asyncio.to_thread(mt5.positions_get, ticket=int(position_id))
        if not positions or len(positions) == 0:
            return CloseResult(success=False, message=f"Position {position_id} not found")

        pos = positions[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        tick = await asyncio.to_thread(mt5.symbol_info_tick, pos.symbol)
        if tick is None:
            return CloseResult(success=False, message=f"No tick data for {pos.symbol}")
        close_price = tick.bid if pos.type == 0 else tick.ask

        base_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(position_id),
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "price": close_price,
            "deviation": 20,
            "magic": 123456,
            "comment": "flowrex-close",
            "type_time": mt5.ORDER_TIME_GTC,
        }

        # Try filling modes in order until one succeeds
        fill_candidates = await asyncio.to_thread(_get_filling_candidates, pos.symbol)
        result = None
        for filling in fill_candidates:
            request = {**base_request, "type_filling": filling}
            result = await asyncio.to_thread(mt5.order_send, request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return CloseResult(success=True, pnl=pos.profit, message="Position closed")
            if result and result.retcode != 10030:
                break

        if result is not None:
            msg = getattr(result, "comment", "unknown error")
        else:
            msg = "order_send returned None for all filling modes"
        return CloseResult(success=False, message=f"Close failed: {msg}")

    async def modify_order(
        self, order_id: str, sl: Optional[float] = None, tp: Optional[float] = None
    ) -> ModifyResult:
        _require_mt5()

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(order_id),
        }
        if sl is not None:
            request["sl"] = sl
        if tp is not None:
            request["tp"] = tp

        result = await asyncio.to_thread(mt5.order_send, request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            msg = result.comment if result else "order_send returned None"
            return ModifyResult(success=False, message=f"Modify failed: {msg}")
        return ModifyResult(success=True, message="Order modified")

    async def get_tick(self, symbol: str) -> Tick:
        _require_mt5()
        tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
        if tick is None:
            raise BrokerError(f"No tick data for {symbol}")
        return Tick(
            symbol=symbol,
            bid=tick.bid,
            ask=tick.ask,
            time=tick.time,
        )
