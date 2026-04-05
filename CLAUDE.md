# Flowrex Algo — Claude Instructions

## Project Overview
- **Name:** Flowrex Algo
- **Description:** Autonomous algorithmic trading platform with ML-powered agents
- **Tech Stack:** FastAPI + Next.js 14 (App Router) + PostgreSQL + TypeScript + Tailwind CSS
- **Starting Symbols:** BTCUSD, XAUUSD, US30 (expand to ES, NAS100 in Phase 7)
- **Brokers:** Oanda, cTrader, MT5

## Current Phase
**POST-MVP: ML Training & Retrain Pipeline** (2026-03-31)

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

## Post-MVP Work (2026-04-05)
- Strategy-informed feature pipeline: 53 new features (ICT, VWAP, S/D Zones, Wyckoff, RSI Divergence, Breakout-Retest)
- features_ict.py: 20 ICT features (liquidity sweeps, enhanced FVG/OB, kill zones, Silver Bullet, MSS, confluence score)
- features_institutional.py: 18 features (VWAP daily reset, volume profile, supply/demand zones, Wyckoff events, absorption)
- features_divergence.py: 15 features (RSI regular/hidden divergence, MACD divergence, breakout/retest detection)
- Strategy-informed labeling system: create_strategy_labels() in model_utils.py (ICT confluence-based)
- label_mode config: "price" (backward compatible) or "strategy" (ICT confluence) per symbol
- Total features: 157 -> ~210

## Deployed Models (as of 2026-03-31)
| Symbol | Best Model | Grade | Sharpe | Source |
|--------|-----------|-------|--------|--------|
| BTCUSD | LightGBM | B | 10.07 | Walk-forward v4 (2020-2024, 4 folds) |
| US30 | XGBoost | C | 2.80 | Monthly retrain (Mar 2024-2025, 12-month rolling) |
| XAUUSD | XGBoost | F | — | Walk-forward v5 (2010-2024, needs fresh data) |

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
- `backend/app/services/ml/features_ict.py` — 20 ICT features (liquidity, FVG, OB, killzones, confluence)
- `backend/app/services/ml/features_institutional.py` — 18 features (VWAP, volume profile, S/D zones, Wyckoff)
- `backend/app/services/ml/features_divergence.py` — 15 features (RSI divergence, breakout-retest)
- `backend/data/ml_models/archive_v4_2026-03-31/` — Archived baseline models
- `History Data/data/` — 15-year CSV history (M1/M5/M15/H1/H4 per symbol)
