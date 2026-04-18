# Flowrex Algo — Next Session Design Document

_Read this before starting. Covers everything queued for implementation._

---

## Overview

3 major features to build, plus experiment deployment. Total: ~15h of work.

| Feature | Effort | Priority |
|---|---|---|
| 1. Agent Analytics & Live Debugging | 7h | HIGH — diagnoses live losses |
| 2. AI Chat Persistence & Memory | 4h | HIGH — users expect chat to persist |
| 3. Deploy Experiment Winners | 2h | HIGH — new models ready |
| 4. Remaining page polish | 2h | MEDIUM — pre-beta cleanup |

---

## Feature 1: Agent Analytics & Live Debugging

### Problem
Current logging is flat text. You can't answer:
- WHY did the model signal BUY when price was falling?
- Which trading session (London/NY/Asian) is profitable vs losing?
- Is 0.90 confidence actually winning 90% of the time?
- What features drove this specific losing trade?

BTCUSD lost $1,187 before anyone noticed because there was no structured performance tracking.

### Solution: 3 Layers

#### Layer 1 — Enriched Trade Data (migration 006)

New columns on `agent_trades`:

| Column | Type | Purpose |
|---|---|---|
| `signal_confidence` | Float | Model confidence at signal time |
| `mtf_score` | Integer | MTF alignment (2/3 or 3/3) |
| `mtf_layers` | JSON | `{"d1_bias": 1, "h4_momentum": 1, "h1_setup": -1}` |
| `session_name` | String(20) | "london", "ny_open", "asian", "off_hours" |
| `top_features` | JSON | Top 5 SHAP features for this prediction |
| `atr_at_entry` | Float | ATR when trade was placed |
| `model_name` | String(50) | Which model won the vote |
| `time_to_exit_seconds` | Integer | Duration of trade |
| `bars_to_exit` | Integer | M5 bars held |

Populated in `engine.py:_create_trade()` and `_check_closed_trades()`.

#### Layer 2 — Analytics API

**`GET /api/agents/{id}/analytics`**

Returns:
```json
{
  "overall": {
    "total_trades": 45, "win_rate": 55.6, "profit_factor": 1.78, "sharpe_daily": 2.4
  },
  "by_session": {
    "ny_open": {"trades": 18, "win_rate": 66.7, "avg_pnl": 45.20},
    "asian":   {"trades": 8,  "win_rate": 37.5, "avg_pnl": -22.40}
  },
  "by_confidence": {
    "0.80-0.90": {"trades": 10, "win_rate": 70.0},
    "0.90-1.00": {"trades": 5,  "win_rate": 80.0}
  },
  "by_mtf_score": {
    "2/3": {"trades": 25, "win_rate": 48.0},
    "3/3": {"trades": 20, "win_rate": 65.0}
  },
  "by_direction": {
    "BUY":  {"trades": 22, "win_rate": 59.1},
    "SELL": {"trades": 23, "win_rate": 52.2}
  },
  "by_exit_reason": {
    "TP_HIT": {"count": 25, "avg_pnl": 142.50},
    "SL_HIT": {"count": 18, "avg_pnl": -89.30}
  },
  "streaks": {
    "current": {"type": "losing", "count": 3},
    "max_winning": 7, "max_losing": 4
  }
}
```

#### Layer 3 — AI Supervisor Auto-Diagnosis

On every trade close, feed analytics into the supervisor prompt:

```
Trade closed: SELL XAUUSD, SL_HIT, -$89.30
- Loss #3 in a row (all SL_HIT, all Asian session)
- Model confidence was high (0.85) but wrong direction
- Top SHAP features: fx_d1_trend_dir (0.15), fx_h4_rsi (0.12)
- Pattern: model overweighting D1 trend in low-liquidity hours
```

The supervisor can then recommend: **PAUSE during Asian session** or **reduce risk during off-hours**.

#### Frontend — Analytics Tab

New tab in Agent Detail Modal (next to Performance, Trades, Logs):
- Win rate by session (bar chart)
- Confidence calibration (scatter: confidence vs actual WR)
- Streak tracker
- AI-generated diagnosis text

### Files to Modify

| File | Changes |
|---|---|
| `alembic/versions/006_analytics.py` | NEW — migration for analytics columns |
| `app/models/agent.py` | Add new columns to AgentTrade |
| `app/services/agent/engine.py` | Populate new fields in _create_trade + _check_closed_trades |
| `app/services/agent/flowrex_agent_v2.py` | Return session_name + top_features in signal dict |
| `app/services/agent/potential_agent.py` | Same |
| `app/api/agent.py` | New `/analytics` endpoint |
| `app/services/llm/supervisor.py` | Enrich trade-close prompt with analytics |
| `frontend/src/components/AgentDetailModal.tsx` | Add Analytics tab |
| `frontend/src/components/AnalyticsCharts.tsx` | NEW — bar charts, scatter plots |

---

## Feature 2: AI Chat Persistence & Memory

### Problem
- Chat messages are in-memory only — lost on restart or page refresh
- No chat sessions — can't start a new topic without losing context
- No cost tracking — unknown monthly spend

### Solution

#### Database (migration 007)

```
chat_sessions:
  id, user_id, title, created_at, updated_at, is_active

chat_messages:
  id, session_id, role ("user"/"assistant"), content, model, tokens_used, created_at
```

#### API Changes

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/llm/sessions` | GET | List user's chat sessions |
| `/api/llm/sessions` | POST | Create new session |
| `/api/llm/sessions/{id}` | GET | Load messages for a session |
| `/api/llm/sessions/{id}` | DELETE | Delete a session |
| `/api/llm/chat` | POST | Send message (now includes `session_id`) |
| `/api/llm/usage` | GET | Monthly token count + cost |

#### Chat Flow (after implementation)

1. User opens `/ai` → loads most recent active session from DB
2. Messages display immediately (loaded from DB, not memory)
3. User sends message → saved to `chat_messages` → sent to Claude with last 20 messages as context → reply saved to DB
4. User clicks "New Chat" → creates new session, previous one stays in sidebar
5. Backend restart → conversation rebuilt from DB, no data loss

#### Frontend Changes

```
┌─────────────────┬──────────────────────────────────┐
│ Chat Sessions    │ Chat                             │
│                  │                                  │
│ + New Chat       │ [messages from selected session] │
│                  │                                  │
│ Today            │                                  │
│  Trading Q&A  ← │                                  │
│  Risk Analysis   │                                  │
│                  │                                  │
│ Yesterday        │                                  │
│  BTCUSD Review   │                                  │
│                  │                                  │
│                  │ [input box]              [send]  │
└─────────────────┴──────────────────────────────────┘
```

Session titles auto-generated from first user message (first 50 chars).

#### Cost Tracking

Each response stores `tokens_used` from Anthropic response headers:
```json
// /api/llm/usage response
{
  "month": "2026-04",
  "input_tokens": 45000,
  "output_tokens": 12000,
  "estimated_cost_usd": 0.87,
  "sessions": 12,
  "messages": 48
}
```

### Files to Modify

| File | Changes |
|---|---|
| `alembic/versions/007_chat_sessions.py` | NEW — chat_sessions + chat_messages tables |
| `app/models/chat.py` | NEW — ChatSession + ChatMessage models |
| `app/api/llm.py` | Add session CRUD + modify chat endpoint |
| `app/services/llm/supervisor.py` | Load context from DB instead of memory |
| `frontend/src/app/ai/page.tsx` | Session sidebar, message loading, new chat button |

---

## Feature 3: Deploy Experiment Winners

After overnight training experiments finish:

1. Read `backend/data/ml_models/experiments/comparison.json`
2. For each symbol, pick the experiment with best OOS Sharpe + Grade A
3. Copy winning models to production: `cp experiments/{symbol}/{winner}/*.joblib ../`
4. Restart agents to load new models
5. Verify via agent logs

### Decision Criteria

| Metric | Threshold |
|---|---|
| OOS Grade | Must be A or B |
| OOS Sharpe | Must be > 2.0 |
| OOS Win Rate | Must be > 50% |
| OOS Trades | Must be > 50 (statistical significance) |
| Walk-forward consistency | No fold worse than Grade D |

If no experiment beats the current production model, keep the current model.

---

## Feature 4: Remaining Page Polish

Quick fixes from the TODO list that haven't been done yet:

| Fix | File | Time |
|---|---|---|
| Login: reset token copy button | `login/page.tsx` | 15min |
| AI Chat: input re-enable after error | `ai/page.tsx` | 10min |
| AI Chat: Telegram test checks config first | `ai/page.tsx` | 10min |
| Dashboard: todayPnl timezone fix | `page.tsx` | 15min |
| Agents: clone adds "(Copy)" suffix | `agents/page.tsx` | 10min |
| Models: loading state on "Retrain All" | `models/page.tsx` | 15min |
| News: loading overlay during filter change | `news/page.tsx` | 15min |
| Placeholder /terms and /privacy pages | `frontend/src/app/terms/` | 20min |

---

## Execution Order

```
1. Check experiment results (30min)
   └→ Deploy winners if any beat current models

2. Agent Analytics (7h)
   ├→ Migration 006
   ├→ Enrich _create_trade + _check_closed_trades
   ├→ Analytics API endpoint
   ├→ AI Supervisor integration
   └→ Frontend Analytics tab

3. AI Chat Persistence (4h)
   ├→ Migration 007
   ├→ Chat session CRUD endpoints
   ├→ Modify chat to use DB
   └→ Frontend session sidebar + message loading

4. Page polish (2h)
   └→ All 8 quick fixes

5. Final pre-beta verification
   ├→ Full test suite (target: 450+ passing)
   ├→ Manual smoke test (register → broker → agent → trade → analytics)
   └→ Generate 3 invite codes
```

---

## What's Already Done (don't redo)

- ✅ 433 tests passing, 0 failures
- ✅ Migration 005 (backtest_results table)
- ✅ Backtest page: model selector + sizing mode
- ✅ LLM config persistence fix (flag_modified)
- ✅ Model ID fix (claude-sonnet-4-5)
- ✅ All v1-v4 audit fixes deployed (166+ findings)
- ✅ Health check cron running
- ✅ Orphan detection on startup
- ✅ BTCUSD wider SL config (0.8→1.2)
- ✅ ETHUSD/XAGUSD/AUS200 symbol configs added
- ✅ Training experiments pipeline created and running

---

_Total estimated effort: ~15h across next session(s). Training experiments running overnight._
