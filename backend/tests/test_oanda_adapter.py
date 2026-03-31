"""Unit tests for the Oanda adapter with mocked HTTP responses."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.broker.oanda import OandaAdapter, to_oanda, from_oanda
from app.services.broker.base import BrokerError


# ── Instrument name mapping ────────────────────────────────────────────


def test_to_oanda_known_symbols():
    assert to_oanda("XAUUSD") == "XAU_USD"
    assert to_oanda("BTCUSD") == "BTC_USD"
    assert to_oanda("US30") == "US30_USD"
    assert to_oanda("EURUSD") == "EUR_USD"
    assert to_oanda("NAS100") == "NAS100_USD"


def test_from_oanda_known_symbols():
    assert from_oanda("XAU_USD") == "XAUUSD"
    assert from_oanda("BTC_USD") == "BTCUSD"
    assert from_oanda("US30_USD") == "US30"
    assert from_oanda("EUR_USD") == "EURUSD"


def test_to_oanda_fallback():
    """Unknown symbol returned as-is; auto-discovery maps it on connect."""
    assert to_oanda("CHFJPY") == "CHFJPY"


def test_from_oanda_fallback():
    """Unknown instrument just strips underscores."""
    assert from_oanda("CHF_JPY") == "CHFJPY"


def test_roundtrip():
    """to_oanda -> from_oanda returns original for known symbols."""
    for sym in ["XAUUSD", "BTCUSD", "US30", "EURUSD", "GBPJPY"]:
        assert from_oanda(to_oanda(sym)) == sym


# ── Candle conversion ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_candle_conversion():
    adapter = OandaAdapter()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candles": [
            {
                "complete": True,
                "time": "2024-01-15T10:00:00.000000000Z",
                "mid": {"o": "2050.50", "h": "2055.00", "l": "2048.00", "c": "2053.25"},
                "volume": 1234,
            },
            {
                "complete": False,  # Incomplete candle — should be skipped
                "time": "2024-01-15T10:05:00.000000000Z",
                "mid": {"o": "2053.25", "h": "2054.00", "l": "2052.00", "c": "2053.50"},
                "volume": 500,
            },
        ]
    }

    adapter._client = AsyncMock()
    adapter._client.request = AsyncMock(return_value=mock_response)
    adapter._account_id = "test-account"

    candles = await adapter.get_candles("XAUUSD", "M5", 100)
    assert len(candles) == 1  # Only complete candles
    assert candles[0].open == 2050.50
    assert candles[0].high == 2055.00
    assert candles[0].close == 2053.25
    assert candles[0].volume == 1234
    assert candles[0].time > 0


# ── Account info ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_info():
    adapter = OandaAdapter()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "account": {
            "balance": "10000.00",
            "NAV": "10500.00",
            "marginUsed": "500.00",
            "currency": "USD",
            "unrealizedPL": "500.00",
        }
    }

    adapter._client = AsyncMock()
    adapter._client.request = AsyncMock(return_value=mock_response)
    adapter._account_id = "test-account"

    info = await adapter.get_account_info()
    assert info.balance == 10000.0
    assert info.equity == 10500.0
    assert info.margin_used == 500.0
    assert info.unrealized_pnl == 500.0


# ── Order placement ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_place_market_order():
    adapter = OandaAdapter()
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {
        "orderFillTransaction": {"id": "12345", "type": "ORDER_FILL"}
    }

    adapter._client = AsyncMock()
    adapter._client.request = AsyncMock(return_value=mock_response)
    adapter._account_id = "test-account"

    result = await adapter.place_order("XAUUSD", "BUY", 0.1, "MARKET", sl=2040.0, tp=2060.0)
    assert result.success is True
    assert result.order_id == "12345"

    # Verify the request body
    call_args = adapter._client.request.call_args
    body = call_args.kwargs.get("json", {})
    assert body["order"]["instrument"] == "XAU_USD"
    assert body["order"]["units"] == "0.1"
    assert "stopLossOnFill" in body["order"]
    assert "takeProfitOnFill" in body["order"]


# ── Error handling ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_failure():
    adapter = OandaAdapter()
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.json.return_value = {"errorMessage": "Invalid API key"}

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_response)
    mock_client.aclose = AsyncMock()

    with patch("app.services.broker.oanda.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BrokerError, match="Invalid API key"):
            await adapter.connect({"api_key": "bad", "account_id": "123", "practice": True})
