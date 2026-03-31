"""Tests for admin endpoints: invite management, user listing, system health."""
import pytest
from app.models.invite import InviteCode


# ── Helpers ───────────────────────────────────────────────────────────


def _make_invite(db_session, test_user, code="FIXED-CODE-001", max_uses=1, is_active=True):
    invite = InviteCode(code=code, created_by=test_user.id, max_uses=max_uses, is_active=is_active)
    db_session.add(invite)
    db_session.commit()
    db_session.refresh(invite)
    return invite


# ── Invite generation ─────────────────────────────────────────────────


def test_generate_invites_default_count(client):
    resp = client.post("/api/admin/invites", json={"count": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["codes"]) == 3
    assert data["count"] == 3


def test_generate_invites_single(client):
    resp = client.post("/api/admin/invites", json={"count": 1})
    assert resp.status_code == 200
    assert len(resp.json()["codes"]) == 1


def test_generate_invites_capped_at_50(client):
    resp = client.post("/api/admin/invites", json={"count": 100})
    assert resp.status_code == 200
    assert len(resp.json()["codes"]) <= 50


def test_generate_invites_with_max_uses(client):
    resp = client.post("/api/admin/invites", json={"count": 2, "max_uses": 5})
    assert resp.status_code == 200
    assert len(resp.json()["codes"]) == 2


def test_generate_invites_no_expiry(client):
    resp = client.post("/api/admin/invites", json={"count": 1, "expires_days": None})
    assert resp.status_code == 200
    assert resp.json()["expires_at"] is None


def test_generate_invites_codes_are_unique(client):
    resp = client.post("/api/admin/invites", json={"count": 10})
    codes = resp.json()["codes"]
    assert len(set(codes)) == len(codes)  # all unique


# ── Invite listing ────────────────────────────────────────────────────


def test_list_invites_empty(client):
    resp = client.get("/api/admin/invites")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_invites_shows_generated(client):
    client.post("/api/admin/invites", json={"count": 3})
    resp = client.get("/api/admin/invites")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_list_invites_status_field(client, db_session, test_user):
    _make_invite(db_session, test_user, code="ACTIVE-001", is_active=True)
    _make_invite(db_session, test_user, code="REVOKED-001", is_active=False)
    resp = client.get("/api/admin/invites")
    statuses = {i["code"]: i["status"] for i in resp.json()}
    assert statuses["ACTIVE-001"] == "active"
    assert statuses["REVOKED-001"] == "revoked"


def test_list_invites_includes_use_count(client, db_session, test_user):
    _make_invite(db_session, test_user, code="TRACK-001")
    resp = client.get("/api/admin/invites")
    invite = next(i for i in resp.json() if i["code"] == "TRACK-001")
    assert "use_count" in invite
    assert "max_uses" in invite
    assert invite["use_count"] == 0


# ── Invite revocation ─────────────────────────────────────────────────


def test_revoke_invite(client, db_session, test_user):
    invite = _make_invite(db_session, test_user, code="TO-REVOKE-001")
    resp = client.delete(f"/api/admin/invites/{invite.id}")
    assert resp.status_code == 200
    assert "revoked" in resp.json()["message"].lower()


def test_revoke_invite_makes_it_inactive(client, db_session, test_user):
    invite = _make_invite(db_session, test_user, code="REVOKE-CHECK-001")
    client.delete(f"/api/admin/invites/{invite.id}")
    resp = client.get("/api/admin/invites")
    match = next(i for i in resp.json() if i["code"] == "REVOKE-CHECK-001")
    assert match["status"] == "revoked"


def test_revoke_nonexistent_invite_returns_404(client):
    resp = client.delete("/api/admin/invites/99999")
    assert resp.status_code == 404


# ── User listing ──────────────────────────────────────────────────────


def test_list_users_returns_list(client):
    resp = client.get("/api/admin/users")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_users_includes_test_user(client, test_user):
    resp = client.get("/api/admin/users")
    emails = [u["email"] for u in resp.json()]
    assert test_user.email in emails


def test_list_users_has_expected_fields(client):
    resp = client.get("/api/admin/users")
    user = resp.json()[0]
    assert "id" in user
    assert "email" in user
    assert "is_admin" in user
    assert "created_at" in user


def test_list_users_admin_flag(client, test_user):
    resp = client.get("/api/admin/users")
    user = next(u for u in resp.json() if u["email"] == test_user.email)
    assert user["is_admin"] is True


# ── System health ─────────────────────────────────────────────────────


def test_system_health_returns_200(client):
    resp = client.get("/api/admin/system")
    assert resp.status_code == 200


def test_system_health_has_database_field(client):
    resp = client.get("/api/admin/system")
    assert "database" in resp.json()


def test_system_health_database_connected(client):
    resp = client.get("/api/admin/system")
    assert resp.json()["database"] == "connected"


def test_system_health_has_running_agents(client):
    resp = client.get("/api/admin/system")
    data = resp.json()
    assert "running_agents" in data


def test_system_health_has_websocket_info(client):
    resp = client.get("/api/admin/system")
    data = resp.json()
    assert "websocket_connections" in data


# ── Auth/me endpoint ──────────────────────────────────────────────────


def test_get_me_returns_profile(client, test_user):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == test_user.email
    assert data["is_admin"] is True
    assert "has_2fa" in data
    assert "created_at" in data


def test_get_me_has_2fa_false_by_default(client, test_user):
    resp = client.get("/api/auth/me")
    assert resp.json()["has_2fa"] is False
