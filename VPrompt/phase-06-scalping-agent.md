# Phase 6 — Scalping Agent

## Objective
Build the agent engine (AlgoEngine + AgentRunner) and the scalping agent. By the end of this phase, you can deploy a scalping agent from the UI, it polls the broker for M5 candles, evaluates signals, and executes trades (paper or live).

---

## Prompt

```
You are building Flowrex Algo. This is Phase 6 of 10.

READ ARCHITECTURE.md Section 7 (Agent Engine Architecture) and Section 8 (Risk Management).

Phases 1-5 are complete — we have the full backend, frontend, broker adapters, and trained ML models.

### What to build in this phase:

**1. Instrument Specs**
Create `backend/app/services/agent/instrument_specs.py`:
- Per-symbol specifications: pip_size, pip_value, min_lot, lot_step, contract_size
- Cover: BTCUSD, XAUUSD, US30 (and later ES, NAS100)
- calc_lot_size(symbol, risk_amount, sl_distance, broker_name) -> float
  - Formula: lot_size = risk_amount / (sl_distance * pip_value_per_lot)
  - Round to nearest lot_step
  - Clamp to min_lot
  - Handle broker-specific variations (Oanda units vs standard lots)

**2. Risk Manager**
Create `backend/app/services/agent/risk_manager.py`:
- RiskManager class that enforces:
  - Per-trade risk limit (configurable, default 0.5%)
  - Daily loss limit (configurable, default 4%)
  - Max drawdown limit (configurable, default 8%)
  - Position size validation (not exceeding risk amount)
- check_trade(balance, risk_per_trade, daily_pnl, signal) -> {approved: bool, reason: str, adjusted_size: float}
- Use dynamic balance (from broker account), not hardcoded values

**3. Trade Monitor**
Create `backend/app/services/agent/trade_monitor.py`:
- Background service that monitors open agent trades
- Periodically checks if SL or TP has been hit (via broker positions)
- When trade is closed (by broker): update agent_trades record with exit_price, pnl, broker_pnl, exit_reason
- Reconcile paper trades vs broker trades
- Use COALESCE(broker_pnl, pnl) pattern when displaying P&L

**4. Scalping Agent**
Create `backend/app/services/agent/scalping_agent.py`:
- ScalpingAgent class with:
  - __init__(agent_id, symbol, broker_name, config)
  - load() -> bool — load XGB + LGB models from disk
  - evaluate(m5_bars, broker_adapter) -> Optional[signal_dict]
  - _agent_log(level, message, data) — helper that writes to DB via injected _log_fn callback
  - _log_fn = None — set by engine to inject the logging callback

- Evaluation pipeline per M5 bar:
  1. Check minimum bars (need >= 60)
  2. Check cooldown (min 3 bars between trades)
  3. Check daily loss limit
  4. Check news filter (skip if high-impact news imminent)
  5. Fetch H1 context bars from broker (cache, refresh every hour)
  6. Compute features using compute_expert_features()
  7. Predict with XGBoost and LightGBM independently
  8. Voting: ANY ONE model with >= 55% confidence fires the signal
     - 0 votes -> rejected
     - 1 vote -> signal fires (single model conviction)
     - 2 votes same direction -> signal fires (both agree)
     - 2 votes different directions -> rejected (disagreement)
  9. Compute ATR-based SL/TP:
     - SL = 1.5 * ATR(14)
     - TP = 2.5 * ATR(14)
  10. Position sizing: lot_size = calc_lot_size(symbol, risk_amount, sl_distance)
      - risk_amount = balance * risk_per_trade * session_multiplier
      - Session multiplier: 0.5x during Asian session for non-crypto
  11. Return signal dict: {direction, confidence, entry_price, stop_loss, take_profit, lot_size, reason, session, grades, agent_type}

- Diagnostic logging:
  - Log signal fires through _agent_log("signal", ...)
  - Log rejections every 10th occurrence (avoid spam) through _agent_log("info", ...)
  - Track _eval_rejects counter

**5. AlgoEngine (singleton)**
Create `backend/app/services/agent/engine.py`:
- AlgoEngine class:
  - Manages multiple AgentRunner tasks
  - start_agent(agent_id) / stop_agent(agent_id) / pause_agent(agent_id)
  - get_running_agents() -> list
  - Singleton pattern (one engine for the entire app)

- AgentRunner class (per-agent):
  - __init__(agent_id)
  - start() — load agent from DB, determine type, instantiate ScalpingAgent, start loop
  - _run_loop() — the main polling loop:
    1. Fetch latest M5 candles from broker (200 bars)
    2. Detect new closed bar (compare last bar timestamp)
    3. If new bar: update bar buffer, call agent.evaluate()
    4. If signal fires: call _create_trade()
    5. Sleep 40 seconds, repeat
  - _create_trade(signal) — execute via broker adapter:
    1. Check _active_direction (no duplicate positions)
    2. Set _active_direction INSIDE this method (not after calling it — avoid race condition)
    3. Place order through broker adapter
    4. Record in agent_trades table
    5. Log the trade
  - _log(level, message, data) — write to agent_logs DB table + broadcast via WebSocket
  - Inject _log_fn into the scalping agent: self._expert_agent._log_fn = self._log

  - Health check: every 12 evaluations (~1 hour on M5), log:
    "Health: {eval_count} evals, {signal_count} signals, bars={bar_buffer_len}, direction={active_direction}"

  - Error handling: catch all exceptions in the loop, log errors, continue running

**6. Wire Agent Start/Stop/Pause**
Replace the stub endpoints from Phase 2:
- POST /api/agents/{id}/start — call AlgoEngine.start_agent(id), update DB status to "running"
- POST /api/agents/{id}/stop — call AlgoEngine.stop_agent(id), update DB status to "stopped"
- POST /api/agents/{id}/pause — call AlgoEngine.pause_agent(id), update DB status to "paused"
- On app startup: auto-restart agents that were "running" when server last stopped

**7. News Filter**
Create `backend/app/services/news/newsapi_provider.py`:
- check_high_impact_news(symbol, window_minutes=15) -> {should_trade: bool, reason: str}
- Per-symbol keyword mapping:
  - XAUUSD: ["gold", "fed", "fomc", "inflation", "cpi", "nfp", "interest rate"]
  - BTCUSD: ["bitcoin", "crypto", "sec", "etf", "regulation"]
  - US30: ["dow", "jobs", "gdp", "fed", "fomc", "earnings"]
- Cache results for 5 minutes
- Fail open: if API unavailable, allow trading
- Use a free news/calendar API (or stub with configurable mock for testing)

**8. Frontend — Agent Controls**
Wire the agent panel buttons to actual API calls:
- Start button: POST /api/agents/{id}/start, then refresh agent list
- Stop button: POST /api/agents/{id}/stop
- Pause button: POST /api/agents/{id}/pause
- Delete button: DELETE /api/agents/{id} with confirmation dialog
- Status badge should update in real-time

**9. End-to-End Test**
Perform a full integration test:
- Create a scalping agent for XAUUSD via the API
- Start the agent
- Verify it logs "Polling {broker} every 40s for XAUUSD/M5"
- Verify it logs M5 candle data
- Verify it evaluates signals (check for rejection or signal logs)
- Stop the agent
- Verify clean shutdown

### Testing Requirements
- Write unit tests for instrument_specs (calc_lot_size for each symbol)
- Write unit tests for risk_manager (approve/reject scenarios)
- Write unit tests for scalping_agent.evaluate() with mock data
- Write integration test for the full engine loop (mock broker adapter)
- Test agent start/stop lifecycle via API
- Verify agent logs appear in the Engine Log tab (use preview tool)
- Verify agent cards in AgentPanel show correct status
- Run ALL tests

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. All test results
3. End-to-end test results (did the agent loop run? any signals?)
4. Agent engine architecture decisions
5. What Phase 7 will build

Then ask me:
- "I tested the scalping agent with [broker/mock]. Here's what happened: [summary]. Looks right?"
- "The polling interval is 40 seconds. Want to adjust?"
- "News filter is using [source/mock]. Want to configure a real API key?"
- "Ready for Phase 7?"
```

---

## Expected Deliverables
- [ ] Instrument specs with lot sizing
- [ ] Risk manager
- [ ] Trade monitor
- [ ] ScalpingAgent with full evaluation pipeline
- [ ] AlgoEngine + AgentRunner
- [ ] Agent start/stop/pause endpoints (real, not stubs)
- [ ] News filter
- [ ] Frontend agent controls wired up
- [ ] End-to-end agent loop tested
- [ ] All tests passing
