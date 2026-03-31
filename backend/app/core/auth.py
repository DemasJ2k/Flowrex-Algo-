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

DEV_USER_EMAIL = "dev@flowrex.local"
DEV_USER_PASSWORD = "devpassword"

ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 7
ALGORITHM = "HS256"

security = HTTPBearer(auto_error=False)


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "type": "access", "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "type": "refresh", "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str, token_type: str = "access") -> Optional[int]:
    """Verify JWT and return user_id, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != token_type:
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
