"""
Databento market data adapter — fetches OHLCV and tick data via REST API.

Supports CME futures (ES, NQ, YM/US30). Does NOT support crypto or forex.
Uses hist.databento.com/v0 for historical data.

Symbol mapping:
  US30  → YM.FUT (CBOT mini Dow)
  ES    → ES.FUT (CME E-mini S&P)
  NAS100 → NQ.FUT (CME E-mini Nasdaq)
"""
import time
import httpx
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass


# Databento dataset for CME Globex futures
DATASET = "GLBX.MDP3"

# Map our symbols to Databento instrument IDs
SYMBOL_MAP = {
    "US30": "YM.FUT",
    "ES": "ES.FUT",
    "NAS100": "NQ.FUT",
    "SPX500": "ES.FUT",
}

# Timeframe to Databento schema + bar size
TIMEFRAME_MAP = {
    "M1": ("ohlcv-1m", 60),
    "M5": ("ohlcv-5m", 300),
    "M15": ("ohlcv-15m", 900),
    "M30": ("ohlcv-30m", 1800),
    "H1": ("ohlcv-1h", 3600),
    "H4": ("ohlcv-4h", 14400),
    "D1": ("ohlcv-1d", 86400),
}

BASE_URL = "https://hist.databento.com/v0"


@dataclass
class DatabentoCandle:
    time: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0


@dataclass
class DatabentoTick:
    time: int = 0
    price: float = 0.0
    size: int = 0
    side: str = ""  # "buy" or "sell"


class DatabentoAdapter:
    """Fetch market data from Databento Historical API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=BASE_URL,
            auth=(api_key, ""),
            timeout=30.0,
        )
        self._async_client: Optional[httpx.AsyncClient] = None

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                base_url=BASE_URL,
                auth=(self.api_key, ""),
                timeout=30.0,
            )
        return self._async_client

    def _resolve_symbol(self, symbol: str) -> str:
        mapped = SYMBOL_MAP.get(symbol.upper())
        if not mapped:
            raise ValueError(
                f"Symbol '{symbol}' not available on Databento. "
                f"Supported: {list(SYMBOL_MAP.keys())}"
            )
        return mapped

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "M5",
        count: int = 200,
    ) -> list[DatabentoCandle]:
        """Fetch OHLCV candles from Databento."""
        db_symbol = self._resolve_symbol(symbol)
        schema_info = TIMEFRAME_MAP.get(timeframe)
        if not schema_info:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        schema, bar_seconds = schema_info

        # Calculate start time based on count
        end = datetime.now(timezone.utc)
        # Add buffer for weekends/holidays (3x to account for non-trading days)
        start = end - timedelta(seconds=bar_seconds * count * 3)

        params = {
            "dataset": DATASET,
            "symbols": db_symbol,
            "schema": schema,
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "encoding": "json",
            "limit": count,
            "sort_order": "desc",  # newest first, then we reverse
        }

        client = self._get_async_client()
        try:
            resp = await client.get("/timeseries/get_range", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise ValueError("Invalid Databento API key")
            elif e.response.status_code == 422:
                raise ValueError(f"Invalid request parameters: {e.response.text}")
            raise

        data = resp.json()
        candles = []

        # Databento returns NDJSON or array depending on encoding
        records = data if isinstance(data, list) else [data]
        for record in records:
            if not isinstance(record, dict):
                continue
            # Databento OHLCV fields
            ts = record.get("ts_event", record.get("hd", {}).get("ts_event", 0))
            # Convert nanosecond timestamp to seconds
            if ts > 1e15:  # nanoseconds
                ts = int(ts / 1e9)
            elif ts > 1e12:  # microseconds
                ts = int(ts / 1e6)

            candles.append(DatabentoCandle(
                time=int(ts),
                open=float(record.get("open", 0)) / 1e9 if record.get("open", 0) > 1e6 else float(record.get("open", 0)),
                high=float(record.get("high", 0)) / 1e9 if record.get("high", 0) > 1e6 else float(record.get("high", 0)),
                low=float(record.get("low", 0)) / 1e9 if record.get("low", 0) > 1e6 else float(record.get("low", 0)),
                close=float(record.get("close", 0)) / 1e9 if record.get("close", 0) > 1e6 else float(record.get("close", 0)),
                volume=int(record.get("volume", 0)),
            ))

        # Sort by time ascending (oldest first) and take last `count`
        candles.sort(key=lambda c: c.time)
        return candles[-count:]

    async def get_ticks(
        self,
        symbol: str,
        count: int = 500,
        seconds_back: int = 300,
    ) -> list[DatabentoTick]:
        """Fetch tick/trade data from Databento."""
        db_symbol = self._resolve_symbol(symbol)

        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=seconds_back)

        params = {
            "dataset": DATASET,
            "symbols": db_symbol,
            "schema": "trades",
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "encoding": "json",
            "limit": count,
        }

        client = self._get_async_client()
        resp = await client.get("/timeseries/get_range", params=params)
        resp.raise_for_status()

        data = resp.json()
        ticks = []
        records = data if isinstance(data, list) else [data]

        for record in records:
            if not isinstance(record, dict):
                continue
            ts = record.get("ts_event", 0)
            if ts > 1e15:
                ts = int(ts / 1e9)
            elif ts > 1e12:
                ts = int(ts / 1e6)

            price = float(record.get("price", 0))
            if price > 1e6:
                price = price / 1e9  # Databento uses fixed-point

            ticks.append(DatabentoTick(
                time=int(ts),
                price=price,
                size=int(record.get("size", record.get("quantity", 0))),
                side=record.get("side", ""),
            ))

        return ticks

    async def test_connection(self) -> dict:
        """Test API key validity."""
        client = self._get_async_client()
        try:
            resp = await client.get("/metadata.list_datasets")
            if resp.status_code == 200:
                return {"status": "ok", "message": "Databento connection successful"}
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_supported_symbols(self) -> list[str]:
        """Return symbols available through Databento."""
        return list(SYMBOL_MAP.keys())

    async def close(self):
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
