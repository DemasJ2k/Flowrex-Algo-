# Flowrex Algo — Deployment Guide

## Quick Start (Local Docker)

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f backend

# Stop
docker-compose down
```

## Quick Start (Local Dev — No Docker)

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

## Deploy to Render

1. Push code to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com)
3. Click "New" → "Blueprint" → Connect your repo
4. Render reads `render.yaml` and creates all services automatically
5. Set `ENCRYPTION_KEY` manually in Render dashboard (it's not auto-generated)

## Required Environment Variables

| Variable | Description | How to generate |
|----------|-------------|-----------------|
| `DATABASE_URL` | PostgreSQL connection string | Auto-set by Render |
| `SECRET_KEY` | JWT signing key (64+ chars) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ENCRYPTION_KEY` | Fernet key for broker creds | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DEBUG` | `false` in production | Set manually |
| `ALLOWED_ORIGINS` | Frontend URL as JSON array | `["https://your-frontend.onrender.com"]` |
| `NEXT_PUBLIC_API_URL` | Backend URL | `https://your-api.onrender.com` |
| `NEXT_PUBLIC_WS_URL` | WebSocket URL | `wss://your-api.onrender.com/ws` |

## Database Migrations

Migrations run automatically on deploy via the start command:
```bash
alembic upgrade head && uvicorn main:app ...
```

To create a new migration:
```bash
cd backend
alembic revision --autogenerate -m "description"
```

## Seed Database

```bash
cd backend
python -m scripts.seed
```

## Health Check

```
GET /api/health
→ {"status": "ok", "version": "0.1.0", "database": "connected", "active_agents": 0}
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Backend won't start | Check `DATABASE_URL` is correct |
| 401 on all endpoints | Set `DEBUG=true` for dev, or register/login for JWT |
| No chart data | Connect a broker first (Trading page → Connect Broker) |
| Models page empty | Run training: `python -m scripts.train_scalping_pipeline` |
| Agent won't start | Ensure broker is connected and models are trained |
