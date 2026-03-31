from typing import Optional
from datetime import datetime, timezone

import httpx

from app.services.broker.base import (
    BrokerAdapter, BrokerError,
    AccountInfo, Position, Order, Candle, SymbolInfo, Tick,
    OrderResult, CloseResult, ModifyResult,
)
from app.services.broker.symbol_registry import get_symbol_registry


class CTraderAdapter(BrokerAdapter):
    """
    cTrader Open API adapter (REST-based).
    Full protobuf/WebSocket streaming deferred to Phase 8.
    """

    BASE_URL = "https://api.ctrader.com"

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._account_id: Optional[int] = None
        self._access_token: str = ""
        self._symbol_cache: dict[int, str] = {}  # symbol_id -> name
        self._symbol_reverse: dict[str, int] = {}  # name -> symbol_id
        self._lot_sizes: dict[str, int] = {}  # name -> lotSize in cents

    @property
    def name(self) -> str:
        return "ctrader"

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self._client:
            raise BrokerError("Not connected to cTrader")
        try:
            resp = await self._client.request(method, path, **kwargs)
            data = resp.json()
            if resp.status_code >= 400:
                msg = data.get("error", {}).get("message", str(data))
                raise BrokerError(f"cTrader API error: {msg}", code=str(resp.status_code))
            return data
        except httpx.HTTPError as e:
            raise BrokerError(f"cTrader HTTP error: {e}")

    async def connect(self, credentials: dict) -> bool:
        self._access_token = credentials.get("access_token", "")
        self._account_id = credentials.get("account_id")
        client_id = credentials.get("client_id", "")
        client_secret = credentials.get("client_secret", "")

        if not self._access_token or not self._account_id:
            raise BrokerError("cTrader requires access_token and account_id")

        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

        # Verify connection and cache symbols
        try:
            await self._cache_symbols()
            return True
        except BrokerError:
            await self.disconnect()
            raise

    async def _cache_symbols(self):
        """Fetch and cache all available symbols, then auto-discover mappings."""
        try:
            data = await self._request("GET", f"/v2/symbols")
            broker_symbols = []
            for sym in data.get("data", []):
                sid = sym.get("symbolId", 0)
                sname = sym.get("symbolName", "")
                self._symbol_cache[sid] = sname
                self._symbol_reverse[sname] = sid
                self._lot_sizes[sname] = sym.get("lotSize", 100000)
                broker_symbols.append(sname)
            # Auto-discover canonical mappings
            get_symbol_registry().auto_discover("ctrader", broker_symbols)
        except BrokerError:
            pass  # Non-fatal: proceed without symbol cache

    def _lots_to_volume(self, symbol: str, lots: float) -> int:
        """Convert lot size to cTrader volume (cents)."""
        lot_size = self._lot_sizes.get(symbol, 100000)
        return int(lots * lot_size)

    def _volume_to_lots(self, symbol: str, volume: int) -> float:
        """Convert cTrader volume to lots."""
        lot_size = self._lot_sizes.get(symbol, 100000)
        return volume / lot_size

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._symbol_cache.clear()
        self._symbol_reverse.clear()

    async def get_account_info(self) -> AccountInfo:
        data = await self._request("GET", f"/v2/accounts/{self._account_id}")
        acct = data.get("data", {})
        return AccountInfo(
            balance=float(acct.get("balance", 0)) / 100,  # cents to currency
            equity=float(acct.get("equity", 0)) / 100,
            margin_used=float(acct.get("marginUsed", 0)) / 100,
            currency=acct.get("currency", "USD"),
            unrealized_pnl=float(acct.get("unrealizedPnl", 0)) / 100,
            account_id=str(self._account_id or ""),
            server="ctrader",
        )

    async def get_positions(self) -> list[Position]:
        data = await self._request("GET", f"/v2/accounts/{self._account_id}/positions")
        positions = []
        for pos in data.get("data", []):
            symbol = self._symbol_cache.get(pos.get("symbolId", 0), str(pos.get("symbolId", "")))
            direction = "BUY" if pos.get("tradeSide", "").upper() == "BUY" else "SELL"
            positions.append(Position(
                id=str(pos.get("positionId", "")),
                symbol=symbol,
                direction=direction,
                size=self._volume_to_lots(symbol, pos.get("volume", 0)),
                entry_price=float(pos.get("entryPrice", 0)),
                current_price=float(pos.get("currentPrice", 0)),
                pnl=float(pos.get("pnl", 0)) / 100,
                sl=float(pos["stopLoss"]) if pos.get("stopLoss") else None,
                tp=float(pos["takeProfit"]) if pos.get("takeProfit") else None,
            ))
        return positions

    async def get_orders(self) -> list[Order]:
        data = await self._request("GET", f"/v2/accounts/{self._account_id}/orders")
        orders = []
        for o in data.get("data", []):
            symbol = self._symbol_cache.get(o.get("symbolId", 0), "")
            orders.append(Order(
                id=str(o.get("orderId", "")),
                symbol=symbol,
                direction="BUY" if o.get("tradeSide", "").upper() == "BUY" else "SELL",
                size=self._volume_to_lots(symbol, o.get("volume", 0)),
                order_type=o.get("orderType", ""),
                price=float(o.get("limitPrice", 0) or o.get("stopPrice", 0)),
                status=o.get("status", ""),
                sl=float(o["stopLoss"]) if o.get("stopLoss") else None,
                tp=float(o["takeProfit"]) if o.get("takeProfit") else None,
            ))
        return orders

    async def get_candles(self, symbol: str, timeframe: str = "M5", count: int = 200) -> list[Candle]:
        # cTrader REST may have limited candle support — try the endpoint
        try:
            symbol_id = self._symbol_reverse.get(symbol)
            if not symbol_id:
                raise BrokerError(f"Unknown symbol: {symbol}")
            data = await self._request(
                "GET",
                f"/v2/symbols/{symbol_id}/trendbars",
                params={"period": timeframe, "count": count},
            )
            candles = []
            for bar in data.get("data", []):
                candles.append(Candle(
                    time=int(bar.get("timestamp", 0)) // 1000,
                    open=float(bar.get("open", 0)),
                    high=float(bar.get("high", 0)),
                    low=float(bar.get("low", 0)),
                    close=float(bar.get("close", 0)),
                    volume=int(bar.get("volume", 0)),
                ))
            return candles
        except BrokerError:
            raise BrokerError(
                f"Candle data for {symbol} not available via REST. "
                "Full candle support requires WebSocket (Phase 8)."
            )

    async def get_symbols(self) -> list[SymbolInfo]:
        symbols = []
        for sname, sid in self._symbol_reverse.items():
            symbols.append(SymbolInfo(
                name=sname,
                min_lot=0.01,
                lot_step=0.01,
                pip_size=0.0001,
                pip_value=1.0,
                digits=5,
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
        symbol_id = self._symbol_reverse.get(symbol)
        if not symbol_id:
            return OrderResult(success=False, message=f"Unknown symbol: {symbol}")

        body = {
            "symbolId": symbol_id,
            "tradeSide": side.upper(),
            "volume": self._lots_to_volume(symbol, size),
            "orderType": order_type.upper(),
        }
        if price is not None:
            body["limitPrice"] = price
        if sl is not None:
            body["stopLoss"] = sl
        if tp is not None:
            body["takeProfit"] = tp

        try:
            data = await self._request(
                "POST", f"/v2/accounts/{self._account_id}/orders", json=body
            )
            order_id = str(data.get("data", {}).get("orderId", ""))
            return OrderResult(success=True, order_id=order_id, message="Order placed")
        except BrokerError as e:
            return OrderResult(success=False, message=e.message)

    async def close_position(self, position_id: str) -> CloseResult:
        try:
            data = await self._request(
                "DELETE", f"/v2/accounts/{self._account_id}/positions/{position_id}"
            )
            pnl = float(data.get("data", {}).get("pnl", 0)) / 100
            return CloseResult(success=True, pnl=pnl, message="Position closed")
        except BrokerError as e:
            return CloseResult(success=False, message=e.message)

    async def modify_order(
        self, order_id: str, sl: Optional[float] = None, tp: Optional[float] = None
    ) -> ModifyResult:
        body: dict = {}
        if sl is not None:
            body["stopLoss"] = sl
        if tp is not None:
            body["takeProfit"] = tp
        try:
            await self._request(
                "PUT", f"/v2/accounts/{self._account_id}/orders/{order_id}", json=body
            )
            return ModifyResult(success=True, message="Order modified")
        except BrokerError as e:
            return ModifyResult(success=False, message=e.message)

    async def get_tick(self, symbol: str) -> Tick:
        symbol_id = self._symbol_reverse.get(symbol)
        if not symbol_id:
            raise BrokerError(f"Unknown symbol: {symbol}")
        data = await self._request("GET", f"/v2/symbols/{symbol_id}/tick")
        tick_data = data.get("data", {})
        return Tick(
            symbol=symbol,
            bid=float(tick_data.get("bid", 0)),
            ask=float(tick_data.get("ask", 0)),
            time=int(datetime.now(timezone.utc).timestamp()),
        )
