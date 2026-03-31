import json
from unittest.mock import patch
from cryptography.fernet import Fernet
import app.core.encryption as enc_module


def setup_function():
    """Reset the cached fernet before each test."""
    enc_module._fernet = None


def test_encrypt_decrypt_roundtrip():
    """Encrypting then decrypting returns the original text."""
    test_key = Fernet.generate_key().decode()
    with patch.object(enc_module.settings, "ENCRYPTION_KEY", test_key):
        with patch.object(enc_module.settings, "DEBUG", True):
            plaintext = "super-secret-api-key-123"
            encrypted = enc_module.encrypt(plaintext)
            assert encrypted != plaintext
            decrypted = enc_module.decrypt(encrypted)
            assert decrypted == plaintext


def test_encrypt_decrypt_json_blob():
    """Broker credentials (JSON) roundtrip correctly."""
    test_key = Fernet.generate_key().decode()
    with patch.object(enc_module.settings, "ENCRYPTION_KEY", test_key):
        with patch.object(enc_module.settings, "DEBUG", True):
            creds = {"api_key": "abc123", "account_id": "001-001-12345"}
            plaintext = json.dumps(creds)
            encrypted = enc_module.encrypt(plaintext)
            decrypted = enc_module.decrypt(encrypted)
            assert json.loads(decrypted) == creds


def test_generate_key_is_valid():
    """generate_key produces a valid Fernet key."""
    key = enc_module.generate_key()
    assert isinstance(key, str)
    # Should not raise
    Fernet(key.encode())


def test_different_keys_cannot_decrypt():
    """Decrypting with a different key fails."""
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()

    with patch.object(enc_module.settings, "ENCRYPTION_KEY", key1):
        with patch.object(enc_module.settings, "DEBUG", True):
            encrypted = enc_module.encrypt("secret")

    enc_module._fernet = None
    with patch.object(enc_module.settings, "ENCRYPTION_KEY", key2):
        with patch.object(enc_module.settings, "DEBUG", True):
            try:
                enc_module.decrypt(encrypted)
                assert False, "Should have raised"
            except Exception:
                pass  # Expected
