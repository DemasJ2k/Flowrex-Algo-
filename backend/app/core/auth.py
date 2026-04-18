"""
JWT authentication — token creation, verification, and user dependency.
In DEBUG mode: auto-creates dev user (no login required).
In production: requires valid JWT from Authorization header.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from app.core.config import settings
from app.core.database import get_db
from app.core.password import hash_password

import secrets as _secrets

DEV_USER_EMAIL = "dev@flowrex.local"
# Random dev password generated once per process start.
# Previously hardcoded as "devpassword" — a known credential that would grant
# admin access if DEBUG=True accidentally reached production (audit V4-C7).
DEV_USER_PASSWORD = _secrets.token_urlsafe(16)

ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 7
ALGORITHM = "HS256"

security = HTTPBearer(auto_error=False)


def create_access_token(user_id: int, scope: str = "full") -> str:
    """
    Create a JWT access token.

    scope="full" — authenticated for all endpoints (default)
    scope="partial" — short-lived token returned from /login when 2FA is enabled.
                      Accepted ONLY by /auth/2fa/verify. Rejected for all other
                      protected endpoints. Expiry capped at 5 minutes.
    """
    if scope == "partial":
        expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "type": "access", "scope": scope, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "type": "refresh", "scope": "full", "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str, token_type: str = "access", allow_partial: bool = False) -> Optional[int]:
    """
    Verify JWT and return user_id, or None if invalid.

    By default (allow_partial=False), tokens with scope="partial" are rejected —
    the user still needs to complete 2FA verification.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != token_type:
            return None
        scope = payload.get("scope", "full")
        if scope != "full" and not allow_partial:
            return None
        user_id = payload.get("sub")
        return int(user_id) if user_id else None
    except (JWTError, ValueError):
        return None


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    """
    Return the current authenticated user.
    DEBUG mode: auto-creates/returns dev user (no token needed).
    Production: requires valid JWT Bearer token.
    """
    from app.models.user import User

    if settings.DEBUG:
        # Check if a real token was provided first
        if credentials and credentials.credentials:
            user_id = verify_token(credentials.credentials, "access")
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    return user

        # Dev fallback: auto-create dev user
        user = db.query(User).filter(User.email == DEV_USER_EMAIL).first()
        if not user:
            user = User(
                email=DEV_USER_EMAIL,
                password_hash=hash_password(DEV_USER_PASSWORD),
                is_admin=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    # Production: require JWT
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = verify_token(credentials.credentials, "access")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


def get_admin_user(current_user=Depends(get_current_user)):
    """Require admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def get_partial_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    """
    Dependency for /auth/2fa/verify — accepts tokens with scope='partial'.
    All OTHER endpoints must use get_current_user which rejects partial tokens.
    """
    from app.models.user import User

    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = verify_token(credentials.credentials, "access", allow_partial=True)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
