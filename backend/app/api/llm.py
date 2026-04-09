"""LLM Supervisor API — chat, config, and status endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.encryption import get_fernet
from app.services.llm.supervisor import get_supervisor
from app.services.llm.telegram import get_telegram_notifier

router = APIRouter(prefix="/api/llm", tags=["llm"])


# ── Schemas ──────────────────────────────────────────────────────────────

class LLMConfigRequest(BaseModel):
    api_key: Optional[str] = None
    model: str = "haiku"  # haiku, sonnet, opus
    enabled: bool = True
    autonomous: bool = False
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    timestamp: str
    model: str


# ── Config endpoints ─────────────────────────────────────────────────────

@router.post("/config")
async def save_llm_config(
    body: LLMConfigRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save LLM supervisor configuration."""
    from app.models.user import UserSettings

    # Get or create user settings
    settings_record = db.query(UserSettings).filter(
        UserSettings.user_id == user.id
    ).first()

    if not settings_record:
        settings_record = UserSettings(user_id=user.id, settings_json={})
        db.add(settings_record)

    settings_data = settings_record.settings_json or {}

    # Encrypt API key if provided
    if body.api_key:
        encrypted_key = get_fernet().encrypt(body.api_key.encode()).decode()
        settings_data["llm_api_key"] = encrypted_key

    settings_data["llm_model"] = body.model
    settings_data["llm_enabled"] = body.enabled
    settings_data["llm_autonomous"] = body.autonomous

    # Encrypt Telegram credentials if provided
    if body.telegram_bot_token:
        settings_data["telegram_bot_token"] = get_fernet().encrypt(
            body.telegram_bot_token.encode()
        ).decode()
    if body.telegram_chat_id:
        settings_data["telegram_chat_id"] = body.telegram_chat_id

    settings_record.settings_json = settings_data
    db.commit()

    # Configure the supervisor singleton
    api_key = None
    if "llm_api_key" in settings_data:
        try:
            api_key = get_fernet().decrypt(settings_data["llm_api_key"].encode()).decode()
        except Exception:
            pass

    supervisor = get_supervisor()
    if api_key:
        supervisor.configure(
            api_key=api_key,
            model=body.model,
            enabled=body.enabled,
            autonomous=body.autonomous,
        )

    # Configure Telegram
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

    # Mask API key
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


# ── Chat endpoint ────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a chat message to the AI supervisor."""
    supervisor = get_supervisor()

    # Load config if not yet configured
    if not supervisor.is_enabled:
        await _ensure_configured(user.id, db)

    if not supervisor.is_enabled:
        raise HTTPException(400, "AI Supervisor not configured. Add your API key in Settings.")

    # Build context from DB
    context = await _build_chat_context(user.id, db)

    reply = await supervisor.chat(body.message, context)

    return ChatResponse(
        reply=reply,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=supervisor._model,
    )


@router.post("/chat/clear")
async def clear_chat(user=Depends(get_current_user)):
    """Clear conversation history."""
    supervisor = get_supervisor()
    supervisor.clear_history()
    return {"status": "ok"}


# ── Status endpoint ──────────────────────────────────────────────────────

@router.get("/status")
async def llm_status(user=Depends(get_current_user)):
    """Get AI supervisor status."""
    supervisor = get_supervisor()
    notifier = get_telegram_notifier()
    return {
        "enabled": supervisor.is_enabled,
        "model": supervisor._model,
        "autonomous": supervisor._autonomous,
        "conversation_length": len(supervisor._conversation),
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
            api_key=api_key,
            model=data.get("llm_model", "haiku"),
            enabled=data.get("llm_enabled", False),
            autonomous=data.get("llm_autonomous", False),
        )

    # Configure Telegram too
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

    # Active agents
    agents = db.query(TradingAgent).filter(
        TradingAgent.created_by == user_id
    ).all()
    for a in agents:
        context["agents"].append({
            "id": a.id,
            "symbol": a.symbol,
            "agent_type": a.agent_type or "flowrex",
            "status": a.status,
            "broker": a.broker_name,
            "models": list((a.risk_config or {}).keys()) if a.risk_config else [],
        })

    # Recent trades (last 50)
    trades = (
        db.query(AgentTrade)
        .join(TradingAgent)
        .filter(TradingAgent.created_by == user_id)
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
