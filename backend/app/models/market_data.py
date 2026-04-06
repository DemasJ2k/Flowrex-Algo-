from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class MarketDataProvider(Base):
    __tablename__ = "market_data_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider_name = Column(String(50), nullable=False)  # databento, alphavantage, finnhub, polygon
    api_key_encrypted = Column(String(500), nullable=False)
    data_type = Column(String(20), default="ohlcv")  # ohlcv, tick
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
