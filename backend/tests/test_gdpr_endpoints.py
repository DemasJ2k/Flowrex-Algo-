"""
Tests for GDPR compliance endpoints (Batch 9, audit C34 + H36).

- DELETE /api/auth/account — Right to erasure (Art. 17)
- GET /api/auth/export-data — Right to access (Art. 15)
"""
import pytest

from app.models.user import User
from app.models.agent import TradingAgent
from app.core.password import hash_password


_STRONG_PW = "TestPassword123"


def test_delete_account_requires_password(client, db_session, test_user):
    """Deleting without password should fail. Uses client.request to pass body on DELETE."""
    r = client.request("DELETE", "/api/auth/account", json={"password": ""})
    assert r.status_code == 401


def test_delete_account_wrong_password(client, db_session, test_user):
    r = client.request("DELETE", "/api/auth/account", json={"password": "wrong-password"})
    assert r.status_code == 401


def test_delete_account_with_correct_password(client, db_session):
    # The test_user fixture has password "testpass" — create a fresh user with strong pw
    user = User(
        email="gdpr-delete@test.local",
        password_hash=hash_password(_STRONG_PW),
        is_admin=False,
    )
    db_session.add(user)
    db_session.commit()

    # Override the auth dep to return THIS user
    from app.core.auth import get_current_user
    from main import app
    app.dependency_overrides[get_current_user] = lambda: user

    try:
        r = client.request("DELETE", "/api/auth/account", json={"password": _STRONG_PW})
        assert r.status_code == 200
        assert "deleted" in r.json()["message"].lower()

        # Verify the user is GONE from the DB
        gone = db_session.query(User).filter(User.email == "gdpr-delete@test.local").first()
        assert gone is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_export_data_returns_user_profile(client, db_session, test_user):
    r = client.get("/api/auth/export-data")
    assert r.status_code == 200
    data = r.json()
    assert "profile" in data
    assert data["profile"]["email"] == test_user.email
    assert "exported_at" in data
    assert "agents" in data
    assert "trades" in data
    assert "agent_logs" in data
    assert "broker_accounts" in data


def test_export_data_includes_agents(client, db_session, test_user):
    agent = TradingAgent(
        created_by=test_user.id,
        name="ExportTest",
        symbol="XAUUSD",
        agent_type="potential",
        broker_name="fake",
        status="running",
        risk_config={"risk_per_trade": 0.001},
    )
    db_session.add(agent)
    db_session.commit()

    r = client.get("/api/auth/export-data")
    assert r.status_code == 200
    agents = r.json()["agents"]
    assert any(a["name"] == "ExportTest" for a in agents)


def test_export_data_excludes_credentials(client, db_session, test_user):
    """Credentials must NEVER appear in the export, even if broker accounts exist."""
    r = client.get("/api/auth/export-data")
    assert r.status_code == 200
    data = r.json()
    # Top level should not contain password_hash, totp_secret, etc.
    serialized = str(data)
    assert "password_hash" not in serialized
    assert "credentials_encrypted" not in serialized
    assert "totp_secret" not in serialized
    assert "reset_token" not in serialized
