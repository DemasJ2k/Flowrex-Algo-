"""News & Economic Calendar endpoints (Finnhub)."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import settings as app_settings
from app.models.user import UserSettings

router = APIRouter(prefix="/api/news", tags=["news"])

FINNHUB_BASE = "https://finnhub.io/api/v1"


def _get_finnhub_key(user, db: Session) -> str:
    """Resolve Finnhub API key: user settings_json > env var."""
    # Try user settings first
    user_settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if user_settings and user_settings.settings_json:
        api_keys = user_settings.settings_json.get("api_keys", {})
        key = api_keys.get("finnhub", "")
        if key and not key.startswith("your-"):
            return key

    # Fall back to env / config
    env_key = app_settings.FINNHUB_API_KEY or os.getenv("FINNHUB_API_KEY", "")
    if env_key and not env_key.startswith("your-"):
        return env_key

    raise HTTPException(status_code=400, detail="Finnhub API key not configured. Add it in Settings > API Keys.")


# ── Economic Calendar ────────────────────────────────────────────────

@router.get("/calendar")
async def get_economic_calendar(
    country: Optional[str] = Query(None, description="Filter by country code (e.g. US, GB, EU)"),
    impact: Optional[str] = Query(None, description="Filter by impact: low, medium, high"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch economic calendar events from Finnhub."""
    api_key = _get_finnhub_key(user, db)

    today = datetime.now(timezone.utc).date()
    from_date = (today - timedelta(days=1)).isoformat()
    to_date = (today + timedelta(days=7)).isoformat()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{FINNHUB_BASE}/calendar/economic",
            params={"from": from_date, "to": to_date, "token": api_key},
        )

    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="Economic calendar requires Finnhub Premium. Headlines are available on the free tier.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Finnhub API error: {resp.status_code}")

    data = resp.json()
    events = data.get("economicCalendar", data.get("result", []))

    # Normalize into consistent format
    result = []
    if isinstance(events, list):
        for ev in events:
            event_country = ev.get("country", "")
            event_impact = ev.get("impact", "low")

            # Country filter
            if country and event_country.upper() != country.upper():
                continue

            # Impact filter
            if impact and event_impact.lower() != impact.lower():
                continue

            result.append({
                "event": ev.get("event", ""),
                "country": event_country,
                "impact": event_impact.lower() if event_impact else "low",
                "actual": ev.get("actual"),
                "estimate": ev.get("estimate"),
                "previous": ev.get("prev"),
                "time": ev.get("time", ""),
                "date": ev.get("date", ""),
                "unit": ev.get("unit", ""),
                "currency": ev.get("currency", ""),
            })

    # Sort by date+time descending (newest first)
    result.sort(key=lambda x: f"{x['date']} {x['time']}", reverse=True)

    return {"events": result, "count": len(result)}


# ── Market Headlines ─────────────────────────────────────────────────

@router.get("/headlines")
async def get_market_headlines(
    category: str = Query("general", description="News category: general, forex, crypto, merger"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch market news headlines from Finnhub."""
    api_key = _get_finnhub_key(user, db)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{FINNHUB_BASE}/news",
            params={"category": category, "token": api_key},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Finnhub API error: {resp.status_code}")

    articles = resp.json()
    if not isinstance(articles, list):
        articles = []

    result = []
    for a in articles[:50]:  # Cap at 50 headlines
        result.append({
            "headline": a.get("headline", ""),
            "source": a.get("source", ""),
            "url": a.get("url", ""),
            "datetime": a.get("datetime", 0),
            "image": a.get("image", ""),
            "summary": a.get("summary", ""),
            "category": a.get("category", ""),
        })

    return {"articles": result, "count": len(result)}
