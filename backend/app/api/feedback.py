"""Feedback & access request endpoints."""
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user, get_admin_user
from app.core.rate_limit import limiter
from app.models.feedback import AccessRequest, FeedbackReport
from app.models.invite import InviteCode

router = APIRouter(prefix="/api", tags=["feedback"])


# ── Access Requests (public — no auth, rate limited to block spam) ────

class AccessRequestCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=50)
    message: Optional[str] = Field(None, max_length=2000)


@router.post("/access-requests")
@limiter.limit("3/hour")
def create_access_request(request: Request, body: AccessRequestCreate, db: Session = Depends(get_db)):
    req = AccessRequest(
        name=body.name,
        email=body.email,
        phone=body.phone,
        message=body.message,
    )
    db.add(req)
    db.commit()
    return {"message": "Access request submitted. You'll receive an invite code once approved."}


# ── Admin: manage access requests ─────────────────────────────────────

@router.get("/admin/access-requests")
def list_access_requests(admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    reqs = db.query(AccessRequest).order_by(AccessRequest.created_at.desc()).all()
    return [
        {
            "id": r.id, "name": r.name, "email": r.email,
            "phone": r.phone, "message": r.message,
            "status": r.status, "created_at": str(r.created_at),
        }
        for r in reqs
    ]


@router.post("/admin/access-requests/{request_id}/approve")
def approve_access_request(
    request_id: int,
    admin=Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    req = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")

    # Generate invite code
    code = secrets.token_urlsafe(12)
    invite = InviteCode(
        code=code,
        created_by=admin.id,
        max_uses=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(invite)
    req.status = "approved"
    db.commit()

    return {
        "message": f"Approved. Invite code: {code}",
        "invite_code": code,
        "email": req.email,
        "name": req.name,
    }


@router.post("/admin/access-requests/{request_id}/reject")
def reject_access_request(
    request_id: int,
    admin=Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    req = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.status = "rejected"
    db.commit()
    return {"message": "Request rejected"}


# ── Feedback (authenticated) ──────────────────────────────────────────

class FeedbackCreate(BaseModel):
    feedback_type: str  # bug, feature, provider_request, other
    message: str


@router.post("/feedback")
def create_feedback(body: FeedbackCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    report = FeedbackReport(
        user_id=user.id,
        feedback_type=body.feedback_type,
        message=body.message,
    )
    db.add(report)
    db.commit()
    return {"message": "Feedback submitted. Thank you!"}


@router.get("/feedback")
def list_my_feedback(user=Depends(get_current_user), db: Session = Depends(get_db)):
    reports = db.query(FeedbackReport).filter(
        FeedbackReport.user_id == user.id
    ).order_by(FeedbackReport.created_at.desc()).all()
    return [
        {
            "id": r.id, "type": r.feedback_type,
            "message": r.message, "status": r.status,
            "created_at": str(r.created_at),
        }
        for r in reports
    ]


@router.get("/admin/feedback")
def list_all_feedback(admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    reports = db.query(FeedbackReport).order_by(FeedbackReport.created_at.desc()).all()
    return [
        {
            "id": r.id, "user_id": r.user_id,
            "type": r.feedback_type, "message": r.message,
            "status": r.status, "created_at": str(r.created_at),
        }
        for r in reports
    ]
