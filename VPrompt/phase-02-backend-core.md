# Phase 2 — Backend Core

## Objective
Build all database models, Alembic migrations, Pydantic schemas, and CRUD API endpoints. By the end of this phase, the backend is a fully functional REST API (minus broker connections and ML).

---

## Prompt

```
You are building Flowrex Algo. This is Phase 2 of 10.

READ ARCHITECTURE.md for the full database schema (Section 3) and API endpoints (Section 4).

Phase 1 is complete — we have the project scaffold, FastAPI app, PostgreSQL connection, and Alembic set up.

### What to build in this phase:

**1. Database Models (SQLAlchemy)**
Create all models defined in ARCHITECTURE.md Section 3:
- User (id, email, password_hash, totp_secret, is_admin, created_at)
- UserSettings (id, user_id, theme, default_broker, notifications_enabled, settings_json)
- BrokerAccount (id, user_id, broker_name, credentials_encrypted, is_active, created_at)
- TradingAgent (id, created_by, name, symbol, timeframe, agent_type, broker_name, mode, status, risk_config JSONB, created_at, deleted_at for soft delete)
- AgentLog (id, agent_id, level, message, data JSONB, created_at)
- AgentTrade (id, agent_id, symbol, direction, entry_price, exit_price, stop_loss, take_profit, lot_size, pnl, broker_pnl, broker_ticket, status, exit_reason, confidence, signal_data JSONB, entry_time, exit_time)
- MLModel (id, created_by, symbol, timeframe, model_type, pipeline, file_path, grade, metrics JSONB, trained_at)
- Strategy (id, created_by, name, description, strategy_type, config JSONB, created_at)

Add proper indexes: agent_logs(agent_id, created_at), agent_trades(agent_id, status), agent_trades(broker_ticket), trading_agents(created_by, deleted_at).

**2. Alembic Migration**
- Generate and run the initial migration that creates all tables
- Verify all tables exist in PostgreSQL

**3. Pydantic Schemas**
Create request/response schemas for:
- Agent: AgentCreate, AgentUpdate, AgentResponse (include computed fields like trade count, P&L)
- AgentLog: LogResponse
- AgentTrade: TradeResponse
- Broker: BrokerConnectRequest, AccountInfoResponse, PositionResponse, OrderResponse
- ML: ModelResponse, TrainRequest
- Settings: SettingsResponse, SettingsUpdate
- Auth: RegisterRequest, LoginRequest, TokenResponse (placeholder — full auth is Phase 9)

**4. API Endpoints — Agents**
Build all agent endpoints from ARCHITECTURE.md Section 4:
- GET /api/agents — list agents (filter by user, exclude soft-deleted)
- POST /api/agents — create agent with validation
- GET /api/agents/engine-logs — unified logs across all agents (MUST be defined BEFORE /{id} routes to avoid FastAPI path parameter conflict)
- GET /api/agents/all-trades — all trades across agents
- GET /api/agents/pnl-summary — per-agent P&L summary using COALESCE(broker_pnl, pnl)
- GET /api/agents/{id} — single agent
- PUT /api/agents/{id} — update agent
- DELETE /api/agents/{id} — soft delete (set deleted_at)
- POST /api/agents/{id}/start — stub (returns 200, actual engine start is Phase 6)
- POST /api/agents/{id}/stop — stub
- POST /api/agents/{id}/pause — stub
- GET /api/agents/{id}/logs — agent-specific logs with pagination
- GET /api/agents/{id}/trades — agent-specific trades
- GET /api/agents/{id}/performance — performance metrics (win rate, total P&L, avg trade)

IMPORTANT: All static path routes (like /engine-logs, /all-trades, /pnl-summary) MUST be defined before any /{id} parameterized routes. FastAPI matches routes in order.

**5. API Endpoints — Broker (stubs)**
Create the broker router with stub endpoints (actual broker logic is Phase 3):
- GET /api/broker/status — returns empty connected status
- POST /api/broker/connect — stub
- POST /api/broker/disconnect — stub
- GET /api/broker/account — stub
- GET /api/broker/positions — stub
- GET /api/broker/orders — stub
- GET /api/broker/symbols — stub
- GET /api/broker/candles/{symbol} — stub

**6. API Endpoints — ML (stubs)**
- GET /api/ml/models — stub returning empty list
- POST /api/ml/train — stub

**7. API Endpoints — Settings**
- GET /api/settings — return user settings
- PUT /api/settings — upsert user settings (handle concurrent requests gracefully)

**8. Temporary Auth Bypass**
Since full auth is Phase 9, create a simple middleware or dependency that:
- In development: auto-creates and returns a default user (no login required)
- Has a clear TODO marker for where real JWT auth will be injected later
- The get_current_user dependency should be easy to swap out in Phase 9

**9. Seed Script**
Create a seed script that:
- Creates a default admin user
- Creates sample agents for BTCUSD, XAUUSD, US30 (both scalping and expert types)

### Testing Requirements
- Write integration tests for all agent CRUD endpoints (create, read, update, soft-delete)
- Write integration tests for agent logs and trades endpoints
- Write a test that verifies static routes match before parameterized routes
- Test PnL summary returns correct aggregated values
- Test the seed script runs without errors
- Run ALL tests, fix any failures

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. All test results
3. Total number of API endpoints implemented
4. Any design decisions you made
5. What Phase 3 will build

Then ask me:
- "The agent schema has these fields: [list]. Want to add or remove any?"
- "I used [approach] for the temp auth bypass. OK for now?"
- "Ready for Phase 3?"
```

---

## Expected Deliverables
- [ ] All 8 database models with proper relationships and indexes
- [ ] Initial Alembic migration applied
- [ ] Pydantic schemas for all entities
- [ ] Full agent CRUD API
- [ ] Broker/ML/Settings stubs
- [ ] Temporary dev auth bypass
- [ ] Seed script
- [ ] Integration tests passing
