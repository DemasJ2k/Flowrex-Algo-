# Flowrex Algo

Autonomous algorithmic trading platform powered by ML-based agents. Connects to Oanda, cTrader, and MT5 brokers to execute trades with risk management, real-time monitoring, and a modern trading terminal UI.

## Tech Stack
- **Backend**: FastAPI + SQLAlchemy + PostgreSQL
- **Frontend**: Next.js 14 (App Router) + TypeScript + Tailwind CSS
- **ML**: XGBoost, LightGBM, optional LSTM
- **Brokers**: Oanda, cTrader, MT5

## Getting Started

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Database
```bash
docker-compose up -d  # starts PostgreSQL
cd backend
alembic upgrade head
```
