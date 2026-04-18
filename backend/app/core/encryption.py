import os
import warnings
from cryptography.fernet import Fernet
from app.core.config import settings


def _get_fernet() -> Fernet:
    key = settings.ENCRYPTION_KEY
    if not key:
        if not settings.DEBUG:
            raise RuntimeError("ENCRYPTION_KEY must be set in production")
        warnings.warn(
            "ENCRYPTION_KEY not set — generating ephemeral key (dev only). "
            "Encrypted data will NOT survive restarts.",
            stacklevel=2,
        )
        key = Fernet.generate_key().decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


_fernet = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _get_fernet()
    return _fernet


def validate_encryption_key():
    """
    Call at startup to fail fast if ENCRYPTION_KEY is invalid.
    Without this, a bad key only surfaces on the first encrypt/decrypt call
    (e.g., when a user connects a broker), which is too late to diagnose.
    """
    try:
        f = get_fernet()
        # Round-trip test: encrypt + decrypt a known value
        test = f.decrypt(f.encrypt(b"flowrex-key-check"))
        assert test == b"flowrex-key-check"
    except Exception as e:
        raise RuntimeError(f"ENCRYPTION_KEY validation failed: {e}. Check .env.") from e


def encrypt(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return get_fernet().decrypt(ciphertext.encode()).decode()


def generate_key() -> str:
    return Fernet.generate_key().decode()
