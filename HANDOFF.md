# Handoff: Flowrex Algo — Session pickup for new Claude Code session

_Last session ended 2026-04-13 in a Claude Code sandbox environment. This doc brings a fresh Claude session up to speed._

---

## Read these first (in order, ~3 minutes)

1. `CLAUDE.md` — project overview, phase checklist, key files
2. `DEVLOG.md` — especially the **2026-04-13** section (training diagnostics + data audit)
3. `PROGRESS.md` — what's built per phase
4. This file (you're reading it)

---

## Current State (as of 2026-04-13)

### What's deployed at flowrexalgo.com
- **Droplet**: DigitalOcean `24.144.117.141`, currently **8GB RAM** (upsized from 2GB for training)
- **Branch**: `main` (just force-pushed from `main-gNXS2` which was the dev branch; backup of prior main is in `main-lstm-archive`)
- **Latest commits** (all on main):
  - `1a9b597` DEVLOG: Apr 13 training diagnostics
  - `111eddb` Add catboost to requirements.txt
  - `382acf9` Fix fx_delta_divergence (bounded CVD)
  - `83e43c3` Fix: hot-reload agent config on Edit Config save ← **critical user-facing bug fix**
  - `90831ba` Fix diagnose_flowrex recommendation logic
  - `33abdfc` Fix lot size bug (max_lot_size cap in risk_pct mode)

### Agents running on the droplet
User has both **Potential Agents** (5 symbols, Grade A per OOS) and **Flowrex v2 Agents** (XAUUSD, NAS100) on Oanda paper. They want to run them in parallel for 1 week to compare P&L.

### Oanda broker status
**Was disconnected** at end of last session — the Docker rebuild dropped the in-memory broker connection. User needs to reconnect via Settings → Broker → Oanda → Connect.

---

## Completed in the previous session (2026-04-13)

### Bugs fixed
1. **Lot size bug** (`33abdfc`): `max_lot_size` was capping trades even in `risk_pct` mode. Fixed so cap only applies in `max_lots` mode.
2. **Config hot-reload bug** (`83e43c3`): Edit Config was saving to DB but running agents held stale in-memory config. User set `risk=0.10%` but agent kept using `1.00%` — caused ~$13k loss on XAUUSD paper account with 123-247 unit trades (should've been 12-25 units). Fixed via `engine.reload_agent_config()` on PUT.
3. **fx_delta_divergence overfitting** (`382acf9`): Unbounded `np.cumsum(volume*sign)` produced symbol-dependent magnitudes. SHAP showed 26.79% importance on US30 vs 1.97% on BTCUSD. Rewrote with bounded rolling 100-bar CVD and 20-bar direction comparison.
4. **catboost missing from Docker** (`111eddb`): Added to `backend/requirements.txt`.
5. **BTCUSD OOS-only recommendation** (`90831ba`): Diagnose script was recommending DEPLOY based on OOS alone, ignoring -10.92 Sharpe / 211% DD in walk-forward. Fixed logic to weight walk-forward health equal to OOS.

### Data audit results
Ran full Dukascopy data audit. Found silent M5 fetch failures:
- **US30**: ✅ Fresh 1.4M rows, 2021-2026
- **BTCUSD**: ✅ Fresh 1M rows, 2021-2026
- **XAUUSD**: ❌ M5 stale (128k rows, Apr 7) → **Re-fetched Apr 13**: now 322k rows
- **ES**: ❌ Complete fallback (no Dukascopy data saved) → **Re-fetched Apr 13**: now 204k rows
- **NAS100**: ❌ M5 stale (88k rows, Dec 2024 only) → **Re-fetched Apr 13**: now 283k rows

All 3 re-fetched symbols have fresh Dukascopy data ready for retraining.

### Flowrex v2 training results (from OLD data, pre-refresh)
| Symbol | OOS Grade | WF Worst | Verdict |
|--------|-----------|----------|---------|
| US30 | F (-0.84) | F (-3.90 Fold 4, 93.7% DD) | Regime sensitivity + feature overfit |
| BTCUSD | A (2.70) | F (**-10.92 Fold 2**, 211% DD = FTX collapse) | Regime sensitivity |
| XAUUSD | A (40.82, 85 trades) | A all folds, Sharpe +1.49 minimum | **Only truly reliable symbol** |
| ES | F (-4.12) | F (-5.97 Fold 4, 133% DD) | Actually stale data bug, not feature issue |
| NAS100 | A (5.75, 245 trades) | A (Sharpe trend +5.73, improving) | Real edge but small sample |

### Potential Agent v2 vs Flowrex v2 (OOS comparison)
| Symbol | Potential v2 | Flowrex v2 | Winner |
|--------|---|---|---|
| US30 | **A / 4.96 / 253** | F / -0.84 / 298 | Potential ✓ |
| BTCUSD | **A / 3.92 / 714** | A / 2.70 / 1510 | Potential (slightly) ✓ |
| XAUUSD | B / 11.38 / 73 | **A / 40.82 / 85** | Flowrex |
| ES | **A / 4.33 / 245** | F / -4.12 / 263 | Potential ✓ |
| NAS100 | **A / 6.39 / 242** | A / 5.75 / 245 | ~Tie |

**Key insight**: Potential Agent (85 features, simpler) beats Flowrex v2 (120 features, more complex) on 4/5 symbols. The extra features add noise, not signal, for indices.

---

## Immediate Tasks (Priority Order)

### 1. Deploy latest fixes to droplet
```bash
cd /opt/flowrex
git pull origin main
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml up -d --force-recreate backend
```
This pulls the config hot-reload fix, fx_delta_divergence rewrite, and adds catboost to the container.

### 2. Reconnect Oanda broker
User needs to do this manually in the UI: Settings → Broker Connections → Oanda → Connect. Or help them via API if they prefer.

### 3. Restart running agents
After broker reconnects, stop → start each running agent so they pick up fresh config from DB (with the hot-reload fix this will be unnecessary going forward, but needed once to clear stale state).

### 4. Retrain XAUUSD, NAS100, ES with fresh Dukascopy data
User wants these 3 retrained. US30 and BTCUSD are SKIPPED — their data was already fresh, their issues are code-level (regime sensitivity).

```bash
cd /opt/flowrex/backend
tmux new -s retrain -d "python3 -m scripts.train_flowrex --symbol XAUUSD --trials 15 --folds 4 2>&1 | tee /tmp/retrain_xauusd.log; python3 -m scripts.train_flowrex --symbol NAS100 --trials 15 --folds 4 2>&1 | tee /tmp/retrain_nas100.log; python3 -m scripts.train_flowrex --symbol ES --trials 15 --folds 4 2>&1 | tee /tmp/retrain_es.log"
```

Estimated ~1-1.5 hours total. Check progress via `tmux attach -t retrain` or `tail -50 /tmp/retrain_xauusd.log`.

**Prerequisites already done**: pip3 has installed numpy, pandas, xgboost, lightgbm, catboost, optuna, shap, joblib, scikit-learn on the host Python. Training runs outside Docker.

### 5. Run diagnostic after retraining
```bash
cd /opt/flowrex/backend
python3 -m scripts.diagnose_flowrex
```
Produces the per-symbol walk-forward breakdown, top features, feature group analysis, deploy/reject recommendations, and Potential vs Flowrex comparison.

### 6. Deploy new models if results improve
If ES goes from Grade F to Grade A/B, or XAUUSD/NAS100 look better with fresh data, user wants to deploy new Flowrex v2 agents. Keep Potential Agents running in parallel for comparison.

---

## Key Files (Quick Reference)

### Training pipeline
- `backend/scripts/train_flowrex.py` — 3-model ensemble (XGBoost + LightGBM + CatBoost), walk-forward, Optuna
- `backend/scripts/train_potential.py` — Original Potential Agent training (85 features)
- `backend/scripts/diagnose_flowrex.py` — Post-training analysis
- `backend/scripts/model_utils.py` — Labels, backtest metrics, grading, SHAP
- `backend/scripts/fetch_dukascopy_node.js` — Node.js Dukascopy fetcher (requires Node 20+, uses snap)

### Features
- `backend/app/services/ml/features_flowrex.py` — 120 curated Flowrex v2 features (fx_ prefix)
- `backend/app/services/ml/features_potential.py` — 85 Potential Agent features (pot_ prefix)
- `backend/app/services/ml/features_ict.py` — ICT/SMC features
- `backend/app/services/ml/features_williams.py` — Larry Williams features
- `backend/app/services/ml/features_quant.py` — Donchian/Quant features

### Agents
- `backend/app/services/agent/flowrex_agent_v2.py` — 4-layer MTF agent with 3-model majority vote
- `backend/app/services/agent/potential_agent.py` — Production Potential Agent
- `backend/app/services/agent/engine.py` — AgentRunner, AlgoEngine singleton, `reload_agent_config()`

### Backend
- `backend/app/api/agent.py` — Agent CRUD endpoints (update_agent now hot-reloads config)
- `backend/app/api/ml.py` — `/api/ml/models`, `/api/ml/potential-models`, `/api/ml/flowrex-models`
- `backend/main.py` — FastAPI app, router registration

### Frontend
- `frontend/src/components/AgentWizard.tsx` — New Agent modal (Flowrex v2 is default agent type)
- `frontend/src/components/AgentConfigEditor.tsx` — Edit Config modal (triggers hot-reload on save)
- `frontend/src/app/models/page.tsx` — Models page with Flowrex v2 section
- `frontend/src/app/ai/page.tsx` — Claude AI Supervisor chat + config page

### Data
- `History Data/data/{SYMBOL}/{SYMBOL}_{TF}.csv` — Dukascopy OHLCV data (M5/H1/H4/D1)
- `backend/data/ml_models/flowrex_{SYMBOL}_M5_{type}.joblib` — Trained Flowrex v2 models
- `backend/data/ml_models/potential_{SYMBOL}_M5_{type}.joblib` — Trained Potential models

---

## Known Issues / Backlog (Not Urgent)

- **US30 Flowrex v2 Grade F** — Regime sensitivity + feature concentration. Needs rolling-window training (not expanding) and/or regime-aware features.
- **BTCUSD Flowrex v2 walk-forward catastrophe** — Fold 2 (-10.92 Sharpe, 211% DD) was FTX collapse. Needs regime detection.
- **ES Flowrex v2** — May be fixed by fresh data; if not, needs investigation.
- **The sandbox Claude was running in** couldn't access Dukascopy externally. Data fetches had to happen on the droplet. User was on phone for part of the session, had trouble with pipe characters in commands — they're now on a computer with VS Code Remote-SSH.

## Gotchas
- **Don't use Dukascopy data for ES futures contract rolls** — Dukascopy provides S&P 500 CFD (`usa500idxusd`), not ES futures. Existing `_databento` files are futures contracts. These are different instruments and shouldn't be mixed.
- **Training uses host Python**, not Docker. Docker container is for production inference, not training.
- **Flowrex v2 agents need all 3 models (XGBoost + LightGBM + CatBoost) for full ensemble**. If catboost fails to load, agent runs with 2/3 models which still works but weaker majority vote.
- **`engine.reload_agent_config()` runs on PUT to update_agent**. If you're testing config changes, verify the log line `"Config reloaded: risk=X.XX%, daily_loss=Y.Y%, cooldown=Z"` appears.

---

## Session Environment
- Droplet IP: `24.144.117.141`
- Working directory: `/opt/flowrex`
- Current branch: `main`
- Backup branch: `main-lstm-archive` (contains 2 old commits: LSTM-Transformer + ICT strategy features — intentionally discarded per user)
- Node.js path (for Dukascopy fetcher): `export PATH=/snap/node/current/bin:$PATH`
- Python: `python3` (host), installed training deps via `pip3 install`

---

_Generated by previous Claude Code session, 2026-04-13_
