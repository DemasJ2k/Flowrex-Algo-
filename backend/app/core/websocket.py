"""
WebSocket connection manager — handles channels, subscriptions, and broadcasting.
Channels: "price:{symbol}", "agent:{agent_id}", "account"
"""
import asyncio
import json
import time
from typing import Optional
from fastapi import WebSocket
from dataclasses import dataclass, field


@dataclass
class WSConnection:
    websocket: WebSocket
    user_id: int
    channels: set[str] = field(default_factory=set)
    connected_at: float = field(default_factory=time.time)
    last_pong: float = field(default_factory=time.time)


MAX_CONNECTIONS_PER_USER = 5


class ConnectionManager:
    """
    Manages WebSocket connections with channel-based pub/sub.
    Singleton — one manager for the entire app.

    Batch E fixes (multi-user readiness):
    - Connection limit per user (MAX_CONNECTIONS_PER_USER) — prevents reconnection storms
    - Broadcast uses asyncio.gather for non-blocking fan-out
    """

    def __init__(self):
        self._connections: dict[WebSocket, WSConnection] = {}
        self._channel_subs: dict[str, set[WebSocket]] = {}
        self._user_connections: dict[int, set[WebSocket]] = {}
        self._last_broadcast: dict[str, float] = {}
        self._min_broadcast_interval = 0.25  # Max 4 updates/sec per channel

    async def connect(self, websocket: WebSocket, user_id: int):
        """Accept and track a new WebSocket connection."""
        # Connection limit per user — close oldest if at capacity
        user_conns = self._user_connections.get(user_id, set())
        if len(user_conns) >= MAX_CONNECTIONS_PER_USER:
            oldest = min(
                user_conns,
                key=lambda ws: self._connections.get(ws, WSConnection(websocket=ws, user_id=0)).connected_at,
            )
            try:
                await oldest.close(code=1008, reason="Connection limit exceeded")
            except Exception:
                pass
            self.disconnect(oldest)

        await websocket.accept()
        conn = WSConnection(websocket=websocket, user_id=user_id)
        self._connections[websocket] = conn

        if user_id not in self._user_connections:
            self._user_connections[user_id] = set()
        self._user_connections[user_id].add(websocket)

    def disconnect(self, websocket: WebSocket):
        """Clean up a disconnected WebSocket."""
        conn = self._connections.pop(websocket, None)
        if not conn:
            return

        # Remove from all channel subscriptions
        for channel in conn.channels:
            subs = self._channel_subs.get(channel)
            if subs:
                subs.discard(websocket)
                if not subs:
                    del self._channel_subs[channel]

        # Remove from user connections
        user_conns = self._user_connections.get(conn.user_id)
        if user_conns:
            user_conns.discard(websocket)
            if not user_conns:
                del self._user_connections[conn.user_id]

    def subscribe(self, websocket: WebSocket, channel: str):
        """Subscribe a connection to a channel."""
        conn = self._connections.get(websocket)
        if not conn:
            return
        conn.channels.add(channel)
        if channel not in self._channel_subs:
            self._channel_subs[channel] = set()
        self._channel_subs[channel].add(websocket)

    def unsubscribe(self, websocket: WebSocket, channel: str):
        """Unsubscribe a connection from a channel."""
        conn = self._connections.get(websocket)
        if not conn:
            return
        conn.channels.discard(channel)
        subs = self._channel_subs.get(channel)
        if subs:
            subs.discard(websocket)
            if not subs:
                del self._channel_subs[channel]

    async def broadcast(self, channel: str, data: dict):
        """
        Send data to all subscribers of a channel (with rate limiting).

        Uses asyncio.gather so a slow client doesn't block all others.
        """
        now = time.time()
        last = self._last_broadcast.get(channel, 0)
        if now - last < self._min_broadcast_interval:
            return  # Rate limited
        self._last_broadcast[channel] = now

        subs = self._channel_subs.get(channel)
        if not subs:
            return

        message = json.dumps({"channel": channel, "data": data})
        # Copy to list to avoid modifying the set during iteration
        ws_list = list(subs)

        async def _send(ws: WebSocket):
            try:
                await ws.send_text(message)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*[_send(ws) for ws in ws_list], return_exceptions=True)

        # Clean up broken connections — _send returns the ws on failure, None on success
        for r in results:
            if r is not None and not isinstance(r, BaseException):
                self.disconnect(r)

    async def send_personal(self, user_id: int, data: dict):
        """Send to all connections for a specific user."""
        conns = self._user_connections.get(user_id, set())
        message = json.dumps({"channel": "personal", "data": data})
        stale = []
        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

    def get_subscriber_count(self, channel: str) -> int:
        return len(self._channel_subs.get(channel, set()))

    def get_connection_count(self) -> int:
        return len(self._connections)

    def get_channels(self) -> list[str]:
        return list(self._channel_subs.keys())

    async def heartbeat(self):
        """Send ping to all connections, clean up stale ones."""
        stale = []
        for ws, conn in self._connections.items():
            try:
                await ws.send_text(json.dumps({"channel": "heartbeat", "data": {"time": time.time()}}))
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


# Singleton
_manager: Optional[ConnectionManager] = None


def get_ws_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
