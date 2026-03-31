"""Unit tests for the BrokerManager."""
import pytest
import json
from unittest.mock import patch, AsyncMock
from app.services.broker.manager import BrokerManager
from app.services.broker.base import BrokerError
from app.models.broker import BrokerAccount


@pytest.fixture()
def manager():
    return BrokerManager()


@pytest.mark.asyncio
async def test_connect_stores_adapter(manager, db_session, test_user):
    """After connect, get_adapter returns the adapter."""
    with patch.object(manager, "_create_adapter") as mock_create:
        fake = AsyncMock()
        fake.connect = AsyncMock(return_value=True)
        mock_create.return_value = fake

        await manager.connect(test_user.id, "oanda", {"api_key": "k", "account_id": "a"}, db_session)

        adapter = manager.get_adapter(test_user.id, "oanda")
        assert adapter is fake


@pytest.mark.asyncio
async def test_disconnect_removes_adapter(manager, db_session, test_user):
    """After disconnect, get_adapter returns None."""
    with patch.object(manager, "_create_adapter") as mock_create:
        fake = AsyncMock()
        fake.connect = AsyncMock(return_value=True)
        fake.disconnect = AsyncMock()
        mock_create.return_value = fake

        await manager.connect(test_user.id, "oanda", {"api_key": "k", "account_id": "a"}, db_session)
        await manager.disconnect(test_user.id, "oanda")

        assert manager.get_adapter(test_user.id, "oanda") is None
        fake.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_get_status_empty(manager, test_user):
    status = manager.get_status(test_user.id)
    assert status == {}


@pytest.mark.asyncio
async def test_get_status_after_connect(manager, db_session, test_user):
    with patch.object(manager, "_create_adapter") as mock_create:
        fake = AsyncMock()
        fake.connect = AsyncMock(return_value=True)
        mock_create.return_value = fake

        await manager.connect(test_user.id, "oanda", {"api_key": "k", "account_id": "a"}, db_session)

        status = manager.get_status(test_user.id)
        assert status == {"oanda": True}


@pytest.mark.asyncio
async def test_credentials_encrypted_in_db(manager, db_session, test_user):
    """Credentials stored in DB should be encrypted, not plaintext."""
    with patch.object(manager, "_create_adapter") as mock_create:
        fake = AsyncMock()
        fake.connect = AsyncMock(return_value=True)
        mock_create.return_value = fake

        creds = {"api_key": "super-secret-key", "account_id": "12345"}
        await manager.connect(test_user.id, "oanda", creds, db_session)

        # Check DB record
        record = db_session.query(BrokerAccount).filter(
            BrokerAccount.user_id == test_user.id,
            BrokerAccount.broker_name == "oanda",
        ).first()
        assert record is not None
        # Encrypted value should NOT contain the plaintext key
        assert "super-secret-key" not in record.credentials_encrypted
        assert record.credentials_encrypted != json.dumps(creds)


@pytest.mark.asyncio
async def test_credentials_decrypt_on_reconnect(manager, db_session, test_user):
    """Reconnecting without credentials should use stored encrypted creds."""
    with patch.object(manager, "_create_adapter") as mock_create:
        fake = AsyncMock()
        fake.connect = AsyncMock(return_value=True)
        fake.disconnect = AsyncMock()
        mock_create.return_value = fake

        creds = {"api_key": "my-key", "account_id": "acc-1"}
        # First connect stores credentials
        await manager.connect(test_user.id, "oanda", creds, db_session)
        await manager.disconnect(test_user.id, "oanda")

        # Reconnect without providing credentials
        fake2 = AsyncMock()
        fake2.connect = AsyncMock(return_value=True)
        mock_create.return_value = fake2

        await manager.connect(test_user.id, "oanda", None, db_session)

        # The adapter should have received the decrypted credentials
        call_args = fake2.connect.call_args[0][0]
        assert call_args["api_key"] == "my-key"
        assert call_args["account_id"] == "acc-1"
