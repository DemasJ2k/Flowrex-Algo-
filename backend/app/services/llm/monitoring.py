"""
AI-powered monitoring service.

Bridges three layers:
  1. Trade events (engine.py → on_trade_opened / on_trade_closed)
  2. Hourly health check (APScheduler cron)
  3. Immediate alerts (loss streaks, drawdown, errors)

All paths funnel AI analysis into the user's Telegram chat (via global bot)
and optionally the in-app AI chat (appended as a system message).

Design principles:
- Each call is fully scoped to one user_id — never leak across users.
- If user hasn't configured an API key, silently skip (no errors).
- If user hasn't connected Telegram, AI analysis still logs to DB for their chat view.
- Token usage is bounded: haiku for event hooks, sonnet for hourly deep dives.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.encryption import get_fernet
from app.models.user import User, UserSettings
from app.models.agent import TradingAgent, AgentTrade, AgentLog
from app.models.chat import ChatSession, ChatMessage
from app.services.llm.supervisor import get_supervisor
from app.services.llm.telegram import get_telegram_notifier, _format_trade_closed
from app.core.config import settings


# ── Context builders ───────────────────────────────────────────────────

def _build_user_context(db: Session, user_id: int, lookback_trades: int = 50) -> dict:
    """Assemble trading context for a single user."""
    from app.services.market_hours import is_market_open
    agents = db.query(TradingAgent).filter(
        TradingAgent.created_by == user_id,
        TradingAgent.deleted_at.is_(None),
    ).all()

    trades = (
        db.query(AgentTrade)
        .join(TradingAgent)
        .filter(
            TradingAgent.created_by == user_id,
            TradingAgent.deleted_at.is_(None),
        )
        .order_by(AgentTrade.entry_time.desc())
        .limit(lookback_trades)
        .all()
    )

    total_pnl = sum((t.pnl or 0) for t in trades if t.status == "closed")
    closed = [t for t in trades if t.status == "closed"]
    wins = sum(1 for t in closed if (t.pnl or 0) > 0)

    return {
        "agents": [
            {
                "id": a.id,
                "symbol": a.symbol,
                "agent_type": a.agent_type or "flowrex",
                "status": a.status,
                "broker": a.broker_name,
                "market_open": is_market_open(a.symbol)[0],
                "market_status": is_market_open(a.symbol)[1],
            }
            for a in agents
        ],
        "recent_trades": [
            {
                "symbol": t.symbol,
                "direction": t.direction,
                "pnl": t.pnl or 0,
                "exit_reason": t.exit_reason or "open",
                "status": t.status,
                "confidence": t.confidence,
                "session_name": t.session_name,
                "mtf_score": t.mtf_score,
            }
            for t in trades
        ],
        "daily_summary": {
            "total_pnl": total_pnl,
            "trade_count": len(closed),
            "win_rate": (wins / len(closed) * 100) if closed else 0,
        },
    }


# ── User config loaders ────────────────────────────────────────────────

def _load_user_settings(db: Session, user_id: int) -> dict:
    sr = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    return (sr.settings_json if sr else None) or {}


def _ensure_supervisor_configured(db: Session, user_id: int) -> bool:
    """Load the user's API key into the supervisor session. Returns True if ready."""
    supervisor = get_supervisor()
    if supervisor.is_enabled_for(user_id):
        return True
    data = _load_user_settings(db, user_id)
    api_key = None
    if "llm_api_key" in data:
        try:
            api_key = get_fernet().decrypt(data["llm_api_key"].encode()).decode()
        except Exception:
            return False
    if not api_key:
        return False
    supervisor.configure(
        user_id=user_id,
        api_key=api_key,
        model=data.get("llm_model", "haiku"),
        enabled=data.get("llm_enabled", False),
        autonomous=data.get("llm_autonomous", False),
    )
    return supervisor.is_enabled_for(user_id)


def _user_telegram(db: Session, user_id: int) -> tuple[Optional[str], Optional[str]]:
    """Return (chat_id, user_bot_token) for a user. Token is None if using global bot."""
    data = _load_user_settings(db, user_id)
    chat_id = data.get("telegram_chat_id")
    user_token = None
    if "telegram_bot_token" in data:
        try:
            user_token = get_fernet().decrypt(data["telegram_bot_token"].encode()).decode()
        except Exception:
            user_token = None
    return chat_id, user_token


# ── In-app chat logging ────────────────────────────────────────────────

def _append_to_monitoring_session(db: Session, user_id: int, role: str,
                                   content: str, model: str = None) -> None:
    """
    Append a monitoring message to the user's dedicated "AI Monitoring" chat session.
    Creates the session on first use. This lets users see the AI's live analysis
    alongside their own chats.
    """
    MONITORING_TITLE = "AI Monitoring"
    session = db.query(ChatSession).filter(
        ChatSession.user_id == user_id,
        ChatSession.title == MONITORING_TITLE,
        ChatSession.is_active == True,
    ).first()
    if not session:
        session = ChatSession(user_id=user_id, title=MONITORING_TITLE)
        db.add(session)
        db.commit()
        db.refresh(session)

    msg = ChatMessage(
        session_id=session.id,
        role=role,
        content=content,
        model=model,
    )
    db.add(msg)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()


# ── Public API (called from engine + cron) ─────────────────────────────

async def on_trade_closed(user_id: int, trade_data: dict, agent_data: dict):
    """
    Hook: called after a trade closes in engine.py.
    Generates AI analysis + sends to Telegram + logs to monitoring chat.
    """
    db = SessionLocal()
    try:
        if not _ensure_supervisor_configured(db, user_id):
            # Still send a basic notification via Telegram
            chat_id, user_token = _user_telegram(db, user_id)
            if chat_id:
                notifier = get_telegram_notifier()
                await notifier.send_to_user(chat_id, _format_trade_closed(trade_data), user_token)
            return

        supervisor = get_supervisor()
        pnl = trade_data.get("pnl", 0) or 0
        # Only call Claude on losses or unusual closes — saves tokens on normal wins
        is_interesting = pnl < 0 or abs(pnl) > 50 or trade_data.get("exit_reason") not in ("TP_HIT",)

        tg_text = _format_trade_closed(trade_data)

        if is_interesting:
            try:
                reply = await supervisor.on_trade_closed(user_id, trade_data, agent_data)
                if reply:
                    tg_text += f"\n\n<b>AI Analysis:</b>\n{reply[:1500]}"
                    _append_to_monitoring_session(db, user_id, "assistant", reply,
                                                  model=supervisor.get_session(user_id).model)
                    # Execute any autonomous actions the AI returned (PAUSE_AGENT, ADJUST_RISK, etc.)
                    try:
                        await execute_autonomous_actions(user_id, reply)
                    except Exception as e:
                        print(f"[monitoring] autonomous action error: {e}")
            except Exception:
                pass

        chat_id, user_token = _user_telegram(db, user_id)
        if chat_id:
            notifier = get_telegram_notifier()
            await notifier.send_to_user(chat_id, tg_text, user_token)
    finally:
        db.close()


async def on_trade_opened(user_id: int, trade_data: dict, agent_data: dict):
    """Hook: called after a trade opens. Telegram notify only (no AI analysis to save tokens)."""
    db = SessionLocal()
    try:
        from app.services.llm.telegram import _format_trade_opened
        chat_id, user_token = _user_telegram(db, user_id)
        if chat_id:
            notifier = get_telegram_notifier()
            await notifier.send_to_user(chat_id, _format_trade_opened(trade_data), user_token)
    finally:
        db.close()


# Rate-limit error diagnostics — don't call Claude for every broker hiccup.
# Key: (user_id, agent_id, error_kind) -> last_sent_utc
_error_rate_limit: dict[tuple, datetime] = {}
_ERROR_COOLDOWN_MIN = 15  # minimum minutes between identical error diagnostics

# Rate-limit alerts — don't spam user with the same alert repeatedly.
# Key: (user_id, agent_id, alert_kind) -> last_sent_utc
_alert_rate_limit: dict[tuple, datetime] = {}
_ALERT_COOLDOWN_MIN = 60  # alerts of same kind suppressed for 1 hour

# Prevent unbounded dict growth over weeks of uptime: cap at 500 entries each,
# evicting oldest when full. 500 keys covers 50 users × 10 agents comfortably.
_RATE_LIMIT_MAX_SIZE = 500


def _rate_limit_set(d: dict[tuple, datetime], key: tuple, value: datetime) -> None:
    """Set a rate-limit entry, evicting the oldest if we hit the cap."""
    if len(d) >= _RATE_LIMIT_MAX_SIZE and key not in d:
        oldest_key = min(d, key=lambda k: d[k])
        d.pop(oldest_key, None)
    d[key] = value


async def on_error(user_id: int, agent_id: int, error_msg: str, agent_data: dict,
                   error_kind: str = "generic"):
    """
    Hook: called when the engine catches a significant error.

    Rate-limited so a flood of identical errors (e.g., broker 500 for 5 min)
    doesn't burn through API tokens. Error_kind groups related errors.
    """
    now = datetime.now(timezone.utc)
    key = (user_id, agent_id, error_kind)
    last = _error_rate_limit.get(key)
    if last and (now - last).total_seconds() < _ERROR_COOLDOWN_MIN * 60:
        return  # rate-limited
    _rate_limit_set(_error_rate_limit, key, now)

    db = SessionLocal()
    try:
        if not _ensure_supervisor_configured(db, user_id):
            return
        supervisor = get_supervisor()
        reply = await supervisor.on_error(user_id, error_msg, agent_data)
        if not reply:
            return
        _append_to_monitoring_session(db, user_id, "assistant", reply,
                                      model=supervisor.get_session(user_id).model)

        chat_id, user_token = _user_telegram(db, user_id)
        if chat_id:
            notifier = get_telegram_notifier()
            msg = (
                f"⚠️ <b>Error on {agent_data.get('symbol', '?')}</b>\n\n"
                f"<i>{error_msg[:200]}</i>\n\n"
                f"<b>AI Diagnosis:</b>\n{reply[:1500]}"
            )
            await notifier.send_to_user(chat_id, msg, user_token)
    finally:
        db.close()


async def execute_autonomous_actions(user_id: int, response: str) -> list[dict]:
    """
    Parse AI response for autonomous action JSON and execute valid ones.
    Returns list of actions that were actually executed.
    Enforces: autonomous mode enabled + bounded risk adjustments.
    """
    supervisor = get_supervisor()
    actions = supervisor.parse_actions(user_id, response)
    if not actions:
        return []

    executed = []
    db = SessionLocal()
    try:
        import json as _json
        for action in actions:
            act = action.get("action")
            agent_id = action.get("agent_id")
            reason = action.get("reason", "AI-triggered action")

            # Log every action attempt (even rejected) for audit trail
            _append_to_monitoring_session(
                db, user_id, "assistant",
                f"🤖 **Autonomous action requested:** `{_json.dumps(action)}`"
            )

            # Security: ensure the agent belongs to this user
            if agent_id:
                owns = db.query(TradingAgent).filter(
                    TradingAgent.id == agent_id,
                    TradingAgent.created_by == user_id,
                    TradingAgent.deleted_at.is_(None),
                ).first()
                if not owns:
                    continue  # not the user's agent — skip silently

            try:
                if act == "PAUSE_AGENT" and agent_id:
                    from app.services.agent.engine import get_algo_engine
                    await get_algo_engine().pause_agent(agent_id)
                    executed.append(action)
                    await send_alert(user_id,
                        f"🤖 Agent #{agent_id} paused by AI",
                        f"<b>Reason:</b> {reason}")

                elif act == "ADJUST_RISK" and agent_id:
                    new_risk = float(action.get("risk_per_trade", 0.001))
                    agent = db.query(TradingAgent).filter(TradingAgent.id == agent_id).first()
                    if agent:
                        rc = dict(agent.risk_config or {})
                        rc["risk_per_trade"] = new_risk
                        agent.risk_config = rc
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(agent, "risk_config")
                        db.commit()
                        # Hot-reload in engine
                        try:
                            from app.services.agent.engine import get_algo_engine
                            get_algo_engine().reload_agent_config(agent_id)
                        except Exception:
                            pass
                        executed.append(action)
                        await send_alert(user_id,
                            f"🤖 Risk adjusted on Agent #{agent_id}",
                            f"New risk_per_trade: <b>{new_risk*100:.2f}%</b>\n<b>Reason:</b> {reason}")

                elif act == "SEND_ALERT":
                    msg = action.get("message", "")
                    await send_alert(user_id, "AI Alert", msg)
                    executed.append(action)

                elif act == "LOG_RECOMMENDATION":
                    rec = action.get("recommendation", "")
                    _append_to_monitoring_session(db, user_id, "assistant",
                                                  f"💡 **Recommendation:** {rec}")
                    executed.append(action)
            except Exception as e:
                print(f"[autonomous_action] failed {act}: {e}")
    finally:
        db.close()

    return executed


async def send_alert(user_id: int, title: str, detail: str):
    """Push an immediate alert to Telegram (used by alert detection logic)."""
    db = SessionLocal()
    try:
        chat_id, user_token = _user_telegram(db, user_id)
        if chat_id:
            notifier = get_telegram_notifier()
            msg = f"⚠️ <b>{title}</b>\n\n{detail}"
            await notifier.send_to_user(chat_id, msg, user_token)
        _append_to_monitoring_session(db, user_id, "assistant",
                                      f"⚠️ **{title}**\n\n{detail}")
    finally:
        db.close()


async def hourly_check_all_users():
    """
    APScheduler-invoked hourly job. Loops users with LLM enabled + Telegram connected,
    runs a health check, sends summary.
    """
    db = SessionLocal()
    try:
        # Find users with LLM enabled AND Telegram connected
        candidates = db.query(UserSettings).all()
        for sr in candidates:
            data = sr.settings_json or {}
            if not data.get("llm_enabled"):
                continue
            if not data.get("telegram_chat_id"):
                continue  # no point running AI if we can't deliver the message
            try:
                await _run_hourly_for_user(db, sr.user_id)
            except Exception as e:
                print(f"[hourly_check] user={sr.user_id} error: {e}")
    finally:
        db.close()


async def _run_hourly_for_user(db: Session, user_id: int):
    """Single-user hourly health check."""
    if not _ensure_supervisor_configured(db, user_id):
        return
    supervisor = get_supervisor()
    ctx = _build_user_context(db, user_id, lookback_trades=50)

    # Only send hourly if there's activity (open agents OR recent trades)
    if not ctx["agents"] and not ctx["recent_trades"]:
        return

    # Include extra stats for hourly
    ctx["open_positions"] = sum(1 for t in ctx["recent_trades"] if t["status"] == "open")
    ctx["daily_pnl"] = ctx["daily_summary"]["total_pnl"]

    reply = await supervisor.hourly_health_check(user_id, ctx)
    if not reply:
        return

    _append_to_monitoring_session(db, user_id, "assistant", reply,
                                  model=supervisor.get_session(user_id).model)

    # Execute any autonomous actions the AI returned
    try:
        await execute_autonomous_actions(user_id, reply)
    except Exception as e:
        print(f"[monitoring] hourly autonomous action error: {e}")

    chat_id, user_token = _user_telegram(db, user_id)
    if chat_id:
        notifier = get_telegram_notifier()
        header = (
            f"📊 <b>Hourly Report</b> · {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
            f"P&L: ${ctx['daily_pnl']:.2f} · Trades: {ctx['daily_summary']['trade_count']} · "
            f"WR: {ctx['daily_summary']['win_rate']:.1f}%\n\n"
        )
        await notifier.send_to_user(chat_id, header + reply, user_token)


# ── Alert detection (called from engine after each trade close) ────────

def _alert_rate_limited(user_id: int, agent_id: int, kind: str) -> bool:
    """Return True if an alert of this kind was sent recently (should suppress)."""
    now = datetime.now(timezone.utc)
    key = (user_id, agent_id, kind)
    last = _alert_rate_limit.get(key)
    if last and (now - last).total_seconds() < _ALERT_COOLDOWN_MIN * 60:
        return True
    _rate_limit_set(_alert_rate_limit, key, now)
    return False


async def detect_and_alert(user_id: int, agent_id: int):
    """
    Check alert-worthy conditions and emit deduped Telegram alerts.
    """
    db = SessionLocal()
    try:
        agent = db.query(TradingAgent).filter(TradingAgent.id == agent_id).first()
        if not agent or agent.deleted_at is not None:
            return

        # Check consecutive losses (last 5 trades)
        recent = (
            db.query(AgentTrade)
            .filter(AgentTrade.agent_id == agent_id, AgentTrade.status == "closed")
            .order_by(AgentTrade.exit_time.desc())
            .limit(5)
            .all()
        )
        consec_losses = 0
        for t in recent:
            if (t.pnl or 0) < 0:
                consec_losses += 1
            else:
                break

        if consec_losses >= 3 and not _alert_rate_limited(user_id, agent_id, "loss_streak"):
            total_loss = sum((t.pnl or 0) for t in recent[:consec_losses])
            await send_alert(
                user_id,
                f"{consec_losses} Consecutive Losses on {agent.symbol}",
                f"Agent #{agent.id} ({agent.name}) has lost {consec_losses} trades in a row "
                f"for a combined <b>${total_loss:.2f}</b>.\n\n"
                f"Consider pausing the agent and reviewing recent market conditions."
            )

        # Recent errors in logs
        recent_errors = (
            db.query(AgentLog)
            .filter(
                AgentLog.agent_id == agent_id,
                AgentLog.level == "error",
                AgentLog.created_at >= datetime.now(timezone.utc) - timedelta(minutes=10),
            )
            .count()
        )
        if recent_errors >= 5 and not _alert_rate_limited(user_id, agent_id, "error_flood"):
            await send_alert(
                user_id,
                f"Repeated Errors on {agent.symbol}",
                f"Agent #{agent.id} has logged {recent_errors} errors in the last 10 minutes. "
                f"Check the Agent Details → Logs tab for details."
            )
    finally:
        db.close()
