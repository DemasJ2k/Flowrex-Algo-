# Phase 9 — Auth, Backtesting & Polish

## Objective
Implement full authentication (JWT + 2FA), backtesting engine, prop firm integration, admin features, and UI polish. This phase transforms the single-user dev app into a multi-tenant production platform.

---

## Prompt

```
You are building Flowrex Algo. This is Phase 9 of 10.

READ ARCHITECTURE.md Section 4 (Auth endpoints) and Section 3 (User/UserSettings models).

Phases 1-8 are complete — the platform is fully functional with live trading, ML models, and real-time WebSockets. But it has no real authentication and some features are still missing.

### What to build in this phase:

**1. Full Authentication System**

**1a. JWT Auth**
Create `backend/app/core/auth.py`:
- Create access tokens (short-lived, 30 min) and refresh tokens (long-lived, 7 days)
- JWT payload: {sub: user_id, type: "access"|"refresh", exp: timestamp}
- Use python-jose with HS256 algorithm
- SECRET_KEY from environment (MUST be strong in production — block startup if default/weak)

**1b. Auth Endpoints**
Create `backend/app/api/auth.py`:
- POST /api/auth/register:
  - Accept email + password
  - Hash password with bcrypt
  - Create user + default user_settings
  - Return tokens
- POST /api/auth/login:
  - Verify email + password
  - If 2FA enabled: return partial token that requires TOTP verification
  - If no 2FA: return full tokens
- POST /api/auth/refresh:
  - Accept refresh token
  - Verify and return new access token
- POST /api/auth/2fa/setup:
  - Generate TOTP secret (pyotp)
  - Return secret + QR code provisioning URI
  - Store encrypted secret in user.totp_secret
- POST /api/auth/2fa/verify:
  - Verify TOTP code against stored secret
  - On success: enable 2FA, return full tokens

**1c. Auth Middleware**
Replace the dev auth bypass with real JWT verification:
- get_current_user dependency: extract token from Authorization header, verify JWT, return user
- Token type enforcement: only accept "access" tokens for API calls
- All existing endpoints should now require authentication
- WebSocket auth: verify token from query parameter

**1d. Frontend Auth**
- Create Login page (/login) and Register page (/register)
- Store tokens in localStorage (or httpOnly cookies if you prefer)
- API client: automatically attach Authorization header
- Auto-redirect to /login when 401 received
- Token refresh: automatically refresh before expiry
- 2FA setup flow in Settings page
- Logout: clear tokens, redirect to login

**2. Backtesting Engine**
Create `backend/app/services/backtest/engine.py`:
- BacktestEngine class:
  - run(symbol, timeframe, start_date, end_date, strategy_config) -> BacktestResult
  - Simulate agent evaluation on historical data
  - Track: entries, exits, P&L per trade, equity curve
  - Support both scalping and expert strategies
  - Use the same feature engineering and model prediction as live trading
  - Respect risk management rules (position sizing, daily limits)

- BacktestResult:
  - trades: list of simulated trades
  - total_pnl, win_rate, profit_factor, max_drawdown, sharpe_ratio
  - equity_curve: list of (timestamp, equity) points
  - summary statistics

Create `backend/app/api/backtest.py`:
- POST /api/backtest/run — trigger backtest (background task)
  - Params: symbol, timeframe, start_date, end_date, agent_type, risk_config
- GET /api/backtest/results — list past backtest results
- GET /api/backtest/results/{id} — detailed result with trades and equity curve

**Frontend Backtest Page:**
- Configuration form: symbol, date range, agent type, risk settings
- "Run Backtest" button (shows progress)
- Results view:
  - Summary cards: Total P&L, Win Rate, Profit Factor, Max DD, Sharpe
  - Equity curve chart (line chart)
  - Trade list table (same format as History tab)
  - Trade markers on a price chart (entries and exits)

**3. Prop Firm Integration**
Add prop firm account tracking:
- Add prop_firm_accounts table or extend broker_accounts:
  - account_size, max_daily_loss, max_total_drawdown, profit_target
  - current_daily_pnl, current_drawdown, total_profit
- Pre-trade validation in risk manager:
  - Block trades that would violate prop firm rules
  - Trailing drawdown tracking
- Dashboard card showing prop firm progress: profit target %, daily loss used %, drawdown used %
- Agent engine checks prop firm limits before executing trades

**4. Admin Features**
Create `backend/app/api/admin.py`:
- Protected by is_admin check
- GET /api/admin/users — list all users
- GET /api/admin/agents — list all agents across users
- GET /api/admin/system — system health (DB connection, active agents, memory usage)
- POST /api/admin/seed — run seed script

Create admin pages in frontend (only visible to admin users):
- Users list
- System health dashboard
- Agent overview across all users

**5. Settings Page Enhancement**
- Theme preference (dark/light — dark default)
- Default broker selection
- Notification preferences
- 2FA setup/disable
- Change password
- API key management (for broker credentials)
- Data export (download trade history as CSV)

**6. UI Polish Pass**
Go through every page and fix:
- Consistent spacing and alignment
- Loading states for all data fetches (skeleton loaders, not blank screens)
- Error states with helpful messages
- Empty states with calls to action
- Confirmation dialogs for destructive actions (delete agent, close position)
- Toast notifications for actions (agent started, trade placed, etc.)
- Mobile: test every page at 375px width, fix any overflow/layout issues
- Transitions: smooth tab switches, modal animations

**7. Error Handling Hardening**
- Backend: global exception handler that returns clean JSON errors
  - Never leak stack traces or internal details to clients
  - Log full errors server-side
- Frontend: global error boundary
- API client: handle network errors, timeouts, 401/403/500 gracefully
- Agent engine: catch and log all errors, never crash the loop

### Testing Requirements
- Write unit tests for JWT creation/verification
- Write unit tests for password hashing and verification
- Write integration tests for auth flow (register -> login -> access protected endpoint)
- Write integration test for 2FA flow (setup -> verify)
- Write unit tests for backtesting engine
- Test prop firm rule enforcement
- Use preview tool to verify:
  - Login/register pages
  - 2FA setup flow
  - Backtest page with results
  - Settings page
  - Admin pages
  - Mobile responsiveness
  - Loading/error/empty states
- Test token refresh flow
- Test that unauthenticated requests are rejected
- Run ALL tests (including all previous phases)

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. All test results
3. Auth implementation details
4. Backtest results for sample runs
5. UI polish changes made
6. What Phase 10 will build

Then ask me:
- "Auth uses JWT with [details]. Want to switch to cookies or adjust token lifetimes?"
- "Backtest ran for XAUUSD: [results]. Model performing as expected?"
- "Here's the login page [preview]. Any design changes?"
- "Prop firm limits are set to: [defaults]. Want to adjust?"
- "Ready for Phase 10 (final phase — deploy)?"
```

---

## Expected Deliverables
- [ ] Full JWT authentication (register, login, refresh, logout)
- [ ] 2FA (TOTP) setup and verification
- [ ] All endpoints protected by auth
- [ ] Frontend login/register pages
- [ ] Token management in frontend
- [ ] Backtesting engine
- [ ] Backtest API and frontend page
- [ ] Prop firm tracking and limits
- [ ] Admin endpoints and pages
- [ ] Enhanced settings page
- [ ] UI polish (loading, error, empty states, mobile)
- [ ] Error handling hardened
- [ ] All tests passing
