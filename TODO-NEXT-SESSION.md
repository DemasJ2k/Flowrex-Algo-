# Flowrex Algo — Next Session TODO

_Generated 2026-04-16 after auditing the Backtest page, Trading page, and Charts._
_43 findings across 3 areas. Priority-ordered for execution._

---

## CRITICAL — Blocking Production (Fix ASAP)

### 1. `backtest_results` table missing — backtest page completely broken
**File:** `backend/app/models/backtest.py` (model exists), no migration
**Error:** `psycopg2.errors.UndefinedTable: relation "backtest_results" does not exist`
**Impact:** User clicks "Run Backtest" → gets "Internal server error" toast. Entire backtest feature is non-functional.
**Fix:** Add `backtest_results` table to a new migration (005). Same idempotent pattern as migrations 002-004.
```sql
CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol VARCHAR(20) NOT NULL,
    agent_type VARCHAR(50),
    config JSON,
    results JSON,
    status VARCHAR(20) DEFAULT 'running',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
```

### 2. Dukascopy data source not visible in backtest UI
**File:** `frontend/src/app/backtest/page.tsx:296-303`
**Issue:** Backend supports 3 data sources (`broker`, `history`, `dukascopy`). UI only shows 2 buttons ("Broker Live" and "Historical"). The new Dukascopy-direct option (built in Batch 5) was never wired into the frontend.
**User requirement:** "Make it so that when we fetch historical data we fetch it from Dukascopy"
**Fix:** Add a third data source button "Dukascopy (Fresh)" that sends `data_source: "dukascopy"` to the API. Make it the default for the "Historical" button (rename "Historical" to "Dukascopy" since that IS the historical source now).

### 3. Orphaned XAUUSD short position still on Oanda
**Location:** Oanda account — ticket `XAU_USD:short`, ~10 units
**Impact:** Detected on every backend restart. No agent manages it.
**Fix:** Close manually in Oanda platform UI.

---

## HIGH — Should Fix Before Beta Users Arrive

### Backtest Page

#### 4. Legacy `/api/backtest/run` endpoint is zombie code
**File:** `backend/app/api/backtest.py:46-152`
**Issue:** The old scalping backtest endpoint has no UI and nobody calls it. Dead code.
**Fix:** Delete or deprecate with a comment.

#### 5. "All data" date range actually means "last 6 months"
**File:** `frontend/src/app/backtest/page.tsx:94`
**Issue:** User clicks "All Data" but backend defaults to 6 months (`oos_ts = UTC.now() - 180 days`).
**Fix:** Send explicit `start_date: "2010-01-01"` when "All Data" is selected. Or implement properly in backend to use earliest available data.

#### 6. Trade list truncated to 50 without warning
**File:** `backend/app/api/backtest.py:604`
**Issue:** Only last 50 trades returned. No pagination or "showing 50 of 312" indicator.
**Fix:** Add pagination or return all trades. Frontend should show count.

#### 7. Monte Carlo results computed but never displayed
**File:** `frontend/src/app/backtest/page.tsx`
**Issue:** Backend computes Monte Carlo simulation but frontend has no component to render it.
**Fix:** Add a Monte Carlo results section showing confidence intervals, worst/best case, and distribution histogram.

#### 8. Backtest results lost on page refresh
**Issue:** Results are in-memory (`_potential_results` dict). Server restart = results lost. DB persistence was added (Batch N) but now we know the table doesn't exist.
**Fix:** After fixing migration (#1), wire up DB read/write so results persist.

### Trading Page

#### 9. Close Position button has no loading state
**File:** `frontend/src/app/trading/page.tsx:298`
**Issue:** Double-click sends two close requests. No disabled state during API call.
**Fix:** Add `disabled={closingId === pos.id}` state.

#### 10. Order Panel doesn't update on symbol change
**File:** `frontend/src/app/trading/page.tsx:597`
**Issue:** Changing symbol with order panel open doesn't update the panel's symbol.
**Fix:** Pass `key={symbol}` to `<OrderPanel>` to force re-mount on symbol change.

#### 11. No cancel button for pending orders
**File:** `frontend/src/app/trading/page.tsx:302-311`
**Issue:** Orders tab shows orders but no way to cancel them from the UI.
**Fix:** Add "Cancel" action column that calls broker cancel endpoint.

#### 12. Agent name column alignment in History tab
**File:** `frontend/src/app/trading/page.tsx:314`
**Issue:** Newly added Agent column renders name + type in a flex column. Long names cause row misalignment.
**Fix:** Add `truncate max-w-[120px]` to the name span.

### Charts

#### 13. RSI indicator not implemented
**File:** `frontend/src/lib/indicators.ts`
**Issue:** Menu offers EMA/SMA/Bollinger only. No RSI despite it being a fundamental trading indicator.
**Fix:** Implement RSI calculation and add to the indicator toggle menu.

#### 14. Indicator menu doesn't close on chart update
**File:** `frontend/src/app/trading/page.tsx:384`
**Issue:** Switching symbol/timeframe leaves indicator menu open.
**Fix:** Add `setIndicatorMenuOpen(false)` in the symbol/timeframe onChange handlers.

#### 15. Bollinger Bands crash on < 20 bars
**File:** `frontend/src/components/CandlestickChart.tsx:170`
**Issue:** BB period=20 but if fewer than 20 candles, calculation returns nulls.
**Fix:** Guard: `if (candles.length < 20) return;` for BB overlay.

---

## MEDIUM — Should Fix Soon

### Backtest

#### 16. Data source UI doesn't match backend options
Three sources exist (broker, history, dukascopy) but only 2 buttons in UI.

#### 17. Backtest progress bar too vague
Only ~4 progress states. User can't tell if it's hung or working.

#### 18. Equity curve subsampling may lose edge points
Subsample to 300 points but first/last point handling is fragile.

### Trading

#### 19. Account info flickers — REST poll + WebSocket duplicate updates
Both REST (5s poll) and WebSocket update the same balance/equity fields.

#### 20. Engine Log default shows ALL levels — too noisy
Default should be "Signal" or "Trade" to reduce noise for new users.

#### 21. History stats disappear when no trades exist
No empty-state message or CTA when the user has zero closed trades.

#### 22. No account risk metrics
No max drawdown, margin usage %, or risk-of-ruin display.

#### 23. No commission/fees display in trade history
P&L shown but not gross vs net or broker fees.

### Charts

#### 24. CandlestickChart indicator update doesn't check disposed state
Can add indicators to a disposed chart if component unmounts during computation.

#### 25. Chart markers don't validate time range
Empty times array causes filter to fail silently.

#### 26. Timeframe doesn't reset on data source switch
Switching from broker (supports M30) to Databento (doesn't support M30) sends invalid timeframe.

#### 27. No "last updated" timestamp on chart
Users can't tell if chart data is fresh or 5 minutes stale.

#### 28. Indicator colors hardcoded — no dark/light mode adaptation
Colors don't change between themes.

---

## LOW — Nice to Have

#### 29. MACD indicator not implemented
#### 30. Volume analysis tools (VWAP overlay, volume profile)
#### 31. Drawing tools (trendlines, support/resistance)
#### 32. Chart zoom/pan (currently view-only)
#### 33. EquityCurveChart shows blank instead of empty-state message
#### 34. Monthly breakdown in backtest hides "wins" field
#### 35. Data source toggle for Databento shows stale-chart warning missing
#### 36. Positions/Orders badges update on 5s poll, not instantly

---

## Dukascopy Historical Data Integration

**User requirement:** "Make it so that when we fetch historical data we fetch it from Dukascopy"

### What's already built (Batch 5):
- `backend/app/services/backtest/data_fetcher.py` — `BacktestDataFetcher` class
- Backend API default changed to `data_source="dukascopy"` for Potential Agent backtest
- Node.js fetcher runs inside the container (Node 20 installed in Dockerfile)
- Tempdir lifecycle: fetch → load → delete → 10-min memory cache

### What's NOT done:
1. **Frontend doesn't show Dukascopy option** — fix #2 above. The UI needs the third button.
2. **Legacy `/api/backtest/run` endpoint** still reads from persistent CSV files, not Dukascopy.
3. **The chart "Historical" data source** reads from Databento CSV files, not Dukascopy. To make ALL historical data come from Dukascopy, the chart data source needs to be rewired too.

### Recommended approach:
1. Rename "Historical" button to "Dukascopy" in the UI
2. Keep "Broker (Live)" as the other option
3. When user selects "Dukascopy" for charts, fetch via the existing `BacktestDataFetcher` pipeline
4. For backtests, `data_source="dukascopy"` is already the default — just needs the `backtest_results` table to work

---

## Execution Priority

| Priority | Items | Est. Time |
|---|---|---|
| **CRITICAL (do first)** | #1 (migration), #2 (Dukascopy UI), #3 (close orphan) | 1.5h |
| **HIGH (before beta)** | #4-#15 (backtest fixes, trading UX, chart bugs) | 6h |
| **MEDIUM (this week)** | #16-#28 (polish, UX improvements) | 4h |
| **LOW (backlog)** | #29-#36 (new features, nice-to-haves) | 3h |
| **Total** | **36 items** | **~14.5h** |

---

_Created 2026-04-16. All findings verified against live codebase + screenshot of backtest page "Internal server error"._
