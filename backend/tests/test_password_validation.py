"""
Tests for password strength validation at the Pydantic schema layer.
Covers Batch 4 audit fix H24.
"""
import pytest
from pydantic import ValidationError
from app.schemas.auth import RegisterRequest


def test_strong_password_accepted():
    req = RegisterRequest(
        email="user@example.com",
        password="StrongPass123!",
        invite_code="abc",
    )
    assert req.password == "StrongPass123!"


def test_short_password_rejected():
    with pytest.raises(ValidationError, match="at least 12"):
        RegisterRequest(
            email="user@example.com",
            password="Short1A",
            invite_code="abc",
        )


def test_no_uppercase_rejected():
    with pytest.raises(ValidationError, match="uppercase"):
        RegisterRequest(
            email="user@example.com",
            password="lowercaseonly1234",
            invite_code="abc",
        )


def test_no_lowercase_rejected():
    with pytest.raises(ValidationError, match="lowercase"):
        RegisterRequest(
            email="user@example.com",
            password="UPPERCASE12345",
            invite_code="abc",
        )


def test_no_digit_rejected():
    with pytest.raises(ValidationError, match="digit"):
        RegisterRequest(
            email="user@example.com",
            password="NoDigitsHereAtAll",
            invite_code="abc",
        )


def test_invalid_email_rejected():
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="not-an-email",
            password="StrongPass123!",
            invite_code="abc",
        )
