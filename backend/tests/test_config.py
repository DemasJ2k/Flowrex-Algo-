from app.core.config import Settings


def test_settings_defaults():
    """Settings have correct default values."""
    s = Settings(
        DATABASE_URL="postgresql://test:test@localhost/test",
        SECRET_KEY="test-key",
    )
    assert s.DEBUG is False  # Default is False for production safety
    assert "http://localhost:3000" in s.ALLOWED_ORIGINS
    assert s.ENCRYPTION_KEY == ""


def test_settings_database_url():
    """DATABASE_URL is read correctly."""
    s = Settings(DATABASE_URL="postgresql://a:b@host/db", SECRET_KEY="k")
    assert s.DATABASE_URL == "postgresql://a:b@host/db"
