import json
import time
from typing import Optional
from sqlalchemy.orm import Session

from app.core.encryption import encrypt, decrypt
from app.models.broker import BrokerAccount
from app.services.broker.base import BrokerAdapter, BrokerError
from app.services.broker.oanda import OandaAdapter
from app.services.broker.ctrader import CTraderAdapter
from app.services.broker.mt5 import MT5Adapter
from app.services.broker.tradovate import TradovateAdapter


_ADAPTER_CLASSES = {
    "oanda": OandaAdapter,
    "ctrader": CTraderAdapter,
    "mt5": MT5Adapter,
    "tradovate": TradovateAdapter,
}


class BrokerManager:
    """
    Manages active broker adapter instances.
    Keyed by (user_id, broker_name) -> BrokerAdapter.
    """

    def __init__(self):
        self._adapters: dict[tuple[int, str], BrokerAdapter] = {}
        self._connect_times: dict[tuple[int, str], float] = {}

    def _create_adapter(self, broker_name: str) -> BrokerAdapter:
        cls = _ADAPTER_CLASSES.get(broker_name)
        if not cls:
            raise BrokerError(f"Unsupported broker: {broker_name}")
        return cls()

    async def connect(
        self,
        user_id: int,
        broker_name: str,
        credentials: Optional[dict],
        db: Session,
    ) -> bool:
        """
        Connect to a broker. If credentials provided, encrypt and store.
        If not, load encrypted credentials from DB.
        """
        key = (user_id, broker_name)

        # Already connected?
        if key in self._adapters:
            return True

        # One-active-broker: disconnect any other broker for this user first
        existing_broker = self.get_connected_broker(user_id)
        if existing_broker and existing_broker != broker_name:
            await self.disconnect(user_id, existing_broker)

        # Store or load credentials
        broker_account = (
            db.query(BrokerAccount)
            .filter(BrokerAccount.user_id == user_id, BrokerAccount.broker_name == broker_name)
            .first()
        )

        if credentials:
            # Encrypt and store
            encrypted = encrypt(json.dumps(credentials))
            if broker_account:
                broker_account.credentials_encrypted = encrypted
                broker_account.is_active = True
            else:
                broker_account = BrokerAccount(
                    user_id=user_id,
                    broker_name=broker_name,
                    credentials_encrypted=encrypted,
                    is_active=True,
                )
                db.add(broker_account)
            db.commit()
        elif broker_account and broker_account.credentials_encrypted:
            # Decrypt stored credentials
            credentials = json.loads(decrypt(broker_account.credentials_encrypted))
        else:
            # No stored credentials — pass empty dict (adapter may auto-fill from .env)
            credentials = {}

        # Instantiate and connect adapter with retry.
        # If connect() raises, we do NOT cache a stale adapter — this is a common
        # wiring bug where a broken adapter sits in the dict, silently failing
        # every poll with no reconnect opportunity.
        adapter = self._create_adapter(broker_name)
        last_err = None
        for attempt in range(3):
            try:
                await adapter.connect(credentials)
                self._adapters[key] = adapter
                self._connect_times[key] = time.time()
                return True
            except Exception as e:
                last_err = e
                if attempt < 2:
                    import asyncio as _a
                    await _a.sleep(2 ** attempt)  # 1s, 2s
                continue
        # All retries failed — DO NOT cache the broken adapter
        raise BrokerError(f"Failed to connect to {broker_name} after 3 attempts: {last_err}")

    async def disconnect(self, user_id: int, broker_name: str) -> None:
        key = (user_id, broker_name)
        adapter = self._adapters.pop(key, None)
        self._connect_times.pop(key, None)
        if adapter:
            await adapter.disconnect()

    def get_adapter(self, user_id: int, broker_name: str) -> Optional[BrokerAdapter]:
        return self._adapters.get((user_id, broker_name))

    def get_connected_broker(self, user_id: int) -> Optional[str]:
        """Return the first connected broker name for a user, or None."""
        for (uid, bname), _ in self._adapters.items():
            if uid == user_id:
                return bname
        return None

    def get_connected_since(self, user_id: int, broker_name: str) -> Optional[float]:
        """Return connection timestamp (epoch) or None."""
        return self._connect_times.get((user_id, broker_name))

    def get_status(self, user_id: int) -> dict[str, bool]:
        """Return connection status for each broker the user has tried."""
        result = {}
        for (uid, bname), _ in self._adapters.items():
            if uid == user_id:
                result[bname] = True
        return result

    async def disconnect_all(self) -> None:
        """Disconnect all adapters (e.g. on shutdown)."""
        for key in list(self._adapters.keys()):
            adapter = self._adapters.pop(key)
            await adapter.disconnect()


# ── Singleton ──────────────────────────────────────────────────────────

_manager: Optional[BrokerManager] = None


def get_broker_manager() -> BrokerManager:
    """FastAPI dependency — returns the singleton BrokerManager."""
    global _manager
    if _manager is None:
        _manager = BrokerManager()
    return _manager
