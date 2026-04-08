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


# ── Standalone helpers (kept for backward compat with tests) ───────────

def to_oanda(symbol: str) -> str:
    """Convert canonical symbol to Oanda format via registry."""
    return get_symbol_registry().to_broker(symbol, "oanda")


def from_oanda(instrument: str) -> str:
    """Convert Oanda instrument to canonical format via registry."""
    return get_symbol_registry().to_canonical(instrument, "oanda")


# ── Timeframe mapping ─────────────────────────────────────────────────

_TF_MAP = {
    "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30",
    "H1": "H1", "H4": "H4", "D1": "D", "W1": "W", "MN1": "M",
}


class OandaAdapter(BrokerAdapter):
    """Oanda v20 REST API adapter."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._account_id: str = ""
        self._semaphore = asyncio.Semaphore(25)  # rate limit
        self._registry = get_symbol_registry()

    @property
    def name(self) -> str:
        return "oanda"

    def _to_broker(self, symbol: str) -> str:
        return self._registry.to_broker(symbol, "oanda")

    def _to_canonical(self, instrument: str) -> str:
        return self._registry.to_canonical(instrument, "oanda")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make a rate-limited request to Oanda API."""
        if not self._client:
            raise BrokerError("Not connected to Oanda")
        async with self._semaphore:
            for attempt in range(2):  # Retry once on transport errors
                try:
                    resp = await self._client.request(method, path, **kwargs)
                    if not resp.text or not resp.text.strip():
                        raise BrokerError(f"Oanda returned empty response for {method} {path}")
                    try:
                        data = resp.json()
                    except Exception:
                        raise BrokerError(f"Oanda returned non-JSON: {resp.text[:200]}")
                    if resp.status_code >= 400:
                        msg = data.get("errorMessage", str(data))
                        raise BrokerError(f"Oanda API error: {msg}", code=str(resp.status_code))
                    return data
                except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout) as e:
                    if attempt == 0:
                        continue  # Retry on transport errors
                    raise BrokerError(f"Oanda connection error: {e}")
                except httpx.HTTPError as e:
                    raise BrokerError(f"Oanda HTTP error: {e}")

    # ── Connection ─────────────────────────────────────────────────────

    async def connect(self, credentials: dict) -> bool:
        api_key = credentials.get("api_key", "")
        self._account_id = credentials.get("account_id", "")
        practice = credentials.get("practice", True)

        # Auto-fill from env if not provided
        if not api_key or not self._account_id:
            import os
            api_key = api_key or os.getenv("OANDA_API_KEY", "")
            self._account_id = self._account_id or os.getenv("OANDA_ACCOUNT_ID", "")
            practice_env = os.getenv("OANDA_PRACTICE", "true")
            practice = practice_env.lower() == "true" if isinstance(practice_env, str) else practice

        if not api_key or not self._account_id:
            raise BrokerError("Oanda requires api_key and account_id. Set OANDA_API_KEY and OANDA_ACCOUNT_ID in .env or provide in credentials.")

        base_url = (
            "https://api-fxpractice.oanda.com"
            if practice
            else "https://api-fxtrade.oanda.com"
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60),
        )

        # Verify connection and auto-discover symbols
        try:
            await self._request("GET", f"/v3/accounts/{self._account_id}/summary")
            await self._discover_symbols()
            return True
        except BrokerError:
            await self.disconnect()
            raise

    async def _discover_symbols(self):
        """Fetch instrument list and auto-discover symbol mappings."""
        try:
            data = await self._request("GET", f"/v3/accounts/{self._account_id}/instruments")
            broker_symbols = [inst.get("name", "") for inst in data.get("instruments", [])]
            self._registry.auto_discover("oanda", broker_symbols)
        except BrokerError:
            pass  # Non-fatal: proceed with defaults

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Account ────────────────────────────────────────────────────────

    async def get_account_info(self) -> AccountInfo:
        data = await self._request("GET", f"/v3/accounts/{self._account_id}/summary")
        acct = data.get("account", {})
        return AccountInfo(
            balance=float(acct.get("balance", 0)),
            equity=float(acct.get("NAV", 0)),
            margin_used=float(acct.get("marginUsed", 0)),
            currency=acct.get("currency", "USD"),
            unrealized_pnl=float(acct.get("unrealizedPL", 0)),
            account_id=str(self._account_id or ""),
            server="practice" if self._client and "practice" in str(self._client.base_url) else "live",
        )

    # ── Positions ──────────────────────────────────────────────────────

    async def get_positions(self) -> list[Position]:
        data = await self._request("GET", f"/v3/accounts/{self._account_id}/openPositions")
        positions = []

        # Fetch open trades to get SL/TP attached to individual trades
        trades_data = await self._request("GET", f"/v3/accounts/{self._account_id}/openTrades")
        trades_by_instrument: dict[str, list[dict]] = {}
        for trade in trades_data.get("trades", []):
            inst = trade.get("instrument", "")
            trades_by_instrument.setdefault(inst, []).append(trade)

        for pos in data.get("positions", []):
            instrument = pos.get("instrument", "")
            symbol = self._to_canonical(instrument)
            for side_key, direction in [("long", "BUY"), ("short", "SELL")]:
                side = pos.get(side_key, {})
                units = float(side.get("units", 0))
                if units == 0:
                    continue

                # Extract SL/TP from the first matching trade for this position side
                sl_price = None
                tp_price = None
                for trade in trades_by_instrument.get(instrument, []):
                    trade_units = float(trade.get("currentUnits", 0))
                    if (direction == "BUY" and trade_units > 0) or (direction == "SELL" and trade_units < 0):
                        if "stopLossOrder" in trade:
                            sl_price = float(trade["stopLossOrder"].get("price", 0))
                        if "takeProfitOrder" in trade:
                            tp_price = float(trade["takeProfitOrder"].get("price", 0))
                        break  # Use first matching trade's SL/TP

                positions.append(Position(
                    id=f"{instrument}:{side_key}",
                    symbol=symbol,
                    direction=direction,
                    size=abs(units),
                    entry_price=float(side.get("averagePrice", 0)),
                    current_price=0.0,
                    pnl=float(side.get("unrealizedPL", 0)),
                    sl=sl_price,
                    tp=tp_price,
                ))
        return positions

    # ── Orders ─────────────────────────────────────────────────────────

    async def get_orders(self) -> list[Order]:
        data = await self._request("GET", f"/v3/accounts/{self._account_id}/pendingOrders")
        orders = []
        for o in data.get("orders", []):
            instrument = o.get("instrument", "")
            units = float(o.get("units", 0))
            orders.append(Order(
                id=o.get("id", ""),
                symbol=self._to_canonical(instrument),
                direction="BUY" if units > 0 else "SELL",
                size=abs(units),
                order_type=o.get("type", "").replace("_ORDER", ""),
                price=float(o.get("price", 0)),
                status="PENDING",
                sl=float(o["stopLossOnFill"]["price"]) if "stopLossOnFill" in o else None,
                tp=float(o["takeProfitOnFill"]["price"]) if "takeProfitOnFill" in o else None,
            ))
        return orders

    # ── Candles ────────────────────────────────────────────────────────

    async def get_candles(self, symbol: str, timeframe: str = "M5", count: int = 200) -> list[Candle]:
        instrument = self._to_broker(symbol)
        granularity = _TF_MAP.get(timeframe, timeframe)
        data = await self._request(
            "GET",
            f"/v3/instruments/{instrument}/candles",
            params={"granularity": granularity, "count": count, "price": "M"},
        )
        candles = []
        for c in data.get("candles", []):
            if not c.get("complete", False):
                continue
            mid = c.get("mid", {})
            time_str = c.get("time", "")
            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except (ValueError, AttributeError):
                ts = 0
            candles.append(Candle(
                time=ts,
                open=float(mid.get("o", 0)),
                high=float(mid.get("h", 0)),
                low=float(mid.get("l", 0)),
                close=float(mid.get("c", 0)),
                volume=int(c.get("volume", 0)),
            ))
        return candles

    # ── Symbols ────────────────────────────────────────────────────────

    async def get_symbols(self) -> list[SymbolInfo]:
        data = await self._request("GET", f"/v3/accounts/{self._account_id}/instruments")
        symbols = []
        for inst in data.get("instruments", []):
            pip_loc = int(inst.get("pipLocation", -4))
            pip_size = 10 ** pip_loc
            symbols.append(SymbolInfo(
                name=self._to_canonical(inst.get("name", "")),
                min_lot=float(inst.get("minimumTradeSize", 1)),
                lot_step=1.0,
                pip_size=pip_size,
                pip_value=1.0,
                digits=abs(pip_loc) + 1 if pip_loc < 0 else 0,
            ))
        return symbols

    # ── Orders ─────────────────────────────────────────────────────────

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
        instrument = self._to_broker(symbol)
        units = size if side.upper() == "BUY" else -size

        # Round prices to Oanda-required precision to avoid "more precision than allowed"
        from app.services.agent.instrument_specs import get_oanda_price_decimals
        decimals = get_oanda_price_decimals(symbol)

        def _fmt(v: float) -> str:
            return f"{round(v, decimals):.{decimals}f}"

        if order_type.upper() == "MARKET":
            body = {
                "order": {
                    "type": "MARKET",
                    "instrument": instrument,
                    "units": str(units),
                }
            }
        else:
            body = {
                "order": {
                    "type": "LIMIT",
                    "instrument": instrument,
                    "units": str(units),
                    "price": _fmt(price) if price is not None else str(price),
                }
            }

        if sl is not None:
            body["order"]["stopLossOnFill"] = {"price": _fmt(sl)}
        if tp is not None:
            body["order"]["takeProfitOnFill"] = {"price": _fmt(tp)}

        data = await self._request("POST", f"/v3/accounts/{self._account_id}/orders", json=body)

        # Check for cancellation FIRST — Oanda may create then immediately cancel
        if "orderCancelTransaction" in data:
            cancel = data["orderCancelTransaction"]
            reason = cancel.get("reason", "unknown")
            return OrderResult(success=False, order_id="", message=f"Order cancelled by broker: {reason}")

        if "orderRejectTransaction" in data:
            reject = data["orderRejectTransaction"]
            reason = reject.get("rejectReason", reject.get("reason", "unknown"))
            return OrderResult(success=False, order_id="", message=f"Order rejected: {reason}")

        if "orderFillTransaction" in data:
            txn = data["orderFillTransaction"]
            return OrderResult(success=True, order_id=txn.get("id", ""), message="Order filled")
        elif "orderCreateTransaction" in data:
            # Order created but not yet filled — check if it was also cancelled in same response
            txn = data["orderCreateTransaction"]
            return OrderResult(success=True, order_id=txn.get("id", ""), message="Order created")
        else:
            return OrderResult(success=False, message=f"Unexpected response: {str(data)[:300]}")

    # ── Close Position ─────────────────────────────────────────────────

    async def close_position(self, position_id: str) -> CloseResult:
        parts = position_id.split(":")
        if len(parts) != 2:
            return CloseResult(success=False, message=f"Invalid position ID format: {position_id}")

        instrument, side = parts[0], parts[1]
        # Ensure instrument is in broker format (e.g. "US30_USD" not "US30")
        # Avoid double-convert: if it already contains "_" it's likely broker format
        if "_" not in instrument:
            instrument = self._to_broker(instrument)
        if side == "long":
            body = {"longUnits": "ALL"}
        else:
            body = {"shortUnits": "ALL"}

        try:
            data = await self._request(
                "PUT",
                f"/v3/accounts/{self._account_id}/positions/{instrument}/close",
                json=body,
            )
            pnl = 0.0
            for txn_key in ["longOrderFillTransaction", "shortOrderFillTransaction"]:
                if txn_key in data:
                    pnl = float(data[txn_key].get("pl", 0))
            return CloseResult(success=True, pnl=pnl, message="Position closed")
        except BrokerError as e:
            return CloseResult(success=False, message=e.message)

    # ── Modify Order ───────────────────────────────────────────────────

    async def modify_order(
        self, order_id: str, sl: Optional[float] = None, tp: Optional[float] = None
    ) -> ModifyResult:
        body: dict = {"order": {}}
        if sl is not None:
            body["order"]["stopLossOnFill"] = {"price": str(sl)}
        if tp is not None:
            body["order"]["takeProfitOnFill"] = {"price": str(tp)}

        try:
            await self._request(
                "PUT",
                f"/v3/accounts/{self._account_id}/orders/{order_id}",
                json=body,
            )
            return ModifyResult(success=True, message="Order modified")
        except BrokerError as e:
            return ModifyResult(success=False, message=e.message)

    # ── Tick ───────────────────────────────────────────────────────────

    async def get_tick(self, symbol: str) -> Tick:
        instrument = self._to_broker(symbol)
        data = await self._request(
            "GET",
            f"/v3/accounts/{self._account_id}/pricing",
            params={"instruments": instrument},
        )
        prices = data.get("prices", [])
        if not prices:
            raise BrokerError(f"No pricing data for {symbol}")
        p = prices[0]
        return Tick(
            symbol=symbol,
            bid=float(p.get("bids", [{}])[0].get("price", 0)),
            ask=float(p.get("asks", [{}])[0].get("price", 0)),
            time=int(datetime.now(timezone.utc).timestamp()),
        )
