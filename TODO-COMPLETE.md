# Flowrex Algo — Complete Task List

_Generated 2026-04-16 after auditing ALL pages + checking training/data state._

---

## PART 1: Page Fixes (from audit of all 10 pages)

### CRITICAL (2 items)

#### 1. AI Chat — no API key validation before enabling supervisor
**File:** `frontend/src/app/ai/page.tsx:57`
**Issue:** User can save config with `enabled: true` but no API key. Supervisor silently fails on every event.
**Fix:** Disable the "Enable" toggle unless `api_key_set` is true. Show inline warning "API key required".

#### 2. Admin — security race during auth check
**File:** `frontend/src/app/admin/page.tsx:30-35`
**Issue:** Admin check redirects to "/" but component briefly renders, leaking data.
**Fix:** Return null/loading spinner until admin check completes, THEN render content.

### HIGH (6 items)

#### 3. Dashboard — no error boundary for failed API calls
**File:** `frontend/src/app/page.tsx:51-60`
**Issue:** `Promise.all().catch(() => null)` swallows all errors. If broker is disconnected, dashboard shows stale data with no indication.
**Fix:** Track fetch errors and show a warning banner.

#### 4. Agents — batch action race condition
**File:** `frontend/src/app/agents/page.tsx:69-75`
**Issue:** `handleBatchAction` fires requests in a loop without awaiting, then calls `fetchData()` immediately. Some actions may not have propagated yet.
**Fix:** Use `Promise.all()` and await before fetching.

#### 5. Settings — stale data after modal close
**File:** `frontend/src/app/settings/page.tsx:92`
**Issue:** After connecting a broker or adding a provider via modal, the settings page shows old data until full page refresh.
**Fix:** Call `fetchData()` in the modal's `onConnected`/`onClose` callback.

#### 6. News — no manual refresh button
**File:** `frontend/src/app/news/page.tsx`
**Issue:** 5-minute auto-refresh but no way to manually trigger. During high-impact events, user has to wait.
**Fix:** Add a "Refresh" button next to the last-updated timestamp.

#### 7. Register — terms links not clickable + no client-side age validation
**File:** `frontend/src/app/register/page.tsx:145`
**Issue:** "Terms of Service" and "Privacy Policy" are plain text, not links. Also no client-side check that DOB makes user 18+.
**Fix:** Make them `<a>` tags (even if pointing to placeholder pages). Add JS age check before submit.

#### 8. Models — training button sends hardcoded `pipeline: "scalping"`
**File:** `frontend/src/app/models/page.tsx:150`
**Issue:** Train button always sends `pipeline: "scalping"` — stale from when scalping was the default. Should send `"flowrex_v2"` or `"potential"`.
**Fix:** Update to `pipeline: "flowrex_v2"` or make it a dropdown.

### MEDIUM (8 items)

#### 9. Login — reset token displayed as raw text, no copy button
#### 10. AI Chat — chat input doesn't re-enable after error
#### 11. AI Chat — Telegram test doesn't check if configured first
#### 12. Dashboard — todayPnl filter may fail on timezone mismatch
#### 13. Agents — clone doesn't indicate it's a copy (no "(Copy)" suffix)
#### 14. Models — no loading state after clicking "Retrain All"
#### 15. News — no loading overlay during filter change
#### 16. Settings — 2FA setup doesn't validate response has provisioning_uri

---

## PART 2: Retraining Plan

### Current Data State

| Symbol | M5 Rows | Last Bar | Status |
|---|---|---|---|
| US30 | 1,105,885 | Apr 2026 | ✅ Fresh |
| BTCUSD | 716,465 | Apr 2026 | ✅ Fresh |
| ES | 543,090 | Apr 2026 | ✅ Fresh |
| XAUUSD | 128,482 | Mar 2026 | ⚠️ Stale (Dukascopy M5 fetch failed) |
| NAS100 | 88,022 | Mar 2026 | ⚠️ Stale (Dukascopy M5 fetch failed) |
| ETHUSD | MISSING | — | ❌ M5 fetch failed entirely |
| XAGUSD | MISSING | — | ❌ M5 fetch failed entirely |
| AUS200 | MISSING | — | ❌ M5 fetch failed entirely |

### Current Model State

| Symbol | Flowrex v2 | Potential | Live Performance |
|---|---|---|---|
| XAUUSD | Grade A (Apr 14) | Grade A/B (Apr 14) | **+$7k profit** (potential agent) |
| US30 | Grade A (Apr 15) | Grade A (Apr 14) | Tested, working |
| BTCUSD | Grade A (Apr 15) | Grade A (Apr 14) | **-$1.2k loss, 20% WR** — regime mismatch |
| NAS100 | Grade A (Apr 14) | Grade A (Apr 14) | Not actively tested |
| ES | **Grade F (archived)** | Grade A (Apr 14) | ES Flowrex v2 was rejected |

### What Needs Retraining

#### Step 1: Re-fetch stale/missing M5 data
The Dukascopy fetcher was fixed in Batch 5 with M5 chunking + retry. Need to re-run for:
- **XAUUSD** — M5 fetch failed (128k rows are from 2010 legacy file, not fresh Dukascopy)
- **NAS100** — M5 fetch failed (88k rows from Dec 2024 Databento, not Dukascopy)
- **ETHUSD** — completely missing M5
- **XAGUSD** — completely missing M5
- **AUS200** — completely missing M5

```bash
cd /opt/flowrex/backend/scripts
export PATH=/snap/node/current/bin:$PATH
# Run one at a time (chunked M5, with retry)
node fetch_dukascopy_node.js XAUUSD 2500
node fetch_dukascopy_node.js NAS100 2500
node fetch_dukascopy_node.js ETHUSD 2500
node fetch_dukascopy_node.js XAGUSD 2500
node fetch_dukascopy_node.js AUS200 2500
```

#### Step 2: Retrain BTCUSD with wider SL
**Problem:** 20% WR, all SL_HIT. SL distance too tight (~$100-150, 0.15% of price).
**Fix:** Update symbol_config.py for BTCUSD: `sl_atr_mult: 1.0` → `1.2` or `1.5` (wider stops for crypto volatility).
**Then:** Retrain BTCUSD Flowrex v2.

```bash
cd /opt/flowrex/backend
python3 -m scripts.train_flowrex --symbol BTCUSD --trials 15 --folds 4
```

#### Step 3: Retrain XAUUSD + NAS100 with fresh data
After Step 1 re-fetches succeed, retrain both:
```bash
python3 -m scripts.train_flowrex --symbol XAUUSD --trials 15 --folds 4
python3 -m scripts.train_flowrex --symbol NAS100 --trials 15 --folds 4
```

#### Step 4: Decide on ES Flowrex v2
ES got Grade F twice. Options:
- **(A)** Accept ES Potential Agent only (Grade A, works) — don't retrain Flowrex v2
- **(B)** Retrain with different config (wider SL, different feature set, rolling window instead of expanding)
- **(C)** Skip ES Flowrex v2 entirely until regime changes

**Recommendation:** Option A — ES Potential Agent is performing. Don't waste time on Flowrex v2 for ES.

#### Step 5: Train new symbols (ETHUSD, XAGUSD, AUS200)
After Step 1 data fetch, these can be trained for the first time:
```bash
# Only if M5 data fetch succeeded
python3 -m scripts.train_flowrex --symbol ETHUSD --trials 15 --folds 4
python3 -m scripts.train_flowrex --symbol XAGUSD --trials 15 --folds 4
python3 -m scripts.train_flowrex --symbol AUS200 --trials 15 --folds 4
```

**Note:** These symbols need to be added to `ALL_SYMBOLS` in `train_flowrex.py` and to `symbol_config.py` first. ETHUSD/XAGUSD/AUS200 don't have configs yet.

#### Step 6: Generate feature_stats.json for all symbols
After retraining, the new `save_training_stats()` code (Batch I) will automatically save feature distribution baselines for live drift detection. No manual action needed — it runs as part of training.

### Retraining Time Estimate

| Step | Symbols | Est. Time |
|---|---|---|
| Data re-fetch | XAUUSD, NAS100, ETHUSD, XAGUSD, AUS200 | 30-60 min |
| BTCUSD retrain (wider SL) | BTCUSD | 30 min |
| XAUUSD retrain (fresh data) | XAUUSD | 20 min |
| NAS100 retrain (fresh data) | NAS100 | 20 min |
| New symbols (if M5 succeeds) | ETHUSD, XAGUSD, AUS200 | 60 min |
| **Total** | — | **~2.5-3 hours** |

### Config Changes Before Retraining

1. **BTCUSD `symbol_config.py`** — increase `sl_atr_mult` from `0.8` to `1.2`
2. **Add ETHUSD to `symbol_config.py`** — `asset_class: "crypto"`, similar to BTCUSD
3. **Add XAGUSD to `symbol_config.py`** — `asset_class: "commodity"`, similar to XAUUSD
4. **Add AUS200 to `symbol_config.py`** — `asset_class: "index"`, similar to NAS100
5. **Add all 3 to `ALL_SYMBOLS` in `train_flowrex.py`**

---

## PART 3: Execution Order

### Phase A — Page fixes (est. 3h)
Fix items #1-#8 (CRITICAL + HIGH). Deploy backend + frontend.

### Phase B — Data re-fetch (est. 1h)
Run Dukascopy fetcher for XAUUSD, NAS100, ETHUSD, XAGUSD, AUS200.
Verify all M5 files have >100k rows and extend to April 2026.

### Phase C — Config + retrain (est. 3h)
1. Update BTCUSD symbol_config (wider SL)
2. Add ETHUSD/XAGUSD/AUS200 to symbol_config + ALL_SYMBOLS
3. Retrain in tmux: BTCUSD → XAUUSD → NAS100 → ETHUSD → XAGUSD → AUS200
4. Run `diagnose_flowrex.py` to check new grades

### Phase D — Deploy new models (est. 30m)
1. Restart agents to pick up new models
2. Verify feature_stats.json files were generated
3. Monitor first few evaluations for drift warnings

---

_Total estimated effort: ~7 hours across 4 phases._
_Training can run in tmux while page fixes are deployed._
