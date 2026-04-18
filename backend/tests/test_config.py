import os
from unittest.mock import patch
from app.core.config import Settings


def test_settings_defaults():
    """Settings have correct default values when env vars are not set."""
    # Patch out the env vars that conftest.py sets, so we test actual defaults
    clean_env = {k: v for k, v in os.environ.items() if k not in ("DEBUG", "ENCRYPTION_KEY")}
    with patch.dict(os.environ, clean_env, clear=True):
        s = Settings(
            DATABASE_URL="postgresql://test:test@localhost/test",
            SECRET_KEY="test-key",
        )
    assert s.DEBUG is False  # Default is False for production safety
    assert s.ENCRYPTION_KEY == ""


def test_settings_database_url():
    """DATABASE_URL is read correctly."""
    s = Settings(DATABASE_URL="postgresql://a:b@host/db", SECRET_KEY="k")
    assert s.DATABASE_URL == "postgresql://a:b@host/db"
