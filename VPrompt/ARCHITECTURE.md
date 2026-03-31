# Flowrex Algo — Architecture Reference

This document is the single source of truth for the entire system design. Claude should read this at the start of every phase.

---

## 1. System Overview

Flowrex Algo is an autonomous algorithmic trading platform that:
- Connects to live broker accounts (Oanda, cTrader, MT5)
- Runs ML-powered trading agents 24/7
- Executes trades automatically with risk management
- Provides a real-time trading terminal UI

### High-Level Flow
```
User deploys agent via UI
  -> Agent Engine starts polling broker for M5 candles (every 40 seconds)
  -> On each new closed bar:
       -> Compute 80+ technical features (M5 + H1/H4/D1 context)
       -> Run ML models (XGBoost, LightGBM, optionally LSTM)
       -> Voting: models must agree on direction with sufficient confidence
       -> If signal fires: compute SL/TP, position size, execute via broker
       -> Log everything to DB (visible in frontend)
  -> Trade monitor watches open positions for SL/TP hits
```

---

## 2. Folder Structure

```
flowrex-algo/
├── backend/
│   ├── main.py                    # FastAPI entrypoint
│   ├── alembic/                   # DB migrations
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py          # Settings (env vars, DB URL, secrets)
│   │   │   ├── database.py        # SQLAlchemy engine + SessionLocal
│   │   │   ├── auth.py            # JWT creation/verification
│   │   │   ├── websocket.py       # WebSocket connection manager
│   │   │   └── encryption.py      # Fernet encryption for API keys
│   │   ├── models/
│   │   │   ├── user.py            # User, UserSettings
│   │   │   ├── agent.py           # TradingAgent, AgentLog, AgentTrade
│   │   │   ├── broker.py          # BrokerAccount (encrypted credentials)
│   │   │   ├── ml.py              # MLModel metadata
│   │   │   └── strategy.py        # Strategy definitions
│   │   ├── schemas/               # Pydantic request/response models
│   │   ├── api/
│   │   │   ├── auth.py            # Login, register, 2FA
│   │   │   ├── agent.py           # Agent CRUD, start/stop, logs, trades
│   │   │   ├── broker.py          # Connect, disconnect, positions, orders
│   │   │   ├── ml.py              # Model status, training triggers
│   │   │   ├── settings.py        # User settings
│   │   │   └── admin.py           # Admin endpoints
│   │   └── services/
│   │       ├── broker/
│   │       │   ├── base.py        # BrokerAdapter ABC
│   │       │   ├── oanda.py       # Oanda v20 REST adapter
│   │       │   ├── ctrader.py     # cTrader Open API adapter
│   │       │   └── mt5.py         # MetaTrader 5 adapter
│   │       ├── agent/
│   │       │   ├── engine.py      # AlgoEngine + AgentRunner
│   │       │   ├── scalping_agent.py
│   │       │   ├── expert_agent.py
│   │       │   ├── risk_manager.py
│   │       │   ├── trade_monitor.py
│   │       │   └── instrument_specs.py  # Per-symbol lot sizing
│   │       ├── ml/
│   │       │   ├── features_mtf.py     # 80+ feature computation
│   │       │   ├── ensemble_engine.py  # Multi-model voting
│   │       │   ├── regime_detector.py  # HMM regime classification
│   │       │   └── meta_labeler.py     # Should-I-trade filter
│   │       ├── backtest/
│   │       │   ├── engine.py      # Backtesting engine
│   │       │   └── indicators.py  # ATR, RSI, EMA, etc.
│   │       └── news/
│   │           └── newsapi_provider.py  # High-impact news filter
│   ├── data/
│   │   └── ml_models/             # Saved .joblib model files
│   └── scripts/
│       ├── train_scalping_pipeline.py
│       ├── train_expert_agent.py
│       └── collect_data.py
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx           # Dashboard
│   │   │   ├── trading/page.tsx   # Trading terminal
│   │   │   ├── agents/page.tsx    # Agent management
│   │   │   ├── models/page.tsx    # ML model status
│   │   │   ├── backtest/page.tsx  # Backtesting
│   │   │   └── settings/page.tsx  # Settings
│   │   ├── components/
│   │   │   ├── AgentPanel.tsx     # Agent cards with logs
│   │   │   ├── CandlestickChart.tsx  # TradingView lightweight-charts
│   │   │   └── ...
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   ├── useAgents.ts
│   │   │   └── useMarketData.ts
│   │   ├── lib/
│   │   │   ├── api.ts             # Axios wrapper
│   │   │   └── indicators.ts      # Client-side indicator calc
│   │   └── types/
│   │       └── index.ts           # TypeScript interfaces
│   └── ...
└── render.yaml                    # Render deploy config
```

---

## 3. Database Schema (PostgreSQL)

### users
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| email | VARCHAR(255) UNIQUE | |
| password_hash | VARCHAR(255) | bcrypt |
| totp_secret | VARCHAR(255) NULL | 2FA (encrypted) |
| is_admin | BOOLEAN DEFAULT false | |
| created_at | TIMESTAMPTZ | |

### user_settings
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| user_id | FK -> users | UNIQUE |
| theme | VARCHAR(20) DEFAULT 'dark' | |
| default_broker | VARCHAR(50) NULL | |
| notifications_enabled | BOOLEAN DEFAULT true | |
| settings_json | JSONB DEFAULT '{}' | Extensible |

### broker_accounts
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| user_id | FK -> users | |
| broker_name | VARCHAR(50) | 'oanda', 'ctrader', 'mt5' |
| credentials_encrypted | TEXT | Fernet-encrypted JSON blob |
| is_active | BOOLEAN DEFAULT true | |
| created_at | TIMESTAMPTZ | |

### trading_agents
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| created_by | FK -> users | |
| name | VARCHAR(100) | User-facing name |
| symbol | VARCHAR(20) | 'XAUUSD', 'BTCUSD', 'US30' |
| timeframe | VARCHAR(10) | 'M5' |
| agent_type | VARCHAR(20) | 'scalping' or 'expert' |
| broker_name | VARCHAR(50) | |
| mode | VARCHAR(20) DEFAULT 'paper' | 'paper' or 'live' |
| status | VARCHAR(20) DEFAULT 'stopped' | 'running','stopped','paused','error' |
| risk_config | JSONB | {risk_per_trade, max_daily_loss_pct, ...} |
| created_at | TIMESTAMPTZ | |
| deleted_at | TIMESTAMPTZ NULL | Soft delete |

### agent_logs
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| agent_id | FK -> trading_agents | |
| level | VARCHAR(20) | 'info','warn','error','signal','trade' |
| message | TEXT | |
| data | JSONB NULL | Optional structured data |
| created_at | TIMESTAMPTZ DEFAULT now() | |

### agent_trades
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| agent_id | FK -> trading_agents | |
| symbol | VARCHAR(20) | |
| direction | VARCHAR(10) | 'BUY' or 'SELL' |
| entry_price | FLOAT | |
| exit_price | FLOAT NULL | |
| stop_loss | FLOAT | |
| take_profit | FLOAT | |
| lot_size | FLOAT | |
| pnl | FLOAT NULL | Paper P&L |
| broker_pnl | FLOAT NULL | Real broker P&L (preferred) |
| broker_ticket | VARCHAR(100) NULL | Broker's trade/position ID |
| status | VARCHAR(20) | 'open','closed','cancelled' |
| exit_reason | VARCHAR(50) NULL | 'SL','TP','TP2','Reversal','Manual' |
| confidence | FLOAT NULL | Model confidence |
| signal_data | JSONB NULL | Full signal metadata |
| entry_time | TIMESTAMPTZ | |
| exit_time | TIMESTAMPTZ NULL | |

### ml_models
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| created_by | FK -> users | |
| symbol | VARCHAR(20) | |
| timeframe | VARCHAR(10) | |
| model_type | VARCHAR(50) | 'xgboost','lightgbm','lstm','meta_labeler','regime_hmm' |
| pipeline | VARCHAR(20) | 'scalping' or 'expert' |
| file_path | VARCHAR(500) | Path to .joblib file |
| grade | VARCHAR(5) NULL | 'A','B','C','D','F' |
| metrics | JSONB | {accuracy, precision, recall, sharpe, etc.} |
| trained_at | TIMESTAMPTZ | |

### strategies
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| created_by | FK -> users | |
| name | VARCHAR(100) | |
| description | TEXT NULL | |
| strategy_type | VARCHAR(50) | |
| config | JSONB | Strategy-specific parameters |
| created_at | TIMESTAMPTZ | |

---

## 4. API Endpoints

### Auth (`/api/auth`)
| Method | Path | Description |
|--------|------|-------------|
| POST | /register | Create account |
| POST | /login | Get JWT tokens |
| POST | /refresh | Refresh access token |
| POST | /2fa/setup | Generate TOTP secret |
| POST | /2fa/verify | Verify TOTP code |

### Agents (`/api/agents`)
| Method | Path | Description |
|--------|------|-------------|
| GET | / | List user's agents |
| POST | / | Create new agent |
| GET | /engine-logs | Unified logs across all agents |
| GET | /all-trades | All trades across agents |
| GET | /pnl-summary | P&L summary cards |
| GET | /{id} | Get single agent |
| PUT | /{id} | Update agent config |
| DELETE | /{id} | Soft delete agent |
| POST | /{id}/start | Start agent |
| POST | /{id}/stop | Stop agent |
| POST | /{id}/pause | Pause agent |
| GET | /{id}/logs | Agent-specific logs |
| GET | /{id}/trades | Agent-specific trades |
| GET | /{id}/performance | Performance metrics |

### Broker (`/api/broker`)
| Method | Path | Description |
|--------|------|-------------|
| GET | /status | Connection status for all brokers |
| POST | /connect | Connect to broker |
| POST | /disconnect | Disconnect from broker |
| GET | /account | Account info (balance, equity) |
| GET | /positions | Open positions |
| GET | /orders | Pending orders |
| GET | /history | Trade history |
| GET | /symbols | Available instruments |
| GET | /candles/{symbol} | OHLCV candle data |
| POST | /order | Place order |
| POST | /close/{position_id} | Close position |
| PUT | /modify/{order_id} | Modify order |

### ML (`/api/ml`)
| Method | Path | Description |
|--------|------|-------------|
| GET | /models | List trained models |
| GET | /models/{id} | Model details + metrics |
| POST | /train | Trigger training job |
| GET | /training-status | Current training status |

### Settings (`/api/settings`)
| Method | Path | Description |
|--------|------|-------------|
| GET | / | Get user settings |
| PUT | / | Update user settings |

---

## 5. Broker Adapter Interface

All broker adapters implement this abstract base class:

### BrokerAdapter ABC
- `connect(credentials) -> bool`
- `disconnect() -> None`
- `get_account_info() -> AccountInfo`
- `get_positions() -> list[Position]`
- `get_orders() -> list[Order]`
- `get_candles(symbol, timeframe, count) -> list[Candle]`
- `get_symbols() -> list[SymbolInfo]`
- `place_order(symbol, side, size, order_type, price, sl, tp) -> OrderResult`
- `close_position(position_id) -> CloseResult`
- `modify_order(order_id, sl, tp) -> ModifyResult`
- `get_tick(symbol) -> Tick` (bid/ask)

### Candle Dict Format (universal)
```
{
    "time": 1774607100,       # Unix timestamp
    "open": 4415.96,
    "high": 4417.80,
    "low": 4411.35,
    "close": 4416.02,
    "volume": 1234
}
```

### Key Adapter Notes
- **Oanda**: REST API (v20), practice/live accounts, instruments use underscore (XAU_USD)
- **cTrader**: WebSocket + Protobuf, requires client_id + client_secret + access_token, async connect
- **MT5**: Local MetaTrader5 Python package, runs on Windows (or Wine on Linux), symbol names match directly

---

## 6. ML Pipeline

### Feature Engineering (80+ features from M5 + HTF context)

**Price-based**: returns, log_returns, high_low_range, body_size, wick_ratios
**Moving averages**: EMA(8,21,50,200), SMA(10,20,50), crossovers, distances
**Momentum**: RSI(14), Stochastic(14,3), MACD(12,26,9), CCI(20), Williams%R
**Volatility**: ATR(14), Bollinger Bands(20,2), Keltner Channels, historical vol
**Volume**: OBV, VWAP proxy, volume_ratio, volume_trend
**Structure**: swing highs/lows, support/resistance proximity, break of structure
**Session**: hour_sin, hour_cos, day_of_week, is_london, is_ny, is_asian, is_dead_zone
**Multi-timeframe**: H1 trend direction, H4 trend, D1 bias (when available)

### Scalping Pipeline (per symbol)
- **Models**: XGBoost + LightGBM (Optuna-tuned)
- **Target**: 3-class (0=sell, 1=hold, 2=buy)
- **Training**: M5 bars, 80+ features, walk-forward validation
- **Voting**: Any ONE model with >=55% confidence fires the signal
- **Files**: `scalping_{SYMBOL}_M5_xgboost.joblib`, `scalping_{SYMBOL}_M5_lightgbm.joblib`

### Expert Pipeline (per symbol)
- **Models**: XGBoost + LightGBM + LSTM (sequence model)
- **Voting**: 2/3 agreement required + min 55% weighted confidence
- **Meta-labeler**: Binary classifier that answers "should I take this trade?" after voting passes
- **Regime detector**: HMM that classifies market as trending_up/trending_down/ranging/volatile
- **Files**: `expert_{SYMBOL}_M5_{model_type}.joblib`

### Grading System
Each trained model gets a letter grade based on backtest performance:
- **A**: Sharpe > 1.5, Win Rate > 55%, Max DD < 15%
- **B**: Sharpe > 1.0, Win Rate > 50%, Max DD < 20%
- **C**: Sharpe > 0.5, Win Rate > 45%, Max DD < 25%
- **D**: Sharpe > 0, positive total return
- **F**: Negative total return

---

## 7. Agent Engine Architecture

### AlgoEngine (singleton)
- Manages multiple AgentRunner asyncio tasks
- start_agent(agent_id) / stop_agent(agent_id) / pause_agent(agent_id)
- Tracks all running agents

### AgentRunner (per-agent loop)
- Loads agent config from DB
- Determines agent type (scalping vs expert)
- Instantiates the appropriate agent class
- Injects `_log_fn` callback so agent can write to DB
- **Polling loop**: every 40 seconds, fetches latest M5 candles from broker
  - Detects new closed bar by comparing timestamps
  - Accumulates bar buffer (up to 200 bars)
  - Calls agent.evaluate(bars, broker_adapter)
  - If signal returned: creates trade via broker adapter
  - Logs everything

### Trade Execution Flow
1. Signal fires from agent evaluate()
2. Engine checks `_active_direction` (no duplicate positions)
3. Engine calls broker_adapter.place_order()
4. Records trade in agent_trades table
5. Trade monitor watches for SL/TP/exit conditions

### Logging
- `self._log(level, message, data)` writes to `agent_logs` table AND broadcasts via WebSocket
- Levels: info, warn, error, signal, trade
- Health check every 12 evaluations (~1 hour on M5)

---

## 8. Risk Management

### Per-Trade
- Default risk: 0.5% of balance per trade
- Dynamic lot sizing based on SL distance and instrument specs
- Session multiplier: 0.5x during Asian session for Gold/Indices

### Per-Agent Daily
- Max daily loss: 4% of balance
- Trade count limit (configurable)
- Cooldown: minimum 3 bars (15 min on M5) between trades

### Position Sizing Formula
```
risk_amount = balance * risk_per_trade * session_mult * regime_mult
lot_size = risk_amount / (sl_distance * pip_value_per_lot)
```

### Instrument Specs
Each symbol has specific pip size, pip value, min lot, lot step:
- XAUUSD: pip=0.01, min_lot=0.01
- BTCUSD: pip=0.01, min_lot=0.01 (varies by broker)
- US30: pip=1.0, min_lot=0.01
- ES: pip=0.25, min_lot=1.0
- NAS100: pip=0.25, min_lot=0.01

---

## 9. Frontend Pages

### Dashboard (`/`)
- Account summary cards (balance, equity, unrealized P&L)
- Active agents count
- Per-agent P&L summary cards (horizontal scroll)
- Quick actions

### Trading Terminal (`/trading`)
- Top: Symbol selector + timeframe buttons + broker selector
- Middle: Candlestick chart (TradingView lightweight-charts) with indicator overlays
- Below chart: Account cards (balance, equity, P&L, active agents)
- Agent summary cards (per-agent P&L)
- Tabs: Agents | Positions | Orders | History | Engine Log
  - Agents: AgentPanel component with per-agent cards, logs, trades, equity
  - Positions: Live positions table with close buttons
  - Orders: Pending orders table with cancel/modify
  - History: Closed trades table
  - Engine Log: Unified cross-agent log view (polls every 5s)

### Agent Management (`/agents`)
- Agent creation wizard
- Agent list with controls (start/stop/pause/delete)
- Agent detail view with performance charts

### ML Models (`/models`)
- Model list with grade badges
- Training trigger
- Model detail with metrics

### Settings (`/settings`)
- Theme, notifications, default broker
- Broker credential management

---

## 10. WebSocket Channels

### Price Updates
- Channel: `price:{symbol}`
- Payload: `{symbol, bid, ask, time}`
- Frequency: real-time from broker

### Agent Updates
- Channel: `agent:{agent_id}`
- Payload: `{type, data}` where type = "log" | "trade" | "status"
- Triggered on: new log entry, trade open/close, status change

### Account Updates
- Channel: `account`
- Payload: `{balance, equity, margin, unrealized_pnl}`
- Frequency: every 5 seconds when broker connected

---

## 11. News Filter

- Check for high-impact economic news before each trade
- Skip trading within configurable window (default: 15 minutes before event)
- News sources: economic calendar APIs
- Per-symbol keyword mapping (e.g., XAUUSD -> ["gold", "fed", "inflation", "cpi"])
- Cache news checks for 5 minutes
- Fail open: if news API is down, allow trading

---

## 12. Deployment (Render)

### Services
- **Web Service**: Backend (FastAPI) — Python, port 8000
- **Web Service**: Frontend (Next.js) — Node.js, port 3000
- **PostgreSQL**: Render managed database

### Environment Variables
- `DATABASE_URL` — PostgreSQL connection string
- `SECRET_KEY` — JWT signing key (must be strong in production)
- `ENCRYPTION_KEY` — Fernet key for broker credentials
- `DEBUG` — false in production
- `ALLOWED_ORIGINS` — frontend URL for CORS

### Build Commands
- Backend: `pip install -r requirements.txt`
- Frontend: `npm install && npm run build`

### Start Commands
- Backend: `uvicorn main:app --host 0.0.0.0 --port 8000`
- Frontend: `npm start`
