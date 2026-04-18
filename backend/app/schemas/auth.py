from datetime import date
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator


def _validate_password_strength(v: str) -> str:
    """Shared password strength check for new passwords."""
    if len(v) < 12:
        raise ValueError("password must be at least 12 characters")
    if not any(c.isupper() for c in v):
        raise ValueError("password must contain an uppercase letter")
    if not any(c.islower() for c in v):
        raise ValueError("password must contain a lowercase letter")
    if not any(c.isdigit() for c in v):
        raise ValueError("password must contain a digit")
    return v


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=12, max_length=128)
    invite_code: str = ""
    terms_accepted: bool = Field(False, description="Must be True — user accepts ToS and Privacy Policy")
    date_of_birth: Optional[date] = Field(None, description="For age verification (must be 18+)")

    @field_validator("password")
    @classmethod
    def _strong_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str  # no validator — existing users may have weaker legacy passwords


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str = ""
    token_type: str = "bearer"
