# Phase 10 — Deploy & Production Hardening

## Objective
Deploy Flowrex Algo to Render, harden for production, add monitoring/alerting, and perform a full end-to-end production test. This is the final phase — after this, the platform is live.

---

## Prompt

```
You are building Flowrex Algo. This is Phase 10 of 10 — the FINAL PHASE.

READ ARCHITECTURE.md Section 12 (Deployment) for Render configuration.

Phases 1-9 are complete — the entire platform is built, tested, and polished. Now deploy it to Render and harden for production.

### What to build in this phase:

**1. Render Configuration**
Create `render.yaml` (Render Blueprint):
- Backend web service:
  - Name: flowrex-algo-api
  - Runtime: Python 3.11
  - Build: pip install -r requirements.txt
  - Start: uvicorn main:app --host 0.0.0.0 --port $PORT
  - Health check path: /api/health
  - Environment variables: DATABASE_URL, SECRET_KEY, ENCRYPTION_KEY, DEBUG=false, ALLOWED_ORIGINS
  - Auto-deploy from main branch

- Frontend web service:
  - Name: flowrex-algo-web
  - Runtime: Node.js
  - Build: npm install && npm run build
  - Start: npm start
  - Environment variables: NEXT_PUBLIC_API_URL (pointing to backend service)

- PostgreSQL database:
  - Name: flowrex-algo-db
  - Plan: free (or starter)

**2. Production Configuration**
Update backend config for production safety:
- SECRET_KEY: MUST be set, block startup if missing or default
- ENCRYPTION_KEY: MUST be set, block startup if missing
- DEBUG: false in production
- CORS: only allow the frontend Render URL
- Database: connection pooling settings (pool_size=5, max_overflow=10)
- Log level: INFO (not DEBUG)
- Remove any dev-only middleware or test endpoints

**3. Database Migrations for Production**
- Ensure Alembic migrations run automatically on deploy
- Add to backend start script: `alembic upgrade head && uvicorn main:app ...`
- Test migration on a fresh database (no existing data)
- Add migration for any indexes not yet created

**4. Environment Variable Setup**
Document all required environment variables:
- DATABASE_URL — from Render PostgreSQL
- SECRET_KEY — generate with: python -c "import secrets; print(secrets.token_hex(32))"
- ENCRYPTION_KEY — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
- ALLOWED_ORIGINS — frontend URL
- DEBUG — false
- NEXT_PUBLIC_API_URL — backend URL

**5. Frontend Production Build**
- Ensure next.config.js has correct output settings for Render
- API base URL reads from NEXT_PUBLIC_API_URL environment variable
- Build optimizations: no source maps in production
- Verify the build succeeds with no TypeScript errors
- Test that all pages load correctly from the production build

**6. Security Hardening**
Review and fix all security concerns:
- HTTPS only (Render provides this automatically)
- Secure headers: X-Content-Type-Options, X-Frame-Options, Strict-Transport-Security
- Rate limiting on auth endpoints (5 attempts per minute per IP)
- Input validation on all endpoints (Pydantic already handles most)
- SQL injection prevention (SQLAlchemy parameterized queries — verify)
- XSS prevention (React auto-escapes — verify no dangerouslySetInnerHTML)
- CSRF: not needed for JWT-based API (but verify no cookie-based auth leaks)
- File upload restrictions (if any upload endpoints exist)
- No sensitive data in logs (mask API keys, passwords)
- No stack traces in production error responses

**7. Monitoring & Health Checks**
- /api/health endpoint returns: {status, database, active_agents, uptime, version}
- /api/health/detailed (admin only): memory usage, DB connection count, WebSocket connections
- Structured logging: JSON format for production logs (Render captures stdout)
- Add request ID to all log entries for traceability
- Agent crash recovery: if agent loop crashes, auto-restart after 60 seconds with log
- Stale agent detection: if agent hasn't evaluated in 10+ minutes, log a warning

**8. Data Persistence**
- Ensure ML model files persist across deploys:
  - Option A: Store in PostgreSQL as binary (BYTEA)
  - Option B: Use Render Disk (persistent storage)
  - Option C: Upload to S3/GCS and download on startup
  - Recommend Option B (Render Disk) for simplicity
- Database backup strategy (Render provides automated backups for paid plans)

**9. Production Seed**
Create a production seed script that:
- Creates the admin user (email from environment variable)
- Skips if admin already exists
- Does NOT create test agents (those are for dev only)
- Can be run safely multiple times (idempotent)

**10. End-to-End Production Test**
After deploying, run through this full test:
1. Load the frontend URL — verify dark theme, login page
2. Register a new account
3. Login with the new account
4. Navigate to Trading page — verify chart loads
5. Connect a broker (Oanda practice account)
6. Verify account info displays (balance, equity)
7. Verify positions/orders tabs show data (or empty state)
8. Navigate to Models page — verify trained models show
9. Create a scalping agent via wizard
10. Start the agent — verify logs appear
11. Wait for at least 2 evaluation cycles — verify M5 candle logs
12. Check Engine Log tab — verify unified logs appear
13. Stop the agent
14. Check History tab — verify any trades (or zero trades if no signal)
15. Disconnect broker
16. Test mobile (resize browser to phone width)
17. Test 2FA setup in settings

**11. Performance Optimization**
- Database query optimization:
  - Verify all queries use indexes (explain analyze key queries)
  - Add missing indexes if needed
  - Use eager loading for relationships where appropriate
- Frontend:
  - Verify no unnecessary re-renders (React DevTools)
  - Lazy load heavy components (chart, backtest)
  - Image optimization (if any images)
- WebSocket:
  - Verify connections are cleaned up properly
  - No memory leaks from accumulated messages

**12. Documentation**
Create a minimal production README:
- How to deploy (Render Blueprint or manual)
- Required environment variables
- How to run migrations
- How to seed the database
- How to monitor health
- Troubleshooting common issues

### Testing Requirements
- Run ALL tests from ALL phases (full regression suite)
- Run the production build locally and test all pages
- Test the full deployment pipeline:
  1. Build succeeds
  2. Migrations run
  3. App starts
  4. Health check passes
  5. Frontend loads
- Security test:
  - Verify unauthenticated requests are rejected
  - Verify CORS blocks unauthorized origins
  - Verify error responses don't leak internals
- Load test (basic): hit /api/health 100 times concurrently, verify no errors
- WebSocket test: connect 10 clients simultaneously, verify all receive updates

### When you're done, present a FINAL CHECKPOINT REPORT:
1. List every file created/modified in this phase
2. All test results (including full regression)
3. Deployment status: is it live on Render?
4. End-to-end test results (all 17 steps)
5. Security audit results
6. Performance observations
7. Known issues or limitations
8. Recommended next steps for the future

Then ask me:
- "Flowrex Algo is deployed at [URL]. Can you verify it loads?"
- "Here's the full end-to-end test report: [summary]. Any concerns?"
- "Known limitations: [list]. Which would you like to address first?"
- "The platform is production-ready. What feature do you want to build next?"
```

---

## Expected Deliverables
- [ ] render.yaml configuration
- [ ] Production environment config
- [ ] Database migrations auto-run on deploy
- [ ] Frontend production build verified
- [ ] Security hardening applied
- [ ] Health check endpoints
- [ ] Structured logging
- [ ] ML model persistence strategy
- [ ] Production seed script
- [ ] Full end-to-end production test passed
- [ ] Performance optimization
- [ ] Production documentation
- [ ] All regression tests passing
- [ ] Deployed and live on Render
