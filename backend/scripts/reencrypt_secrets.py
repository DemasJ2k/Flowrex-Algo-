"""
Re-encryption migration script — rotates ENCRYPTION_KEY.

Decrypts all encrypted fields with the old key, re-encrypts with the new key,
and commits in a single transaction. Rollback on any failure.

Usage:
    cd backend
    python3 -m scripts.reencrypt_secrets --old-key "OLD_KEY_HERE" --new-key "NEW_KEY_HERE" [--dry-run]

IMPORTANT:
  1. Stop all agents BEFORE running this (they hold decrypted creds in memory)
  2. Take a pg_dump backup BEFORE running
  3. After success, update ENCRYPTION_KEY in .env to the new key
  4. Restart the backend
  5. Verify broker connections still work
"""
import sys
import os
import json
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cryptography.fernet import Fernet
from app.core.database import SessionLocal
from app.models.broker import BrokerAccount
from app.models.user import User


def reencrypt(old_key: str, new_key: str, dry_run: bool = False):
    """Re-encrypt all secrets from old_key to new_key."""
    old_fernet = Fernet(old_key.encode())
    new_fernet = Fernet(new_key.encode())

    db = SessionLocal()
    changes = []

    try:
        # ── Re-encrypt broker credentials ──
        accounts = db.query(BrokerAccount).all()
        for ba in accounts:
            if not ba.credentials_encrypted:
                continue
            try:
                plaintext = old_fernet.decrypt(ba.credentials_encrypted.encode()).decode()
                new_encrypted = new_fernet.encrypt(plaintext.encode()).decode()
                changes.append(f"  broker_accounts[{ba.id}] ({ba.broker_name} for user {ba.user_id})")
                if not dry_run:
                    ba.credentials_encrypted = new_encrypted
            except Exception as e:
                print(f"ERROR: Could not decrypt broker_accounts[{ba.id}]: {e}")
                print("  This record may already use a different key or be corrupted.")
                print("  Aborting — no changes committed.")
                db.rollback()
                return False

        # ── Re-encrypt TOTP secrets ──
        users = db.query(User).filter(User.totp_secret.isnot(None)).all()
        for u in users:
            try:
                plaintext = old_fernet.decrypt(u.totp_secret.encode()).decode()
                new_encrypted = new_fernet.encrypt(plaintext.encode()).decode()
                changes.append(f"  users[{u.id}].totp_secret ({u.email})")
                if not dry_run:
                    u.totp_secret = new_encrypted
            except Exception as e:
                print(f"ERROR: Could not decrypt users[{u.id}].totp_secret: {e}")
                print("  Aborting — no changes committed.")
                db.rollback()
                return False

        # ── Re-encrypt LLM API keys in user_settings ──
        from app.models.user import UserSettings
        all_settings = db.query(UserSettings).all()
        for s in all_settings:
            if not s.settings_json:
                continue
            sj = s.settings_json
            updated = False
            for field in ("llm_api_key", "telegram_bot_token"):
                val = sj.get(field)
                if not val:
                    continue
                try:
                    plaintext = old_fernet.decrypt(val.encode()).decode()
                    sj[field] = new_fernet.encrypt(plaintext.encode()).decode()
                    changes.append(f"  user_settings[{s.id}].settings_json.{field}")
                    updated = True
                except Exception as e:
                    print(f"WARN: Could not decrypt user_settings[{s.id}].{field}: {e}")
                    # Non-fatal for settings — they can be re-entered by the user
            if updated and not dry_run:
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(s, "settings_json")

        # ── Summary ──
        print(f"\n{'DRY RUN — ' if dry_run else ''}Re-encryption summary:")
        print(f"  Records to update: {len(changes)}")
        for c in changes:
            print(c)

        if dry_run:
            print("\nDry run complete. No changes committed.")
            db.rollback()
            return True

        if not changes:
            print("\nNo records need re-encryption.")
            db.rollback()
            return True

        # ── Commit ──
        db.commit()
        print(f"\n✓ {len(changes)} records re-encrypted successfully.")
        print(f"  Timestamp: {datetime.now(timezone.utc).isoformat()}")
        print(f"\nNEXT STEPS:")
        print(f"  1. Update ENCRYPTION_KEY in .env to the new key")
        print(f"  2. Restart the backend: docker compose -f docker-compose.prod.yml restart backend")
        print(f"  3. Verify broker connections still work")
        return True

    except Exception as e:
        print(f"FATAL: {e}")
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-encrypt all secrets with a new ENCRYPTION_KEY")
    parser.add_argument("--old-key", required=True, help="Current ENCRYPTION_KEY (from .env)")
    parser.add_argument("--new-key", required=True, help="New ENCRYPTION_KEY (generate with: python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without committing")
    args = parser.parse_args()

    ok = reencrypt(args.old_key, args.new_key, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
