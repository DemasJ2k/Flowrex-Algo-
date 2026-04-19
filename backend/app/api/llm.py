"""LLM Supervisor API — chat, config, sessions, and status endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, Literal
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.encryption import get_fernet
from app.core.rate_limit import limiter
from app.services.llm.supervisor import get_supervisor
from app.services.llm.telegram import get_telegram_notifier
from app.models.chat import ChatSession, ChatMessage

router = APIRouter(prefix="/api/llm", tags=["llm"])


# ── Schemas ──────────────────────────────────────────────────────────────

class LLMConfigRequest(BaseModel):
    api_key: Optional[str] = Field(None, max_length=500)
    model: Literal["haiku", "sonnet", "opus"] = "haiku"
    enabled: bool = True
    autonomous: bool = False
    telegram_bot_token: Optional[str] = Field(None, max_length=200)
    telegram_chat_id: Optional[str] = Field(None, max_length=100)


class MonitoringConfig(BaseModel):
    enabled: bool = True
    frequency: Literal["off", "1h", "4h", "12h", "daily"] = "1h"
    quiet_hours_start: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end:   Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    skip_when_markets_closed: bool = True
    skip_when_unchanged:      bool = True


class TimezoneConfig(BaseModel):
    timezone: str = Field(..., min_length=1, max_length=64)
    confirmed: bool = True


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[int] = None


class ChatResponse(BaseModel):
    reply: str
    timestamp: str
    model: str
    session_id: int


class SessionCreate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)


class SessionResponse(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str
    message_count: int

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    model: Optional[str] = None
    created_at: str


class UsageResponse(BaseModel):
    month: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    sessions: int
    messages: int


# ── Config endpoints ─────────────────────────────────────────────────────

@router.post("/config")
async def save_llm_config(
    body: LLMConfigRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save LLM supervisor configuration."""
    from app.models.user import UserSettings

    settings_record = db.query(UserSettings).filter(
        UserSettings.user_id == user.id
    ).first()

    if not settings_record:
        settings_record = UserSettings(user_id=user.id, settings_json={})
        db.add(settings_record)

    settings_data = settings_record.settings_json or {}

    if body.api_key:
        encrypted_key = get_fernet().encrypt(body.api_key.encode()).decode()
        settings_data["llm_api_key"] = encrypted_key

    settings_data["llm_model"] = body.model
    settings_data["llm_enabled"] = body.enabled
    settings_data["llm_autonomous"] = body.autonomous

    if body.telegram_bot_token:
        settings_data["telegram_bot_token"] = get_fernet().encrypt(
            body.telegram_bot_token.encode()
        ).decode()
    if body.telegram_chat_id:
        settings_data["telegram_chat_id"] = body.telegram_chat_id

    from sqlalchemy.orm.attributes import flag_modified
    settings_record.settings_json = settings_data
    flag_modified(settings_record, "settings_json")
    db.commit()

    api_key = None
    if "llm_api_key" in settings_data:
        try:
            api_key = get_fernet().decrypt(settings_data["llm_api_key"].encode()).decode()
        except Exception:
            pass

    supervisor = get_supervisor()
    if api_key:
        supervisor.configure(
            user_id=user.id,
            api_key=api_key,
            model=body.model,
            enabled=body.enabled,
            autonomous=body.autonomous,
        )

    notifier = get_telegram_notifier()
    bot_token = None
    if "telegram_bot_token" in settings_data:
        try:
            bot_token = get_fernet().decrypt(
                settings_data["telegram_bot_token"].encode()
            ).decode()
        except Exception:
            pass
    chat_id = settings_data.get("telegram_chat_id", "")
    if bot_token and chat_id:
        notifier.configure(bot_token, chat_id)

    return {"status": "ok", "enabled": body.enabled, "model": body.model}


@router.get("/monitoring")
async def get_monitoring_config(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the user's report scheduling config merged over defaults."""
    from app.services.llm.monitoring import _load_monitoring_config
    from app.models.user import UserSettings

    sr = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    data = (sr.settings_json if sr else None) or {}
    cfg = _load_monitoring_config(data)

    state = data.get("monitoring_state") or {}
    return {
        **cfg,
        "timezone": data.get("timezone") or "UTC",
        "tz_confirmed": bool(data.get("tz_confirmed")),
        "last_sent_at": state.get("last_sent_at"),
        "markets_closed_notified": bool(state.get("markets_closed_notified")),
    }


@router.put("/monitoring")
async def update_monitoring_config(
    body: MonitoringConfig,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Persist the user's report scheduling preferences."""
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.user import UserSettings

    sr = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not sr:
        sr = UserSettings(user_id=user.id, settings_json={})
        db.add(sr)
    data = dict(sr.settings_json or {})
    data["monitoring"] = body.model_dump()
    sr.settings_json = data
    flag_modified(sr, "settings_json")
    db.commit()
    return {"status": "ok", "monitoring": body.model_dump()}


@router.get("/timezone")
async def get_user_timezone(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the user's saved timezone + confirmation flag."""
    from app.models.user import UserSettings
    sr = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    data = (sr.settings_json if sr else None) or {}
    return {
        "timezone": data.get("timezone") or "UTC",
        "confirmed": bool(data.get("tz_confirmed")),
    }


@router.put("/timezone")
async def set_user_timezone(
    body: TimezoneConfig,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Persist the user's timezone (validated against zoneinfo)."""
    from sqlalchemy.orm.attributes import flag_modified
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from app.models.user import UserSettings

    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError:
        raise HTTPException(400, f"Unknown timezone: {body.timezone}")

    sr = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not sr:
        sr = UserSettings(user_id=user.id, settings_json={})
        db.add(sr)
    data = dict(sr.settings_json or {})
    data["timezone"] = body.timezone
    data["tz_confirmed"] = bool(body.confirmed)
    sr.settings_json = data
    flag_modified(sr, "settings_json")
    db.commit()
    return {"status": "ok", "timezone": body.timezone, "confirmed": body.confirmed}


@router.get("/config")
async def get_llm_config(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get current LLM supervisor configuration (masked keys)."""
    from app.models.user import UserSettings

    settings_record = db.query(UserSettings).filter(
        UserSettings.user_id == user.id
    ).first()

    if not settings_record:
        return {
            "api_key_set": False,
            "api_key_masked": "",
            "model": "haiku",
            "enabled": False,
            "autonomous": False,
            "telegram_configured": False,
            "telegram_chat_id": "",
        }

    data = settings_record.settings_json or {}

    api_key_masked = ""
    if "llm_api_key" in data:
        try:
            decrypted = get_fernet().decrypt(data["llm_api_key"].encode()).decode()
            api_key_masked = "****" + decrypted[-8:] if len(decrypted) > 8 else "****"
        except Exception:
            api_key_masked = "****"

    return {
        "api_key_set": bool(api_key_masked),
        "api_key_masked": api_key_masked,
        "model": data.get("llm_model", "haiku"),
        "enabled": data.get("llm_enabled", False),
        "autonomous": data.get("llm_autonomous", False),
        "telegram_configured": bool(data.get("telegram_bot_token")),
        "telegram_chat_id": data.get("telegram_chat_id", ""),
    }


# ── Session CRUD ─────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List user's chat sessions (most recent first)."""
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id, ChatSession.is_active == True)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
        .all()
    )
    result = []
    for s in sessions:
        msg_count = db.query(ChatMessage).filter(ChatMessage.session_id == s.id).count()
        result.append({
            "id": s.id,
            "title": s.title or "New Chat",
            "created_at": s.created_at.isoformat() if s.created_at else "",
            "updated_at": s.updated_at.isoformat() if s.updated_at else "",
            "message_count": msg_count,
        })
    return result


@router.post("/sessions")
async def create_session(
    body: SessionCreate = SessionCreate(),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new chat session."""
    session = ChatSession(
        user_id=user.id,
        title=body.title or "New Chat",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at.isoformat() if session.created_at else "",
    }


@router.get("/sessions/{session_id}")
async def get_session_messages(
    session_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get messages for a chat session."""
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == user.id,
    ).first()
    if not session:
        raise HTTPException(404, "Session not found")

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return {
        "session": {
            "id": session.id,
            "title": session.title,
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "model": m.model,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in messages
        ],
    }


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a chat session (soft delete)."""
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == user.id,
    ).first()
    if not session:
        raise HTTPException(404, "Session not found")

    session.is_active = False
    db.commit()
    return {"status": "ok"}


# ── Chat endpoint ────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
@limiter.limit("10/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a chat message to the AI supervisor."""
    supervisor = get_supervisor()

    if not supervisor.is_enabled_for(user.id):
        await _ensure_configured(user.id, db)

    if not supervisor.is_enabled_for(user.id):
        raise HTTPException(400, "AI Supervisor not configured. Add your API key in Settings.")

    # Get or create session
    session_id = body.session_id
    if session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id == session_id,
            ChatSession.user_id == user.id,
            ChatSession.is_active == True,
        ).first()
        if not session:
            raise HTTPException(404, "Chat session not found")
    else:
        session = ChatSession(user_id=user.id, title=body.message[:50])
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id

    # Save user message to DB
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=body.message,
    )
    db.add(user_msg)
    db.commit()

    # Auto-title: set title from first message if still "New Chat"
    if session.title == "New Chat":
        session.title = body.message[:50]
        db.commit()

    # Load last 20 messages from DB for context
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(20)
        .all()
    )
    history.reverse()
    conversation = [{"role": m.role, "content": m.content} for m in history]

    # Build trading context
    context = await _build_chat_context(user.id, db)

    # Query Claude with DB-loaded conversation
    sess = supervisor.get_session(user.id)
    reply, usage = await supervisor.chat_with_history(
        user_id=user.id,
        conversation=conversation,
        context=context,
    )

    if not reply:
        reply = "I couldn't generate a response. Please check your API key."

    # Save assistant reply to DB
    assistant_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=reply,
        model=sess.model,
        input_tokens=usage.get("input_tokens") if usage else None,
        output_tokens=usage.get("output_tokens") if usage else None,
    )
    db.add(assistant_msg)

    # Update session timestamp
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    return ChatResponse(
        reply=reply,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=sess.model,
        session_id=session_id,
    )


@router.post("/chat/clear")
async def clear_chat(user=Depends(get_current_user)):
    """Clear in-memory conversation history for this user."""
    supervisor = get_supervisor()
    supervisor.clear_history(user.id)
    return {"status": "ok"}


# ── Usage endpoint ───────────────────────────────────────────────────────

@router.get("/usage")
async def get_usage(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get monthly token usage and estimated cost."""
    from sqlalchemy import func, extract

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Count sessions this month
    session_count = (
        db.query(func.count(ChatSession.id))
        .filter(
            ChatSession.user_id == user.id,
            ChatSession.created_at >= month_start,
        )
        .scalar() or 0
    )

    # Aggregate tokens from messages this month
    stats = (
        db.query(
            func.count(ChatMessage.id),
            func.coalesce(func.sum(ChatMessage.input_tokens), 0),
            func.coalesce(func.sum(ChatMessage.output_tokens), 0),
        )
        .join(ChatSession)
        .filter(
            ChatSession.user_id == user.id,
            ChatMessage.created_at >= month_start,
        )
        .first()
    )

    msg_count = stats[0] or 0
    input_tokens = stats[1] or 0
    output_tokens = stats[2] or 0

    # Estimate cost (Haiku pricing: $0.25/MTok input, $1.25/MTok output)
    cost = (input_tokens * 0.25 + output_tokens * 1.25) / 1_000_000

    return {
        "month": now.strftime("%Y-%m"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": round(cost, 4),
        "sessions": session_count,
        "messages": msg_count,
    }


# ── Status endpoint ──────────────────────────────────────────────────────

@router.get("/status")
async def llm_status(user=Depends(get_current_user)):
    """Get AI supervisor status for this user."""
    supervisor = get_supervisor()
    notifier = get_telegram_notifier()
    sess = supervisor.get_session(user.id)
    return {
        "enabled": sess.is_enabled,
        "model": sess.model,
        "autonomous": sess.autonomous,
        "conversation_length": len(sess.conversation),
        "telegram_configured": notifier.is_configured,
    }


# ── Telegram test ────────────────────────────────────────────────────────

@router.post("/telegram/test")
async def test_telegram(user=Depends(get_current_user)):
    """Send a test message via Telegram."""
    notifier = get_telegram_notifier()
    if not notifier.is_configured:
        raise HTTPException(400, "Telegram not configured")

    success = await notifier.send("FlowrexAlgo test message — Telegram integration is working!")
    if not success:
        raise HTTPException(500, "Failed to send Telegram message")
    return {"status": "ok", "message": "Test message sent"}


# ── Helpers ──────────────────────────────────────────────────────────────

async def _ensure_configured(user_id: int, db: Session):
    """Load LLM config from DB and configure supervisor singleton."""
    from app.models.user import UserSettings

    settings_record = db.query(UserSettings).filter(
        UserSettings.user_id == user_id
    ).first()
    if not settings_record:
        return

    data = settings_record.settings_json or {}
    api_key = None
    if "llm_api_key" in data:
        try:
            api_key = get_fernet().decrypt(data["llm_api_key"].encode()).decode()
        except Exception:
            return

    if api_key:
        supervisor = get_supervisor()
        supervisor.configure(
            user_id=user_id,
            api_key=api_key,
            model=data.get("llm_model", "haiku"),
            enabled=data.get("llm_enabled", False),
            autonomous=data.get("llm_autonomous", False),
        )

    bot_token = None
    if "telegram_bot_token" in data:
        try:
            bot_token = get_fernet().decrypt(
                data["telegram_bot_token"].encode()
            ).decode()
        except Exception:
            pass
    chat_id = data.get("telegram_chat_id", "")
    if bot_token and chat_id:
        notifier = get_telegram_notifier()
        notifier.configure(bot_token, chat_id)


async def _build_chat_context(user_id: int, db: Session) -> dict:
    """Build trading context for the AI supervisor."""
    from app.models.agent import TradingAgent, AgentTrade

    context = {"agents": [], "recent_trades": [], "daily_summary": {}}

    agents = db.query(TradingAgent).filter(
        TradingAgent.created_by == user_id,
        TradingAgent.deleted_at.is_(None),
    ).all()
    for a in agents:
        context["agents"].append({
            "id": a.id,
            "symbol": a.symbol,
            "agent_type": a.agent_type or "flowrex",
            "status": a.status,
            "broker": a.broker_name,
            "model_type": "flowrex_v2" if a.agent_type == "flowrex_v2" else "potential",
        })

    trades = (
        db.query(AgentTrade)
        .join(TradingAgent)
        .filter(
            TradingAgent.created_by == user_id,
            TradingAgent.deleted_at.is_(None),
        )
        .order_by(AgentTrade.entry_time.desc())
        .limit(50)
        .all()
    )
    total_pnl = 0
    wins = 0
    for t in trades:
        pnl = t.pnl or 0
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        context["recent_trades"].append({
            "symbol": t.symbol,
            "direction": t.direction,
            "pnl": pnl,
            "exit_reason": t.exit_reason or "open",
            "status": t.status,
        })

    closed_count = sum(1 for t in trades if t.status == "closed")
    context["daily_summary"] = {
        "total_pnl": total_pnl,
        "trade_count": closed_count,
        "win_rate": (wins / closed_count * 100) if closed_count > 0 else 0,
    }

    return context
