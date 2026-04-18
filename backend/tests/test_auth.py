"""Tests for JWT auth system and auth endpoints."""
import pytest
from app.core.auth import create_access_token, create_refresh_token, verify_token
from app.core.password import hash_password, verify_password


# ── JWT token tests ────────────────────────────────────────────────────

def test_create_and_verify_access_token():
    token = create_access_token(user_id=42)
    user_id = verify_token(token, "access")
    assert user_id == 42


def test_create_and_verify_refresh_token():
    token = create_refresh_token(user_id=42)
    user_id = verify_token(token, "refresh")
    assert user_id == 42


def test_access_token_rejected_as_refresh():
    token = create_access_token(user_id=42)
    result = verify_token(token, "refresh")
    assert result is None


def test_refresh_token_rejected_as_access():
    token = create_refresh_token(user_id=42)
    result = verify_token(token, "access")
    assert result is None


def test_invalid_token():
    result = verify_token("invalid.token.here", "access")
    assert result is None


# ── Password hashing tests ────────────────────────────────────────────

def test_hash_and_verify():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed) is True
    assert verify_password("wrongpassword", hashed) is False


def test_hash_is_not_plaintext():
    hashed = hash_password("secret")
    assert hashed != "secret"


# ── Auth endpoint tests ───────────────────────────────────────────────

def _create_invite(db_session, test_user):
    """Helper: create a valid invite code in the test DB."""
    from app.models.invite import InviteCode
    invite = InviteCode(code="TEST-INVITE-001", created_by=test_user.id, max_uses=10)
    db_session.add(invite)
    db_session.commit()
    return "TEST-INVITE-001"


# Strong password meeting Batch 4 (audit H24) validation:
# 12+ chars, uppercase, lowercase, digit
_STRONG_PW = "TestPassword123"


def test_register_without_terms_rejected(client):
    """REGRESSION: Migration 003 — terms_accepted is required for new registrations."""
    resp = client.post("/api/auth/register", json={"email": "new@test.com", "password": _STRONG_PW, "invite_code": "X"})
    assert resp.status_code == 400
    assert "Terms of Service" in resp.json()["detail"]


def test_register_without_invite_rejected(client):
    resp = client.post("/api/auth/register", json={"email": "new@test.com", "password": _STRONG_PW, "terms_accepted": True})
    assert resp.status_code == 400
    assert "Invite code is required" in resp.json()["detail"]


def test_register_with_invalid_invite_rejected(client):
    resp = client.post("/api/auth/register", json={"email": "new@test.com", "password": _STRONG_PW, "invite_code": "FAKE-CODE", "terms_accepted": True})
    assert resp.status_code == 400
    assert "Invalid invite code" in resp.json()["detail"]


def test_register_with_valid_invite(client, db_session, test_user):
    code = _create_invite(db_session, test_user)
    resp = client.post("/api/auth/register", json={"email": "new@test.com", "password": _STRONG_PW, "invite_code": code, "terms_accepted": True})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_register_weak_password_rejected(client, db_session, test_user):
    """REGRESSION: H24 — weak passwords must be rejected by Pydantic schema."""
    code = _create_invite(db_session, test_user)
    resp = client.post("/api/auth/register", json={"email": "weak@test.com", "password": "weak", "invite_code": code})
    assert resp.status_code == 422  # Pydantic validation error


def test_register_duplicate_email(client, db_session, test_user):
    code = _create_invite(db_session, test_user)
    client.post("/api/auth/register", json={"email": "dup@test.com", "password": _STRONG_PW, "invite_code": code, "terms_accepted": True})
    resp = client.post("/api/auth/register", json={"email": "dup@test.com", "password": _STRONG_PW + "x", "invite_code": code, "terms_accepted": True})
    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"]


def test_login_success(client, db_session, test_user):
    code = _create_invite(db_session, test_user)
    client.post("/api/auth/register", json={"email": "login@test.com", "password": _STRONG_PW, "invite_code": code, "terms_accepted": True})
    resp = client.post("/api/auth/login", json={"email": "login@test.com", "password": _STRONG_PW})
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"


def test_login_wrong_password(client, db_session, test_user):
    code = _create_invite(db_session, test_user)
    client.post("/api/auth/register", json={"email": "wrong@test.com", "password": _STRONG_PW, "invite_code": code, "terms_accepted": True})
    resp = client.post("/api/auth/login", json={"email": "wrong@test.com", "password": "WrongPassword999"})
    assert resp.status_code == 401


def test_refresh_token_flow(client, db_session, test_user):
    code = _create_invite(db_session, test_user)
    reg = client.post("/api/auth/register", json={"email": "refresh@test.com", "password": _STRONG_PW, "invite_code": code, "terms_accepted": True})
    refresh = reg.json()["refresh_token"]
    resp = client.post(f"/api/auth/refresh?token={refresh}")
    assert resp.status_code == 200
    assert "access_token" in resp.json()


# ── Invite management tests ──────────────────────────────────────────

def test_admin_generate_invites(client):
    resp = client.post("/api/admin/invites", json={"count": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["codes"]) == 3


def test_admin_list_invites(client):
    client.post("/api/admin/invites", json={"count": 2})
    resp = client.get("/api/admin/invites")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


# ── Change password tests ────────────────────────────────────────────

def test_change_password_success(client):
    """Should update password when current password is correct (JSON body, not query params)."""
    resp = client.put("/api/auth/change-password", json={
        "current_password": "testpass",
        "new_password": "newSecure123",
    })
    assert resp.status_code == 200
    assert resp.json()["message"] == "Password updated successfully"


def test_change_password_wrong_current(client):
    """Should reject when current password is wrong."""
    resp = client.put("/api/auth/change-password", json={
        "current_password": "wrongpassword",
        "new_password": "newSecure123",
    })
    assert resp.status_code == 400
    assert "incorrect" in resp.json()["detail"]


def test_change_password_too_short(client):
    """Should reject new passwords shorter than 8 characters."""
    resp = client.put("/api/auth/change-password", json={
        "current_password": "testpass",
        "new_password": "short",
    })
    assert resp.status_code == 400
    assert "8 characters" in resp.json()["detail"]


def test_change_password_not_in_url(client):
    """Password must NOT be accepted via query params — only JSON body."""
    resp = client.put(
        "/api/auth/change-password",
        params={"current_password": "testpass", "new_password": "newSecure123"},
    )
    # Should fail with 422 (missing required body) not 200
    assert resp.status_code == 422


# ── Backtest endpoint tests ───────────────────────────────────────────

def test_backtest_results_empty(client):
    resp = client.get("/api/backtest/results")
    assert resp.status_code == 200


# ── Admin endpoint tests ──────────────────────────────────────────────

def test_admin_users(client):
    """Dev user is admin, should be able to list users."""
    resp = client.get("/api/admin/users")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_admin_system(client):
    resp = client.get("/api/admin/system")
    assert resp.status_code == 200
    assert "database" in resp.json()
