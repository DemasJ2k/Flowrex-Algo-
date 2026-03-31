# Flowrex Algo — VPrompt Build Guide

## What This Is

This folder contains **10 phase prompts** that will guide Claude Code (Opus 4.6, 1M context) to build **Flowrex Algo** — a full autonomous trading platform — from scratch. Each phase is a self-contained prompt you paste into Claude Desktop.

## How To Use

### Setup
1. Open **Claude Desktop** (Claude Opus 4.6, 1M context)
2. Create an empty project folder: `flowrex-algo/`
3. Point Claude Desktop at that folder

### Workflow Per Phase
1. **Read the phase file** (e.g., `phase-01-foundation.md`)
2. **Copy the entire prompt section** into Claude Desktop
3. Let Claude work — it will code, create files, and test using the preview tool
4. When Claude finishes, it will present a **checkpoint report** and ask questions
5. **Answer the checkpoint questions** before moving to the next phase
6. Repeat for all 10 phases

### Rules for Claude
- Claude must **test with the built-in preview tool** after building each component
- Claude must **run unit tests and integration tests** it writes
- Claude must **NOT proceed to the next phase** until you say so
- Claude must **ask checkpoint questions** at the end of every phase
- Claude must **report what was built, test results, and what's next**

## Phase Overview

| Phase | Name | What Gets Built |
|-------|------|-----------------|
| 1 | Foundation | Project scaffold, PostgreSQL, config, folder structure |
| 2 | Backend Core | FastAPI app, DB models, CRUD APIs, migrations |
| 3 | Broker Adapters | Oanda REST, cTrader WebSocket, MT5 adapters |
| 4 | Frontend Shell | Next.js app, layout, pages, chart, dark theme |
| 5 | ML Pipeline | Data collection, feature engineering, model training |
| 6 | Scalping Agent | Scalping agent, engine loop, trade execution |
| 7 | Expert Agent | Full ensemble, meta-labeler, regime detection, LSTM |
| 8 | Real-Time & WebSockets | Live prices, agent logs, notifications |
| 9 | Auth & Polish | Full auth, 2FA, admin, prop firm, backtesting |
| 10 | Deploy & Harden | Render deploy, production hardening, monitoring |

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy, Alembic, asyncio
- **Frontend**: Next.js 14+ (App Router), React 18, TypeScript, Tailwind CSS, UI kit (Claude's choice)
- **Database**: PostgreSQL (Render free tier)
- **ML**: scikit-learn, XGBoost, LightGBM, TensorFlow/Keras (LSTM), Optuna
- **Brokers**: Oanda v20 REST, cTrader Open API (protobuf/WebSocket), MT5 (MetaTrader5 package)
- **Real-time**: WebSockets (native FastAPI + frontend hooks)
- **Deploy**: Render (Web Service + PostgreSQL)

## Starting Symbols

- BTCUSD (crypto, 24/7)
- XAUUSD (gold, session-aware)
- US30 (index, session-aware)

Expand to ES and NAS100 after Phase 7.

## Architecture Reference

See `ARCHITECTURE.md` for the full system design, database schema, API contracts, and component relationships. Claude should read this file at the start of every phase.

## Important Notes

- **No code in these prompts** — they describe WHAT to build, not HOW
- Claude decides implementation details, file structure, naming conventions
- Each phase builds on the previous — don't skip phases
- If Claude asks a question you're unsure about, tell it to use its best judgment
- The prompts reference `ARCHITECTURE.md` — Claude should read it for schema/API details
