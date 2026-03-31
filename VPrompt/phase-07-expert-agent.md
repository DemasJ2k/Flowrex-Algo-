# Phase 7 — Expert Agent

## Objective
Build the expert agent with the full ensemble pipeline: multi-timeframe analysis, 3-model voting, meta-labeler gate, HMM regime detection, and LSTM-informed SL/TP. Also expand to ES and NAS100.

---

## Prompt

```
You are building Flowrex Algo. This is Phase 7 of 10.

READ ARCHITECTURE.md Sections 6, 7, and 8 for the expert pipeline, agent engine, and risk management details.

Phases 1-6 are complete — the scalping agent runs end-to-end. Now build the more sophisticated expert agent.

### What to build in this phase:

**1. Expert Agent**
Create `backend/app/services/agent/expert_agent.py`:
- ExpertAgent class with:
  - __init__(agent_id, symbol, broker_name, config)
  - load() -> bool — load ensemble models, regime detector, meta-labeler
  - evaluate(m5_bars, broker_adapter) -> Optional[signal_dict]
  - _agent_log(level, message, data) — same pattern as ScalpingAgent
  - _log_fn = None, _eval_rejects = 0

- Config fields (from agent.risk_config JSONB):
  - risk_per_trade: 0.005 (0.5%)
  - max_daily_loss_pct: 0.04
  - max_drawdown_pct: 0.08
  - news_filter_enabled: true
  - news_window_minutes: 15
  - ensemble_min_agreement: 2 (need 2/3 models)
  - ensemble_min_confidence: 0.55
  - session_filter: true
  - regime_filter: true

- Full evaluation pipeline per M5 bar:
  1. Check ensemble loaded, minimum bars (>= 60), daily loss gate
  2. Fetch HTF context bars from broker:
     - H1 bars (200, refresh every hour)
     - H4 bars (100, refresh every 4 hours)
     - D1 bars (50, refresh every day)
  3. Compute expert features (M5 + H1 + H4 + D1)
     - Handle NaN: nan_to_num(latest_features, nan=0.0)
     - Build LSTM sequence: last 60 bars of features
  4. News filter (if enabled):
     - Call check_high_impact_news()
     - If blocked: log via _agent_log("info", "Trade blocked by news filter")
  5. Session awareness:
     - Determine current session: asian (0-8 UTC), london (8-13), ny (13-21), dead_zone (21-24)
     - Dead zone + non-crypto: skip entirely
     - Asian + Gold/Indices: session_risk_mult = 0.5
  6. Regime detection (if available):
     - Call regime_detector.predict_regime(bars)
     - volatile + high confidence: regime_risk_mult = 0.6
     - ranging + high confidence: regime_risk_mult = 0.8
     - trending: regime_risk_mult = 1.1
  7. Ensemble vote:
     - Call ensemble.predict(feature_vector, feature_sequence)
     - Needs 2/3 agreement + min 55% confidence
     - If rejected: log reason via _agent_log every 10th rejection
  8. Cooldown check (min 3 bars / 15 min between trades)
  9. Compute SL/TP:
     - ATR-based: SL = 2.0 * ATR, TP = 3.0 * ATR (wider than scalping)
     - If LSTM available: use LSTM price range prediction to adjust
  10. Position sizing:
      - effective_risk = risk_per_trade * session_mult * regime_mult
      - risk_amount = balance * effective_risk
      - lot_size = calc_lot_size(symbol, risk_amount, sl_distance)
  11. Build and return signal dict with all metadata

- Signal logging through _agent_log("signal", ...):
  "SIGNAL: BUY XAUUSD @ 4416.02 | SL=4412.00 TP=4422.00 | conf=67.3% | regime=trending_up session=london | 2/3 models agreed"

**2. Engine Updates for Expert Agent**
Update AgentRunner to handle expert agent type:
- When agent_type == "expert": instantiate ExpertAgent instead of ScalpingAgent
- Inject _log_fn callback: self._expert_agent._log_fn = self._log
- Update balance before each evaluation: agent._balance = broker_account_balance
- Everything else (polling, trade execution, logging) stays the same

**3. Agent Wizard Update**
Update the frontend agent creation wizard:
- When "Expert" is selected, show additional config options:
  - Session filter: on/off
  - Regime filter: on/off
  - News filter: on/off
  - Min agreement: 2 (default, can set to 3 for stricter)
- Pass these in the risk_config when creating the agent

**4. Expand to ES and NAS100**
- Add instrument specs for ES and NAS100
- Update news filter keywords for ES and NAS100:
  - ES: ["s&p", "sp500", "fed", "jobs", "gdp", "earnings", "inflation"]
  - NAS100: ["nasdaq", "tech", "earnings", "fed", "semiconductor"]
- Train models for ES and NAS100 (run the training pipelines)
- Add ES and NAS100 to the symbol selector in the frontend

**5. Portfolio-Level Awareness (optional but recommended)**
Consider cross-agent coordination:
- If multiple agents are running on correlated symbols (e.g., US30 and ES), they shouldn't all enter positions at the same time
- Simple approach: a portfolio manager that tracks total exposure and blocks new trades when total open positions exceed a threshold
- This can be a simple check in the engine before executing a trade

**6. Performance Endpoint Enhancement**
Enhance GET /api/agents/{id}/performance to return:
- Total P&L, Win Rate, Average Win, Average Loss
- Profit Factor (gross wins / gross losses)
- Max Drawdown
- Sharpe Ratio (if enough trades)
- Equity curve data points (cumulative P&L over time)
- Win/loss streak

Wire this to the agent panel's Equity sub-tab — show a line chart of the equity curve.

### Testing Requirements
- Write unit tests for ExpertAgent.evaluate() with mock data
- Test each pipeline stage in isolation:
  - Test regime detection returns valid regime strings
  - Test meta-labeler returns boolean
  - Test ensemble voting with various agreement scenarios
  - Test session awareness returns correct session for different UTC hours
- Integration test: start an expert agent, verify it logs evaluations
- Test that both scalping and expert agents can run simultaneously
- Verify agent wizard shows expert-specific options (preview tool)
- Verify performance endpoint returns correct calculations
- Run ALL tests

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. All test results
3. Expert agent vs scalping agent behavior comparison
4. Model grades for ES and NAS100
5. Any architectural decisions
6. What Phase 8 will build

Then ask me:
- "Expert agents are configured with these defaults: [list]. Want to adjust?"
- "I trained models for ES and NAS100. Grades: [table]. Good enough?"
- "Portfolio-level checks are [implemented/skipped]. Want me to add/adjust?"
- "Ready for Phase 8?"
```

---

## Expected Deliverables
- [ ] ExpertAgent with full pipeline (9 stages)
- [ ] Engine updated to handle both agent types
- [ ] Agent wizard updated for expert config
- [ ] ES and NAS100 models trained
- [ ] Symbols expanded to all 5
- [ ] Performance endpoint enhanced
- [ ] Equity curve in frontend
- [ ] All tests passing
