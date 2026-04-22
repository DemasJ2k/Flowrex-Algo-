"""
Tradovate broker adapter — futures trading.

Authentication: OAuth2 (username + password + app_id + device_id)
Base URLs: demo.tradovateapi.com/v1 (paper) / live.tradovateapi.com/v1
Contracts: quarterly roll (ESZ6, NQZ6, YMZ6, GCZ6, ...)

Batch 10 fixes (2026-04-15 audit):
  - C30: Live/demo toggle now reads both 'live' and 'demo' credential keys
  - C31: Bracket orders pass actual symbol, not empty string
  - C32: Token refresh with expiresIn + refreshToken + 401 auto-refresh
  - C33: Contract specs added for GC, SI, BTC, ETH futures
"""
import os
import asyncio
from typing import Optional
from datetime import datetime, timezone, timedelta

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
RENEW_PATH = "/auth/renewaccesstoken"

# Tradovate timeframe mapping
TF_MAP = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800,
}

# Futures contract sizing — verified against CME specs. Point value × 1 contract = notional per 1-point move.
# Added GC, SI, BTC, ETH per audit C33. Values match front-month standard contracts.
CONTRACT_SPECS = {
    "ES":   {"point_value": 50.0,   "tick_size": 0.25,  "tick_value": 12.50},   # S&P 500 E-mini
    "NQ":   {"point_value": 20.0,   "tick_size": 0.25,  "tick_value": 5.00},    # Nasdaq 100 E-mini
    "YM":   {"point_value": 5.0,    "tick_size": 1.0,   "tick_value": 5.00},    # Dow E-mini
    "GC":   {"point_value": 100.0,  "tick_size": 0.10,  "tick_value": 10.00},   # Gold futures
    "SI":   {"point_value": 5000.0, "tick_size": 0.005, "tick_value": 25.00},   # Silver futures
    "BTC":  {"point_value": 5.0,    "tick_size": 5.0,   "tick_value": 25.00},   # Bitcoin futures (standard)
    "ETH":  {"point_value": 50.0,   "tick_size": 0.05,  "tick_value": 2.50},    # Ether futures (standard)
    "CL":   {"point_value": 1000.0, "tick_size": 0.01,  "tick_value": 10.00},   # Crude oil
    "ZN":   {"point_value": 1000.0, "tick_size": 0.015625, "tick_value": 15.625},  # 10-year T-note
}

RATE_LIMIT = 20
# Refresh token 5 minutes before expiry to avoid race with mid-flight requests
TOKEN_REFRESH_BUFFER_SEC = 300


class TradovateAdapter(BrokerAdapter):
    """Tradovate futures broker adapter."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._access_token: str = ""
        self._md_access_token: str = ""  # returned alongside main token, same lifetime
        self._token_expires_at: Optional[datetime] = None
        self._refresh_lock = asyncio.Lock()
        # Credentials snapshot for re-auth (no refresh token on Tradovate — we re-login)
        self._creds_snapshot: dict = {}
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
        """
        Connect to Tradovate via OAuth2.

        Live/demo mode: prefers explicit `live` key, falls back to `not demo`.
        Previously (audit C30) the code only read `live`, so the frontend's
        `demo: true` toggle was silently ignored and everyone got demo mode.
        """
        # NO env-var fallback (removed 2026-04-22) — same multi-user leak
        # risk as Oanda. Require explicit per-user credentials. device_id
        # keeps its static default since it's not a credential.
        username = credentials.get("username", "")
        password = credentials.get("password", "")
        app_id = credentials.get("app_id", "")
        device_id = credentials.get("device_id", "flowrex-algo")
        cid = credentials.get("cid", "")
        sec = credentials.get("sec", "")

        # ── Live/demo mode resolution (C30 fix) ──
        # Priority order: explicit 'live' key > 'demo' inverted > default demo.
        if "live" in credentials:
            self._is_live = bool(credentials["live"])
        elif "demo" in credentials:
            self._is_live = not bool(credentials["demo"])
        else:
            self._is_live = False  # default to demo for safety

        if not username or not password:
            raise BrokerError(
                "Tradovate requires username and password in the connection "
                "credentials. Fill them in Settings → Broker Connections → Tradovate."
            )

        # Snapshot credentials for token refresh
        self._creds_snapshot = {
            "username": username,
            "password": password,
            "app_id": app_id,
            "device_id": device_id,
            "cid": cid,
            "sec": sec,
        }

        self._base_url = LIVE_URL if self._is_live else DEMO_URL

        # Perform initial auth
        await self._authenticate()

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

    async def _authenticate(self):
        """
        Acquire a fresh access token from Tradovate.

        Tradovate OAuth2 doesn't issue a refresh token — we re-send credentials
        via /auth/accesstokenrequest. Called from connect() on first auth and
        from _ensure_token_fresh() when the token is within 5 min of expiry.
        """
        auth_url = LIVE_AUTH_URL if self._is_live else AUTH_URL
        creds = self._creds_snapshot

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(auth_url, json={
                    "name": creds.get("username", ""),
                    "password": creds.get("password", ""),
                    "appId": creds.get("app_id") or None,
                    "appVersion": "1.0",
                    "deviceId": creds.get("device_id", "flowrex-algo"),
                    "cid": creds.get("cid") or None,
                    "sec": creds.get("sec") or None,
                })
                if resp.status_code != 200:
                    raise BrokerError(f"Auth failed: {resp.status_code} — {resp.text[:200]}")

                data = resp.json()
                self._access_token = data.get("accessToken", "")
                self._md_access_token = data.get("mdAccessToken", "")
                if not self._access_token:
                    raise BrokerError(f"No access token in response: {data}")

                # Track expiry. Tradovate returns ISO8601 "expirationTime" field.
                expiration = data.get("expirationTime")
                if expiration:
                    try:
                        self._token_expires_at = datetime.fromisoformat(
                            expiration.replace("Z", "+00:00")
                        )
                    except ValueError:
                        # Fallback: assume 80 minutes (Tradovate default)
                        self._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=80)
                else:
                    self._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=80)

                # Update the client headers so subsequent requests use the new token
                if self._client is not None:
                    self._client.headers["Authorization"] = f"Bearer {self._access_token}"

        except httpx.HTTPError as e:
            raise BrokerError(f"Tradovate auth error: {e}")

    async def _ensure_token_fresh(self):
        """Refresh the access token if it's near expiry. C32 fix."""
        if not self._token_expires_at:
            return
        remaining = (self._token_expires_at - datetime.now(timezone.utc)).total_seconds()
        if remaining > TOKEN_REFRESH_BUFFER_SEC:
            return
        async with self._refresh_lock:
            # Double-check inside the lock — another coroutine may have refreshed already
            if not self._token_expires_at:
                return
            remaining = (self._token_expires_at - datetime.now(timezone.utc)).total_seconds()
            if remaining <= TOKEN_REFRESH_BUFFER_SEC:
                await self._authenticate()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._access_token = ""
        self._md_access_token = ""
        self._token_expires_at = None
        self._creds_snapshot = {}
        self._account_id = 0
        self._contract_cache.clear()

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
        # Removed the hardcoded `data[:100]` cap (audit H40) — Tradovate has 1000+
        # instruments and we need to see all futures, not just the first 100.
        for c in data:
            name = c.get("name", "")
            canonical = self._to_canonical(name)
            # Try 3-char prefix first (BTC, ETH), then 2-char (ES, NQ, YM, GC, SI, CL, ZN)
            spec = (
                CONTRACT_SPECS.get(canonical[:3])
                or CONTRACT_SPECS.get(canonical[:2])
                or {}
            )
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

            # Place bracket (SL/TP) if provided. C31 fix: pass the actual
            # broker_symbol so Tradovate can route the bracket correctly.
            bracket_warning = None
            if sl or tp:
                try:
                    await self._place_bracket(
                        order_id, broker_symbol, contract_id, action, int(size), sl, tp,
                    )
                except BrokerError as be:
                    # Don't silently swallow — surface as a warning on the result
                    bracket_warning = f"Main order filled but bracket failed: {be}"

            msg = result.get("ordStatus", "Placed")
            if bracket_warning:
                msg = f"{msg} ({bracket_warning})"

            return OrderResult(
                success=True,
                order_id=order_id,
                message=msg,
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
        """
        Rate-limited request with proactive token refresh and 401 auto-retry.

        C32 fix: before each request, check if the token is about to expire.
        On a 401 response, refresh the token ONCE and retry the request.
        """
        if not self._client:
            raise BrokerError("Not connected to Tradovate")

        # Proactive refresh (doesn't count against the retry budget below)
        try:
            await self._ensure_token_fresh()
        except BrokerError:
            # If refresh fails, still try the request — we may get lucky
            pass

        async with self._semaphore:
            refreshed_after_401 = False
            for attempt in range(2):
                try:
                    resp = await self._client.request(method, path, **kwargs)

                    # Reactive 401 refresh: if the token expired between the proactive
                    # check and the actual request, we get a 401 — refresh and retry once.
                    if resp.status_code == 401 and not refreshed_after_401:
                        refreshed_after_401 = True
                        try:
                            await self._authenticate()
                        except BrokerError as e:
                            raise BrokerError(f"Token refresh failed after 401: {e}")
                        continue  # Retry the request with the fresh token

                    if not resp.text or not resp.text.strip():
                        raise BrokerError(f"Empty response for {method} {path}")
                    try:
                        data = resp.json()
                    except Exception:
                        raise BrokerError(f"Non-JSON response: {resp.text[:200]}")
                    if resp.status_code >= 400:
                        msg = data.get("errorText", data.get("message", str(data)))
                        raise BrokerError(f"API error: {msg}", code=str(resp.status_code))
                    return data
                except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout):
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

    async def _place_bracket(self, order_id, broker_symbol, contract_id, action, qty, sl, tp):
        """
        Place stop-loss and take-profit as OCO bracket.

        C31 fix: now passes `broker_symbol` (was empty string). Tradovate requires
        a symbol on each OSO leg to route the order — silently rejecting the
        bracket with empty symbol meant live trades had NO stop-loss protection.

        Propagates exceptions instead of swallowing them so callers can surface
        the failure to the user.
        """
        if not broker_symbol:
            raise BrokerError("Bracket order requires a symbol")
        exit_action = "Sell" if action == "Buy" else "Buy"
        bracket = []
        if sl:
            bracket.append({
                "accountSpec": self._account_spec,
                "accountId": self._account_id,
                "action": exit_action,
                "symbol": broker_symbol,
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
                "symbol": broker_symbol,
                "orderQty": qty,
                "orderType": "Limit",
                "price": tp,
                "isAutomated": True,
            })
        if bracket:
            # Propagate errors to caller — no silent swallowing. The caller
            # (place_order) decides how to surface the failure.
            await self._request("POST", "/order/placeoso", json={
                "accountSpec": self._account_spec,
                "accountId": self._account_id,
                "orderType": "Market",
                "action": action,
                "symbol": broker_symbol,
                "orderQty": qty,
                "bracket1": bracket[0] if len(bracket) > 0 else None,
                "bracket2": bracket[1] if len(bracket) > 1 else None,
            })

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
