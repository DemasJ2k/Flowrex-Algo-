"""
Interactive Brokers adapter — Client Portal Web API (REST) mode.

Why REST-only here:
  The Gateway-based `ib_insync` path requires a local process and daily manual
  login, which doesn't fit our cloud deployment. Client Portal is stateless
  and token-authenticated, which matches how we talk to Oanda and Tradovate.

Credential shape (stored in BrokerAccount.credentials_encrypted):
    {
      "account_id":   "DU1234567",
      "consumer_key": "<Client Portal gateway key>",
      "environment":  "paper" | "live",
      # Optional for self-hosted Gateway deployments — if set, we use it
      # instead of the hosted Client Portal endpoint:
      "base_url":     "https://localhost:5000/v1/api",
    }

The hosted Client Portal endpoint at `https://api.ibkr.com/v1/api` requires a
valid session cookie obtained from the IBKR OAuth flow. For first-pass support
we require the user to run the IBKR Client Portal Gateway locally (or in their
own VPS) and paste the resulting URL; this is the same flow used by most
third-party integrations today and is the path IBKR officially supports for
automated trading.

All order routing goes through the bracket-order endpoint so SL/TP are attached
on the broker side — matches our existing agent runtime which expects
broker-side stops to survive restarts.
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

import httpx

from app.services.broker.base import (
    BrokerAdapter, BrokerError,
    AccountInfo, Position, Order, Candle, SymbolInfo, Tick,
    OrderResult, CloseResult, ModifyResult,
)
from app.services.broker.symbol_registry import get_symbol_registry


logger = logging.getLogger("flowrex.ibkr")


_TF_MAP = {
    "M1":  "1min",
    "M5":  "5mins",
    "M15": "15mins",
    "M30": "30mins",
    "H1":  "1h",
    "H4":  "4h",
    "D1":  "1d",
    "W1":  "1w",
    "MN1": "1mo",
}


class InteractiveBrokersAdapter(BrokerAdapter):
    """Interactive Brokers Client Portal REST adapter (paper + live)."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._account_id: str = ""
        self._base_url: str = ""
        self._environment: str = "paper"
        self._semaphore = asyncio.Semaphore(10)  # conservative — IBKR limit 50/sec/account
        self._registry = get_symbol_registry()
        self._contract_cache: dict[str, int] = {}  # symbol -> conid

    @property
    def name(self) -> str:
        return "interactive_brokers"

    def _to_broker(self, symbol: str) -> str:
        return self._registry.to_broker(symbol, "interactive_brokers")

    def _to_canonical(self, broker_symbol: str) -> str:
        return self._registry.to_canonical(broker_symbol, "interactive_brokers")

    # ── Connection ───────────────────────────────────────────────────────

    async def connect(self, credentials: dict) -> bool:
        account_id   = credentials.get("account_id")
        consumer_key = credentials.get("consumer_key")
        environment  = (credentials.get("environment") or "paper").lower()
        base_url     = credentials.get("base_url") or "https://api.ibkr.com/v1/api"

        if not account_id:
            raise BrokerError("Missing 'account_id' for Interactive Brokers")
        if not consumer_key:
            raise BrokerError("Missing 'consumer_key' for Interactive Brokers")
        if environment not in ("paper", "live"):
            raise BrokerError(f"Unknown IBKR environment: {environment}")

        self._account_id = account_id
        self._base_url = base_url.rstrip("/")
        self._environment = environment

        headers = {
            "Authorization": f"Bearer {consumer_key}",
            "User-Agent": "FlowrexAlgo/1.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self._base_url, headers=headers, timeout=20.0, verify=True,
        )

        # Probe auth by hitting the portfolio endpoint. We don't need the
        # payload right now — we just need a non-auth failure.
        try:
            r = await self._request("GET", f"/portfolio/{self._account_id}/summary")
            if not isinstance(r, dict):
                raise BrokerError("Unexpected IBKR response shape")
        except BrokerError:
            await self.disconnect()
            raise
        except Exception as e:
            await self.disconnect()
            raise BrokerError(f"IBKR connection probe failed: {e}") from e

        logger.info(f"IBKR connected: account={self._account_id} env={self._environment}")
        return True

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        self._contract_cache.clear()

    async def _request(self, method: str, path: str, **kwargs):
        if not self._client:
            raise BrokerError("Not connected to Interactive Brokers")
        async with self._semaphore:
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.RequestError as e:
                raise BrokerError(f"IBKR network error: {e}") from e
            if resp.status_code == 401 or resp.status_code == 403:
                raise BrokerError("IBKR auth failed — renew your Client Portal token")
            if resp.status_code >= 500:
                raise BrokerError(f"IBKR server error {resp.status_code}")
            if resp.status_code >= 400:
                raise BrokerError(f"IBKR {resp.status_code}: {resp.text[:200]}")
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                return {}

    # ── Account / positions / orders ─────────────────────────────────────

    async def get_account_info(self) -> AccountInfo:
        try:
            summary = await self._request("GET", f"/portfolio/{self._account_id}/summary")
        except BrokerError:
            return AccountInfo(account_id=self._account_id, server="ibkr")

        def _amt(key: str) -> float:
            node = summary.get(key) or {}
            try:
                return float(node.get("amount", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return AccountInfo(
            balance=_amt("netliquidation"),
            equity=_amt("equitywithloanvalue") or _amt("netliquidation"),
            margin_used=_amt("initmarginreq"),
            margin_available=_amt("availablefunds"),
            currency=(summary.get("netliquidation", {}) or {}).get("currency", "USD") or "USD",
            unrealized_pnl=_amt("unrealizedpnl"),
            account_id=self._account_id,
            server=f"ibkr-{self._environment}",
        )

    async def get_positions(self) -> list[Position]:
        try:
            data = await self._request("GET", f"/portfolio/{self._account_id}/positions/0")
        except BrokerError:
            return []
        positions: list[Position] = []
        for row in data or []:
            size = float(row.get("position") or 0.0)
            if size == 0:
                continue
            sym_ib = row.get("ticker") or row.get("contractDesc") or ""
            canonical = self._to_canonical(sym_ib) if sym_ib else sym_ib
            positions.append(Position(
                id=str(row.get("conid", "")),
                symbol=canonical,
                direction="BUY" if size > 0 else "SELL",
                size=abs(size),
                entry_price=float(row.get("avgCost") or 0.0),
                current_price=float(row.get("mktPrice") or 0.0),
                pnl=float(row.get("unrealizedPnl") or 0.0),
            ))
        return positions

    async def get_orders(self) -> list[Order]:
        try:
            data = await self._request("GET", "/iserver/account/orders")
        except BrokerError:
            return []
        result: list[Order] = []
        for row in data.get("orders", []) if isinstance(data, dict) else []:
            status = (row.get("status") or "").lower()
            if status not in ("presubmitted", "submitted", "preSubmitted"):
                continue
            canonical = self._to_canonical(row.get("ticker") or "")
            result.append(Order(
                id=str(row.get("orderId", "")),
                symbol=canonical,
                direction=(row.get("side") or "").upper(),
                size=float(row.get("remainingQuantity") or 0.0),
                order_type=(row.get("orderType") or "MARKET").upper(),
                price=float(row.get("price") or 0.0),
                status=status,
            ))
        return result

    # ── Market data ──────────────────────────────────────────────────────

    async def _resolve_conid(self, symbol: str) -> Optional[int]:
        """Fuzzy-resolve a canonical symbol to an IBKR contract id (conid)."""
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]
        broker_sym = self._to_broker(symbol)
        try:
            data = await self._request("GET", f"/iserver/secdef/search?symbol={broker_sym}")
        except BrokerError:
            return None
        for row in data or []:
            conid = row.get("conid")
            if conid:
                self._contract_cache[symbol] = int(conid)
                return int(conid)
        return None

    async def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> list[Candle]:
        conid = await self._resolve_conid(symbol)
        if not conid:
            return []
        period = f"{count * 5}min" if timeframe == "M5" else _TF_MAP.get(timeframe, "1d")
        try:
            data = await self._request(
                "GET",
                f"/iserver/marketdata/history?conid={conid}&period={period}"
                f"&bar={_TF_MAP.get(timeframe, '1d')}",
            )
        except BrokerError:
            return []
        candles: list[Candle] = []
        for bar in (data.get("data") or [])[-count:]:
            candles.append(Candle(
                time=int((bar.get("t") or 0) // 1000),
                open=float(bar.get("o") or 0.0),
                high=float(bar.get("h") or 0.0),
                low=float(bar.get("l") or 0.0),
                close=float(bar.get("c") or 0.0),
                volume=int(bar.get("v") or 0),
            ))
        return candles

    async def get_symbols(self) -> list[SymbolInfo]:
        """
        IBKR has hundreds of thousands of instruments — we only expose the
        ones already in our registry so the UI isn't flooded.
        """
        out: list[SymbolInfo] = []
        for canonical in self._registry.get_broker_symbols("interactive_brokers").keys():
            out.append(SymbolInfo(name=canonical))
        return out

    async def get_tick(self, symbol: str) -> Tick:
        conid = await self._resolve_conid(symbol)
        if not conid:
            return Tick(symbol=symbol)
        try:
            data = await self._request(
                "GET",
                f"/iserver/marketdata/snapshot?conids={conid}&fields=31,84,86",
            )
        except BrokerError:
            return Tick(symbol=symbol)
        row = data[0] if isinstance(data, list) and data else {}
        return Tick(
            symbol=symbol,
            bid=float(row.get("84") or 0.0),
            ask=float(row.get("86") or 0.0),
            time=int(datetime.now(timezone.utc).timestamp()),
        )

    # ── Trading ──────────────────────────────────────────────────────────

    async def place_order(
        self, symbol: str, side: str, size: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        sl: Optional[float] = None, tp: Optional[float] = None,
    ) -> OrderResult:
        conid = await self._resolve_conid(symbol)
        if not conid:
            return OrderResult(success=False, message=f"Unknown symbol on IBKR: {symbol}")

        side_up = (side or "").upper()
        ib_side = "BUY" if side_up == "BUY" else "SELL"

        # Native bracket: parent + SL + TP as child orders so broker manages them
        parent_id = f"fx-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        orders = [{
            "acctId":    self._account_id,
            "conid":     conid,
            "orderType": (order_type or "MARKET").upper(),
            "side":      ib_side,
            "quantity":  size,
            "tif":       "GTC",
            "cOID":      parent_id,
            "outsideRTH": False,
            **({"price": price} if price is not None and order_type.upper() != "MARKET" else {}),
        }]
        if sl is not None:
            orders.append({
                "acctId": self._account_id, "conid": conid,
                "orderType": "STP", "side": "SELL" if ib_side == "BUY" else "BUY",
                "quantity": size, "tif": "GTC",
                "parentId": parent_id, "price": sl,
            })
        if tp is not None:
            orders.append({
                "acctId": self._account_id, "conid": conid,
                "orderType": "LMT", "side": "SELL" if ib_side == "BUY" else "BUY",
                "quantity": size, "tif": "GTC",
                "parentId": parent_id, "price": tp,
            })

        try:
            resp = await self._request(
                "POST",
                f"/iserver/account/{self._account_id}/orders",
                json={"orders": orders},
            )
        except BrokerError as e:
            return OrderResult(success=False, message=e.message)

        # IBKR may return a list of replies asking to confirm the order — auto-accept.
        if isinstance(resp, list):
            for reply in resp:
                reply_id = reply.get("id")
                if reply_id:
                    try:
                        await self._request(
                            "POST", f"/iserver/reply/{reply_id}", json={"confirmed": True},
                        )
                    except BrokerError:
                        break

        order_id = parent_id
        return OrderResult(
            success=True, order_id=order_id, message="IBKR order submitted",
            requested_price=price or 0.0,
        )

    async def close_position(self, position_id: str) -> CloseResult:
        """
        `position_id` is the conid string — matches what get_positions() emits.
        We flatten by placing an opposing market order for the current size.
        """
        try:
            positions = await self.get_positions()
        except Exception:
            positions = []
        matching = next((p for p in positions if p.id == str(position_id)), None)
        if not matching:
            return CloseResult(success=False, message=f"No open position {position_id}")

        opposite = "SELL" if matching.direction == "BUY" else "BUY"
        r = await self.place_order(
            symbol=matching.symbol, side=opposite, size=matching.size,
            order_type="MARKET",
        )
        return CloseResult(success=r.success, pnl=matching.pnl, message=r.message)

    async def modify_order(
        self, order_id: str,
        sl: Optional[float] = None, tp: Optional[float] = None,
    ) -> ModifyResult:
        payload = {}
        if sl is not None:
            payload["auxPrice"] = sl
        if tp is not None:
            payload["price"] = tp
        if not payload:
            return ModifyResult(success=False, message="No changes supplied")
        try:
            await self._request(
                "POST",
                f"/iserver/account/{self._account_id}/order/{order_id}",
                json=payload,
            )
            return ModifyResult(success=True, message="Order modified")
        except BrokerError as e:
            return ModifyResult(success=False, message=e.message)
