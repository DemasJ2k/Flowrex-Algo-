# Phase 1 — Foundation

## Objective
Set up the entire project scaffold, PostgreSQL database connection, configuration system, and development environment. By the end of this phase, you should have a running FastAPI server and Next.js dev server with PostgreSQL connected.

---

## Prompt

```
You are building a full autonomous algorithmic trading platform called "Flowrex Algo" from scratch. This is Phase 1 of 10.

READ the file ARCHITECTURE.md first — it contains the full system design, database schema, API contracts, and folder structure you must follow.

### What to build in this phase:

**1. Project Root**
- Create the folder structure exactly as defined in ARCHITECTURE.md Section 2
- Create a root README.md with project name "Flowrex Algo" and a brief description
- Create a .gitignore covering Python, Node.js, .env files, __pycache__, node_modules, .next, data/ml_models/*.joblib

**2. Backend Foundation**
- Initialize a Python project with requirements.txt
- Dependencies: fastapi, uvicorn, sqlalchemy, alembic, psycopg2-binary, python-dotenv, pydantic, pydantic-settings, python-jose[cryptography], passlib[bcrypt], cryptography, httpx, websockets, python-multipart
- Create `backend/main.py` — FastAPI app with CORS middleware, lifespan handler, health check endpoint at /api/health
- Create `backend/app/core/config.py` — Settings class using pydantic-settings, reading from environment variables: DATABASE_URL, SECRET_KEY, DEBUG, ALLOWED_ORIGINS, ENCRYPTION_KEY. Default DATABASE_URL should point to a local PostgreSQL database.
- Create `backend/app/core/database.py` — SQLAlchemy engine, SessionLocal factory, Base declarative base, get_db dependency
- Create `backend/app/core/encryption.py` — Fernet encryption utilities for encrypting/decrypting broker API keys. Generate ENCRYPTION_KEY if not set (dev only, must be set in production).
- Initialize Alembic for migrations pointing to the database
- Create a .env.example with all required environment variables documented

**3. Frontend Foundation**
- Initialize a Next.js 14+ project with App Router, TypeScript, Tailwind CSS
- Choose and install a UI component library (your choice — shadcn/ui, Radix, or similar). Pick whichever you think produces the best dark trading terminal aesthetic.
- Set up the base layout with a dark theme by default
- Create a sidebar navigation component with links: Dashboard, Trading, Agents, Models, Backtest, Settings
- Create placeholder pages for each route (just the page shell with the title)
- Create `frontend/src/lib/api.ts` — an API client wrapper (axios or fetch-based) that handles base URL, auth headers, and error responses
- Create `frontend/src/types/index.ts` — start with empty file, types will be added per phase

**4. Development Scripts**
- Backend: create a start script that runs uvicorn with auto-reload
- Frontend: standard Next.js dev script
- Create a root-level docker-compose.yml (optional) for PostgreSQL if the user wants local DB

**5. Verification**
- Backend /api/health returns {"status": "ok", "database": "connected"} (actually test the DB connection)
- Frontend loads at localhost:3000 with the sidebar and dark theme
- Alembic can run migrations against the PostgreSQL database

### Testing Requirements
- Write a unit test for the health check endpoint
- Write a unit test for the encryption utilities (encrypt -> decrypt roundtrip)
- Use the preview tool to verify the frontend loads correctly with the dark theme and sidebar
- Run all tests and confirm they pass

### When you're done, present a CHECKPOINT REPORT:
1. List every file you created
2. Show test results (all passing?)
3. Screenshot/preview of the frontend
4. Any issues or decisions you made
5. What Phase 2 will build

Then ask me:
- "Are you happy with the UI kit I chose? Want me to switch?"
- "Any changes to the folder structure before we build on it?"
- "Ready for Phase 2?"
```

---

## Expected Deliverables
- [ ] Full folder structure matching ARCHITECTURE.md
- [ ] FastAPI app running with /api/health
- [ ] PostgreSQL connected via SQLAlchemy
- [ ] Alembic initialized
- [ ] Next.js app with dark theme, sidebar, placeholder pages
- [ ] API client wrapper
- [ ] Encryption utilities
- [ ] Unit tests passing
- [ ] Frontend visually verified via preview tool
