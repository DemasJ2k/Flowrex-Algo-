"""Tests for app.api.telegram — webhook binding flow + secret validation.

Uses the shared `db_session` + `client` fixtures from conftest.py which set up
an in-memory SQLite with all models registered.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.user import User, UserSettings
from app.models.telegram import TelegramBinding
from app.core.password import hash_password


_PW = "TelegramTest123"


@pytest.fixture()
def webhook_user(db_session):
    """Create a test user and cleanup after."""
    u = User(email="webhook_test@test.local", password_hash=hash_password(_PW))
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    yield u


class TestWebhookSecretValidation:
    def test_webhook_accepts_valid_secret(self, client, db_session, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "test-secret")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")  # disable real API call
        resp = client.post(
            "/api/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json={"message": {"chat": {"id": "123"}, "text": "/help"}},
        )
        assert resp.status_code == 200

    def test_webhook_rejects_missing_secret(self, client, db_session, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "test-secret")
        resp = client.post(
            "/api/telegram/webhook",
            json={"message": {"chat": {"id": "123"}, "text": "/help"}},
        )
        assert resp.status_code == 403

    def test_webhook_rejects_wrong_secret(self, client, db_session, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "test-secret")
        resp = client.post(
            "/api/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"message": {"chat": {"id": "123"}, "text": "/help"}},
        )
        assert resp.status_code == 403

    def test_webhook_no_secret_configured_allows_all(self, client, db_session, monkeypatch):
        """If server has no secret set (dev), webhook accepts unsigned requests."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
        resp = client.post(
            "/api/telegram/webhook",
            json={"message": {"chat": {"id": "123"}, "text": "unrecognized"}},
        )
        assert resp.status_code == 200


class TestBindingFlow:
    def test_start_with_valid_code_links_chat(self, client, db_session, webhook_user, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")

        code = "ABC123"
        binding = TelegramBinding(
            user_id=webhook_user.id,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db_session.add(binding)
        db_session.commit()

        resp = client.post(
            "/api/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "555666", "username": "testuser", "first_name": "Test"},
                    "text": f"/start {code}",
                }
            },
        )
        assert resp.status_code == 200

        sr = db_session.query(UserSettings).filter(UserSettings.user_id == webhook_user.id).first()
        assert sr is not None
        assert sr.settings_json.get("telegram_chat_id") == "555666"
        assert sr.settings_json.get("telegram_username") == "testuser"
        assert sr.settings_json.get("telegram_first_name") == "Test"

        db_session.refresh(binding)
        assert binding.used_at is not None

    def test_start_with_invalid_code_rejected(self, client, db_session, webhook_user, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")

        resp = client.post(
            "/api/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "555666", "username": "testuser"},
                    "text": "/start WRONGCODE",
                }
            },
        )
        assert resp.status_code == 200  # Telegram convention: always 200
        sr = db_session.query(UserSettings).filter(UserSettings.user_id == webhook_user.id).first()
        assert sr is None or sr.settings_json.get("telegram_chat_id") != "555666"

    def test_expired_code_rejected(self, client, db_session, webhook_user, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")

        binding = TelegramBinding(
            user_id=webhook_user.id,
            code="EXPIRE",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db_session.add(binding)
        db_session.commit()

        resp = client.post(
            "/api/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "555666", "username": "testuser"},
                    "text": "/start EXPIRE",
                }
            },
        )
        assert resp.status_code == 200
        sr = db_session.query(UserSettings).filter(UserSettings.user_id == webhook_user.id).first()
        assert sr is None or sr.settings_json.get("telegram_chat_id") != "555666"

    def test_code_reuse_rejected(self, client, db_session, webhook_user, monkeypatch):
        """Once a binding code is used, it can't be used again."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")

        binding = TelegramBinding(
            user_id=webhook_user.id,
            code="REUSE1",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            used_at=datetime.now(timezone.utc),
        )
        db_session.add(binding)
        db_session.commit()

        resp = client.post(
            "/api/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "attacker-chat-id", "username": "attacker"},
                    "text": "/start REUSE1",
                }
            },
        )
        assert resp.status_code == 200
        sr = db_session.query(UserSettings).filter(UserSettings.user_id == webhook_user.id).first()
        assert sr is None or sr.settings_json.get("telegram_chat_id") != "attacker-chat-id"
