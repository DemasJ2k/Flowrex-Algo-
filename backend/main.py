from contextlib import asynccontextmanager
import json
import logging
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
from app.api.llm import router as llm_router
from app.api.telegram import router as telegram_router
from app.api.market import router as market_router
import app.models  # noqa: F401 — register all models


setup_logging()
logger = logging.getLogger("flowrex.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — create tables if using SQLite (dev mode)
    if settings.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    # ── Sentry error tracking (Batch D) ──
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk
            sentry_sdk.init(
                dsn=settings.SENTRY_DSN,
                traces_sample_rate=0.1,
                environment="production" if not settings.DEBUG else "development",
                release="flowrex-algo@1.0",
            )
            logger.info("Sentry initialized")
        except Exception as e:
            logger.warning(f"Sentry init failed (non-fatal): {e}")

    # ── Secrets validation (Batch B — fail fast on bad config) ──
    try:
        from app.core.encryption import validate_encryption_key
        validate_encryption_key()
        logger.info("ENCRYPTION_KEY validated")
    except RuntimeError as e:
        logger.critical(f"ENCRYPTION_KEY validation failed: {e}")
        if not settings.DEBUG:
            raise  # Don't start production with a bad key

    if settings.SECRET_KEY.startswith("dev-") and not settings.DEBUG:
        logger.critical("SECRET_KEY is the development default. Set a production key in .env.")

    db_ok = check_db_connection()
    if db_ok:
        logger.info("Database connected successfully")
    else:
        logger.warning("Database connection failed")

    # Auto-reconnect brokers for running agents + orphan check.
    try:
        import asyncio as _asyncio
        from app.services.broker.manager import get_broker_manager
        from app.core.database import SessionLocal
        from app.models.agent import TradingAgent
        # Capture the main loop so sync worker threads (BackgroundTasks,
        # APScheduler) can dispatch broker coroutines onto it via
        # `manager.run_coroutine_on_loop(...)`. Fixes the "asyncio.locks.Event
        # is bound to a different event loop" error seen from the backtest
        # broker-live path.
        try:
            get_broker_manager().set_main_loop(_asyncio.get_running_loop())
        except RuntimeError:
            pass
        db = SessionLocal()
        running = db.query(TradingAgent).filter(
            TradingAgent.status == "running",
            TradingAgent.deleted_at.is_(None),
        ).all()
        manager = get_broker_manager()
        brokers_needed: set[tuple[int, str]] = set()
        if running:
            brokers_needed = set((a.created_by, a.broker_name) for a in running)
            for user_id, broker_name in brokers_needed:
                try:
                    await manager.connect(user_id, broker_name, None, db)
                    logger.info(f"Auto-connected broker: {broker_name} for user {user_id}")
                except Exception as e:
                    logger.warning(f"Auto-connect failed for {broker_name}: {e}")
            # Auto-restart running agents
            from app.services.agent.engine import get_algo_engine
            algo_engine = get_algo_engine()
            for agent in running:
                try:
                    await algo_engine.start_agent(agent.id)
                    logger.info(f"Auto-started agent: {agent.name} ({agent.symbol})")
                except Exception as e:
                    logger.warning(f"Auto-start failed for {agent.name}: {e}")

        # ── Orphan reconciliation: also check brokers for users who have any
        # trading_agents at all (not just running), since stopped agents could
        # still have leftover positions on the broker.
        try:
            from app.models.agent import AgentTrade
            # Union with inactive-but-connected brokers
            all_agents = db.query(TradingAgent).filter(TradingAgent.deleted_at.is_(None)).all()
            brokers_to_check = brokers_needed | set((a.created_by, a.broker_name) for a in all_agents)
            for user_id, broker_name in brokers_to_check:
                adapter = manager.get_adapter(user_id, broker_name)
                if not adapter:
                    continue
                try:
                    broker_positions = await adapter.get_positions()
                except Exception:
                    continue
                for pos in broker_positions:
                    ticket = str(pos.id) if hasattr(pos, "id") else ""
                    if not ticket:
                        continue
                    match = db.query(AgentTrade).filter(
                        AgentTrade.broker_ticket == ticket,
                        AgentTrade.status == "open",
                    ).first()
                    if not match:
                        logger.critical(
                            f"Orphaned broker position — "
                            f"ticket={ticket} symbol={pos.symbol} "
                            f"direction={pos.direction} size={pos.size} "
                            f"pnl={pos.pnl}. No matching AgentTrade in DB."
                        )
        except Exception as e:
            logger.warning(f"Trade orphan check failed: {e}", exc_info=True)

        db.close()
    except Exception as e:
        logger.warning(f"Auto-reconnect error: {e}", exc_info=True)

    # Initialize retrain scheduler
    try:
        from app.services.ml.retrain_scheduler import init_scheduler
        init_scheduler()
        logger.info("Retrain scheduler initialized")
    except Exception as e:
        logger.warning(f"Retrain scheduler init skipped: {e}")

    yield
    # Shutdown
    try:
        from app.services.ml.retrain_scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:
        pass
    from app.services.agent.engine import get_algo_engine
    await get_algo_engine().stop_all()
    logger.info("Shutting down Flowrex Algo")


app = FastAPI(
    title="Flowrex Algo",
    description="Autonomous algorithmic trading platform — ML-powered agents for US30, BTCUSD, XAUUSD, ES, NAS100, ETHUSD, XAGUSD, AUS200",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
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
app.include_router(llm_router)
app.include_router(telegram_router)
app.include_router(market_router)


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

    Auth:
    - Production: requires a valid 'access' JWT with scope='full' via either
      the Authorization header (Bearer token) or ?token=... query param.
    - DEBUG mode: if no token, falls back to user_id=1 (dev only).
    - Origin header is validated against ALLOWED_ORIGINS to block CSWSH.
    """
    ws_manager = get_ws_manager()

    # ── Origin validation (blocks cross-site WebSocket hijacking) ──
    origin = websocket.headers.get("origin", "")
    if settings.ALLOWED_ORIGINS and origin:
        allowed = any(
            origin == o or origin.startswith(o + "/") for o in settings.ALLOWED_ORIGINS
        )
        if not allowed and not settings.DEBUG:
            await websocket.close(code=1008)  # policy violation
            return

    # ── Token extraction: header first (not logged), query param fallback ──
    user_id = 0
    token = None
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = websocket.query_params.get("token")

    if token:
        try:
            from app.core.auth import verify_token
            uid = verify_token(token, "access")
            if uid:
                user_id = int(uid)
        except Exception:
            pass

    if user_id == 0 and not settings.DEBUG:
        # Production requires auth — log the rejection for debugging
        logger.warning(f"WebSocket rejected: no valid token. Origin={origin}, auth_header={'yes' if auth_header else 'no'}, query_token={'yes' if websocket.query_params.get('token') else 'no'}")
        await websocket.close(code=1008)
        return
    if user_id == 0:
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
                    # ── Channel ownership check (Batch E — multi-user isolation) ──
                    # Prevent User B from subscribing to User A's agent channels.
                    # Agent channels are "agent:{id}" — verify the agent belongs to
                    # the authenticated user before allowing the subscription.
                    if channel.startswith("agent:"):
                        try:
                            agent_id = int(channel.split(":")[1])
                            from app.core.database import SessionLocal
                            from app.models.agent import TradingAgent
                            _db = SessionLocal()
                            _agent = _db.query(TradingAgent).filter(
                                TradingAgent.id == agent_id,
                                TradingAgent.created_by == user_id,
                            ).first()
                            _db.close()
                            if not _agent:
                                await websocket.send_text(json.dumps({
                                    "channel": "system",
                                    "data": {"action": "error", "message": "Not authorized for this agent channel"},
                                }))
                                continue
                        except (ValueError, IndexError):
                            pass  # Not a valid agent channel format — allow (might be "agent:summary")

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
