# Flowrex Algo — Claude Instructions

## Project Overview
- **Name:** Flowrex Algo
- **Description:** Autonomous algorithmic trading platform with ML-powered agents
- **Tech Stack:** FastAPI + Next.js 14 (App Router) + PostgreSQL + TypeScript + Tailwind CSS
- **Starting Symbols:** BTCUSD, XAUUSD, US30 (expand to ES, NAS100 in Phase 7)
- **Brokers:** Oanda, cTrader, MT5, Tradovate, Interactive Brokers (Client Portal REST)

## Current Phase
**BACKTEST INTEGRITY + POTENTIAL AGENT TUNING + FUNDEDNEXT BOLT PLAN**
(2026-04-20)

### 2026-04-20 — FundedNext Bolt research + known issues queue
- Bolt plan documented (see Prop Firm → Bolt section below). $50k account,
  $3k profit target, $1k daily loss, $2k trailing DD, 40% consistency rule
  in both phases, no overnight holds, EA/automation allowed via Tradovate
  or NinjaTrader 8, strategy switching challenge↔funded prohibited, 5-
  payout lifecycle (first 4 × $1,200 + final $7,700 = $12,500 cap/account).
- Known open issues queued for next sprint:
  1. Symbol-mismatch bug — live agents log "Symbol 'US30/BTCUSD/XAUUSD'
     not available on oanda"; registry maps to US30_USD / BTC_USD / XAU_USD,
     something's bypassing `to_broker()`.
  2. Feature drift on BTCUSD flowrex_v2 — 3 features at z=5-14σ outside
     training distribution; same failure shape as the CVD scale-mismatch bug
     we already fixed — need to audit `features_flowrex.py` for the rest.
  3. Mobile UI: trading-page agent cards clustered; engine log + settings
     page overflow viewport; risk slider needed (0.01 %–3 %, 0.01 lot min).
  4. Agent card layout: collapse-to-one-row + tap-to-expand on mobile.

### 2026-04-19 — Dukascopy delta-merge + Backtest integrity + Potential tuning
- Dukascopy backtest fetch rewritten as delta-merge against bind-mounted
  History Data CSVs. First fetch bootstraps 2,500 days (~2-3 min);
  subsequent runs pull only the delta (~5-25 s) and write back.
  Fixes the "fetching fresh Dukascopy data…" timeout toast.
- Backtest integrity pass (api/backtest.py + broker/manager.py):
  - User-scoped adapter lookup (was iterating all users globally → a minor
    security bug; another user's broker could be used in your backtest).
  - Per-broker M5 candle caps (oanda 5k · mt5 50k · ctrader 5k · tradovate 5k
    · ibkr 1k); H1/H4/D1 scale proportionally. Date range that exceeds the
    broker cap is silently clamped to the earliest bar available and the UI
    shows the real data window (source, broker, first/last bar, cap).
  - Broker asyncio loop-affinity bug fixed: backtest worker thread now
    dispatches broker coroutines to the FastAPI main loop via
    `BrokerManager.run_coroutine_on_loop(...)` (same pattern as
    retrain_scheduler). Was throwing "asyncio.locks.Event bound to a
    different event loop" on Potential + Broker (Live) runs.
  - `oos_start_ts` from the trained joblib now flows to the UI; monthly
    rows tagged IS / OOS / BND (straddle).
  - Breakdowns (direction / exit_type / session / confidence bucket /
    oos_split) computed per trade using `predict_proba` confidences.
  - New `POST /api/backtest/analyze` sends stats + breakdowns to the user's
    Claude supervisor for a 400-word markdown review. Reuses existing
    per-user supervisor — no new LLM stack.
  - Frontend: data-coverage card, OOS caption on equity curve, In-Sample
    vs True-OOS comparison card, 4 breakdown cards, Analyse-with-AI panel.
- Potential agent per-symbol tuning:
  - Runtime TP/SL + min-confidence now read from `symbol_config.py`
    (previously hardcoded 1.5/1.0 ATR + 0.52 — matched XAUUSD by luck, hurt
    BTCUSD/US30/NAS100 with wrong-sized stops).
  - Training labels in `scripts/train_potential.py` now use per-symbol
    `tp_atr_mult` / `sl_atr_mult` / `hold_bars` from the same config, so
    model labels and runtime exits finally agree.
  - US30 + NAS100 config widened from 1.2/0.8 → 1.5/1.0 after OOS backtest
    showed tight stops got chopped on indices.
  - Per-asset-class confidence defaults: commodity 0.52, forex 0.53,
    index/crypto 0.55. Overridable per-agent via `config.min_confidence`.
- Retrains completed (post-CVD-fix + post-TP/SL-fix):
  - `potential_US30`  → Grade A all 4 folds, OOS ready
  - `potential_NAS100` → folds 1-2 A / fold 3 D / fold 4 F — **regime
    break 2024-11 onwards, model saved but DO NOT enable live**
  - `flowrex_XAUUSD` → Grade A walk-forward, B/B/A on OOS block
  - In queue (tmux `experiments2`): ETHUSD, XAGUSD, AUS200
- Tests: 59/59 targeted green (same suite as yesterday — monitoring +17,
  market_hours +9, broker_manager +1; all still passing).

### 2026-04-19 (earlier) — Reporting fixes + IB + multi-broker + Help + PWA
- AI reports: per-user frequency (off/1h/4h/12h/daily), quiet hours, skip
  when markets closed, skip-when-unchanged state hash, 24h liveness ping.
- User timezone autodetect + confirm banner; report headers in local time.
- Supervisor prompt hardened with asset-class hours + no-change rule
  (fixes "SYSTEM FAILURE clock corrupted" hallucinations).
- Interactive Brokers adapter (Client Portal REST) — paper + live, native
  bracket orders, IB contract mapping.
- Multi-broker simultaneous connections; `/api/broker/status` returns
  `brokers: [...]`.
- /help page replacing Settings Feedback tab — broker setup, prop-firm
  compatibility table, FAQ, feedback. Help replaces AI on mobile bottom-nav.
- PWA: manifest + minimal SW (network-first, never caches /api or /ws).

### 2026-04-18 — Engine wiring + AI autonomy + label-leakage fix
- 25+ wiring gaps closed (on_error, parse_actions, RiskManager, feature drift,
  margin check, symbol validation, cooldown persistence, broker reconcile,
  max hold, stale-data hash, broker 5XX retry, feature cache invalidation)
- Label leakage fix: bounded CVD in features_potential.py (root cause of
  backtest 60-79% WR vs live 30% WR divergence). Requires retraining
  existing `potential`-type models before re-enabling those agents.
- Central Telegram bot (@FlowrexAgent_bot): binding codes, webhook,
  per-user chat_id, hourly AI reports, trade alerts, autonomous actions
- Market hours awareness: agents auto-sleep until next open on weekends
- AI chat persistence (migration 006): sessions + messages in DB
- Agent analytics (migration 007): session, MTF, SHAP, timing per trade
- Telegram bindings (migration 008): 6-char connect codes
- Logging cleanup: 25 print()s → structured logger.* with exc_info
- LOG_LEVEL env override (separate from DEBUG flag)
- Frontend: AI chat page rewrite, Telegram Connect card, Analytics tab,
  MKT OPEN/CLOSED badges, sidebar click-to-pin, settings modal,
  broker balance auto-refresh
- 479/479 tests passing (40 new: market_hours + monitoring + webhook)
- 12+ commits pushed to GitHub
- docs/USER-GUIDE.txt written (full user documentation)
- Training in progress: potential XAUUSD+ES retraining (fixes CVD leak)

### 2026-04-15 — Post-Audit Hardening (prior phase)
- 166-finding audit (AUDIT-2026-04-15.md) executed across 11 batches
- 56 new tests added (453 baseline → 479 after this phase)
- Migration 002 closed 6-item schema drift
- 2FA bypass closed, LLM per-user, CORS restricted, CSP headers, rate limits
- Training auto-archive, Dukascopy-direct backtest, walk-forward embargo
- Tradovate 4 critical fixes, GDPR endpoints shipped

## Completed Phases
- Phase 1 — Foundation (2026-03-28)
- Phase 2 — Backend Core (2026-03-28)
- Phase 3 — Broker Adapters (2026-03-28)
- Phase 4 — Frontend Shell (2026-03-28)
- Phase 5 — ML Pipeline (2026-03-28)
- Phase 6 — Scalping Agent (2026-03-28)
- Phase 7 — Expert Agent (2026-03-28)
- Phase 8 — Real-Time WebSockets (2026-03-28)
- Phase 9 — Auth & Polish (2026-03-28)
- Phase 10 — Deploy & Harden (2026-03-28)

## Post-MVP Work (2026-03-30 to 2026-03-31)
- Cross-symbol correlation features (20 features per symbol)
- M15 intermediate timeframe features (7 features)
- Walk-forward training v4: BTCUSD (Grade B, Sharpe 8.9), XAUUSD, US30
- History Data integration (15 years for US30/XAUUSD, 7 years for BTCUSD)
- BTCUSD config overhaul (2x ATR, no trend filter, 28.6% break-even WR)
- Monthly retrain pipeline (retrain_monthly.py, APScheduler, comparison gate)
- Model Feature Toggles UI (correlations, M15, macro features)
- Retrain UI on Models page (trigger, schedule, history table)
- Monthly retrain executed: US30 swapped to fresh 12-month model (Sharpe 2.8), BTCUSD kept (gate protected Grade B)

## Strategy-Informed ML Overhaul (2026-03-31 — in progress)

### User Requirements
- **Trading style:** Hybrid (scalp up to 2hr + swing overnight)
- **Account:** $10,000 prop firm (FTMO), 5% max daily DD, 10% max total DD
- **Target:** 2%+ daily ($200+)
- **Methodologies:** ICT/SMC (full suite), Supply/Demand, Price Action, Larry Williams, Donchian
- **Symbol priority:** US30 first → BTCUSD → XAUUSD
- **Agent structure:** Rapid Agent (multi-strategy, separate models per strategy, highest confidence wins)

### Research Completed (4 streams)
1. **ICT/SMC** — OB, FVG, liquidity sweeps, breakers, OTE, PD arrays, BOS/CHOCH, displacement (~30 features)
2. **Larry Williams** — Volatility breakout (stretch), trend-day ID, Williams %R, COT data, seasonality (~59 features)
3. **Donchian + Quant** — Donchian channels, Turtle rules, RenTech mean-reversion, AQR momentum, Lopez de Prado (meta-labeling, triple barrier, frac diff), Ernest Chan (Hurst, half-life, cointegration)
4. **Prop Firm Risk** — Position sizing (0.75%/trade), tiered DD management, anti-martingale, session windows, R:R math (55% WR × 1:2 R:R × 4 trades = $200/day)

### Implementation Plan (ordered)
| # | Task | New Features | Priority |
|---|------|-------------|----------|
| 1 | ICT/SMC feature module | 30 features (OB, FVG, liq sweeps, BOS/CHOCH, PD, OTE, displacement) | DONE |
| 2 | Larry Williams feature module | 25 features (stretch, range expansion, %R multi-period, smash day) | DONE |
| 3 | Donchian/Turtle feature module | 15 features (MTF channels, squeeze, Hurst, TSMOM) | DONE |
| 4 | COT data pipeline | 8 features (Williams COT Index, commercial positioning) | DONE |
| 5 | Prop firm risk manager overhaul | Tiered DD, anti-martingale, session windows | DONE |
| 6 | Meta-labeling pipeline | Secondary model filters false signals | DONE |
| 7 | Strategy-informed labels | Triple barrier + ICT setup quality scoring | DONE |
| 8 | Retrain US30 with new features | Walk-forward with ~210 features | DONE |
| 9 | Retrain BTCUSD | Same pipeline | DONE |
| 10 | Retrain XAUUSD | Same pipeline | DONE |

### Key Prop Firm Config
```
base_risk_per_trade: 0.75% ($75)
daily_hard_stop: -3% ($300) — 2% buffer below 5% kill switch
max_trades_per_day: 5
max_concurrent_positions: 2
target_rr: 1:2
target_wr: 55%
us30_primary_session: 13:30-15:30 UTC (cash open)
```

### FundedNext Bolt — target prop-firm account (researched 2026-04-20)

Source: fundednext.com/futures/bolt + fundednext.com/futures-challenge-terms
+ helpfutures.fundednext.com/en/articles/11170648 (EAs allowed).

**Account / fees**
- One tier only: **$50,000 account**
- Challenge fee: **$99.99** one-time
- Reset fee: **$91.99**

**Rules (apply to challenge AND funded phase unless noted)**
| Rule | Value |
|---|---|
| Profit target (challenge only) | **$3,000** (6 %) |
| Daily loss limit | **$1,000** (2 %) — EOD aggregate |
| Max trailing drawdown | **$2,000** (4 %) — EOD trailing, stops trailing once EOD hits $50,100 (locks floor at $50k) |
| Consistency rule | **40 % — both phases**. No single day's profit > 40 % of total profit |
| Overnight holds | **Not allowed** — all positions must close before market close |
| News trading | Allowed |
| EAs / automation | **Allowed** (Tradovate + NinjaTrader 8) |
| Strategy switch challenge↔funded | **Prohibited** |
| Min trading days | None |

**Payouts (funded phase)**
- 24-hour payout promise ("or we pay $1,000 extra")
- 5-payout lifecycle, then the account ends
- Payout cap: first 4 × $1,200 + final 5th × $7,700 = **$12,500 total per account**

**What this means for our agent**
- `prop_firm_mode=True` with **$1,000 daily stop**, **$2,000 trailing stop
  tracked EOD**, **0.75 % base risk** ($375 max risk per trade) — existing
  RiskManager covers daily + trailing but needs:
  1. EOD forced-flat at 21:00 UTC (CME close) — new feature.
  2. 40 % consistency gate: track daily P&L, skip trades that would push
     today's profit above 40 % of the $3,000 target ($1,200) — new feature.
  3. Trailing DD that stops trailing at balance ≥ $50,100 — tweak to
     existing RiskManager trailing logic.
  4. Fixed strategy: once we pick the config, lock it. No live re-tunes
     between challenge and funded.
- Training data source: **Databento** for ES / NQ / YM / GC / CL (real CME)
  — we already have that integration wired via `market_data`. Skip
  Tradovate's $25/mo API add-on for training; only needed for live routing.
- Live execution: existing Tradovate adapter. Confirm whether the user's
  live credentials work without the $25 add-on; if 403s, switch to
  NinjaTrader 8 (not yet implemented) or pay.

## Deployed Models (as of 2026-04-07)
| Symbol | Best Model | Grade | Sharpe | Source |
|--------|-----------|-------|--------|--------|
| US30 | LightGBM | A | 4.96 | Potential Agent v2 (2019-2025, 85 features, ATR-normalized) |
| BTCUSD | LightGBM | A | 3.92 | Potential Agent v2 (2020-2025, 85 features, ATR-normalized) |
| XAUUSD | XGBoost | A | 24.17 | Potential Agent v2 (2010-2025, 85 features, small OOS 85 trades) |
| ES | XGBoost | A | 5.78 | Potential Agent v2 (Databento Dec 2024-Mar 2026, 88k bars) |
| NAS100 | LightGBM | A | 6.39 | Potential Agent v2 (Databento Dec 2024-Mar 2026, 88k bars) |

## Production Deployment (2026-04-07)
- **Domain:** flowrexalgo.com (GoDaddy → Cloudflare DNS)
- **Server:** DigitalOcean Droplet 24.144.117.141 (2vCPU, 2GB RAM, NYC1)
- **Stack:** Docker Compose (nginx + FastAPI + Next.js + PostgreSQL)
- **SSL:** Let's Encrypt (expires 2026-07-06)
- **Admin:** Flowrexflex@gmail.com (is_admin=True)
- **Beta codes:** FLOWREX-BETA-001, FLOWREX-BETA-002 (30-day expiry)
- **Broker:** Oanda (primary, paper trading)

## Phase Checklist
| # | Phase | Status |
|---|-------|--------|
| 1 | Foundation (scaffold, DB, config, dev env) | done |
| 2 | Backend Core (models, auth, CRUD APIs) | done |
| 3 | Broker Adapters (Oanda, cTrader, MT5) | done |
| 4 | Frontend Shell (layout, dashboard, agent UI) | done |
| 5 | ML Pipeline (features, ensemble, regime detection) | done |
| 6 | Scalping Agent (engine, risk manager, trade monitor) | done |
| 7 | Expert Agent (multi-symbol, ES/NAS100 expansion) | done |
| 8 | Real-Time WebSockets (live data, notifications) | done |
| 9 | Auth & Polish (2FA, settings, error handling) | done |
| 10 | Deploy & Harden (Docker, CI/CD, monitoring) | done |
| 11 | Potential Agent v2 (institutional features, Grade A) | done |
| 12 | Production Deployment (flowrexalgo.com live) | done |
| 13 | Paper Trading (Oanda, 5 symbols) | in progress |
| 14 | Multi-Symbol (BTCUSD + XAUUSD + ES + NAS100 training) | done |
| 15 | UI Polish + News + Cleanup + Audit | done |
| 16 | Beta Testers + Iteration | in progress |
| 17 | Full Page Audit + Bug Fixes | done |
| 18 | Flowrex Agent v2 (120 features, 3-model ensemble, 4-layer MTF) | done |
| 19 | Claude AI Supervisor (autonomous, chat, Telegram) | done |
| 20 | Tradovate Broker Adapter | done |

## Rules (ALWAYS follow these)
1. **ALWAYS** read `ARCHITECTURE.md` (in `VPrompt/`) before starting any phase.
2. **ALWAYS** read `DEVLOG.md` before starting any task.
3. **ALWAYS** update `DEVLOG.md` after completing any task with what changed and why.
4. **ALWAYS** update `PROGRESS.md` after completing any phase.
5. **ALWAYS** run all existing tests before marking a phase complete.
6. **NEVER** proceed to the next phase without user approval.
7. **ALWAYS** test with the preview tool after building UI components.
8. If you lose context or are unsure where we left off, read `CLAUDE.md`, `PROGRESS.md`, and `DEVLOG.md` in that order.

## Key Files
- `VPrompt/ARCHITECTURE.md` — Full system design, DB schema, API contracts, folder structure
- `VPrompt/phase-XX-*.md` — Detailed instructions per phase
- `PROGRESS.md` — What has been built, per phase
- `DEVLOG.md` — Chronological log of all changes

## ML Training Key Files
- `backend/scripts/train_walkforward.py` — Full walk-forward training (research/initial training)
- `backend/scripts/retrain_monthly.py` — Monthly retrain (12-month rolling + 2-week holdout)
- `backend/scripts/model_utils.py` — Labels, backtest metrics, grading, SHAP
- `backend/app/services/ml/features_mtf.py` — 157-feature pipeline (M5+M15+H1+H4+D1)
- `backend/app/services/ml/features_correlation.py` — Cross-symbol correlation features
- `backend/app/services/ml/symbol_config.py` — Per-symbol TP/SL/cost/trend_filter config
- `backend/app/services/ml/retrain_scheduler.py` — APScheduler monthly cron
- `backend/data/ml_models/` — Deployed models (.joblib)
- `backend/app/services/ml/features_ict.py` — 30 ICT/SMC features (OB, FVG, sweeps, BOS/CHOCH, PD, OTE)
- `backend/app/services/ml/features_williams.py` — 25 Larry Williams features (stretch, %R, smash day)
- `backend/app/services/ml/features_quant.py` — 15 Donchian/Quant features (Hurst, TSMOM, z-scores)
- `backend/app/services/ml/features_cot.py` — 8 COT features (Williams COT Index, commercial positioning)
- `backend/app/services/ml/meta_labeler_v2.py` — Lopez de Prado two-stage meta-labeling
- `backend/scripts/strategy_labels.py` — Triple barrier + ICT quality scoring
- `backend/scripts/fetch_cot_data.py` — CFTC disaggregated futures downloader
- `backend/data/ml_models/archive_v4_2026-03-31/` — Archived baseline models
- `backend/app/services/ml/features_potential.py` — 85 institutional features v2 (ATR-normalized, anchored VWAPs)
- `backend/scripts/train_potential.py` — Potential Agent training (GBM + SHAP, no LSTM in v2)
- `backend/app/services/agent/potential_agent.py` — Potential Agent runtime inference
- `backend/scripts/compare_agents.py` — Side-by-side agent backtesting
- `backend/scripts/forward_test_potential.py` — Dollar P&L forward test ($10k MT5)
- `backend/app/models/market_data.py` — MarketDataProvider model (encrypted API keys)
- `backend/app/api/market_data.py` — Market data provider CRUD + test endpoints
- `backend/app/models/feedback.py` — AccessRequest + FeedbackReport models
- `backend/app/api/feedback.py` — Access request + feedback endpoints + admin approval
- `docker-compose.prod.yml` — Production deployment (nginx + SSL + memory limits)
- `nginx/nginx.conf` — Reverse proxy, WebSocket, rate limiting, security headers
- `scripts/server-setup.sh` — DigitalOcean droplet provisioning
- `scripts/deploy.sh` — Pull + build + restart + health check
- `scripts/backup-db.sh` — PostgreSQL backup (6-hourly, 7-day retention)
- `History Data/data/` — 15-year CSV history (M1/M5/M15/H1/H4 per symbol)
- `backend/app/services/ml/features_flowrex.py` — 120 curated Flowrex v2 features (fx_ prefix)
- `backend/scripts/train_flowrex.py` — Flowrex v2 training (3-model ensemble, walk-forward, Optuna)
- `backend/app/services/agent/flowrex_agent_v2.py` — 4-layer MTF agent + 3-model majority vote
- `backend/app/services/llm/supervisor.py` — Claude AI Supervisor (event-driven monitoring)
- `backend/app/services/llm/telegram.py` — Telegram notifications (trade alerts, daily summary)
- `backend/app/api/llm.py` — LLM config, chat, status API routes
- `backend/app/services/broker/tradovate.py` — Tradovate futures broker adapter (OAuth2)
- `backend/scripts/fetch_dukascopy_node.js` — Fast Node.js Dukascopy data fetcher
