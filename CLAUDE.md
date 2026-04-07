# Flowrex Algo — Claude Instructions

## Project Overview
- **Name:** Flowrex Algo
- **Description:** Autonomous algorithmic trading platform with ML-powered agents
- **Tech Stack:** FastAPI + Next.js 14 (App Router) + PostgreSQL + TypeScript + Tailwind CSS
- **Starting Symbols:** BTCUSD, XAUUSD, US30 (expand to ES, NAS100 in Phase 7)
- **Brokers:** Oanda, cTrader, MT5

## Current Phase
**PRODUCTION DEPLOYMENT** (2026-04-07)
- Phase: Site live at https://flowrexalgo.com → Paper trading next → BTCUSD/XAUUSD training

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
| 13 | Paper Trading (Oanda US30) | in progress |
| 14 | Multi-Symbol (BTCUSD + XAUUSD + ES + NAS100 training) | done |
| 15 | UI Polish + News + Cleanup | next |

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
