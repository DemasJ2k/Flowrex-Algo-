"""
AI-powered monitoring service.

Bridges three layers:
  1. Trade events (engine.py → on_trade_opened / on_trade_closed)
  2. Scheduled status reports (APScheduler cron, per-user cadence)
  3. Immediate alerts (loss streaks, drawdown, errors)

All paths funnel AI analysis into the user's Telegram chat (via global bot)
and optionally the in-app AI chat (appended as a system message).

Design principles:
- Each call is fully scoped to one user_id — never leak across users.
- If user hasn't configured an API key, silently skip (no errors).
- If user hasn't connected Telegram, AI analysis still logs to DB for their chat view.
- Token usage is bounded: haiku for event hooks, sonnet for hourly deep dives.
- Scheduled reports respect per-user frequency, quiet hours, market status,
  and a state-change hash so we never spam identical reports.
"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.encryption import get_fernet

logger = logging.getLogger("flowrex.monitoring")
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
                "id": t.id,
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

# Monitoring frequency → minimum interval (minutes) between reports for that user.
# "off" disables scheduled reports entirely. Custom/unknown values fall back to 1h.
_FREQUENCY_MINUTES = {
    "off":   None,
    "1h":    60,
    "4h":    4 * 60,
    "12h":   12 * 60,
    "daily": 24 * 60,
}


_MONITORING_DEFAULTS = {
    "enabled":                  True,
    "frequency":                "1h",
    "quiet_hours_start":        None,   # "HH:MM" in user TZ, optional
    "quiet_hours_end":          None,
    "skip_when_markets_closed": True,
    "skip_when_unchanged":      True,
}


def _load_user_settings(db: Session, user_id: int) -> dict:
    sr = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    return (sr.settings_json if sr else None) or {}


def _load_monitoring_config(data: dict) -> dict:
    """Return user's monitoring config merged over defaults."""
    cfg = dict(_MONITORING_DEFAULTS)
    cfg.update((data.get("monitoring") or {}))
    return cfg


def _user_timezone(data: dict) -> ZoneInfo:
    """Resolve the user's tz from settings, falling back to UTC."""
    tz_name = (data.get("timezone") or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _is_within_quiet_hours(now_local: datetime, start: Optional[str], end: Optional[str]) -> bool:
    """
    True if `now_local` falls inside [start, end) where both are "HH:MM" strings
    in the user's timezone. Ranges that cross midnight (e.g. 22:00→07:00) are
    handled by OR-ing the two halves.
    """
    if not start or not end:
        return False
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except (ValueError, AttributeError):
        return False
    now_mins = now_local.hour * 60 + now_local.minute
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em
    if start_mins == end_mins:
        return False
    if start_mins < end_mins:
        return start_mins <= now_mins < end_mins
    # wraps midnight
    return now_mins >= start_mins or now_mins < end_mins


def _frequency_minutes(freq: str) -> Optional[int]:
    """Minutes between reports for a frequency preset, or None if 'off'."""
    return _FREQUENCY_MINUTES.get(freq, 60)


def _compute_state_hash(ctx: dict) -> str:
    """
    Hash the user-visible state we report on. If this matches the previous
    send, we can skip calling Claude. We round P&L so tiny unrealised swings
    don't invalidate the hash.
    """
    daily = ctx.get("daily_summary") or {}
    pieces = {
        "pnl":       round(float(daily.get("total_pnl") or 0.0), 2),
        "trades":    int(daily.get("trade_count") or 0),
        "open_pos":  ctx.get("open_positions") or 0,
        "running":   sum(1 for a in ctx.get("agents", []) if a.get("status") == "running"),
        "last_id":   (ctx.get("recent_trades") or [{}])[0].get("id") if ctx.get("recent_trades") else None,
    }
    return hashlib.sha1(json.dumps(pieces, sort_keys=True).encode()).hexdigest()[:12]


def _get_monitoring_state(data: dict) -> dict:
    """Read the mutable monitoring state blob from settings_json."""
    return dict(data.get("monitoring_state") or {})


def _write_monitoring_state(db: Session, user_id: int, updates: dict) -> None:
    """Merge updates into settings_json.monitoring_state and persist."""
    from sqlalchemy.orm.attributes import flag_modified
    sr = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not sr:
        return
    data = dict(sr.settings_json or {})
    state = dict(data.get("monitoring_state") or {})
    state.update(updates)
    data["monitoring_state"] = state
    sr.settings_json = data
    flag_modified(sr, "settings_json")
    db.commit()


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
                        logger.warning(f"Autonomous action error: {e}", exc_info=True)
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
                logger.warning(f"Autonomous action {act} failed: {e}", exc_info=True)
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
    APScheduler top-of-hour tick. Fans out to each eligible user and lets
    `_run_hourly_for_user` apply per-user cadence + quiet-hours + skip logic.
    """
    db = SessionLocal()
    try:
        candidates = db.query(UserSettings).all()
        for sr in candidates:
            data = sr.settings_json or {}
            if not data.get("llm_enabled"):
                continue
            if not data.get("telegram_chat_id"):
                continue  # no point running AI if we can't deliver the message
            mon = _load_monitoring_config(data)
            if not mon.get("enabled", True):
                continue
            try:
                await _run_hourly_for_user(db, sr.user_id)
            except Exception as e:
                logger.warning(
                    f"Hourly check failed for user {sr.user_id}: {e}", exc_info=True
                )
    finally:
        db.close()


async def _run_hourly_for_user(db: Session, user_id: int):
    """
    Scheduled status report for one user. Applies the user's monitoring config:
      - frequency preset gates how often we fire
      - quiet hours suppress sends in their local window
      - skip_when_markets_closed holds reports during weekends etc.
      - skip_when_unchanged suppresses duplicates unless 24h have passed
    """
    from app.services.market_hours import (
        get_asset_class_status,
        any_market_open_for_symbols,
    )

    if not _ensure_supervisor_configured(db, user_id):
        return

    data = _load_user_settings(db, user_id)
    mon = _load_monitoring_config(data)
    if not mon.get("enabled", True):
        return

    # 1. Frequency gate — skip unless enough minutes have elapsed.
    now_utc = datetime.now(timezone.utc)
    freq_min = _frequency_minutes(mon.get("frequency", "1h"))
    if freq_min is None:
        return  # "off"

    state = _get_monitoring_state(data)
    last_sent_iso = state.get("last_sent_at")
    if last_sent_iso:
        try:
            last_sent = datetime.fromisoformat(last_sent_iso)
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            if (now_utc - last_sent).total_seconds() < freq_min * 60 - 30:
                return  # too soon
        except ValueError:
            pass

    # 2. Quiet-hours gate — evaluated in user's timezone.
    tz = _user_timezone(data)
    now_local = now_utc.astimezone(tz)
    if _is_within_quiet_hours(now_local, mon.get("quiet_hours_start"),
                              mon.get("quiet_hours_end")):
        return

    # 3. Build context + market-aware skip.
    ctx = _build_user_context(db, user_id, lookback_trades=50)
    if not ctx["agents"] and not ctx["recent_trades"]:
        return  # nothing to report on

    user_symbols = [a.get("symbol") for a in ctx["agents"] if a.get("symbol")]
    any_open = any_market_open_for_symbols(user_symbols, now_utc) if user_symbols else False

    if mon.get("skip_when_markets_closed", True) and user_symbols and not any_open:
        # Send a one-shot "markets closed" heads-up, then stay silent until
        # a market opens again. The flag resets itself the next time any
        # market is open.
        if not state.get("markets_closed_notified"):
            chat_id, user_token = _user_telegram(db, user_id)
            if chat_id:
                notifier = get_telegram_notifier()
                local_str = now_local.strftime("%H:%M %Z")
                msg = (
                    f"🕑 <b>Markets closed</b> · {local_str}\n\n"
                    f"All of your agents' markets are closed right now. "
                    f"I'll resume status reports as soon as one reopens."
                )
                await notifier.send_to_user(chat_id, msg, user_token)
            _write_monitoring_state(db, user_id, {
                "markets_closed_notified": True,
                "last_sent_at": now_utc.isoformat(),
            })
        return

    # Markets are open again (or crypto user) — clear the flag so next closure notifies.
    if state.get("markets_closed_notified"):
        _write_monitoring_state(db, user_id, {"markets_closed_notified": False})

    # 4. Extra context for hourly + state-hash.
    ctx["open_positions"] = sum(1 for t in ctx["recent_trades"] if t["status"] == "open")
    ctx["daily_pnl"] = ctx["daily_summary"]["total_pnl"]
    ctx["asset_class_status"] = get_asset_class_status(now_utc)
    ctx["user_timezone"] = str(tz)
    ctx["local_time_display"] = now_local.strftime("%Y-%m-%d %H:%M %Z")
    ctx["report_cadence"] = mon.get("frequency", "1h")

    state_hash = _compute_state_hash(ctx)
    last_hash = state.get("last_state_hash")
    last_liveness_iso = state.get("last_liveness_at") or last_sent_iso
    liveness_due = True
    if last_liveness_iso:
        try:
            last_live = datetime.fromisoformat(last_liveness_iso)
            if last_live.tzinfo is None:
                last_live = last_live.replace(tzinfo=timezone.utc)
            liveness_due = (now_utc - last_live).total_seconds() >= 24 * 3600
        except ValueError:
            pass

    state_changed = (state_hash != last_hash)
    ctx["state_changed"] = state_changed

    if (
        mon.get("skip_when_unchanged", True)
        and not state_changed
        and not liveness_due
    ):
        return

    # 5. Ask the supervisor.
    reply = await supervisor_hourly(user_id, ctx)
    if not reply:
        return

    supervisor = get_supervisor()
    _append_to_monitoring_session(db, user_id, "assistant", reply,
                                  model=supervisor.get_session(user_id).model)

    # Execute any autonomous actions the AI returned
    try:
        await execute_autonomous_actions(user_id, reply)
    except Exception as e:
        logger.warning(f"Hourly autonomous action error: {e}", exc_info=True)

    # 6. Deliver to Telegram with local-time header.
    chat_id, user_token = _user_telegram(db, user_id)
    if chat_id:
        notifier = get_telegram_notifier()
        cadence_label = {
            "1h": "Hourly", "4h": "4-Hour", "12h": "12-Hour", "daily": "Daily",
        }.get(mon.get("frequency", "1h"), "Report")
        header = (
            f"📊 <b>{cadence_label} Report</b> · {now_local.strftime('%H:%M %Z')}\n"
            f"P&L: ${ctx['daily_pnl']:.2f} · Trades: {ctx['daily_summary']['trade_count']} · "
            f"WR: {ctx['daily_summary']['win_rate']:.1f}%\n\n"
        )
        await notifier.send_to_user(chat_id, header + reply, user_token)

    # 7. Persist state.
    updates = {
        "last_sent_at":    now_utc.isoformat(),
        "last_state_hash": state_hash,
    }
    if state_changed or liveness_due:
        updates["last_liveness_at"] = now_utc.isoformat()
    _write_monitoring_state(db, user_id, updates)


async def supervisor_hourly(user_id: int, ctx: dict) -> Optional[str]:
    """Thin wrapper so tests can monkeypatch the Claude call."""
    return await get_supervisor().hourly_health_check(user_id, ctx)


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
