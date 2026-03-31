"""
Production seed script — creates admin user from env vars.
Idempotent: safe to run multiple times.
Does NOT create test agents (dev only).

Run: python -m scripts.seed_production
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import Session
from app.core.password import hash_password
from app.models.user import User, UserSettings
from app.core.database import SessionLocal


def run_production_seed(db: Session) -> dict:
    admin_email = os.getenv("ADMIN_EMAIL", "admin@flowrex.local")
    admin_password = os.getenv("ADMIN_PASSWORD", "")

    if not admin_password:
        return {"status": "skipped", "reason": "ADMIN_PASSWORD env var not set"}

    existing = db.query(User).filter(User.email == admin_email).first()
    if existing:
        return {"status": "skipped", "reason": f"Admin {admin_email} already exists"}

    admin = User(
        email=admin_email,
        password_hash=hash_password(admin_password),
        is_admin=True,
    )
    db.add(admin)
    db.flush()

    settings = UserSettings(user_id=admin.id, theme="dark")
    db.add(settings)
    db.commit()

    return {"status": "created", "email": admin_email}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    db = SessionLocal()
    try:
        result = run_production_seed(db)
        print(f"Production seed: {result}")
    finally:
        db.close()
