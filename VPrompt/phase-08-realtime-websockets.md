# Phase 8 — Real-Time & WebSockets

## Objective
Implement WebSocket infrastructure for live price updates, agent log streaming, and account updates. Replace polling with real-time push where appropriate. By the end of this phase, the trading terminal feels live and responsive.

---

## Prompt

```
You are building Flowrex Algo. This is Phase 8 of 10.

READ ARCHITECTURE.md Section 10 (WebSocket Channels) for channel specs.

Phases 1-7 are complete — both scalping and expert agents run end-to-end. But the frontend relies on HTTP polling for all data. Now add real-time WebSocket push.

### What to build in this phase:

**1. WebSocket Connection Manager (Backend)**
Create `backend/app/core/websocket.py`:
- ConnectionManager class:
  - connect(websocket, user_id) — accept and track connection
  - disconnect(websocket) — clean up
  - subscribe(websocket, channel) — subscribe to a channel
  - unsubscribe(websocket, channel) — unsubscribe
  - broadcast(channel, data) — send to all subscribers of a channel
  - send_personal(user_id, data) — send to specific user
- Channel format: "price:{symbol}", "agent:{agent_id}", "account"
- Handle multiple connections per user (multiple browser tabs)
- Singleton instance

**2. WebSocket Endpoint**
Add WebSocket endpoint to FastAPI:
- ws://host/ws?token={jwt_token}
- On connect: verify token (or skip in dev mode since auth is Phase 9)
- Client sends subscription messages: {"action": "subscribe", "channel": "price:XAUUSD"}
- Client sends unsubscribe: {"action": "unsubscribe", "channel": "price:XAUUSD"}
- Server pushes: {"channel": "price:XAUUSD", "data": {bid, ask, time}}

**3. Price Streaming**
When a broker is connected and has streaming capability:
- Oanda: use the streaming API to get real-time ticks
- cTrader: use the spot subscription already in the adapter
- MT5: poll tick data every 500ms (MT5 doesn't have a push API)
- On each new tick: broadcast to "price:{symbol}" channel
- Payload: {symbol, bid, ask, spread, time}
- Deduplicate: only broadcast if bid/ask actually changed

**4. Agent Event Streaming**
Update the engine's _log() method to broadcast via WebSocket:
- Currently writes to DB — keep that
- Additionally: call ws_manager.broadcast(f"agent:{agent_id}", {"type": "log", "data": log_entry})
- On trade events: broadcast {"type": "trade", "data": trade_entry}
- On status change (start/stop/pause): broadcast {"type": "status", "data": {status}}

**5. Account Updates**
When broker is connected:
- Every 5 seconds, fetch account info and broadcast to "account" channel
- Payload: {balance, equity, margin_used, unrealized_pnl, currency}
- Also broadcast position updates when they change

**6. Frontend WebSocket Hook**
Create `frontend/src/hooks/useWebSocket.ts`:
- useWebSocket() hook:
  - Connect to ws://backend/ws on mount
  - Auto-reconnect on disconnect (exponential backoff: 1s, 2s, 4s, 8s, max 30s)
  - subscribe(channel) / unsubscribe(channel) methods
  - onMessage callback handler
  - Connection status: connected/disconnected/reconnecting
  - Clean up subscriptions on unmount

**7. Frontend Integration — Live Prices**
Update the Trading page:
- Subscribe to "price:{symbol}" when chart symbol changes
- Display live bid/ask/spread in the price bar (currently static)
- Show "live" badge when receiving real-time data
- Show "stale" badge when no update in 60+ seconds
- Update candlestick chart in real-time (append tick to current candle)
- Relative time indicator: "live", "2s ago", "5m ago"

**8. Frontend Integration — Agent Logs**
Update the AgentPanel:
- Subscribe to "agent:{agent_id}" when agent card is expanded
- Unsubscribe when collapsed (prevent subscription churn)
- Prepend new log entries in real-time (no need to poll)
- Flash/highlight new entries briefly on arrival
- Keep polling as fallback (reduce interval to 30s when WS is connected)

**9. Frontend Integration — Engine Log Tab**
Update the Engine Log tab:
- Subscribe to agent channels for all running agents
- Append new logs in real-time
- Keep the 100-entry limit (trim oldest when new arrive)
- Keep polling as fallback (reduce to 15s when WS is connected)

**10. Frontend Integration — Positions/Account**
Update the Trading page:
- Subscribe to "account" channel
- Update balance/equity/P&L cards in real-time
- Update positions table when positions change
- Show live unrealized P&L that updates with price ticks

**11. Connection Status Indicator**
- Show WebSocket connection status in the UI:
  - Green dot + "Live" when connected
  - Yellow dot + "Reconnecting..." when reconnecting
  - Red dot + "Disconnected" when disconnected
- Place near the price bar or in the header

### Testing Requirements
- Write unit tests for ConnectionManager (subscribe, broadcast, cleanup)
- Test WebSocket endpoint accepts connections and processes subscriptions
- Integration test: connect WS, subscribe to price channel, verify messages arrive
- Integration test: start an agent, connect WS, verify log messages stream in real-time
- Use preview tool to verify:
  - Live price updates in the price bar
  - Agent logs appearing in real-time
  - Connection status indicator
  - Chart updates with new ticks
- Test reconnection: simulate disconnect, verify auto-reconnect
- Test multiple tabs: open two browser tabs, verify both receive updates
- Run ALL tests

### Performance Considerations
- Don't broadcast every tick to every client — only to subscribers of that channel
- Debounce rapid updates (e.g., buffer ticks and send at most 4/second to frontend)
- Clean up stale connections (heartbeat ping/pong every 30 seconds)
- Memory: don't let message queues grow unbounded

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. All test results
3. WebSocket channels implemented
4. Real-time vs polling: what's now real-time vs still polling?
5. Connection reliability observations
6. What Phase 9 will build

Then ask me:
- "Live prices are streaming at [X updates/sec]. Want to adjust the rate?"
- "Agent logs stream in real-time now. Polling is kept as fallback at [X]s. OK?"
- "The reconnection strategy is [description]. Want to adjust?"
- "Ready for Phase 9?"
```

---

## Expected Deliverables
- [ ] WebSocket ConnectionManager
- [ ] WS endpoint with channel subscriptions
- [ ] Price streaming from broker adapters
- [ ] Agent event streaming (logs, trades, status)
- [ ] Account update broadcasting
- [ ] Frontend useWebSocket hook with auto-reconnect
- [ ] Live price display in trading page
- [ ] Real-time agent logs
- [ ] Real-time engine log tab
- [ ] Live position/account updates
- [ ] Connection status indicator
- [ ] All tests passing
