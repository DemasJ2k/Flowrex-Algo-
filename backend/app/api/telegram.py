"""
Telegram central bot API.

Flow:
1. User clicks "Connect Telegram" in app → POST /api/telegram/connect
   → backend generates 6-char code, stores in telegram_bindings with 10min TTL
   → returns deep link: https://t.me/FlowrexAlgoBot?start=<code>
2. User clicks link → Telegram opens bot chat pre-filled with /start <code>
3. Telegram POSTs the message to POST /api/telegram/webhook
4. Backend looks up code → stores chat_id in UserSettings → replies "Connected!"
5. All future notifications use the global bot + user's chat_id
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import secrets
import httpx

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import settings
from app.models.telegram import TelegramBinding
from app.models.user import UserSettings

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class ConnectResponse(BaseModel):
    code: str
    deep_link: str
    expires_at: str


def _gen_code() -> str:
    """6-char alphanumeric binding code."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no confusing chars
    return "".join(secrets.choice(alphabet) for _ in range(6))


@router.get("/status")
async def telegram_status(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check if the user has connected Telegram, and whether the global bot is enabled."""
    sr = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    data = (sr.settings_json if sr else None) or {}
    return {
        "connected": bool(data.get("telegram_chat_id")),
        "chat_id": data.get("telegram_chat_id", ""),
        "telegram_username": data.get("telegram_username", ""),
        "telegram_first_name": data.get("telegram_first_name", ""),
        "global_bot_enabled": bool(settings.TELEGRAM_BOT_TOKEN),
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
    }


@router.post("/connect", response_model=ConnectResponse)
async def connect(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a binding code and return the Telegram deep link."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(503, "Central Telegram bot is not configured on the server.")

    # Invalidate any unused bindings for this user
    db.query(TelegramBinding).filter(
        TelegramBinding.user_id == user.id,
        TelegramBinding.used_at.is_(None),
    ).delete()

    code = _gen_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    binding = TelegramBinding(user_id=user.id, code=code, expires_at=expires_at)
    db.add(binding)
    db.commit()

    deep_link = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={code}"
    return ConnectResponse(
        code=code,
        deep_link=deep_link,
        expires_at=expires_at.isoformat(),
    )


@router.post("/disconnect")
async def disconnect(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unlink this user's chat_id."""
    sr = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if sr and sr.settings_json:
        from sqlalchemy.orm.attributes import flag_modified
        data = dict(sr.settings_json)
        data.pop("telegram_chat_id", None)
        data.pop("telegram_username", None)
        data.pop("telegram_first_name", None)
        data.pop("telegram_bot_token", None)  # also drop legacy per-user token
        sr.settings_json = data
        flag_modified(sr, "settings_json")
        db.commit()
    return {"status": "ok"}


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    """
    Telegram webhook endpoint. Validates secret header, then:
    - On /start <code>: match code to user, bind chat_id
    - On /help, /status, /unlink: respond appropriately
    - Other messages: ignored (could route to AI chat in future)

    Security: Telegram sends X-Telegram-Bot-Api-Secret-Token header if the
    webhook was set with a secret_token param. We verify it against env.
    """
    # Validate webhook secret (if configured)
    if settings.TELEGRAM_WEBHOOK_SECRET:
        hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if hdr != settings.TELEGRAM_WEBHOOK_SECRET:
            raise HTTPException(403, "Invalid webhook secret")

    update = await request.json()
    message = update.get("message") or update.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    username = (chat.get("username") or "")
    first_name = (chat.get("first_name") or "")
    last_name = (chat.get("last_name") or "")
    display_name = (f"{first_name} {last_name}".strip()) or username or f"user{chat_id}"

    if not chat_id or not text:
        return {"ok": True}

    # Handle /start <code>
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await _reply(chat_id,
                f"👋 Hi {display_name}! Welcome to FlowrexAlgo.\n\n"
                "To connect your account, generate a connection code in the app "
                "(Settings → AI Supervisor → Connect Telegram) and click the link there.")
            return {"ok": True}

        code = parts[1].strip().upper()
        binding = db.query(TelegramBinding).filter(
            TelegramBinding.code == code,
            TelegramBinding.used_at.is_(None),
            TelegramBinding.expires_at > datetime.now(timezone.utc),
        ).first()

        if not binding:
            await _reply(chat_id,
                "❌ Invalid or expired code. Generate a new one in the app.")
            return {"ok": True}

        # Bind chat_id + username to user
        sr = db.query(UserSettings).filter(UserSettings.user_id == binding.user_id).first()
        if not sr:
            sr = UserSettings(user_id=binding.user_id, settings_json={})
            db.add(sr)

        from sqlalchemy.orm.attributes import flag_modified
        data = dict(sr.settings_json or {})
        data["telegram_chat_id"] = chat_id
        if username:
            data["telegram_username"] = username
        if first_name:
            data["telegram_first_name"] = first_name
        sr.settings_json = data
        flag_modified(sr, "settings_json")

        binding.used_at = datetime.now(timezone.utc)
        db.commit()

        user_tag = f"@{username}" if username else display_name
        await _reply(chat_id,
            f"✅ <b>Connected, {display_name}!</b>\n\n"
            f"Your FlowrexAlgo account is now linked to <b>{user_tag}</b>.\n\n"
            f"You'll receive:\n"
            f"• Trade open/close alerts\n"
            f"• Hourly performance summaries\n"
            f"• Critical alerts (losing streaks, drawdown breaches)\n\n"
            f"Commands:\n"
            f"/status — current account status\n"
            f"/unlink — disconnect from the app")
        return {"ok": True}

    # Handle /help
    if text == "/help":
        await _reply(chat_id,
            "<b>FlowrexAlgo Bot Commands</b>\n\n"
            "/start &lt;code&gt; — connect your account\n"
            "/status — show account status\n"
            "/unlink — disconnect\n"
            "/help — show this message")
        return {"ok": True}

    # Handle /status — scan UserSettings for this chat_id.
    # PostgreSQL JSONB lookup would be faster but we're <100 users so a
    # linear scan is fine and keeps the code portable to SQLite.
    if text == "/status":
        matched = None
        for s in db.query(UserSettings).all():
            if (s.settings_json or {}).get("telegram_chat_id") == chat_id:
                matched = s
                break
        if matched:
            await _reply(chat_id,
                f"✅ Connected to FlowrexAlgo account #{matched.user_id}\n"
                f"Notifications: active")
        else:
            await _reply(chat_id,
                "❌ Not connected. Generate a code in the app and send /start &lt;code&gt;.")
        return {"ok": True}

    # Handle /unlink
    if text == "/unlink":
        from sqlalchemy.orm.attributes import flag_modified
        all_settings = db.query(UserSettings).all()
        unlinked = False
        for s in all_settings:
            data = dict(s.settings_json or {})
            if data.get("telegram_chat_id") == chat_id:
                data.pop("telegram_chat_id", None)
                s.settings_json = data
                flag_modified(s, "settings_json")
                unlinked = True
        if unlinked:
            db.commit()
            await _reply(chat_id, "✅ Disconnected. To reconnect, generate a new code in the app.")
        else:
            await _reply(chat_id, "ℹ️ You weren't connected.")
        return {"ok": True}

    # Free-form chat — route to the user's AI supervisor if they're connected.
    # Otherwise, politely prompt them to connect.
    matched_user_id: Optional[int] = None
    for s in db.query(UserSettings).all():
        if (s.settings_json or {}).get("telegram_chat_id") == chat_id:
            matched_user_id = s.user_id
            break

    if matched_user_id is None:
        await _reply(chat_id,
            "Not connected to FlowrexAlgo. Send <code>/start &lt;code&gt;</code> "
            "using a code from Settings → AI Supervisor → Connect Telegram.")
        return {"ok": True}

    # Fire-and-forget AI chat — Telegram times out webhook responses at 60s.
    # We ack immediately, then send the reply via a separate Telegram API call.
    import asyncio
    asyncio.create_task(_handle_free_chat(matched_user_id, chat_id, text))
    return {"ok": True}


async def _handle_free_chat(user_id: int, chat_id: str, user_message: str):
    """
    Full chat routing: Telegram → AI supervisor → Telegram.

    - Looks up user's Anthropic API key + LLM config
    - Uses their default chat session (or creates "Telegram" session)
    - Saves both user message + AI reply to the same DB session
    - Sends reply back via Telegram
    """
    from app.core.database import SessionLocal as _SL
    from app.core.encryption import get_fernet
    from app.services.llm.supervisor import get_supervisor
    from app.models.chat import ChatSession, ChatMessage
    from datetime import datetime, timezone as _tz

    _db = _SL()
    try:
        # Confirm user has LLM enabled
        sr = _db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        data = (sr.settings_json if sr else None) or {}
        if not data.get("llm_enabled"):
            await _reply(chat_id,
                "🤖 AI Supervisor is not enabled on your account. Enable it in Settings → AI Supervisor.")
            return

        api_key = None
        if "llm_api_key" in data:
            try:
                api_key = get_fernet().decrypt(data["llm_api_key"].encode()).decode()
            except Exception:
                pass
        if not api_key:
            await _reply(chat_id,
                "🤖 No Anthropic API key set. Add one in Settings → AI Supervisor.")
            return

        supervisor = get_supervisor()
        supervisor.configure(
            user_id=user_id,
            api_key=api_key,
            model=data.get("llm_model", "haiku"),
            enabled=True,
            autonomous=data.get("llm_autonomous", False),
        )

        # Find or create a "Telegram" chat session for this user
        TG_TITLE = "Telegram"
        session = _db.query(ChatSession).filter(
            ChatSession.user_id == user_id,
            ChatSession.title == TG_TITLE,
            ChatSession.is_active == True,
        ).first()
        if not session:
            session = ChatSession(user_id=user_id, title=TG_TITLE)
            _db.add(session)
            _db.commit()
            _db.refresh(session)

        # Save user message
        _db.add(ChatMessage(session_id=session.id, role="user", content=user_message))
        _db.commit()

        # Load last 20 messages for context
        history = (
            _db.query(ChatMessage)
            .filter(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(20)
            .all()
        )
        history.reverse()
        conversation = [{"role": m.role, "content": m.content} for m in history]

        # Build trading context (agents + trades + P&L)
        from app.api.llm import _build_chat_context
        context = await _build_chat_context(user_id, _db)

        # Query Claude
        reply, usage = await supervisor.chat_with_history(
            user_id=user_id,
            conversation=conversation,
            context=context,
        )

        if not reply:
            reply = "I couldn't generate a response. Please try again."

        # Save AI reply
        _db.add(ChatMessage(
            session_id=session.id, role="assistant", content=reply,
            model=supervisor.get_session(user_id).model,
            input_tokens=(usage or {}).get("input_tokens") if usage else None,
            output_tokens=(usage or {}).get("output_tokens") if usage else None,
        ))
        session.updated_at = datetime.now(_tz.utc)
        _db.commit()

        # Send back via Telegram (HTML; Telegram has 4096 char limit — handled by send_to_user)
        from app.services.llm.telegram import get_telegram_notifier
        notifier = get_telegram_notifier()
        # Strip markdown for Telegram — it only supports HTML, not GFM
        safe_reply = _markdown_to_telegram_html(reply)
        await notifier.send_to_user(chat_id, safe_reply)
    except Exception as e:
        try:
            await _reply(chat_id, f"⚠️ Chat error: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        _db.close()


def _markdown_to_telegram_html(md: str) -> str:
    """
    Minimal markdown → Telegram HTML conversion.
    Telegram supports: <b>, <i>, <u>, <s>, <code>, <pre>, <a>
    """
    import re as _re
    # Escape HTML special chars first
    s = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Code blocks (```text```)
    s = _re.sub(r"```(\w*)\n?(.*?)```", r"<pre>\2</pre>", s, flags=_re.DOTALL)
    # Inline code `text`
    s = _re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # Bold **text**
    s = _re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    # Italic *text* (careful not to conflict with bold — already handled)
    s = _re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    # Headers → bold
    s = _re.sub(r"^#+\s*(.+?)$", r"<b>\1</b>", s, flags=_re.MULTILINE)
    return s


async def _reply(chat_id: str, text: str):
    """Send a message back to a Telegram chat using the global bot token."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception:
        pass
