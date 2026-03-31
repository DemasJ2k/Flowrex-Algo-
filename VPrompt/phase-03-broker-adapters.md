# Phase 3 — Broker Adapters

## Objective
Implement all three broker adapters (Oanda, cTrader, MT5) behind a unified abstract interface, wire them into the broker API endpoints, and handle live account connections, candle fetching, order placement, and position management.

---

## Prompt

```
You are building Flowrex Algo. This is Phase 3 of 10.

READ ARCHITECTURE.md Section 5 (Broker Adapter Interface) for the adapter ABC and Section 4 for broker API endpoints.

Phase 2 is complete — all DB models, migrations, CRUD APIs, and schemas are built.

### What to build in this phase:

**1. Broker Adapter Base Class**
Create `backend/app/services/broker/base.py`:
- Abstract base class `BrokerAdapter` with all methods from ARCHITECTURE.md Section 5
- Standard data classes / TypedDicts for: AccountInfo, Position, Order, Candle, SymbolInfo, Tick, OrderResult, CloseResult, ModifyResult
- All methods are async
- Every adapter must normalize data into these universal formats

**2. Oanda Adapter**
Create `backend/app/services/broker/oanda.py`:
- Uses Oanda v20 REST API via httpx (async)
- Connection: requires api_key + account_id + practice flag (determines base URL)
- Instrument name mapping: Oanda uses underscore format (XAU_USD) while our system uses concatenated (XAUUSD). Build bidirectional mapping.
- get_candles: fetch from /instruments/{instrument}/candles, convert to universal Candle format
- get_positions: fetch open positions, extract unrealized P&L
- place_order: POST to /accounts/{id}/orders, support MARKET and LIMIT types
- close_position: PUT to /accounts/{id}/positions/{instrument}/close
- Streaming: Oanda has a streaming API for prices — implement subscribe_prices() that yields Tick objects
- Handle rate limiting (Oanda allows ~30 requests/second)
- Handle Oanda-specific position ID format (they use instrument_side format like "XAU_USD_LONG")

**3. cTrader Adapter**
Create `backend/app/services/broker/ctrader.py`:
- Uses cTrader Open API (protobuf messages over WebSocket)
- Connection flow: connect to WebSocket -> authenticate with client_id, client_secret, access_token -> authorize trading account
- Maintain persistent WebSocket connection with heartbeat
- Symbol cache: on connect, fetch all available symbols and cache symbol_id -> symbol_name mapping
- Price cache: subscribe to spot prices for active symbols, cache latest bid/ask
- get_candles: request historical trend bars via protobuf
- place_order: send NewOrderReq protobuf message
- close_position: send ClosePositionReq
- Handle lot size conversion: cTrader uses volume in cents (1 lot = 100 units for forex, varies by symbol). Use the symbol's lotSize from the symbol cache for correct conversion.
- Handle async reconnection on disconnect

**4. MT5 Adapter**
Create `backend/app/services/broker/mt5.py`:
- Uses MetaTrader5 Python package
- Connection: requires path to MT5 terminal, login, password, server
- Note: MT5 package is synchronous — wrap all calls in asyncio.to_thread()
- get_candles: mt5.copy_rates_from_pos()
- place_order: mt5.order_send() with appropriate request structure
- Handle the MT5 initialization/shutdown lifecycle
- If MT5 package is not available (Linux without Wine), gracefully degrade with clear error message

**5. Broker Manager**
Create a broker manager service that:
- Holds active broker adapter instances (one per connected broker per user)
- Provides get_adapter(broker_name) to retrieve the active adapter
- Handles connect/disconnect lifecycle
- Stores encrypted credentials in broker_accounts table (using encryption utilities from Phase 1)
- On connect: decrypt credentials, instantiate adapter, call adapter.connect()
- On disconnect: call adapter.disconnect(), remove from active instances

**6. Wire Broker API Endpoints**
Replace the stub broker endpoints from Phase 2 with real implementations:
- POST /api/broker/connect — decrypt stored credentials or accept new ones, connect adapter
- POST /api/broker/disconnect — disconnect adapter
- GET /api/broker/status — check which brokers are connected
- GET /api/broker/account — get account info from active adapter
- GET /api/broker/positions — get open positions, include agent attribution (look up agent_trades by broker_ticket, fall back to symbol-based matching)
- GET /api/broker/orders — get pending orders
- GET /api/broker/symbols — get available instruments with min lot, lot step, pip size
- GET /api/broker/candles/{symbol} — get OHLCV data with timeframe and count query params
- POST /api/broker/order — place order through adapter
- POST /api/broker/close/{position_id} — close position through adapter

**7. Credential Security**
- Never log or return raw API keys
- Encrypt credentials before storing in DB
- Decrypt only when connecting
- In production, require ENCRYPTION_KEY env var (block startup without it)

### Testing Requirements
- Write unit tests for the Oanda adapter (mock httpx responses):
  - Test candle fetching and format conversion
  - Test order placement
  - Test instrument name mapping (XAUUSD <-> XAU_USD)
- Write unit tests for the broker manager (connect/disconnect lifecycle)
- Write integration tests for broker API endpoints (using mocked adapters)
- Test encryption: credentials survive encrypt -> store -> load -> decrypt roundtrip
- If possible, test cTrader WebSocket connection with mock server
- Run ALL tests (including Phase 1 and 2 tests), fix any regressions

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. All test results
3. Adapter implementation status (which methods are fully implemented vs stubbed)
4. Any broker-specific quirks you handled
5. What Phase 4 will build

Then ask me:
- "Do you have Oanda API credentials I can test with? (practice account is fine)"
- "Do you have cTrader API credentials (client_id, client_secret, access_token)?"
- "Should I add any additional broker-specific features?"
- "Ready for Phase 4?"
```

---

## Expected Deliverables
- [ ] BrokerAdapter ABC with universal data types
- [ ] Oanda adapter (REST + streaming)
- [ ] cTrader adapter (WebSocket + protobuf)
- [ ] MT5 adapter (with async wrapping)
- [ ] Broker manager service
- [ ] All broker API endpoints wired up
- [ ] Credential encryption working
- [ ] Unit + integration tests passing
