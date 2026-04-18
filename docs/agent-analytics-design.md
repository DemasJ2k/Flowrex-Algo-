# Agent Analytics & Live Debugging — Design Doc

_Created 2026-04-16. For implementation in next session._

---

## Problem

Current agent logging is flat text in `agent_logs` table:
```
Eval #14: no signal | bars=499, balance=89404.19
SIGNAL BUY XAUUSD conf=0.777
Sizing: balance=89404 x risk=0.10% = $89.40, SL_dist=6.50, units=14
OPENED BUY XAUUSD @ 4776.94 | SL:4783.44 TP:4767.19
CLOSED BUY XAUUSD | SL_HIT | P&L:-1146.68
```

This tells you WHAT happened but not WHY. You can't answer:
- Why did the model signal BUY when price was falling?
- Which features were most influential for this specific signal?
- Is the model's confidence calibrated? (Does 0.90 confidence actually win 90%?)
- What's the win rate by session (London vs NY vs Asian)?
- What's the win rate by signal type (MTF 2/3 vs 3/3)?
- Is there a pattern in the losses (all SL_HIT at the same time of day)?

---

## Solution: 3 layers

### Layer 1: Enriched Trade Logging (in `agent_trades` table)

Add columns to `agent_trades` for structured analysis:

```python
# Already added (migration 004):
requested_price = Column(Float)
fill_price = Column(Float) 
slippage_pips = Column(Float)

# NEW — add in migration 006:
signal_confidence = Column(Float)        # model confidence at signal time
mtf_score = Column(Integer)              # MTF alignment score (2/3 or 3/3)
mtf_layers = Column(JSON)               # {"d1_bias": 1, "h4_momentum": 1, "h1_setup": -1}
session_name = Column(String(20))        # "london", "ny", "asian", "off_hours"
top_features = Column(JSON)              # top 5 SHAP features for this specific prediction
atr_at_entry = Column(Float)             # ATR value when trade was placed
spread_at_entry = Column(Float)          # broker spread at entry time
model_name = Column(String(50))          # "flowrex_v2_xgboost" or "potential_lightgbm"
time_to_exit_seconds = Column(Integer)   # how long the trade was open
bars_to_exit = Column(Integer)           # how many M5 bars
```

**Where to populate:** In `engine.py:_create_trade()` and `_check_closed_trades()`.

### Layer 2: Performance Analytics API

New endpoint: `GET /api/agents/{id}/analytics`

Returns structured performance breakdown:

```json
{
  "overall": {
    "total_trades": 45,
    "win_rate": 55.6,
    "avg_win": 142.50,
    "avg_loss": -89.30,
    "profit_factor": 1.78,
    "sharpe_daily": 2.4
  },
  "by_session": {
    "ny_open": {"trades": 18, "win_rate": 66.7, "avg_pnl": 45.20},
    "london": {"trades": 12, "win_rate": 50.0, "avg_pnl": 12.10},
    "asian": {"trades": 8, "win_rate": 37.5, "avg_pnl": -22.40},
    "off_hours": {"trades": 7, "win_rate": 42.9, "avg_pnl": -15.60}
  },
  "by_confidence": {
    "0.50-0.60": {"trades": 10, "win_rate": 40.0},
    "0.60-0.70": {"trades": 12, "win_rate": 50.0},
    "0.70-0.80": {"trades": 8, "win_rate": 62.5},
    "0.80-0.90": {"trades": 10, "win_rate": 70.0},
    "0.90-1.00": {"trades": 5, "win_rate": 80.0}
  },
  "by_mtf_score": {
    "2/3": {"trades": 25, "win_rate": 48.0},
    "3/3": {"trades": 20, "win_rate": 65.0}
  },
  "by_direction": {
    "BUY": {"trades": 22, "win_rate": 59.1},
    "SELL": {"trades": 23, "win_rate": 52.2}
  },
  "by_exit_reason": {
    "TP_HIT": {"count": 25, "avg_pnl": 142.50},
    "SL_HIT": {"count": 18, "avg_pnl": -89.30},
    "TIMEOUT": {"count": 2, "avg_pnl": -12.40}
  },
  "streaks": {
    "current": {"type": "losing", "count": 3},
    "max_winning": 7,
    "max_losing": 4
  },
  "recent_pattern": "3 consecutive SL_HIT losses in Asian session (01:00-05:00 UTC). Model signaling BUY but D1 trend is bearish. Consider pausing Asian session trading."
}
```

### Layer 3: AI Supervisor Integration

Feed Layer 2 analytics into the AI Supervisor's context so it can diagnose patterns automatically.

**On every trade close**, the supervisor gets:
```
Trade closed: SELL XAUUSD, SL_HIT, -$89.30
Context:
- This is loss #3 in a row (all SL_HIT)
- All 3 losses were during Asian session (01:00-05:00 UTC)
- Model confidence was high (0.85, 0.79, 0.82) but all wrong direction
- Top SHAP features: fx_d1_trend_dir (0.15), fx_h4_rsi (0.12), fx_volume_spike (0.09)
- Conclusion: model is overweighting D1 trend in low-liquidity hours

Recommendation: PAUSE agent during 00:00-06:00 UTC (Asian session) for XAUUSD.
```

**Hourly health check** includes:
```
Agent XAUUSD Flowrex v2 health:
- Last 24h: 8 trades, 50% WR, +$123 net
- Best session: NY open (4/5 wins)
- Worst session: Asian (0/2 wins)  
- Confidence calibration: 0.80+ signals win 71% (good)
- Feature drift: no warnings
- Recommendation: agent is healthy, NY session is the edge
```

---

## Frontend: Agent Performance Dashboard

Add a new tab "Analytics" to the Agent Detail Modal (next to Performance, Trades, Logs).

Shows:
1. **Win rate by session** — bar chart (London/NY/Asian/Off-hours)
2. **Confidence calibration** — scatter plot (confidence vs actual win %)
3. **MTF score breakdown** — pie chart (2/3 vs 3/3 trades)
4. **Streak tracker** — current streak + historical worst
5. **Recent pattern** — AI-generated text from supervisor
6. **Feature importance for recent trades** — top 5 SHAP features

---

## Implementation Priority

| Component | Effort | Impact |
|---|---|---|
| Migration 006 (new columns) | 30min | Enables everything |
| Populate in _create_trade + _check_closed_trades | 1h | Data collection |
| Analytics API endpoint | 2h | Backend analysis |
| AI Supervisor integration | 1h | Auto-diagnosis |
| Frontend Analytics tab | 3h | User-facing dashboard |
| **Total** | **~7h** | Full live debugging |

---

## What this solves for BTCUSD

With this system, the BTCUSD 20% WR problem would have been caught within the first 5 trades:

```
AI Supervisor alert:
"BTCUSD Flowrex v2 has 1/5 win rate (20%). All 4 losses are SL_HIT with 
tight SL distance (~$100, 0.13% of price). The model signals BUY with high 
confidence (0.89-0.96) but price is in a choppy bearish regime. 

Top losing feature: fx_d1_trend_dir = bullish (1.0) — the model is reading 
a daily uptrend that doesn't match the M5 reality.

RECOMMENDATION: PAUSE_AGENT and retrain with wider sl_atr_mult (current 0.8, 
suggest 1.2-1.5 for crypto)."
```

Instead of losing $1,187 before you noticed manually.
