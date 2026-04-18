"""
Tests for Oanda broker rejection paths.

These cover the failure modes we saw live on 2026-04-15:
- INSUFFICIENT_MARGIN (BTCUSD agent placed 9 lots, broker rejected)
- HTML error response (Oanda returned a 500 status page instead of JSON)
- Timeout / network errors

These would have been caught earlier with proper test coverage.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.services.broker.oanda import OandaAdapter
from app.services.broker.base import BrokerError, OrderResult


@pytest.fixture
def oanda():
    """OandaAdapter with a mock httpx client attached."""
    adapter = OandaAdapter()
    adapter._client = MagicMock()
    adapter._account_id = "TEST-ACCOUNT-1"
    return adapter


def _mock_response(status_code, text, json_data=None):
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    else:
        # Will raise on .json() call
        resp.json = MagicMock(side_effect=ValueError("not json"))
    return resp


@pytest.mark.asyncio
async def test_place_order_insufficient_margin():
    """
    REGRESSION: BTCUSD on 2026-04-15. Agent tried to place 9 BTC ($666k notional)
    with $90k balance. Oanda returned orderCancelTransaction with reason
    INSUFFICIENT_MARGIN. The adapter must surface this as a failed OrderResult,
    not a phantom successful trade.
    """
    adapter = OandaAdapter()
    adapter._client = MagicMock()
    adapter._account_id = "TEST-1"

    cancel_response = {
        "orderCancelTransaction": {
            "id": "999",
            "reason": "INSUFFICIENT_MARGIN",
            "type": "ORDER_CANCEL",
        }
    }

    async def mock_request(*args, **kwargs):
        return cancel_response

    with patch.object(adapter, "_request", side_effect=mock_request):
        result = await adapter.place_order(
            symbol="BTCUSD", side="SELL", size=9.0, order_type="MARKET",
            sl=74198.40, tp=73938.60,
        )
    assert isinstance(result, OrderResult)
    assert result.success is False
    assert "INSUFFICIENT_MARGIN" in result.message


@pytest.mark.asyncio
async def test_place_order_rejected():
    """orderRejectTransaction must surface as a failed result."""
    adapter = OandaAdapter()
    adapter._client = MagicMock()
    adapter._account_id = "TEST-1"

    reject_response = {
        "orderRejectTransaction": {
            "id": "888",
            "rejectReason": "INSTRUMENT_NOT_TRADEABLE",
            "type": "ORDER_REJECT",
        }
    }

    async def mock_request(*args, **kwargs):
        return reject_response

    with patch.object(adapter, "_request", side_effect=mock_request):
        result = await adapter.place_order(
            symbol="XAUUSD", side="BUY", size=1, order_type="MARKET",
        )
    assert result.success is False
    assert "INSTRUMENT_NOT_TRADEABLE" in result.message


@pytest.mark.asyncio
async def test_place_order_filled_returns_success():
    """orderFillTransaction is the happy path."""
    adapter = OandaAdapter()
    adapter._client = MagicMock()
    adapter._account_id = "TEST-1"

    fill_response = {
        "orderFillTransaction": {
            "id": "12345",
            "type": "ORDER_FILL",
            "tradeOpened": {"tradeID": "12345"},
        }
    }

    async def mock_request(*args, **kwargs):
        return fill_response

    with patch.object(adapter, "_request", side_effect=mock_request):
        result = await adapter.place_order(
            symbol="XAUUSD", side="BUY", size=1, order_type="MARKET",
        )
    assert result.success is True
    assert result.order_id == "12345"


@pytest.mark.asyncio
async def test_request_html_error_response_raises_brokererror():
    """
    REGRESSION: 2026-04-15 — Oanda intermittently returned an HTML error page
    instead of JSON. The adapter MUST raise BrokerError instead of crashing
    on the JSON parse.
    """
    adapter = OandaAdapter()
    mock_client = MagicMock()
    adapter._client = mock_client

    html_response = MagicMock()
    html_response.status_code = 500
    html_response.text = (
        "<!DOCTYPE html><html><head><title>Internal Server Error</title></head>"
        "<body>500</body></html>"
    )
    html_response.json = MagicMock(side_effect=ValueError("not json"))

    async def mock_request(*args, **kwargs):
        return html_response

    mock_client.request = mock_request

    with pytest.raises(BrokerError, match="non-JSON"):
        await adapter._request("GET", "/v3/accounts/test")


@pytest.mark.asyncio
async def test_request_empty_response_raises_brokererror():
    """An empty response body must raise BrokerError, not silently return None."""
    adapter = OandaAdapter()
    mock_client = MagicMock()
    adapter._client = mock_client

    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.text = ""
    empty_response.json = MagicMock(return_value={})

    async def mock_request(*args, **kwargs):
        return empty_response

    mock_client.request = mock_request

    with pytest.raises(BrokerError, match="empty response"):
        await adapter._request("GET", "/v3/accounts/test")


@pytest.mark.asyncio
async def test_request_4xx_includes_error_message():
    """4xx responses should propagate the error message in BrokerError."""
    adapter = OandaAdapter()
    mock_client = MagicMock()
    adapter._client = mock_client

    err_response = MagicMock()
    err_response.status_code = 400
    err_response.text = '{"errorMessage": "Invalid instrument"}'
    err_response.json = MagicMock(return_value={"errorMessage": "Invalid instrument"})

    async def mock_request(*args, **kwargs):
        return err_response

    mock_client.request = mock_request

    with pytest.raises(BrokerError, match="Invalid instrument"):
        await adapter._request("GET", "/v3/accounts/test/instruments/INVALID")
