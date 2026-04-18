# Migration: Potential-Agent CVD Leakage Fix

**Date:** 2026-04-18
**Affects:** agents with `agent_type="potential"` (XAUUSD id=72, ES id=85)
**Does not affect:** agents with `agent_type="flowrex_v2"` (already had bounded CVD since 2026-04-15)

## What changed

`app/services/ml/features_potential.py` previously used an **unbounded cumulative sum** for CVD (cumulative volume delta):

```python
# OLD — BUG
cvd = np.cumsum(cvd_delta)
```

During training, this ran over 500k+ bars of history, producing CVD values in the millions. During **live inference**, the agent loads only the last 500 M5 bars, so CVD accumulates from zero — values are ~1000× smaller.

The model learned patterns from large-scale CVD during training but sees small-scale CVD in production. This is a root cause of the observed backtest-vs-live divergence (backtest Grade A with 60-79% WR → live ~30% WR).

## Fix

Replaced with a bounded rolling window (identical to what `features_flowrex.py` already has):

```python
# NEW — FIXED
cvd = pd.Series(cvd_delta).rolling(100, min_periods=20).sum().fillna(0).values
```

Rolling-100 keeps values consistent between backtest (sees many 100-bar windows) and live (sees the same 100-bar window at the latest bar).

## Required action before re-enabling `potential` agents

**⚠️ The existing deployed `potential_*.joblib` models were trained on BUGGY CVD. Running them now will make live predictions worse, not better, until you retrain.**

### Step 1: Retrain potential models

```bash
cd /opt/flowrex/backend
python3 -m scripts.train_potential --symbol XAUUSD
python3 -m scripts.train_potential --symbol ES
# Optional: re-check other symbols if you plan to use them with potential type
python3 -m scripts.train_potential --symbol BTCUSD
python3 -m scripts.train_potential --symbol US30
python3 -m scripts.train_potential --symbol NAS100
```

Each symbol takes ~1-2 hours on the droplet (features + walk-forward 4 folds + final models + OOS eval).

### Step 2: Verify grade before deploying

The training pipeline will save new models to `backend/data/ml_models/potential_{SYMBOL}_M5_*.joblib` after passing its internal gate (Grade A or B). If a symbol fails to grade, the old model stays in place — check the training log.

### Step 3: Restart the agent

```bash
# From the frontend
Agents page → click the agent → Start
```

Or via API:
```bash
curl -X POST https://flowrexalgo.com/api/agents/{id}/start \
  -H "Authorization: Bearer $TOKEN"
```

The agent's pre-flight check will validate the feature count matches (`EXPECTED_FEATURE_COUNT` on the model), so any mismatch will surface cleanly at start.

### Step 4: Monitor for 1-2 days on paper

Before switching to real money:

1. Create a `mode=paper` agent (not `live`)
2. Let it run for 48h
3. Compare live WR vs the OOS backtest WR:
   - **If gap < 10%**: the label leakage was the problem, and you're good to go live
   - **If gap > 20%**: other leakage sources exist — open an issue and revert

## Symbols currently safe to run NOW (no retraining needed)

These use `flowrex_v2` which was never affected by the CVD bug:

| Agent | Symbol | Status |
|-------|--------|--------|
| 78 "GOLD" | XAUUSD | ✅ Safe — `flowrex_v2` |
| 81 "BTCUSD Flowrex" | BTCUSD | ✅ Safe — `flowrex_v2` |
| 83 "NAS100 Flowrex" | NAS100 | ✅ Safe — `flowrex_v2` |
| 84 "US30 Flowrex" | US30 | ✅ Safe — `flowrex_v2` |

## Symbols that need retraining

| Agent | Symbol | Status |
|-------|--------|--------|
| 72 "XAUUSD Flowrex" | XAUUSD | ⚠️ Retrain before start (`potential`) |
| 85 "ES Flowrex" | ES | ⚠️ Retrain before start (`potential`) |

## Rollback

If retraining fails or you want to revert to the old feature pipeline:

```bash
cd /opt/flowrex
git checkout HEAD~1 -- backend/app/services/ml/features_potential.py
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml up -d backend
```

This restores the buggy cumsum but keeps the old models working with their original training distribution. **Not recommended** — the backtest-vs-live gap will remain.
