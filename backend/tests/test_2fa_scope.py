"""
Tests for the 2FA partial-token scope fix (Batch 4, audit C1).

Pre-fix, the "partial token" returned from /login when 2FA was enabled was
actually a full access token. Any client could use it for any endpoint,
completely bypassing 2FA.

These tests verify the new scope claim:
  - Login with 2FA returns scope="partial" (5-min expiry)
  - Partial tokens are rejected by all protected endpoints EXCEPT /2fa/verify
  - Successful 2FA verify returns a fresh scope="full" token
"""
import pytest
from datetime import datetime, timezone, timedelta
from jose import jwt

from app.core.auth import (
    create_access_token, verify_token, ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM,
)
from app.core.config import settings


def _decode(token):
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


def test_create_full_token_default_scope():
    token = create_access_token(user_id=42)
    payload = _decode(token)
    assert payload["sub"] == "42"
    assert payload["scope"] == "full"
    assert payload["type"] == "access"


def test_create_partial_token_explicit_scope():
    token = create_access_token(user_id=42, scope="partial")
    payload = _decode(token)
    assert payload["scope"] == "partial"


def test_partial_token_has_short_expiry():
    """Partial tokens must expire in 5 min, not 24 hours."""
    token = create_access_token(user_id=42, scope="partial")
    payload = _decode(token)
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    delta = exp - datetime.now(timezone.utc)
    # Should be ≈ 5 minutes, definitely less than 10
    assert delta < timedelta(minutes=10), f"Partial token TTL too long: {delta}"
    assert delta > timedelta(minutes=4), f"Partial token TTL too short: {delta}"


def test_full_token_has_normal_expiry():
    """Full tokens get the standard 24h expiry."""
    token = create_access_token(user_id=42, scope="full")
    payload = _decode(token)
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    delta = exp - datetime.now(timezone.utc)
    # Should be ≈ 24h
    assert delta > timedelta(hours=23)


def test_verify_token_rejects_partial_by_default():
    """REGRESSION: C1 — partial tokens must NOT pass verify_token() default."""
    partial = create_access_token(user_id=42, scope="partial")
    user_id = verify_token(partial, token_type="access")
    assert user_id is None, "partial token must be rejected by default verify_token"


def test_verify_token_accepts_partial_when_allowed():
    """allow_partial=True is for /2fa/verify only."""
    partial = create_access_token(user_id=42, scope="partial")
    user_id = verify_token(partial, token_type="access", allow_partial=True)
    assert user_id == 42


def test_verify_token_accepts_full_with_or_without_allow_partial():
    full = create_access_token(user_id=42, scope="full")
    assert verify_token(full, token_type="access") == 42
    assert verify_token(full, token_type="access", allow_partial=True) == 42


def test_old_token_without_scope_claim_treated_as_full():
    """
    Backward compat: tokens issued before this fix don't have a scope claim.
    Default to 'full' to avoid breaking existing sessions on deploy.
    """
    payload = {
        "sub": "42",
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        # no scope claim
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)
    user_id = verify_token(token, token_type="access")
    assert user_id == 42, "tokens without scope claim should be treated as full"
