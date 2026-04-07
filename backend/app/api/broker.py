from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from dataclasses import asdict

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.encryption import get_fernet
from app.models.user import User
from app.models.market_data import MarketDataProvider
from app.services.broker.base import BrokerError
from app.services.broker.manager import BrokerManager, get_broker_manager
from app.schemas.broker import (
    BrokerConnectRequest, BrokerDisconnectRequest,
    AccountInfoResponse, PositionResponse, OrderResponse,
    SymbolResponse, CandleResponse,
    PlaceOrderRequest, PlaceOrderResponse,
    ClosePositionResponse, ModifyOrderRequest, ModifyOrderResponse,
)

router = APIRouter(prefix="/api/broker", tags=["broker"])


def _get_adapter(user: User, manager: BrokerManager, broker: Optional[str] = None):
    """Get the active adapter for a user. Returns None if no broker connected."""
    broker_name = broker or manager.get_connected_broker(user.id)
    if not broker_name:
        return None
    return manager.get_adapter(user.id, broker_name)


# ── Connection ─────────────────────────────────────────────────────────


@router.get("/connections")
async def list_connections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    manager: BrokerManager = Depends(get_broker_manager),
):
    """List all broker accounts with connection status and balance."""
    from app.models.broker import BrokerAccount
    accounts = db.query(BrokerAccount).filter(BrokerAccount.user_id == current_user.id).all()

    brokers = {}
    for acct in accounts:
        brokers[acct.broker_name] = {"broker_name": acct.broker_name, "stored": True, "is_active": acct.is_active}

    # Add known brokers even if no stored credentials
    for name in ["oanda", "ctrader", "mt5"]:
        if name not in brokers:
            brokers[name] = {"broker_name": name, "stored": False, "is_active": False}

    # Check live connection status and get balance
    result = []
    for name, info in brokers.items():
        adapter = manager.get_adapter(current_user.id, name)
        connected_since = manager.get_connected_since(current_user.id, name)
        entry = {**info, "connected": adapter is not None, "balance": None, "currency": None, "account_id": None, "server": None, "connected_since": connected_since}
        if adapter:
            try:
                acct_info = await adapter.get_account_info()
                entry["balance"] = acct_info.balance
                entry["currency"] = acct_info.currency
                entry["account_id"] = acct_info.account_id or None
                entry["server"] = acct_info.server or None
            except Exception:
                pass
        result.append(entry)

    return result


@router.get("/status")
def broker_status(
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    connected_broker = manager.get_connected_broker(current_user.id)
    return {
        "connected": connected_broker is not None,
        "broker": connected_broker,
    }


@router.post("/connect")
async def connect_broker(
    body: BrokerConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    manager: BrokerManager = Depends(get_broker_manager),
):
    try:
        await manager.connect(current_user.id, body.broker_name, body.credentials, db)
        return {"status": "connected", "broker": body.broker_name}
    except BrokerError as e:
        return {"status": "error", "message": e.message}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/disconnect")
async def disconnect_broker(
    body: BrokerDisconnectRequest,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    await manager.disconnect(current_user.id, body.broker_name)
    return {"status": "disconnected"}


# ── Account Data ───────────────────────────────────────────────────────


@router.get("/account", response_model=AccountInfoResponse)
async def get_account(
    broker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    adapter = _get_adapter(current_user, manager, broker)
    if not adapter:
        return AccountInfoResponse()
    try:
        info = await adapter.get_account_info()
        return AccountInfoResponse(**asdict(info))
    except BrokerError:
        return AccountInfoResponse()


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions(
    broker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    adapter = _get_adapter(current_user, manager, broker)
    if not adapter:
        return []
    try:
        positions = await adapter.get_positions()
        return [PositionResponse(**asdict(p)) for p in positions]
    except BrokerError:
        return []


@router.get("/orders", response_model=list[OrderResponse])
async def get_orders(
    broker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    adapter = _get_adapter(current_user, manager, broker)
    if not adapter:
        return []
    try:
        orders = await adapter.get_orders()
        return [OrderResponse(**asdict(o)) for o in orders]
    except BrokerError:
        return []


@router.get("/symbols", response_model=list[SymbolResponse])
async def get_symbols(
    broker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    adapter = _get_adapter(current_user, manager, broker)
    if not adapter:
        return []
    try:
        symbols = await adapter.get_symbols()
        return [SymbolResponse(
            name=s.name, min_lot=s.min_lot, lot_step=s.lot_step,
            pip_size=s.pip_size, digits=s.digits,
        ) for s in symbols]
    except BrokerError:
        return []


@router.get("/candles/{symbol}", response_model=list[CandleResponse])
async def get_candles(
    symbol: str,
    timeframe: str = Query("M5"),
    count: int = Query(200, ge=1, le=5000),
    source: str = Query("broker"),  # "broker" or "databento"
    broker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
    db: Session = Depends(get_db),
):
    # If source is databento, use Databento adapter
    if source == "databento":
        try:
            from app.services.data.databento_adapter import DatabentoAdapter
            provider = db.query(MarketDataProvider).filter(
                MarketDataProvider.user_id == current_user.id,
                MarketDataProvider.provider_name == "databento",
                MarketDataProvider.is_active == True,
            ).first()
            if not provider:
                return []
            api_key = get_fernet().decrypt(provider.api_key_encrypted.encode()).decode()
            adapter = DatabentoAdapter(api_key)
            candles = await adapter.get_candles(symbol, timeframe, count)
            await adapter.close()
            return [CandleResponse(
                time=c.time, open=c.open, high=c.high,
                low=c.low, close=c.close, volume=c.volume,
            ) for c in candles]
        except ValueError as e:
            # Symbol not supported on Databento — fall back to broker
            pass
        except Exception:
            pass

    # Default: use broker adapter
    adapter = _get_adapter(current_user, manager, broker)
    if not adapter:
        return []
    try:
        candles = await adapter.get_candles(symbol, timeframe, count)
        return [CandleResponse(**asdict(c)) for c in candles]
    except BrokerError:
        return []


@router.get("/ticks/{symbol}")
async def get_ticks(
    symbol: str,
    count: int = Query(500, ge=1, le=5000),
    seconds_back: int = Query(300, ge=10, le=3600),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch tick/trade data from Databento (CME futures only)."""
    from app.services.data.databento_adapter import DatabentoAdapter
    provider = db.query(MarketDataProvider).filter(
        MarketDataProvider.user_id == current_user.id,
        MarketDataProvider.provider_name == "databento",
        MarketDataProvider.is_active == True,
    ).first()
    if not provider:
        return {"error": "No Databento provider configured. Add one in Settings → Providers."}
    try:
        api_key = get_fernet().decrypt(provider.api_key_encrypted.encode()).decode()
        adapter = DatabentoAdapter(api_key)
        ticks = await adapter.get_ticks(symbol, count, seconds_back)
        await adapter.close()
        return [{"time": t.time, "price": t.price, "size": t.size, "side": t.side} for t in ticks]
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Databento error: {str(e)}"}


# ── Trading ────────────────────────────────────────────────────────────


@router.post("/order", response_model=PlaceOrderResponse)
async def place_order(
    body: PlaceOrderRequest,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    adapter = _get_adapter(current_user, manager, body.broker)
    if not adapter:
        return PlaceOrderResponse(success=False, message="No broker connected")
    try:
        result = await adapter.place_order(
            symbol=body.symbol,
            side=body.direction,
            size=body.size,
            order_type=body.order_type,
            price=body.price,
            sl=body.sl,
            tp=body.tp,
        )
        return PlaceOrderResponse(success=result.success, order_id=result.order_id, message=result.message)
    except BrokerError as e:
        return PlaceOrderResponse(success=False, message=e.message)


@router.post("/close/{position_id}", response_model=ClosePositionResponse)
async def close_position(
    position_id: str,
    broker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    adapter = _get_adapter(current_user, manager, broker)
    if not adapter:
        return ClosePositionResponse(success=False, message="No broker connected")
    try:
        result = await adapter.close_position(position_id)
        return ClosePositionResponse(success=result.success, pnl=result.pnl, message=result.message)
    except BrokerError as e:
        return ClosePositionResponse(success=False, message=e.message)


@router.put("/modify/{order_id}", response_model=ModifyOrderResponse)
async def modify_order(
    order_id: str,
    body: ModifyOrderRequest,
    broker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    manager: BrokerManager = Depends(get_broker_manager),
):
    adapter = _get_adapter(current_user, manager, broker)
    if not adapter:
        return ModifyOrderResponse(success=False, message="No broker connected")
    try:
        result = await adapter.modify_order(order_id, sl=body.sl, tp=body.tp)
        return ModifyOrderResponse(success=result.success, message=result.message)
    except BrokerError as e:
        return ModifyOrderResponse(success=False, message=e.message)
