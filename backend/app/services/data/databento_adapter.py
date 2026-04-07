"""
Databento market data adapter — fetches OHLCV and tick data via REST API.

Supports CME futures (ES, NQ, YM/US30). Does NOT support crypto or forex.
Uses hist.databento.com/v0 with dot-notation endpoints.

Key findings from API testing:
  - Endpoint: timeseries.get_range (dot, not slash)
  - Prices are fixed-point: divide by 1e9
  - Timestamps are nanoseconds: divide by 1e9
  - Available OHLCV schemas: ohlcv-1s, ohlcv-1m, ohlcv-1h, ohlcv-1d (NO 5m/15m/30m)
  - Symbol format: YMM6 (specific contract) or YM.v.0 (continuous)
  - Encoding: csv works reliably (json returns binary)
"""
import httpx
import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass

DATASET = "GLBX.MDP3"

# Map our symbols to Databento continuous front-month contracts
SYMBOL_MAP = {
    "US30": "YM.v.0",
    "ES": "ES.v.0",
    "NAS100": "NQ.v.0",
    "SPX500": "ES.v.0",
}

# Map timeframes to available Databento schemas
# Databento only has 1s, 1m, 1h, 1d — we map closest
TIMEFRAME_MAP = {
    "M1": ("ohlcv-1m", 60),
    "M5": ("ohlcv-1m", 60),        # Use 1m, aggregate 5 bars client-side
    "M15": ("ohlcv-1m", 60),       # Use 1m, aggregate 15 bars
    "M30": ("ohlcv-1m", 60),       # Use 1m, aggregate 30 bars
    "H1": ("ohlcv-1h", 3600),
    "H4": ("ohlcv-1h", 3600),      # Use 1h, aggregate 4 bars
    "D1": ("ohlcv-1d", 86400),
}

BASE_URL = "https://hist.databento.com/v0"
FIXED_POINT_SCALE = 1e9  # Databento uses fixed-point prices


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
    side: str = ""


def _aggregate_candles(candles: list[DatabentoCandle], factor: int) -> list[DatabentoCandle]:
    """Aggregate N 1-minute candles into larger bars (e.g., 5 for M5)."""
    if factor <= 1:
        return candles
    result = []
    for i in range(0, len(candles), factor):
        chunk = candles[i:i + factor]
        if not chunk:
            break
        result.append(DatabentoCandle(
            time=chunk[0].time,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
            volume=sum(c.volume for c in chunk),
        ))
    return result


class DatabentoAdapter:
    """Fetch market data from Databento Historical API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._async_client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
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

    def _parse_price(self, val) -> float:
        """Convert Databento fixed-point price to float."""
        v = int(val)
        if abs(v) > 1e6:
            return v / FIXED_POINT_SCALE
        return float(v)

    def _parse_timestamp(self, val) -> int:
        """Convert Databento nanosecond timestamp to Unix seconds."""
        v = int(val)
        if v > 1e15:
            return int(v / 1e9)
        elif v > 1e12:
            return int(v / 1e6)
        return v

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "M5",
        count: int = 200,
    ) -> list[DatabentoCandle]:
        """Fetch OHLCV candles from Databento."""
        db_symbol = self._resolve_symbol(symbol)
        tf_info = TIMEFRAME_MAP.get(timeframe)
        if not tf_info:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        schema, bar_seconds = tf_info

        # Determine aggregation factor
        agg_factors = {"M5": 5, "M15": 15, "M30": 30, "H4": 4}
        agg_factor = agg_factors.get(timeframe, 1)

        # Need more raw bars if aggregating
        raw_count = count * agg_factor

        # Calculate time range
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=bar_seconds * raw_count * 3)  # 3x buffer for non-trading hours

        params = {
            "dataset": DATASET,
            "symbols": db_symbol,
            "schema": schema,
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "encoding": "csv",
            "limit": raw_count,
        }

        client = self._get_client()
        try:
            resp = await client.get("/timeseries.get_range", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise ValueError("Invalid Databento API key")
            raise ValueError(f"Databento API error: {e.response.status_code} {e.response.text[:200]}")

        # Parse CSV response
        candles = []
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            if not row.get("ts_event"):
                continue
            candles.append(DatabentoCandle(
                time=self._parse_timestamp(row["ts_event"]),
                open=self._parse_price(row["open"]),
                high=self._parse_price(row["high"]),
                low=self._parse_price(row["low"]),
                close=self._parse_price(row["close"]),
                volume=int(row.get("volume", 0)),
            ))

        # Sort ascending
        candles.sort(key=lambda c: c.time)

        # Aggregate if needed (e.g., 1m → 5m)
        if agg_factor > 1:
            candles = _aggregate_candles(candles, agg_factor)

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
            "encoding": "csv",
            "limit": count,
        }

        client = self._get_client()
        resp = await client.get("/timeseries.get_range", params=params)
        resp.raise_for_status()

        ticks = []
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            if not row.get("ts_event"):
                continue
            side_code = row.get("side", "")
            side = "buy" if side_code == "A" else "sell" if side_code == "B" else ""
            ticks.append(DatabentoTick(
                time=self._parse_timestamp(row["ts_event"]),
                price=self._parse_price(row["price"]),
                size=int(row.get("size", 0)),
                side=side,
            ))

        return ticks

    async def test_connection(self) -> dict:
        """Test API key validity."""
        client = self._get_client()
        try:
            resp = await client.get("/metadata.list_datasets")
            if resp.status_code == 200:
                datasets = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
                has_glbx = "GLBX.MDP3" in str(datasets)
                return {
                    "status": "ok",
                    "message": f"Databento connected. CME Globex: {'available' if has_glbx else 'not found'}",
                }
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_supported_symbols(self) -> list[str]:
        return list(SYMBOL_MAP.keys())

    async def close(self):
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
