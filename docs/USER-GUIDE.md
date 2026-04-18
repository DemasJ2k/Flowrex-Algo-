# Flowrex Algo — User Guide

_Last updated: 2026-04-18_

---

## Table of Contents

1. [What is Flowrex Algo?](#1-what-is-flowrex-algo)
2. [What Flowrex Does](#2-what-flowrex-does)
3. [What You Can Do With the App](#3-what-you-can-do-with-the-app)
4. [Supported Brokers & Symbols](#4-supported-brokers--symbols)
5. [The Pages — Full Tour](#5-the-pages--full-tour)
   - [Dashboard](#dashboard)
   - [Trading](#trading)
   - [Agents](#agents)
   - [Models](#models)
   - [Backtest](#backtest)
   - [News](#news)
   - [AI Chat](#ai-chat)
   - [Settings](#settings)
   - [Admin](#admin)
6. [How the Models Page Works](#6-how-the-models-page-works)
7. [How the Backtest Page Works](#7-how-the-backtest-page-works)
8. [Default Trading Configuration](#8-default-trading-configuration)
9. [Reading Agent Analytics](#9-reading-agent-analytics)
10. [Glossary](#10-glossary)

---

## 1. What is Flowrex Algo?

**Flowrex Algo** is an autonomous algorithmic trading platform. It combines:

- **Machine-learning models** trained on 7+ years of historical candlestick data
- **Live broker integration** (Oanda, cTrader, Tradovate, MT5)
- **AI-powered supervision** via Claude for analysis and autonomous risk management
- **Real-time monitoring** through a web dashboard + Telegram notifications

Think of it as a **managed execution layer** around ML trading signals. You don't write code — you configure agents, pick symbols, set risk limits, and the platform handles everything else.

## 2. What Flowrex Does

For each trading symbol (e.g. XAUUSD, BTCUSD, US30), Flowrex:

1. **Fetches live M5 (5-minute) candlestick data** from your broker every 60 seconds
2. **Computes 120 technical features** (price patterns, order blocks, volume profile, volatility, multi-timeframe alignment, ICT/SMC structure, momentum)
3. **Runs a 3-model ML ensemble** (XGBoost + LightGBM + CatBoost) trained specifically for that symbol
4. **Generates BUY / SELL / HOLD signals** with a confidence score
5. **Applies risk filters** — news filter, session filter, drawdown limits, cooldown
6. **Places orders** on your broker with configured position sizing
7. **Monitors exits** — TP/SL hits, max-hold time, broker reconciliation
8. **Analyzes every trade** via the AI Supervisor and reports to Telegram

All fully automated. You can pause, adjust, or stop any agent at any time.

## 3. What You Can Do With the App

| Capability | Description |
|---|---|
| **Create agents** | One ML-powered bot per symbol. Configure risk, filters, broker. |
| **Start/stop/pause** | Full control over every agent individually or in bulk |
| **Backtest strategies** | Test any config against historical data before going live |
| **Retrain models** | Trigger fresh training on latest data, optionally scheduled monthly |
| **Paper trade or live** | Every agent can run in paper mode (simulated) or live (real money) |
| **AI chat** | Ask Claude about your portfolio, trade analysis, risk exposure |
| **Autonomous actions** | Let Claude auto-pause agents during losing streaks (opt-in) |
| **Telegram alerts** | Every trade open/close, hourly summaries, critical alerts |
| **Economic calendar** | Check upcoming high-impact news before trading |
| **Full analytics** | Win rate by session, confidence calibration, streak tracking |

## 4. Supported Brokers & Symbols

### Brokers

| Broker | Best For | Live Trading | Paper Trading |
|---|---|---|---|
| **Oanda** | Forex, commodities, indices (CFDs) | ✅ | ✅ (practice account) |
| **Tradovate** | US futures (ES, NQ, GC, CL) | ✅ | ✅ (demo) |
| **cTrader** | Forex, prop firms (FTMO, MyForexFunds, etc.) | ✅ | ✅ |
| **MT5** | MT5-compatible brokers | ✅ | ✅ |

### Symbols per Broker

| Symbol | Description | Recommended Broker | Asset Class |
|---|---|---|---|
| **XAUUSD** | Gold | Oanda, MT5, cTrader (prop firms) | Commodity |
| **XAGUSD** | Silver | Oanda, MT5, cTrader | Commodity |
| **BTCUSD** | Bitcoin | Oanda, MT5 | Crypto |
| **ETHUSD** | Ethereum | Oanda, MT5 | Crypto |
| **US30** | Dow Jones Industrial Average | Oanda (CFD), Tradovate (YM futures) | Index |
| **NAS100** | Nasdaq-100 | Oanda (CFD), Tradovate (NQ futures) | Index |
| **ES** | S&P 500 E-mini futures | **Tradovate** (native), Oanda (SPX500 CFD) | Index |
| **AUS200** | Australian S&P/ASX 200 | Oanda, cTrader | Asian Index |

### Prop Firm Compatibility

Prop firms (FTMO, MyForexFunds, TopStep, etc.) typically use **cTrader** or **MT5**. When creating an agent:
- Set broker to `ctrader` or `mt5`
- Enable **Prop Firm Mode** in the agent config
- This enforces tiered drawdown limits: yellow (−1.5%) → red (−2.5%) → hard stop (−3%)

### Which broker for which symbols?

- **ES (S&P 500)** → Tradovate (cheapest, native futures contract)
- **XAUUSD, forex** → Oanda (tight spreads, regulated) or cTrader (prop firms)
- **BTCUSD, ETHUSD** → Oanda (CFD, no crypto exchange needed)
- **US30 / NAS100** → Oanda CFDs (most flexible) or Tradovate futures (cheaper long-term)

## 5. The Pages — Full Tour

### Dashboard

**URL:** `/`

**What's shown:**
- **Today's P&L** — sum across all your agents, live-updating
- **Total equity** — current account balance from your connected broker
- **Open positions** — count of trades currently active
- **Win rate** — rolling win % over recent trades
- **Sparkline charts** — 24-hour P&L trend per symbol
- **Active agents list** — status badges + P&L per agent

**Features:**
- Click any agent to jump to its detail modal
- Real-time updates via WebSocket (no refresh needed)
- Timezone shown in your local time (Sydney by default — configurable)

### Trading

**URL:** `/trading`

**What's shown:**
- **Interactive price chart** (TradingView-style) for the selected symbol
- **Timeframe selector** (M5 / M15 / H1 / H4 / D1)
- **Indicators** — EMA(50), Bollinger Bands, RSI(14)
- **Live orders panel** — your open positions + pending orders
- **Trade history table** — all closed trades, filterable by agent + date
- **Account summary** — balance, margin used, margin available

**Features:**
- Indicator toggle menu (turn on/off per indicator)
- Click a trade row to see entry/exit markers on the chart
- "Agent" column shows which bot placed each trade
- Manual trade buttons (BUY / SELL) — bypass the agents if needed

### Agents

**URL:** `/agents`

**What's shown:**
- **Agent cards** — one per bot, with:
  - Name, symbol, broker, agent type (`flowrex_v2` or `potential`)
  - Status badge (`running` / `stopped` / `paused`)
  - **MKT OPEN / MKT CLOSED** badge (market hours per symbol)
  - P&L, win rate, trade count, profit factor, sparkline
- **Action buttons per agent**: Play / Pause / Stop / Clone / Delete
- **Bulk actions**: Start All, Stop All
- **Filters**: by status, symbol, search

**Features:**
- **New Agent wizard** — guided flow to create an agent in 4 steps:
  1. Choose symbol + agent type
  2. Pick broker + mode (paper/live)
  3. Set risk parameters (or use defaults)
  4. Review & deploy
- **Click agent → detail modal** with 4 tabs:
  - **Performance** — P&L chart, drawdown, equity curve
  - **Analytics** — breakdowns by session, confidence, MTF score, direction, exit reason
  - **Trades** — every trade ever taken by this agent (DataTable with export)
  - **Logs** — every eval, signal, trade, warning, error (filterable)
- **Edit Config** button — hot-reload settings without stopping the agent

### Models

**URL:** `/models`

**What's shown:**
- **Model list** — one row per (symbol, model_type) combination
  - Columns: Symbol, Type (xgboost/lightgbm/catboost), Grade (A/B/C/D/F), Sharpe, WR, Trades, Last Trained
- **Retrain history** — table of past retraining runs
- **Retrain Scheduler** — cron config for monthly auto-retrain

**Features:**
- **Retrain button per symbol** — kicks off a training run in the background
- **Retrain All** — trains every symbol (takes 8-12 hours)
- **Schedule Retrain** — enable monthly auto-retrain on the 1st of each month
- **View history** — see Grade changes over time, which retrains were promoted to production

### Backtest

**URL:** `/backtest`

**What's shown:**
- **Backtest configuration panel**:
  - Symbol dropdown
  - Agent type (`flowrex_v2` / `potential`)
  - Date range (start/end)
  - Sizing mode (Risk % / Max Lots)
  - Risk parameters (risk per trade, daily loss limit, cooldown)
  - Filter toggles (news, session, MTF)
- **Run button** — runs the backtest against Dukascopy historical data
- **Results panel** — Grade, Sharpe, WR, drawdown, total trades, return %, profit factor
- **Equity curve chart**
- **Trade list** — every simulated trade with entry/exit/P&L

**Features:**
- Compare multiple backtests side-by-side
- Export results to CSV
- **Uses Dukascopy M5 tick data** — same source as training, so backtest matches training distribution

### News

**URL:** `/news`

**What's shown:**
- **Economic calendar** — upcoming high-impact events (NFP, FOMC, CPI, GDP)
  - Columns: Date, Time (UTC), Event, Country, Impact (low/medium/high), Actual, Estimate, Previous
- **Filters**: country, impact level, date range
- **Market headlines** — news feed from Finnhub (forex, crypto, general)

**Features:**
- **Manual refresh** button
- **Auto-refresh** every 5 min
- Links to full article on source site
- **News filter** on agents uses the same data — automatically skips trading 30 min before high-impact events

### AI Chat

**URL:** `/ai`

**What's shown:**
- **Left sidebar** — list of your chat sessions (auto-titled from first message)
- **Chat area** — conversation with Claude, renders markdown (tables, headers, code)
- **"New Chat"** button — start a fresh topic
- **Delete** per-session (hover to reveal)
- **Monthly cost** badge in top-right

**Features:**
- **Settings modal** (gear icon) — set Anthropic API key, model choice (Haiku/Sonnet), autonomous mode, Telegram connect
- **Telegram Connect** — click to generate a binding code, send `/start <code>` to @FlowrexAgent_bot
- **Auto-generated "AI Monitoring" session** — where all event-driven AI analyses land (trade closes, hourly summaries)
- **Chat persists across restarts** — nothing is lost

### Settings

**URL:** `/settings`

Tabs:

**Account** — email, password change, 2FA setup, delete account
**Trading** — default risk params, default filters, broker connections
**Security** — 2FA status, active sessions, reset recovery codes
**Providers** — external API keys (Finnhub, AlphaVantage, Databento, Polygon)
**Feedback** — report a bug / request a feature
**Privacy & Data** — GDPR: export your data, delete account permanently
**AI Supervisor** — same settings as the AI page modal

**Features:**
- Auto-refresh broker balance/uptime every 30s
- Provider API keys are encrypted per-user
- Broker Connect button opens a modal with credential fields

### Admin

**URL:** `/admin` (admin users only)

**What's shown:**
- **Invite codes** — generate / revoke beta invite codes
- **Users** — list all users, view last login, disable account
- **Access requests** — pending signup requests, approve/reject
- **Feedback** — user-submitted bug reports and feature requests
- **System** — health status, Docker container stats, DB size

**Features:**
- Create invite code with expiry + single-use toggle
- Search/filter users
- Export data for GDPR compliance

## 6. How the Models Page Works

### Understanding the Model List

Each row represents one **trained ML model** for a specific (symbol, algorithm) pair.

**Columns:**

| Column | Meaning |
|---|---|
| Symbol | Trading pair (XAUUSD, US30, etc.) |
| Type | Algorithm: `xgboost`, `lightgbm`, or `catboost` |
| Grade | Overall backtest grade: **A** (excellent), **B** (good), **C** (marginal), **D** (poor), **F** (unusable) |
| Sharpe | Risk-adjusted return (higher = better; >2 is good, >4 is great, >10 is exceptional) |
| WR | Win rate % on out-of-sample data |
| Trades | Number of trades in the OOS evaluation |
| Last Trained | Timestamp of when this model was created |

**Ensemble behavior:**

A live agent loads all 3 model types (xgboost + lightgbm + catboost) and requires **2-of-3 majority agreement** on direction before generating a signal. All-3 agreement gets a +5% confidence bonus.

### Retraining

**When to retrain:**
- After 4-6 weeks of live trading (market regimes drift)
- After a major feature pipeline change (CVD fix, new indicators)
- When live WR drops significantly below backtest WR
- When a new symbol is added

**How to retrain:**

1. **One symbol** — click the `Retrain` button next to the symbol. Runs in background (~1-2h per symbol). Progress visible in retrain history.
2. **All symbols** — click `Retrain All`. Runs sequentially (~8-12h total).
3. **Scheduled** — enable monthly auto-retrain. Runs on 1st of each month at 00:00 UTC.

**What happens during retraining:**

1. Load historical data (up to 500k bars of M5)
2. Compute 120 features per bar
3. Create triple-barrier labels (TP hit / SL hit / timeout)
4. Walk-forward validation — 4 folds
5. Hyperparameter search (Optuna, 15 trials per fold per model)
6. Train final models on 100% of training data
7. Evaluate on out-of-sample (OOS) data
8. Only save if Grade >= B (quality gate)
9. Archive old models before overwrite (safety)
10. Hot-reload in live agents (no restart needed)

### Retrain Gate

Models only get deployed if they pass the automatic gate:
- Grade must be **A** or **B**
- OOS Sharpe must be > 2.0
- No fold worse than Grade D (walk-forward consistency)
- OOS trade count > 50 (statistical significance)

If a retrain fails the gate, the old model stays in place and the retrain history shows the failure reason.

## 7. How the Backtest Page Works

### Purpose

Backtest a strategy against historical data **before** deploying an agent or changing config. The engine uses the same feature pipeline + models as the live agent, so results reflect what would have happened historically.

### Configuration

| Setting | Meaning |
|---|---|
| **Symbol** | Which pair to test |
| **Agent type** | `flowrex_v2` (3-model ensemble) or `potential` (institutional features) |
| **Date range** | Historical window (default: last 2 years) |
| **Sizing mode** | `Risk %` (size by % of balance) or `Max Lots` (cap at N lots) |
| **Risk per trade** | % of account risked per trade (default 0.10%) |
| **Max daily loss** | Stop trading for the day if losses exceed this % |
| **Cooldown bars** | M5 bars to wait after a trade before taking another |
| **News filter** | Skip trades 30min before high-impact news |
| **Session filter** | Only trade during prime hours for the symbol |

### Running a Backtest

1. Pick symbol, agent type, date range
2. Adjust risk parameters (or leave defaults)
3. Click **Run**
4. Watch progress in real-time (feature computation → simulation → stats)
5. Results appear in 30-60 seconds for a 2-year window

### Reading Results

| Metric | Interpretation |
|---|---|
| **Grade** | A/B/C/D/F overall quality |
| **Sharpe** | Risk-adjusted return; >2 is good |
| **Win Rate** | % of winning trades |
| **Total Trades** | More = more statistical confidence |
| **Total Return** | Absolute % return over the period |
| **Max Drawdown** | Biggest equity dip (lower = better; <10% is acceptable) |
| **Profit Factor** | Gross profit / gross loss; >1.5 is good |

### Equity Curve

The chart shows account value over time. Look for:
- **Smooth upward slope** = consistent strategy (good)
- **Step pattern** = a few big wins dominate (risky)
- **Choppy / sideways** = strategy isn't adding edge
- **Big drawdowns** = risk management needs tightening

### Comparing Backtests

Run the same symbol with different risk/filter settings to see what combo works best. Common experiments:
- Change risk from 0.1% → 0.5% → 1% → see how Sharpe changes
- Toggle news filter on/off → measure impact
- Adjust SL multiplier (ATR×0.8 → ATR×1.2) → trade-off win rate vs avg loss

## 8. Default Trading Configuration

Located at **Settings → Trading**. These defaults apply whenever you create a new agent (you can override per-agent in the wizard).

### Core Risk Settings

| Setting | Default | What it does |
|---|---|---|
| **Risk per Trade (%)** | 0.10% | % of account balance risked per trade. Position size is calculated so that if SL is hit, you lose exactly this %. |
| **Max Daily Loss (%)** | 3.00% | If today's cumulative loss hits this, all agents pause until midnight UTC. |
| **Max Open Positions** | 4 | Total simultaneous open trades per agent. |
| **Cooldown Bars** | 3 | Minimum M5 bars between trades (15 min). Prevents overtrading. |

### Default Agent Filters

| Filter | Default | What it does |
|---|---|---|
| **News Filter** | ON | Skip trades 30 min before / 15 min after high-impact news events (NFP, FOMC, CPI). Requires Finnhub API key. |
| **Session Filter** | ON | Only trade during the symbol's prime session hours. E.g. US30 only trades 13:00-21:00 UTC (NY session). |
| **External Macro Features** | OFF | Adds VIX, TIPS yield, BTC dominance as features. Requires API keys. Keep OFF if you don't have them. |

### When to change defaults

- **Prop firm challenge** → lower Risk per Trade to 0.25%, Max Daily Loss to 2%
- **Live real money** → start at 0.10%, scale up only after 2 weeks of positive live performance
- **Paper trading** → 1-2% is fine for faster learning
- **Crypto (BTCUSD)** → cooldown bars can be higher (10-15) since sessions are 24/7

Defaults are saved per-user. Every new agent you create inherits them as starting values.

## 9. Reading Agent Analytics

Open any agent → **Analytics tab** to see detailed breakdowns.

### Top Stat Cards

| Metric | What it means |
|---|---|
| **Trades** | Total closed trades for this agent |
| **Win Rate** | % profitable. >50% is good; prop firm target is 55%+ |
| **Profit Factor** | Gross wins / gross losses. 1.5+ is solid; 2.0+ is excellent |
| **Total P&L** | Cumulative P&L in account currency |
| **Avg P&L** | Per-trade average. Should be positive. |

### Streak Tracker

- **Current**: `3 winning` or `2 losing` — the ongoing streak
- **Max Win Streak**: best consecutive winners ever
- **Max Loss Streak**: worst consecutive losers ever

> If **Max Loss Streak** is very high (e.g. 8+), the model has occasional bad regimes. Consider reducing risk % or adding a regime filter.

### By Session

Shows win rate + avg P&L for each trading session:

| Session | UTC hours | Best for |
|---|---|---|
| **asian** | 00-08 | Low volatility, XAUUSD can trend |
| **london** | 08-13 | High volatility, forex active |
| **ny_open** | 13-17 | Highest volatility, indices active |
| **ny_close** | 17-21 | Moderate, positioning |
| **off_hours** | 21-24 | Very low, avoid |

> **Actionable**: If one session has >70% WR and another <40%, turn on the **Session Filter** and restrict to the good session.

### By Confidence

Shows win rate for each confidence bucket (0.50-1.00):

| Bucket | What to expect |
|---|---|
| 0.50-0.60 | Weak signals, typically low WR |
| 0.60-0.70 | Borderline |
| 0.70-0.80 | Good — most trades should be here |
| 0.80-0.90 | Strong conviction |
| 0.90-1.00 | High conviction, usually the best WR |

> **Check calibration**: If 0.90+ confidence is only winning 55%, the model is **overconfident**. Consider retraining.

### By MTF Score

Multi-timeframe alignment: 2/3 = two of [D1 bias, H4 momentum, H1 setup] agree. 3/3 = all three agree (strongest setup).

> **Expected**: 3/3 should have higher WR than 2/3. If they're equal, the MTF filter isn't adding edge and you could simplify.

### By Direction

Win rate + P&L split between BUY and SELL.

> **Actionable**: If BUY is 65% WR and SELL is 35%, the model has a directional bias (common in trending markets). Consider disabling the losing direction.

### By Exit Reason

| Reason | Meaning |
|---|---|
| **TP_HIT** | Take-profit reached — a clean win |
| **SL_HIT** | Stop-loss reached — loss |
| **MAX_HOLD_TIME** | Trade closed after 24h timeout — usually small win/loss |
| **MAX_HOLD_RECONCILED** | Max-hold close failed on broker, DB reconciled after broker closed it |
| **RECONCILED** | Trade found missing on broker (manual close or error) |
| **CANCELLED** | Order rejected or cancelled before filling |

> **Healthy ratio**: ~60-70% TP_HIT, 30-40% SL_HIT, <5% others. If MAX_HOLD_TIME or RECONCILED is high, something's wrong.

### Recent Trades List

Last 20 closed trades with enriched data:
- Direction, P&L, confidence, session, MTF score, model name
- Entry / exit times
- Time to exit (how long the trade lasted)
- Top features (when available — shows which feature drove the prediction)

## 10. Glossary

| Term | Meaning |
|---|---|
| **Agent** | A running bot for one symbol. You can have many agents simultaneously. |
| **Agent Type** | `flowrex_v2` (3-model ensemble + 4-layer MTF filter) or `potential` (institutional features, VWAP/ORB-heavy) |
| **Backtest** | Simulated trading against historical data |
| **Ensemble** | Multiple ML models voting on direction (2-of-3 majority) |
| **Feature** | A numerical input to the ML model (e.g. RSI, EMA distance, volume profile position) |
| **Grade (A/B/C/D/F)** | Automatic backtest quality rating based on Sharpe + WR + drawdown |
| **M5** | 5-minute candlestick timeframe |
| **MTF** | Multi-Timeframe — using D1/H4/H1 context when deciding on M5 |
| **OOS** | Out-of-Sample — data the model never saw during training |
| **Paper mode** | Simulated trades, no real money |
| **Potential Agent** | Alternative agent type using institutional strategies (VWAP, Volume Profile, anchored indicators) |
| **Prop Firm Mode** | Strict tiered drawdown limits for FTMO-style challenges |
| **Sharpe Ratio** | Risk-adjusted return. >1 is acceptable, >2 is good, >4 is excellent |
| **SL / TP** | Stop-Loss / Take-Profit (automatic exit prices) |
| **Walk-forward** | Training validation method — model trained on past, tested on future |
| **Win Rate (WR)** | % of trades that made money |

---

## Quick Start Checklist for a New User

1. ☐ Register with invite code → accept terms → verify age
2. ☐ Go to **Settings → Trading** — review defaults, adjust if needed
3. ☐ Go to **Settings → Broker Connections** — add Oanda (practice account for safety)
4. ☐ Go to **Agents → New Agent** — create one agent (XAUUSD recommended first, paper mode)
5. ☐ Click the agent → watch **Logs** tab for "Eval #1" confirming it's polling
6. ☐ Wait 1-2 hours for first trade (or backtest first to preview behavior)
7. ☐ Go to **AI Chat → Settings** — add Anthropic API key, enable Supervisor
8. ☐ Connect Telegram (optional but recommended) — get live alerts
9. ☐ After 2 weeks on paper, switch one agent to `live` mode with small size

**Support:** Submit feedback via **Settings → Feedback** tab. For bugs, include logs from the **Agents → Logs** tab.
