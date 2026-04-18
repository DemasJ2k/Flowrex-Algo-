from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, ForeignKey, JSON, Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    totp_secret = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)
    reset_token = Column(String(100), nullable=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # GDPR consent tracking (migration 003)
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    terms_version = Column(String(20), nullable=True)
    privacy_accepted_at = Column(DateTime(timezone=True), nullable=True)
    date_of_birth = Column(Date, nullable=True)

    settings = relationship("UserSettings", uselist=False, back_populates="user", cascade="all, delete-orphan")
    broker_accounts = relationship("BrokerAccount", back_populates="user", cascade="all, delete-orphan")
    agents = relationship("TradingAgent", back_populates="creator", cascade="all, delete-orphan")


class AdminAuditLog(Base):
    """Tracks admin access to user data for GDPR Art. 32 compliance."""
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(Integer, nullable=True)
    ip_address = Column(String(45), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    theme = Column(String(20), default="dark")
    default_broker = Column(String(50), nullable=True)
    notifications_enabled = Column(Boolean, default=True)
    settings_json = Column(JSON, default=dict)

    user = relationship("User", back_populates="settings")
