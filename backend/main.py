from contextlib import asynccontextmanager
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.rate_limit import limiter
from app.core.config import settings
from app.core.websocket import get_ws_manager
from app.core.database import check_db_connection, Base, engine
from app.core.middleware import SecurityHeadersMiddleware, RequestLoggingMiddleware, setup_global_error_handler, setup_logging
from app.api.auth import router as auth_router
from app.api.agent import router as agent_router
from app.api.broker import router as broker_router
from app.api.ml import router as ml_router
from app.api.settings import router as settings_router
from app.api.backtest import router as backtest_router
from app.api.admin import router as admin_router
from app.api.feedback import router as feedback_router
from app.api.market_data import router as market_data_router
from app.api.news import router as news_router
import app.models  # noqa: F401 — register all models


setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — create tables if using SQLite (dev mode)
    if settings.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    db_ok = check_db_connection()
    if db_ok:
        print("Database connected successfully")
    else:
        print("WARNING: Database connection failed")

    # Auto-reconnect brokers for running agents
    try:
        from app.services.broker.manager import get_broker_manager
        from app.core.database import SessionLocal
        from app.models.agent import TradingAgent
        db = SessionLocal()
        running = db.query(TradingAgent).filter(
            TradingAgent.status == "running",
            TradingAgent.deleted_at.is_(None),
        ).all()
        if running:
            manager = get_broker_manager()
            brokers_needed = set((a.created_by, a.broker_name) for a in running)
            for user_id, broker_name in brokers_needed:
                try:
                    await manager.connect(user_id, broker_name, None, db)
                    print(f"Auto-connected broker: {broker_name} for user {user_id}")
                except Exception as e:
                    print(f"Auto-connect failed for {broker_name}: {e}")
            # Auto-restart running agents
            from app.services.agent.engine import get_algo_engine
            algo_engine = get_algo_engine()
            for agent in running:
                try:
                    await algo_engine.start_agent(agent.id)
                    print(f"Auto-started agent: {agent.name} ({agent.symbol})")
                except Exception as e:
                    print(f"Auto-start failed for {agent.name}: {e}")
        db.close()
    except Exception as e:
        print(f"Auto-reconnect error: {e}")

    # Initialize retrain scheduler
    try:
        from app.services.ml.retrain_scheduler import init_scheduler
        init_scheduler()
        print("Retrain scheduler initialized")
    except Exception as e:
        print(f"Retrain scheduler init skipped: {e}")

    yield
    # Shutdown
    try:
        from app.services.ml.retrain_scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:
        pass
    from app.services.agent.engine import get_algo_engine
    await get_algo_engine().stop_all()
    print("Shutting down Flowrex Algo")


app = FastAPI(
    title="Flowrex Algo",
    description="Autonomous algorithmic trading platform — ML-powered agents for US30, BTCUSD, XAUUSD, ES, NAS100",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
setup_global_error_handler(app)


app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(broker_router)
app.include_router(ml_router)
app.include_router(settings_router)
app.include_router(backtest_router)
app.include_router(admin_router)
app.include_router(feedback_router)
app.include_router(market_data_router)
app.include_router(news_router)


@app.get("/api/health")
def health_check():
    db_connected = check_db_connection()
    ws_manager = get_ws_manager()
    from app.services.agent.engine import get_algo_engine
    engine = get_algo_engine()
    return {
        "status": "ok",
        "version": "0.1.0",
        "database": "connected" if db_connected else "disconnected",
        "active_agents": len(engine.get_running_agents()),
        "websocket_connections": ws_manager.get_connection_count(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time data streaming.
    Client sends: {"action": "subscribe", "channel": "price:XAUUSD"}
    Server pushes: {"channel": "price:XAUUSD", "data": {...}}
    """
    ws_manager = get_ws_manager()
    # Extract user_id from query param token (e.g., /ws?token=xxx)
    user_id = 0
    token = websocket.query_params.get("token")
    if token:
        try:
            from app.core.auth import verify_token
            payload = verify_token(token)
            user_id = int(payload.get("sub", 0))
        except Exception:
            pass
    if user_id == 0 and settings.DEBUG:
        user_id = 1  # Dev fallback only
    await ws_manager.connect(websocket, user_id)

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                action = msg.get("action", "")
                channel = msg.get("channel", "")

                if action == "subscribe" and channel:
                    ws_manager.subscribe(websocket, channel)
                    await websocket.send_text(json.dumps({
                        "channel": "system",
                        "data": {"action": "subscribed", "channel": channel},
                    }))
                elif action == "unsubscribe" and channel:
                    ws_manager.unsubscribe(websocket, channel)
                    await websocket.send_text(json.dumps({
                        "channel": "system",
                        "data": {"action": "unsubscribed", "channel": channel},
                    }))
                elif action == "ping":
                    await websocket.send_text(json.dumps({"channel": "system", "data": {"action": "pong"}}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
