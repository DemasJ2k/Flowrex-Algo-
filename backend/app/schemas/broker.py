from pydantic import BaseModel
from typing import Optional


class BrokerConnectRequest(BaseModel):
    broker_name: str
    credentials: dict


class BrokerDisconnectRequest(BaseModel):
    broker_name: str


class AccountInfoResponse(BaseModel):
    balance: float = 0.0
    equity: float = 0.0
    margin_used: float = 0.0
    currency: str = "USD"
    unrealized_pnl: float = 0.0
    account_id: str = ""
    server: str = ""


class PositionResponse(BaseModel):
    id: str
    symbol: str
    direction: str
    size: float
    entry_price: float
    current_price: float
    pnl: float
    sl: Optional[float] = None
    tp: Optional[float] = None


class OrderResponse(BaseModel):
    id: str
    symbol: str
    direction: str
    size: float
    order_type: str
    price: float
    status: str
    sl: Optional[float] = None
    tp: Optional[float] = None


class SymbolResponse(BaseModel):
    name: str
    min_lot: float = 0.01
    lot_step: float = 0.01
    pip_size: float = 0.0001
    digits: int = 5


class CandleResponse(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class PlaceOrderRequest(BaseModel):
    symbol: str
    direction: str
    size: float
    order_type: str = "MARKET"
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    broker: Optional[str] = None


class PlaceOrderResponse(BaseModel):
    success: bool
    order_id: str = ""
    message: str = ""


class ClosePositionResponse(BaseModel):
    success: bool
    pnl: float = 0.0
    message: str = ""


class ModifyOrderRequest(BaseModel):
    sl: Optional[float] = None
    tp: Optional[float] = None


class ModifyOrderResponse(BaseModel):
    success: bool
    message: str = ""
