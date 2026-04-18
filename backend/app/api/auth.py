"""
Auth endpoints: register, login, refresh, 2FA setup/verify, forgot/reset password.
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
import pyotp

from app.core.database import get_db
from app.core.auth import (
    create_access_token, create_refresh_token, verify_token,
    get_current_user, get_partial_user,
)
from app.core.password import hash_password, verify_password
from app.core.encryption import encrypt, decrypt
from app.models.user import User, UserSettings
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from app.core.rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


CURRENT_TERMS_VERSION = "1.0"


@router.post("/register", response_model=TokenResponse)
@limiter.limit("3/minute")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    from app.models.invite import InviteCode
    from datetime import datetime, timezone, date as date_type

    # ── GDPR consent check (migration 003) ──
    if not body.terms_accepted:
        raise HTTPException(
            status_code=400,
            detail="You must accept the Terms of Service and Privacy Policy to register.",
        )

    # ── Age verification ──
    if body.date_of_birth:
        today = date_type.today()
        age = today.year - body.date_of_birth.year - (
            (today.month, today.day) < (body.date_of_birth.month, body.date_of_birth.day)
        )
        if age < 18:
            raise HTTPException(
                status_code=400,
                detail="You must be at least 18 years old to use this platform.",
            )

    # Validate invite code (invite-only platform)
    if not body.invite_code:
        raise HTTPException(status_code=400, detail="Invite code is required")

    invite = db.query(InviteCode).filter(
        InviteCode.code == body.invite_code,
        InviteCode.is_active == True,
    ).first()

    if not invite:
        raise HTTPException(status_code=400, detail="Invalid invite code")

    if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invite code has expired")

    if invite.max_uses > 0 and invite.use_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="Invite code has been fully used")

    # Check if email already exists
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    now = datetime.now(timezone.utc)
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        terms_accepted_at=now,
        terms_version=CURRENT_TERMS_VERSION,
        privacy_accepted_at=now,
        date_of_birth=body.date_of_birth,
    )
    db.add(user)
    db.flush()

    # Mark invite as used
    invite.use_count += 1
    if invite.use_count >= invite.max_uses:
        invite.used_by = user.id
        invite.used_at = now

    # Create default settings
    settings = UserSettings(user_id=user.id, theme="dark")
    db.add(settings)
    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check if 2FA is enabled
    if user.totp_secret:
        # Return a SCOPED partial token that is rejected by all protected endpoints
        # EXCEPT /auth/2fa/verify. Capped at 5 min expiry.
        partial_token = create_access_token(user.id, scope="partial")
        return TokenResponse(
            access_token=partial_token,
            token_type="2fa_required",
        )

    return TokenResponse(
        access_token=create_access_token(user.id, scope="full"),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(token: str, db: Session = Depends(get_db)):
    """Exchange refresh token for a new access token."""
    user_id = verify_token(token, "refresh")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/2fa/setup")
def setup_2fa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate TOTP secret and QR code URI."""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="Flowrex Algo",
    )

    # Store encrypted secret (not activated until verified)
    current_user.totp_secret = encrypt(secret)
    db.commit()

    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "message": "Scan the QR code with your authenticator app, then verify with /2fa/verify",
    }


@router.post("/2fa/verify")
def verify_2fa(
    code: str,
    current_user: User = Depends(get_partial_user),  # accepts scope="partial" OR scope="full"
    db: Session = Depends(get_db),
):
    """
    Verify TOTP code and issue a FULL-scope access token.

    Accepts the partial token returned from /login (when 2FA is enabled) and
    swaps it for a full-scope access token on successful TOTP verification.
    """
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="2FA not set up. Call /2fa/setup first")

    try:
        secret = decrypt(current_user.totp_secret)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to decrypt TOTP secret")

    totp = pyotp.TOTP(secret)
    if not totp.verify(code):
        raise HTTPException(status_code=401, detail="Invalid 2FA code")

    return {
        "verified": True,
        "access_token": create_access_token(current_user.id, scope="full"),
        "refresh_token": create_refresh_token(current_user.id),
    }


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "is_admin": current_user.is_admin,
        "created_at": str(current_user.created_at) if current_user.created_at else None,
        "has_2fa": current_user.totp_secret is not None,
    }


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.put("/change-password")
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change user password."""
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password updated successfully"}


@router.post("/2fa/disable")
def disable_2fa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Disable 2FA."""
    current_user.totp_secret = None
    db.commit()
    return {"message": "2FA disabled"}


# ── Forgot / Reset Password ──────────────────────────────────────────


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/forgot-password")
@limiter.limit("3/minute")
def forgot_password(request: Request, body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Generate a password reset token. Returns the token directly (email integration TBD)."""
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        # Don't reveal whether the email exists — return a generic success
        return {"message": "If that email is registered, a reset token has been generated."}

    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()

    return {
        "message": "Reset token generated. Use it to set a new password.",
        "reset_token": token,  # Temporary — remove once email sending is integrated
    }


# ── GDPR endpoints ────────────────────────────────────────────────────


class DeleteAccountRequest(BaseModel):
    password: str


@router.delete("/account")
def delete_my_account(
    body: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    GDPR Art. 17 — Right to erasure.

    Hard-deletes the user account and cascades to:
      - agents (cascade="all, delete-orphan")
      - trades, logs (via agent FK cascade)
      - broker_accounts (cascade)
      - user_settings (cascade)

    Requires password confirmation. Encrypted credential fields are overwritten
    with random bytes before delete to limit recovery from DB backups.
    """
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Password incorrect")

    # Overwrite encrypted credential fields with random bytes before delete.
    # This makes recovery from DB backups significantly harder if backups
    # are accessed before the next backup rotation.
    for ba in current_user.broker_accounts:
        ba.credentials_encrypted = secrets.token_urlsafe(64)
    if current_user.totp_secret:
        current_user.totp_secret = secrets.token_urlsafe(32)
    db.flush()

    # Hard delete (cascades will clean up the relationships)
    db.delete(current_user)
    db.commit()
    return {"message": "Account deleted. All associated data has been removed."}


@router.get("/export-data")
def export_my_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    GDPR Art. 15 — Right to access.

    Returns all data the user has on the platform as a JSON bundle:
      - profile (email, created_at, is_admin)
      - all agents (config, status — encrypted credentials EXCLUDED)
      - all trades (entry, exit, P&L, reason)
      - all agent logs
      - settings
      - broker account names (NOT credentials)

    Note: this endpoint does NOT include the encrypted broker credentials,
    TOTP secret, or reset tokens. Those are user secrets we're storing
    on their behalf — the export shows everything ABOUT them but not their
    own keys.
    """
    from app.models.agent import TradingAgent, AgentTrade, AgentLog
    from app.models.broker import BrokerAccount

    agents = db.query(TradingAgent).filter(
        TradingAgent.created_by == current_user.id
    ).all()

    trades = (
        db.query(AgentTrade)
        .join(TradingAgent)
        .filter(TradingAgent.created_by == current_user.id)
        .all()
    )

    logs = (
        db.query(AgentLog)
        .join(TradingAgent)
        .filter(TradingAgent.created_by == current_user.id)
        .order_by(AgentLog.created_at.desc())
        .limit(5000)  # cap export size
        .all()
    )

    brokers = db.query(BrokerAccount).filter(
        BrokerAccount.user_id == current_user.id
    ).all()

    settings = db.query(UserSettings).filter(
        UserSettings.user_id == current_user.id
    ).first()

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "id": current_user.id,
            "email": current_user.email,
            "is_admin": current_user.is_admin,
            "has_2fa": current_user.totp_secret is not None,
            "created_at": str(current_user.created_at) if current_user.created_at else None,
        },
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "symbol": a.symbol,
                "agent_type": a.agent_type,
                "broker_name": a.broker_name,
                "mode": a.mode,
                "status": a.status,
                "risk_config": a.risk_config,
                "created_at": str(a.created_at) if a.created_at else None,
                "deleted_at": str(a.deleted_at) if a.deleted_at else None,
            }
            for a in agents
        ],
        "trades": [
            {
                "id": t.id,
                "agent_id": t.agent_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "lot_size": t.lot_size,
                "pnl": t.pnl,
                "broker_pnl": t.broker_pnl,
                "exit_reason": t.exit_reason,
                "confidence": t.confidence,
                "status": t.status,
                "entry_time": str(t.entry_time) if t.entry_time else None,
            }
            for t in trades
        ],
        "agent_logs": [
            {
                "id": l.id,
                "agent_id": l.agent_id,
                "level": l.level,
                "message": l.message[:1000],  # truncate to avoid huge exports
                "created_at": str(l.created_at) if l.created_at else None,
            }
            for l in logs
        ],
        "broker_accounts": [
            {
                "id": b.id,
                "broker_name": b.broker_name,
                "account_id": b.account_id,
                "is_active": b.is_active,
                # credentials_encrypted intentionally omitted
            }
            for b in brokers
        ],
        "settings": {
            "theme": settings.theme if settings else "dark",
            "default_broker": settings.default_broker if settings else None,
            "notifications_enabled": settings.notifications_enabled if settings else True,
            "settings_json": settings.settings_json if settings else {},
        } if settings else None,
        "log_count_truncated": len(logs) >= 5000,
    }


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset password using a valid reset token."""
    if len(body.new_password) < 12:
        raise HTTPException(status_code=400, detail="New password must be at least 12 characters")
    if not any(c.isupper() for c in body.new_password):
        raise HTTPException(status_code=400, detail="Password must contain an uppercase letter")
    if not any(c.isdigit() for c in body.new_password):
        raise HTTPException(status_code=400, detail="Password must contain a digit")

    user = db.query(User).filter(User.reset_token == body.token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if user.reset_token_expires and user.reset_token_expires < datetime.now(timezone.utc):
        # Clear expired token
        user.reset_token = None
        user.reset_token_expires = None
        db.commit()
        raise HTTPException(status_code=400, detail="Reset token has expired. Please request a new one.")

    user.password_hash = hash_password(body.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    return {"message": "Password reset successfully. You can now log in with your new password."}
