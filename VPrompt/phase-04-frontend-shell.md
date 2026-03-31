# Phase 4 — Frontend Shell

## Objective
Build the complete frontend UI with all pages, components, and data fetching. By the end of this phase, the app looks and feels like a professional trading terminal with live data from the backend APIs.

---

## Prompt

```
You are building Flowrex Algo. This is Phase 4 of 10.

READ ARCHITECTURE.md Section 9 (Frontend Pages) for page layouts and Section 4 for API endpoints the frontend consumes.

Phases 1-3 are complete — the backend has full CRUD APIs, broker adapters, and PostgreSQL. The frontend has the scaffold with sidebar, dark theme, and placeholder pages.

### What to build in this phase:

**1. TypeScript Types**
Create all TypeScript interfaces in `frontend/src/types/index.ts`:
- AccountInfo: {balance, equity, margin_used, unrealized_pnl, currency}
- LivePosition: {position_id, symbol, side, size, entry_price, current_price, stop_loss, take_profit, unrealized_pnl, open_time, agent_name?}
- LiveOrder: {order_id, symbol, side, size, order_type, price, stop_loss, take_profit, status, created_at}
- TradeHistory: {id, symbol, direction, lot_size, entry_price, exit_price, stop_loss, take_profit, pnl, exit_reason, agent_name, source, entry_time, exit_time, duration_seconds}
- Agent: {id, name, symbol, timeframe, agent_type, broker_name, mode, status, risk_config, created_at}
- AgentLog: {id, agent_id, level, message, data, created_at}
- AgentTrade: same as TradeHistory but with agent-specific fields
- EngineLog: {id, agent_id, agent_name, agent_symbol, level, message, data, created_at}
- MLModel: {id, symbol, timeframe, model_type, pipeline, grade, metrics, trained_at}
- BrokerListResponse, PlaceOrderRequest, etc.

**2. Dashboard Page (`/`)**
- Account summary cards: Balance, Equity, Unrealized P&L, Active Agents count
- Per-agent P&L summary cards in a horizontal scrollable row (agent name, symbol, P&L, trade count, win rate)
- Quick action buttons: "Go to Trading", "New Agent"
- Fetch data from /api/broker/account and /api/agents/pnl-summary

**3. Trading Terminal Page (`/trading`) — THE MAIN PAGE**
This is the most complex page. Build it in layers:

**3a. Chart Section (top)**
- Symbol selector dropdown (searchable, with recent symbols memory in localStorage)
- Timeframe buttons: M1, M5, M10, M15, M30, H1, H4, D1
- Broker selector (when multiple brokers connected)
- Candlestick chart using `lightweight-charts` from TradingView
  - Install lightweight-charts package
  - Implement CandlestickChart component with: candlestick series, volume bars, overlay lines for indicators
  - Support indicator overlays (EMA, SMA, Bollinger Bands) via an indicator dropdown
  - Auto-load candles from /api/broker/candles/{symbol}?timeframe={tf}&count=200
  - Display live bid/ask/spread from WebSocket (placeholder — actual WS is Phase 8)
- Price display bar: Symbol, Bid (green), Ask (red), Spread, "live" indicator

**3b. Account Section (below chart)**
- Balance, Equity, Unrealized P&L, Active Agents cards
- Per-agent summary cards (horizontal scroll)

**3c. Tab Section (bottom)**
Build 5 tabs using a Tabs component:

**Agents Tab:**
- AgentPanel component (separate file: `components/AgentPanel.tsx`)
- Lists all agents as expandable cards
- Each card shows: name, symbol, timeframe, status badge (RUNNING/STOPPED/PAUSED), control buttons (start/pause/stop/delete/edit)
- Expanded view shows: P&L, Win Rate, Trades, Wins, Losses
- Sub-tabs within each agent: Trades (table), Logs (scrollable list), Equity (chart placeholder)
- Logs should display with color-coded level badges: info=gray, warn=yellow, error=red, signal=blue, trade=green
- Fetch from /api/agents, /api/agents/{id}/logs, /api/agents/{id}/trades

**Positions Tab:**
- Table: Symbol, Side (badge), Size, Entry, Current, SL, TP, P&L (colored), Agent (badge), Opened, Action (Close button)
- Fetch from /api/broker/positions
- Close button calls POST /api/broker/close/{position_id}

**Orders Tab:**
- Table: Symbol, Side, Type, Size, Price, SL, TP, Status, Created, Action (Cancel button)
- Fetch from /api/broker/orders

**History Tab:**
- Table: Symbol, Side, Size, Entry, Exit, SL, TP, P&L, Exit Reason (badge), Agent (badge), Source (Agent/Broker badge), Duration, Time
- Show total P&L at top
- Fetch from /api/agents/all-trades

**Engine Log Tab:**
- Unified cross-agent log viewer
- Table: Time, Agent (name + symbol), Level (color badge), Message (monospace)
- Polls /api/agents/engine-logs?limit=100 every 5 seconds
- Scrollable, max 100 entries, rolls off naturally
- Count badge on tab: "Engine Log (N)"

**4. Agent Wizard**
Build a multi-step agent creation dialog:
- Step 1: Choose agent type (Scalping / Expert) with descriptions
- Step 2: Select symbol (BTCUSD, XAUUSD, US30) — only show symbols with trained models
- Step 3: Configure risk: preset buttons (Conservative 0.25% / Moderate 0.5% / Aggressive 1%) + custom input (0.1-3%)
- Step 4: Select mode (Paper / Live) with warning on live
- Step 5: Review and deploy
- POST to /api/agents on deploy

**5. Broker Connection Modal**
- Modal triggered from Trading page when no broker connected
- Broker selector: Oanda, cTrader, MT5
- Dynamic form fields based on broker:
  - Oanda: API Key, Account ID, Practice/Live toggle
  - cTrader: Client ID, Client Secret, Access Token, Account ID
  - MT5: Login, Password, Server
- Connect button calls POST /api/broker/connect
- Show connection status

**6. Place Order Panel**
- Slide-out panel or modal for manual order placement
- Fields: Symbol, Side (BUY/SELL), Size, Type (MARKET/LIMIT), Price (for limit), SL, TP
- Broker selector if multiple connected
- Submit calls POST /api/broker/order

**7. Other Pages (simpler)**
- Agents page: list view of all agents with status, link to trading page
- Models page: table of trained models with grade badges, metrics
- Settings page: theme toggle, default broker, notification preferences
- Backtest page: placeholder for now (Phase 9)

**8. Polling & Data Refresh**
- Trading page: poll positions/orders/account every 5 seconds when broker connected
- Agent panel: poll agent logs every 5 seconds per expanded agent
- Engine log: poll every 5 seconds
- Use useCallback + useRef for polling intervals, clean up on unmount

**9. Responsive Design**
- Must work on mobile (the screenshots show mobile usage)
- Sidebar collapses to hamburger menu on mobile
- Tables scroll horizontally on small screens
- Chart takes full width

### Testing Requirements
- Use the preview tool to verify EVERY page looks correct
- Verify the Trading page layout: chart on top, account cards below, tabs at bottom
- Verify the Agent panel expands/collapses correctly
- Verify the Agent wizard flows through all steps
- Verify the broker connection modal shows correct fields per broker
- Verify mobile responsiveness (check narrow viewport in preview)
- Verify dark theme is consistent across all pages
- Test that API calls work against the running backend (start backend first)

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. Preview screenshots of: Dashboard, Trading page, Agent panel expanded, Agent wizard, Broker modal
3. Any UI kit decisions you made
4. Mobile responsiveness status
5. What Phase 5 will build

Then ask me:
- "Here's how the trading terminal looks [preview]. Any layout changes?"
- "I used [UI kit] with these design choices. Want adjustments?"
- "The agent wizard has these steps: [list]. Want to modify the flow?"
- "Ready for Phase 5?"
```

---

## Expected Deliverables
- [ ] All TypeScript interfaces
- [ ] Dashboard with summary cards
- [ ] Full Trading terminal page (chart, account, 5 tabs)
- [ ] AgentPanel component with expandable cards and logs
- [ ] Agent creation wizard
- [ ] Broker connection modal
- [ ] Order placement panel
- [ ] All other pages (Agents, Models, Settings, Backtest placeholder)
- [ ] Polling for live data
- [ ] Mobile responsive
- [ ] All pages verified via preview tool
