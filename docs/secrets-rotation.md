# Secrets Rotation Procedures

## When to rotate

- **Immediately:** If any secret is suspected compromised (server breach, `.env` leaked, employee offboarding)
- **Quarterly:** Recommended for proactive hygiene
- **On demand:** When changing hosting provider or infrastructure

---

## SECRET_KEY (JWT signing)

**What it does:** Signs all JWT access and refresh tokens.
**What happens if you rotate:** All active user sessions are invalidated. Users must re-login.

**Steps:**
1. Generate a new key: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Update `SECRET_KEY` in `/opt/flowrex/.env`
3. Restart backend: `docker compose -f docker-compose.prod.yml restart backend`
4. All users will be logged out — inform them before rotating

**Rollback:** Restore the old key in `.env` and restart.

---

## ENCRYPTION_KEY (Fernet — broker credentials, TOTP, API keys)

**What it does:** Encrypts `broker_accounts.credentials_encrypted`, `users.totp_secret`, LLM API keys in `user_settings`.
**What happens if you rotate WITHOUT re-encryption:** ALL encrypted data becomes permanently unrecoverable.

**Steps:**
1. **Take a backup:** `docker exec flowrex-postgres pg_dump -U flowrex flowrex_algo > /tmp/pre-rotation-backup.sql`
2. **Stop all agents:** Via the UI or API (agents hold decrypted creds in memory)
3. **Generate new key:** `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
4. **Dry run:** `cd /opt/flowrex/backend && python3 -m scripts.reencrypt_secrets --old-key "CURRENT_KEY" --new-key "NEW_KEY" --dry-run`
5. **Execute:** `python3 -m scripts.reencrypt_secrets --old-key "CURRENT_KEY" --new-key "NEW_KEY"`
6. **Update `.env`:** Replace `ENCRYPTION_KEY=` with the new key
7. **Restart backend:** `docker compose -f docker-compose.prod.yml restart backend`
8. **Verify:** Connect a broker, check the connection works with the re-encrypted credentials

**Rollback:** Restore from the backup taken in step 1.

---

## POSTGRES_PASSWORD

**Steps:**
1. `docker exec flowrex-postgres psql -U flowrex -c "ALTER USER flowrex PASSWORD 'new_password';"`
2. Update `POSTGRES_PASSWORD` and `DATABASE_URL` in `.env`
3. Restart backend: `docker compose -f docker-compose.prod.yml restart backend`

**Rollback:** Connect to postgres directly and reset the password.

---

## Oanda API Key

**What it does:** Authenticates API calls to Oanda for live/paper trading.
**Stored in:** `broker_accounts.credentials_encrypted` (Fernet-encrypted in DB)

**Steps:**
1. Generate a new API key in your Oanda account settings
2. Go to Settings → Broker Connections → Oanda → Disconnect
3. Reconnect with the new API key
4. The old key is overwritten in the DB with the new encrypted value

**No backend restart needed.** The agent will pick up the new connection on the next poll cycle.

---

## Anthropic/Claude API Key

**Stored in:** `user_settings.settings_json.llm_api_key` (Fernet-encrypted)

**Steps:**
1. Generate a new key at console.anthropic.com
2. Go to Settings → AI Supervisor → enter the new key
3. Click Save — the old key is overwritten

**No backend restart needed.**

---

## Telegram Bot Token

**Stored in:** `user_settings.settings_json.telegram_bot_token` (Fernet-encrypted)

**Steps:**
1. Create a new bot or regenerate the token via @BotFather on Telegram
2. Go to AI Chat page → update the bot token
3. Click Save

---

## Compromise Playbook

If you suspect the server's `.env` has been accessed by an unauthorized party:

1. **Rotate ENCRYPTION_KEY** (most critical — protects all broker credentials)
2. **Rotate SECRET_KEY** (invalidates all sessions)
3. **Rotate POSTGRES_PASSWORD**
4. **Rotate Oanda API key** (via Oanda account settings)
5. **Notify all users** to change their passwords
6. **Check `admin_audit_logs`** for any suspicious admin access
7. **Review `docker logs`** for anomalous requests
8. **Timeline:** All rotations should complete within 4 hours of detection
