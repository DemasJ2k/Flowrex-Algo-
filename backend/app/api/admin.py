"""Admin endpoints — protected by is_admin check, with audit logging."""
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_admin_user
from app.models.user import User, AdminAuditLog
from app.models.agent import TradingAgent
from app.models.invite import InviteCode
from app.services.agent.engine import get_algo_engine

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _audit(db: Session, admin_id: int, action: str, resource_type: str = None,
           resource_id: int = None, ip: str = None, details: str = None):
    """Write an admin audit log entry. GDPR Art. 32 compliance."""
    try:
        log = AdminAuditLog(
            admin_id=admin_id, action=action,
            resource_type=resource_type, resource_id=resource_id,
            ip_address=ip, details=details,
        )
        db.add(log)
        db.flush()
    except Exception:
        pass  # Audit logging is best-effort, must not block the request


# ── Users ──────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(request: Request, admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    _audit(db, admin.id, "list_users", ip=request.client.host if request.client else None)
    users = db.query(User).all()
    db.commit()
    return [{"id": u.id, "email": u.email, "is_admin": u.is_admin, "created_at": str(u.created_at)} for u in users]


# ── Agents ─────────────────────────────────────────────────────────────

@router.get("/agents")
def list_all_agents(request: Request, admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    _audit(db, admin.id, "list_agents", ip=request.client.host if request.client else None)
    agents = db.query(TradingAgent).filter(TradingAgent.deleted_at.is_(None)).all()
    db.commit()
    return [{"id": a.id, "name": a.name, "symbol": a.symbol, "status": a.status, "created_by": a.created_by} for a in agents]


# ── System Health ──────────────────────────────────────────────────────

@router.get("/system")
def system_health(admin=Depends(get_admin_user)):
    engine = get_algo_engine()
    from app.core.websocket import get_ws_manager
    from app.core.database import check_db_connection
    ws = get_ws_manager()
    return {
        "database": "connected" if check_db_connection() else "disconnected",
        "running_agents": engine.get_running_agents(),
        "websocket_connections": ws.get_connection_count(),
        "websocket_channels": ws.get_channels(),
    }


# ── Invite Codes ───────────────────────────────────────────────────────

class GenerateInvitesRequest(BaseModel):
    count: int = 5
    max_uses: int = 1
    expires_days: Optional[int] = 30


@router.post("/invites")
def generate_invites(
    body: GenerateInvitesRequest,
    admin=Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Generate batch of invite codes."""
    codes = []
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days) if body.expires_days else None

    for _ in range(min(body.count, 50)):  # Cap at 50 per request
        code = secrets.token_urlsafe(12)  # 16-char alphanumeric code
        invite = InviteCode(
            code=code,
            created_by=admin.id,
            max_uses=body.max_uses,
            expires_at=expires_at,
        )
        db.add(invite)
        codes.append(code)

    db.commit()
    return {"codes": codes, "count": len(codes), "expires_at": str(expires_at) if expires_at else None}


@router.get("/invites")
def list_invites(admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    """List all invite codes with status."""
    invites = db.query(InviteCode).order_by(InviteCode.created_at.desc()).all()
    return [
        {
            "id": inv.id,
            "code": inv.code,
            "is_active": inv.is_active,
            "max_uses": inv.max_uses,
            "use_count": inv.use_count,
            "used_by": inv.used_by,
            "used_at": str(inv.used_at) if inv.used_at else None,
            "expires_at": str(inv.expires_at) if inv.expires_at else None,
            "created_at": str(inv.created_at),
            "status": (
                "used" if inv.use_count >= inv.max_uses
                else "active" if inv.is_active
                else "revoked"
            ),
        }
        for inv in invites
    ]


@router.delete("/invites/{invite_id}")
def revoke_invite(invite_id: int, admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    """Revoke an invite code."""
    invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite.is_active = False
    db.commit()
    return {"message": "Invite revoked", "code": invite.code}
