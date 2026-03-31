"""
Seed script — creates default admin user and sample agents.
Run: python -m scripts.seed  (from backend/ directory)
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import Session
from app.core.password import hash_password

from app.models.user import User, UserSettings
from app.models.agent import TradingAgent


ADMIN_EMAIL = "admin@flowrex.local"
ADMIN_PASSWORD = "admin123"

DEFAULT_RISK_CONFIG = {
    "risk_per_trade": 0.005,
    "max_daily_loss_pct": 0.04,
    "cooldown_bars": 3,
}

SAMPLE_AGENTS = [
    {"name": "BTC Scalper", "symbol": "BTCUSD", "agent_type": "scalping", "broker_name": "oanda"},
    {"name": "BTC Expert", "symbol": "BTCUSD", "agent_type": "expert", "broker_name": "oanda"},
    {"name": "Gold Scalper", "symbol": "XAUUSD", "agent_type": "scalping", "broker_name": "oanda"},
    {"name": "Gold Expert", "symbol": "XAUUSD", "agent_type": "expert", "broker_name": "oanda"},
    {"name": "US30 Scalper", "symbol": "US30", "agent_type": "scalping", "broker_name": "oanda"},
    {"name": "US30 Expert", "symbol": "US30", "agent_type": "expert", "broker_name": "oanda"},
]


def run_seed(db: Session) -> dict:
    """
    Seed the database with an admin user and sample agents.
    Returns a summary dict. Idempotent — skips if admin already exists.
    """
    existing = db.query(User).filter(User.email == ADMIN_EMAIL).first()
    if existing:
        return {"status": "skipped", "message": "Admin user already exists"}

    # Create admin user
    admin = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        is_admin=True,
    )
    db.add(admin)
    db.flush()

    # Create user settings
    settings = UserSettings(user_id=admin.id, theme="dark")
    db.add(settings)

    # Create sample agents
    for agent_def in SAMPLE_AGENTS:
        agent = TradingAgent(
            created_by=admin.id,
            name=agent_def["name"],
            symbol=agent_def["symbol"],
            timeframe="M5",
            agent_type=agent_def["agent_type"],
            broker_name=agent_def["broker_name"],
            mode="paper",
            risk_config=DEFAULT_RISK_CONFIG,
        )
        db.add(agent)

    db.commit()
    return {
        "status": "created",
        "user": ADMIN_EMAIL,
        "agents_created": len(SAMPLE_AGENTS),
    }


if __name__ == "__main__":
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        result = run_seed(db)
        print(f"Seed result: {result}")
    finally:
        db.close()
