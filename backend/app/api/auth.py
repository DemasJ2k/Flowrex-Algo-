"""
Auth endpoints: register, login, refresh, 2FA setup/verify.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
import pyotp

from app.core.database import get_db
from app.core.auth import (
    create_access_token, create_refresh_token, verify_token, get_current_user,
)
from app.core.password import hash_password, verify_password
from app.core.encryption import encrypt, decrypt
from app.models.user import User, UserSettings
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    from app.models.invite import InviteCode
    from datetime import datetime, timezone

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

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.flush()

    # Mark invite as used
    invite.use_count += 1
    if invite.use_count >= invite.max_uses:
        invite.used_by = user.id
        invite.used_at = datetime.now(timezone.utc)

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
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check if 2FA is enabled
    if user.totp_secret:
        # Return a partial token that requires 2FA verification
        partial_token = create_access_token(user.id)  # Short-lived
        return TokenResponse(
            access_token=partial_token,
            token_type="2fa_required",
        )

    return TokenResponse(
        access_token=create_access_token(user.id),
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify TOTP code and activate 2FA."""
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
        "access_token": create_access_token(current_user.id),
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
