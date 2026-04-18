from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class TradingAgent(Base):
    __tablename__ = "trading_agents"
    __table_args__ = (
        Index("ix_trading_agents_created_by_deleted_at", "created_by", "deleted_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), default="M5")
    agent_type = Column(String(20), nullable=False)
    broker_name = Column(String(50), nullable=False)
    mode = Column(String(20), default="paper")
    status = Column(String(20), default="stopped")
    risk_config = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    creator = relationship("User", back_populates="agents")
    logs = relationship("AgentLog", back_populates="agent", cascade="all, delete-orphan")
    trades = relationship("AgentTrade", back_populates="agent", cascade="all, delete-orphan")


class AgentLog(Base):
    __tablename__ = "agent_logs"
    __table_args__ = (
        Index("ix_agent_logs_agent_id_created_at", "agent_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("trading_agents.id"), nullable=False)
    level = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    agent = relationship("TradingAgent", back_populates="logs")


class AgentTrade(Base):
    __tablename__ = "agent_trades"
    __table_args__ = (
        Index("ix_agent_trades_agent_id_status", "agent_id", "status"),
        Index("ix_agent_trades_broker_ticket", "broker_ticket"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("trading_agents.id"), nullable=False)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    lot_size = Column(Float, nullable=False)
    pnl = Column(Float, nullable=True)
    broker_pnl = Column(Float, nullable=True)
    broker_ticket = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="open")
    exit_reason = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)
    signal_data = Column(JSON, nullable=True)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=True)

    # Execution quality tracking (Batch M — V4 audit)
    requested_price = Column(Float, nullable=True)
    fill_price = Column(Float, nullable=True)
    slippage_pips = Column(Float, nullable=True)

    # Analytics columns (migration 007)
    mtf_score = Column(Integer, nullable=True)
    mtf_layers = Column(JSON, nullable=True)
    session_name = Column(String(20), nullable=True)
    top_features = Column(JSON, nullable=True)
    atr_at_entry = Column(Float, nullable=True)
    model_name = Column(String(50), nullable=True)
    time_to_exit_seconds = Column(Integer, nullable=True)
    bars_to_exit = Column(Integer, nullable=True)

    agent = relationship("TradingAgent", back_populates="trades")
