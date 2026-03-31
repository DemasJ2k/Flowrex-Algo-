from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class AgentCreate(BaseModel):
    name: str
    symbol: str
    timeframe: str = "M5"
    agent_type: str
    broker_name: str
    mode: str = "paper"
    risk_config: dict = {}


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    agent_type: Optional[str] = None
    broker_name: Optional[str] = None
    mode: Optional[str] = None
    risk_config: Optional[dict] = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    symbol: str
    timeframe: str
    agent_type: str
    broker_name: str
    mode: str
    status: str
    risk_config: Optional[dict] = None
    created_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    trade_count: int = 0
    total_pnl: float = 0.0


class LogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    level: str
    message: str
    data: Optional[dict] = None
    created_at: Optional[datetime] = None


class TradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    symbol: str
    direction: str
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float
    take_profit: float
    lot_size: float
    pnl: Optional[float] = None
    broker_pnl: Optional[float] = None
    broker_ticket: Optional[str] = None
    status: str
    exit_reason: Optional[str] = None
    confidence: Optional[float] = None
    signal_data: Optional[dict] = None
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None


class PnlSummaryItem(BaseModel):
    agent_id: int
    agent_name: str
    symbol: str
    total_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
