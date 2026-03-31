import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.core.password import hash_password

from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.services.broker.base import (
    BrokerAdapter, AccountInfo, Position, Order, Candle, SymbolInfo, Tick,
    OrderResult, CloseResult, ModifyResult,
)
from app.services.broker.manager import BrokerManager, get_broker_manager
import app.models  # noqa: F401 — register all models
from main import app

TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


# ── Fake Broker Adapter for testing ────────────────────────────────────


class FakeBrokerAdapter(BrokerAdapter):
    """Test double that returns canned data without hitting any real broker."""

    def __init__(self):
        self._connected = False

    @property
    def name(self) -> str:
        return "fake"

    async def connect(self, credentials: dict) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    async def get_account_info(self) -> AccountInfo:
        return AccountInfo(balance=10000.0, equity=10500.0, margin_used=500.0, currency="USD", unrealized_pnl=500.0)

    async def get_positions(self) -> list[Position]:
        return [Position(id="POS1", symbol="XAUUSD", direction="BUY", size=0.1, entry_price=2000.0, current_price=2010.0, pnl=100.0)]

    async def get_orders(self) -> list[Order]:
        return [Order(id="ORD1", symbol="BTCUSD", direction="BUY", size=0.01, order_type="LIMIT", price=50000.0, status="PENDING")]

    async def get_candles(self, symbol: str, timeframe: str = "M5", count: int = 200) -> list[Candle]:
        return [Candle(time=1700000000, open=2000.0, high=2010.0, low=1995.0, close=2005.0, volume=1000)]

    async def get_symbols(self) -> list[SymbolInfo]:
        return [SymbolInfo(name="XAUUSD", min_lot=0.01, lot_step=0.01, pip_size=0.01, pip_value=1.0, digits=2)]

    async def place_order(self, symbol, side, size, order_type="MARKET", price=None, sl=None, tp=None) -> OrderResult:
        return OrderResult(success=True, order_id="TEST123", message="Order filled")

    async def close_position(self, position_id: str) -> CloseResult:
        return CloseResult(success=True, pnl=50.0, message="Position closed")

    async def modify_order(self, order_id: str, sl=None, tp=None) -> ModifyResult:
        return ModifyResult(success=True, message="Order modified")

    async def get_tick(self, symbol: str) -> Tick:
        return Tick(symbol=symbol, bid=2000.0, ask=2000.5, time=1700000000)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
    """Create tables, yield a session, then drop all tables."""
    Base.metadata.create_all(bind=TEST_ENGINE)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture()
def test_user(db_session):
    """Create and return a test user in the test DB."""
    user = User(
        email="test@flowrex.local",
        password_hash=hash_password("testpass"),
        is_admin=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def client(db_session, test_user):
    """TestClient with DB and auth overrides (no broker connected)."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    def override_get_current_user():
        return test_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def fake_adapter():
    """A FakeBrokerAdapter instance."""
    return FakeBrokerAdapter()


@pytest.fixture()
def client_with_broker(db_session, test_user, fake_adapter):
    """TestClient with a fake broker already connected."""

    manager = BrokerManager()
    manager._adapters[(test_user.id, "fake")] = fake_adapter

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    def override_get_current_user():
        return test_user

    def override_get_broker_manager():
        return manager

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_broker_manager] = override_get_broker_manager

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
