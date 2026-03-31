"""Unit tests for WebSocket ConnectionManager + endpoint."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core.websocket import ConnectionManager


@pytest.fixture
def manager():
    return ConnectionManager()


def _mock_ws(user_id=1):
    """Create a mock WebSocket."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.accept = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_connect_tracks_connection(manager):
    ws = _mock_ws()
    await manager.connect(ws, user_id=1)
    assert manager.get_connection_count() == 1
    ws.accept.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_removes_connection(manager):
    ws = _mock_ws()
    await manager.connect(ws, user_id=1)
    manager.disconnect(ws)
    assert manager.get_connection_count() == 0


@pytest.mark.asyncio
async def test_subscribe_adds_to_channel(manager):
    ws = _mock_ws()
    await manager.connect(ws, user_id=1)
    manager.subscribe(ws, "price:XAUUSD")
    assert manager.get_subscriber_count("price:XAUUSD") == 1


@pytest.mark.asyncio
async def test_unsubscribe_removes_from_channel(manager):
    ws = _mock_ws()
    await manager.connect(ws, user_id=1)
    manager.subscribe(ws, "price:XAUUSD")
    manager.unsubscribe(ws, "price:XAUUSD")
    assert manager.get_subscriber_count("price:XAUUSD") == 0


@pytest.mark.asyncio
async def test_broadcast_sends_to_subscribers(manager):
    ws1 = _mock_ws()
    ws2 = _mock_ws()
    ws3 = _mock_ws()

    await manager.connect(ws1, user_id=1)
    await manager.connect(ws2, user_id=2)
    await manager.connect(ws3, user_id=3)

    manager.subscribe(ws1, "price:XAUUSD")
    manager.subscribe(ws2, "price:XAUUSD")
    # ws3 NOT subscribed

    await manager.broadcast("price:XAUUSD", {"bid": 2000, "ask": 2001})

    ws1.send_text.assert_called_once()
    ws2.send_text.assert_called_once()
    ws3.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_not_sent_to_other_channels(manager):
    ws = _mock_ws()
    await manager.connect(ws, user_id=1)
    manager.subscribe(ws, "price:BTCUSD")

    await manager.broadcast("price:XAUUSD", {"bid": 2000})
    ws.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_disconnect_cleans_up_subscriptions(manager):
    ws = _mock_ws()
    await manager.connect(ws, user_id=1)
    manager.subscribe(ws, "price:XAUUSD")
    manager.subscribe(ws, "agent:1")
    manager.disconnect(ws)

    assert manager.get_subscriber_count("price:XAUUSD") == 0
    assert manager.get_subscriber_count("agent:1") == 0


@pytest.mark.asyncio
async def test_multiple_tabs_same_user(manager):
    ws1 = _mock_ws()
    ws2 = _mock_ws()

    await manager.connect(ws1, user_id=1)
    await manager.connect(ws2, user_id=1)

    assert manager.get_connection_count() == 2

    await manager.send_personal(1, {"msg": "test"})
    ws1.send_text.assert_called_once()
    ws2.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_stale_connection_cleanup(manager):
    ws = _mock_ws()
    ws.send_text = AsyncMock(side_effect=Exception("connection lost"))

    await manager.connect(ws, user_id=1)
    manager.subscribe(ws, "price:XAUUSD")

    # Broadcast should remove the broken connection
    await manager.broadcast("price:XAUUSD", {"bid": 2000})
    assert manager.get_connection_count() == 0
    assert manager.get_subscriber_count("price:XAUUSD") == 0


@pytest.mark.asyncio
async def test_get_channels(manager):
    ws = _mock_ws()
    await manager.connect(ws, user_id=1)
    manager.subscribe(ws, "price:XAUUSD")
    manager.subscribe(ws, "agent:1")

    channels = manager.get_channels()
    assert "price:XAUUSD" in channels
    assert "agent:1" in channels


# ── WebSocket endpoint integration test ────────────────────────────────

def test_ws_endpoint_exists(client):
    """The /ws endpoint should exist (even if we can't easily test WS via TestClient)."""
    # FastAPI TestClient supports WS via `with client.websocket_connect("/ws")`
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "channel": "price:XAUUSD"})
        resp = ws.receive_json()
        assert resp["channel"] == "system"
        assert resp["data"]["action"] == "subscribed"
        assert resp["data"]["channel"] == "price:XAUUSD"

        # Test ping/pong
        ws.send_json({"action": "ping"})
        resp = ws.receive_json()
        assert resp["data"]["action"] == "pong"
