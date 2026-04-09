"""
Tradovate broker adapter — futures trading (ES, NQ, YM).

Authentication: OAuth2 (username + password + app_id + device_id)
Base URLs: demo.tradovateapi.com/v1 (paper) / live.tradovateapi.com/v1
Contracts: quarterly roll (ESZ6, NQZ6, YMZ6)
"""
import os
import asyncio
from typing import Optional
from datetime import datetime, timezone

import httpx

from app.services.broker.base import (
    BrokerAdapter, BrokerError,
    AccountInfo, Position, Order, Candle, SymbolInfo, Tick,
    OrderResult, CloseResult, ModifyResult,
)
from app.services.broker.symbol_registry import get_symbol_registry

DEMO_URL = "https://demo.tradovateapi.com/v1"
LIVE_URL = "https://live.tradovateapi.com/v1"
AUTH_URL = "https://demo.tradovateapi.com/v1/auth/accesstokenrequest"
LIVE_AUTH_URL = "https://live.tradovateapi.com/v1/auth/accesstokenrequest"

# Tradovate timeframe mapping
TF_MAP = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800,
}

# Futures contract sizing
CONTRACT_SPECS = {
    "ES": {"point_value": 50.0, "tick_size": 0.25, "tick_value": 12.50},
    "NQ": {"point_value": 20.0, "tick_size": 0.25, "tick_value": 5.00},
    "YM": {"point_value": 5.0, "tick_size": 1.0, "tick_value": 5.00},
}

RATE_LIMIT = 20


class TradovateAdapter(BrokerAdapter):
    """Tradovate futures broker adapter."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._access_token: str = ""
        self._account_id: int = 0
        self._account_spec: str = ""
        self._base_url: str = DEMO_URL
        self._is_live: bool = False
        self._semaphore = asyncio.Semaphore(RATE_LIMIT)
        self._registry = get_symbol_registry()
        self._contract_cache: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "tradovate"

    async def connect(self, credentials: dict) -> bool:
        """Connect to Tradovate via OAuth2."""
        username = credentials.get("username", os.environ.get("TRADOVATE_USERNAME", ""))
        password = credentials.get("password", os.environ.get("TRADOVATE_PASSWORD", ""))
        app_id = credentials.get("app_id", os.environ.get("TRADOVATE_APP_ID", ""))
        device_id = credentials.get("device_id", os.environ.get("TRADOVATE_DEVICE_ID", "flowrex-algo"))
        cid = credentials.get("cid", os.environ.get("TRADOVATE_CID", ""))
        sec = credentials.get("sec", os.environ.get("TRADOVATE_SEC", ""))
        self._is_live = credentials.get("live", False)

        if not username or not password:
            raise BrokerError("Tradovate credentials required (username, password)")

        self._base_url = LIVE_URL if self._is_live else DEMO_URL
        auth_url = LIVE_AUTH_URL if self._is_live else AUTH_URL

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(auth_url, json={
                    "name": username,
                    "password": password,
                    "appId": app_id or None,
                    "appVersion": "1.0",
                    "deviceId": device_id,
                    "cid": cid or None,
                    "sec": sec or None,
                })
                if resp.status_code != 200:
                    raise BrokerError(f"Auth failed: {resp.status_code} — {resp.text[:200]}")

                data = resp.json()
                self._access_token = data.get("accessToken", "")
                if not self._access_token:
                    raise BrokerError(f"No access token in response: {data}")

        except httpx.HTTPError as e:
            raise BrokerError(f"Tradovate connection error: {e}")

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

        # Get account info
        try:
            accounts = await self._request("GET", "/account/list")
            if accounts:
                self._account_id = accounts[0].get("id", 0)
                self._account_spec = accounts[0].get("name", "")
        except BrokerError:
            await self.disconnect()
            raise

        # Discover symbols
        await self._discover_symbols()

        return True

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._access_token = ""
        self._account_id = 0

    async def get_account_info(self) -> AccountInfo:
        data = await self._request("GET", f"/account/item?id={self._account_id}")
        cash_balance = await self._request(
            "GET", f"/cashBalance/getcashbalancesnapshot?accountId={self._account_id}"
        )

        return AccountInfo(
            balance=cash_balance.get("totalCashValue", data.get("balance", 0)),
            equity=cash_balance.get("totalCashValue", 0),
            margin_used=cash_balance.get("initialMargin", 0),
            currency="USD",
            unrealized_pnl=cash_balance.get("openPL", 0),
            account_id=str(self._account_id),
            server="live" if self._is_live else "demo",
        )

    async def get_positions(self) -> list[Position]:
        data = await self._request("GET", "/position/list")
        positions = []
        for p in data:
            if p.get("netPos", 0) == 0:
                continue

            contract_id = p.get("contractId", 0)
            contract = await self._get_contract(contract_id)
            symbol = self._to_canonical(contract.get("name", ""))

            net_pos = p.get("netPos", 0)
            direction = "BUY" if net_pos > 0 else "SELL"

            positions.append(Position(
                id=str(p.get("id", "")),
                symbol=symbol,
                direction=direction,
                size=abs(net_pos),
                entry_price=p.get("netPrice", 0),
                current_price=p.get("netPrice", 0),
                pnl=p.get("openPL", 0),
            ))
        return positions

    async def get_orders(self) -> list[Order]:
        data = await self._request("GET", "/order/list")
        orders = []
        for o in data:
            if o.get("ordStatus", "") not in ("Working", "Accepted"):
                continue

            contract_id = o.get("contractId", 0)
            contract = await self._get_contract(contract_id)
            symbol = self._to_canonical(contract.get("name", ""))

            orders.append(Order(
                id=str(o.get("id", "")),
                symbol=symbol,
                direction="BUY" if o.get("action", "") == "Buy" else "SELL",
                size=o.get("qty", 0),
                order_type=o.get("ordType", "Market").upper(),
                price=o.get("price", 0),
                status=o.get("ordStatus", ""),
            ))
        return orders

    async def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> list[Candle]:
        broker_symbol = self._to_broker(symbol)
        element_size = TF_MAP.get(timeframe, 300)
        element_size_unit = "UnderlyingUnits"

        # Tradovate uses chart subscriptions; for historical, use /md/getChart
        # Simplified: use contract/find + chart endpoint
        try:
            contract = await self._request(
                "GET", f"/contract/find?name={broker_symbol}"
            )
            contract_id = contract.get("id", 0)
        except BrokerError:
            return []

        # Build chart request
        chart_data = await self._request("POST", "/md/getChart", json={
            "symbol": broker_symbol,
            "chartDescription": {
                "underlyingType": "MinuteBar",
                "elementSize": element_size // 60 if element_size < 86400 else 1440,
                "elementSizeUnit": "UnderlyingUnits",
                "withHistogram": False,
            },
            "timeRange": {
                "closestTimestamp": datetime.now(timezone.utc).isoformat(),
                "asFarAsTimestamp": "",
                "closestTickId": 0,
                "asFarAsTickId": 0,
            },
        })

        candles = []
        bars = chart_data.get("bars", chart_data.get("charts", []))
        if isinstance(bars, list):
            for bar in bars[-count:]:
                ts = bar.get("timestamp", "")
                if isinstance(ts, str) and ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        unix_ts = int(dt.timestamp())
                    except ValueError:
                        unix_ts = 0
                else:
                    unix_ts = int(ts) if ts else 0

                candles.append(Candle(
                    time=unix_ts,
                    open=bar.get("open", 0),
                    high=bar.get("high", 0),
                    low=bar.get("low", 0),
                    close=bar.get("close", 0),
                    volume=bar.get("upVolume", 0) + bar.get("downVolume", 0),
                ))
        return candles

    async def get_symbols(self) -> list[SymbolInfo]:
        data = await self._request("GET", "/contract/list")
        symbols = []
        for c in data[:100]:
            name = c.get("name", "")
            canonical = self._to_canonical(name)
            spec = CONTRACT_SPECS.get(canonical[:2], {})
            symbols.append(SymbolInfo(
                name=canonical or name,
                min_lot=1,
                lot_step=1,
                pip_size=spec.get("tick_size", 0.25),
                pip_value=spec.get("tick_value", 12.50),
                digits=2,
            ))
        return symbols

    async def place_order(
        self, symbol: str, side: str, size: float,
        order_type: str = "MARKET", price: Optional[float] = None,
        sl: Optional[float] = None, tp: Optional[float] = None,
    ) -> OrderResult:
        broker_symbol = self._to_broker(symbol)

        try:
            contract = await self._request(
                "GET", f"/contract/find?name={broker_symbol}"
            )
            contract_id = contract.get("id", 0)
        except BrokerError as e:
            return OrderResult(success=False, message=str(e))

        action = "Buy" if side.upper() == "BUY" else "Sell"
        ord_type = "Market" if order_type.upper() == "MARKET" else "Limit"

        order_body = {
            "accountSpec": self._account_spec,
            "accountId": self._account_id,
            "action": action,
            "symbol": broker_symbol,
            "orderQty": int(size),
            "orderType": ord_type,
            "isAutomated": True,
        }
        if price and ord_type == "Limit":
            order_body["price"] = price

        try:
            result = await self._request("POST", "/order/placeorder", json=order_body)
            order_id = str(result.get("orderId", result.get("id", "")))

            # Place bracket (SL/TP) if provided
            if sl or tp:
                await self._place_bracket(order_id, contract_id, action, int(size), sl, tp)

            return OrderResult(
                success=True,
                order_id=order_id,
                message=result.get("ordStatus", "Placed"),
            )
        except BrokerError as e:
            return OrderResult(success=False, message=str(e))

    async def close_position(self, position_id: str) -> CloseResult:
        try:
            result = await self._request(
                "POST", "/order/liquidateposition",
                json={"accountId": self._account_id, "positionId": int(position_id)},
            )
            return CloseResult(
                success=True,
                pnl=result.get("realizedPnl", 0),
                message="Position closed",
            )
        except BrokerError as e:
            return CloseResult(success=False, message=str(e))

    async def modify_order(
        self, order_id: str, sl: Optional[float] = None, tp: Optional[float] = None,
    ) -> ModifyResult:
        try:
            body = {"orderId": int(order_id)}
            if sl is not None:
                body["stopPrice"] = sl
            if tp is not None:
                body["price"] = tp
            await self._request("POST", "/order/modifyorder", json=body)
            return ModifyResult(success=True, message="Order modified")
        except BrokerError as e:
            return ModifyResult(success=False, message=str(e))

    async def get_tick(self, symbol: str) -> Tick:
        broker_symbol = self._to_broker(symbol)
        try:
            data = await self._request(
                "GET", f"/md/getquote?symbol={broker_symbol}"
            )
            return Tick(
                symbol=symbol,
                bid=data.get("bid", {}).get("price", 0),
                ask=data.get("ask", {}).get("price", 0),
                time=int(datetime.now(timezone.utc).timestamp()),
            )
        except BrokerError:
            return Tick(symbol=symbol)

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self._client:
            raise BrokerError("Not connected to Tradovate")
        async with self._semaphore:
            for attempt in range(2):
                try:
                    resp = await self._client.request(method, path, **kwargs)
                    if not resp.text or not resp.text.strip():
                        raise BrokerError(f"Empty response for {method} {path}")
                    data = resp.json()
                    if resp.status_code >= 400:
                        msg = data.get("errorText", data.get("message", str(data)))
                        raise BrokerError(f"API error: {msg}", code=str(resp.status_code))
                    return data
                except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):
                    if attempt == 0:
                        continue
                    raise BrokerError(f"Connection error on attempt {attempt + 1}")
                except httpx.HTTPError as e:
                    raise BrokerError(f"HTTP error: {e}")

    async def _get_contract(self, contract_id: int) -> dict:
        """Get contract details with caching."""
        cache_key = str(contract_id)
        if cache_key in self._contract_cache:
            return self._contract_cache[cache_key]
        try:
            data = await self._request("GET", f"/contract/item?id={contract_id}")
            self._contract_cache[cache_key] = data
            return data
        except BrokerError:
            return {}

    async def _place_bracket(self, order_id, contract_id, action, qty, sl, tp):
        """Place stop-loss and take-profit as OCO bracket."""
        exit_action = "Sell" if action == "Buy" else "Buy"
        bracket = []
        if sl:
            bracket.append({
                "accountSpec": self._account_spec,
                "accountId": self._account_id,
                "action": exit_action,
                "symbol": "",
                "orderQty": qty,
                "orderType": "Stop",
                "stopPrice": sl,
                "isAutomated": True,
            })
        if tp:
            bracket.append({
                "accountSpec": self._account_spec,
                "accountId": self._account_id,
                "action": exit_action,
                "symbol": "",
                "orderQty": qty,
                "orderType": "Limit",
                "price": tp,
                "isAutomated": True,
            })
        if bracket:
            try:
                await self._request("POST", "/order/placeoso", json={
                    "accountSpec": self._account_spec,
                    "accountId": self._account_id,
                    "orderType": "Market",
                    "action": action,
                    "symbol": "",
                    "orderQty": qty,
                    "bracket1": bracket[0] if len(bracket) > 0 else None,
                    "bracket2": bracket[1] if len(bracket) > 1 else None,
                })
            except BrokerError:
                pass  # Bracket placement is best-effort

    async def _discover_symbols(self):
        """Fetch available contracts and populate symbol registry."""
        try:
            data = await self._request("GET", "/contract/list")
            broker_symbols = [c.get("name", "") for c in data if c.get("name")]
            self._registry.auto_discover("tradovate", broker_symbols)
        except BrokerError:
            pass

    def _to_broker(self, symbol: str) -> str:
        return self._registry.to_broker(symbol, "tradovate")

    def _to_canonical(self, broker_symbol: str) -> str:
        return self._registry.to_canonical(broker_symbol, "tradovate")
