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
    country: Optional[str] = Query(None, description="Filter by country code"),
    impact: Optional[str] = Query(None, description="Filter by impact: low, medium, high"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Fetch economic calendar.

    Trading Economics free tier was locked down in 2026 (only returns stale
    2025 data via guest:guest), so we now use Finnhub's economic calendar
    endpoint which requires a per-user API key — fresh, reliable, free tier
    allows 60 req/min.

    If user has no Finnhub key, falls back to Trading Economics with a warning.
    """
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    week_from = today - timedelta(days=3)
    week_to = today + timedelta(days=14)

    # Prefer Finnhub (needs user API key)
    api_key = _get_finnhub_key(user, db)
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://finnhub.io/api/v1/calendar/economic",
                    params={
                        "from": week_from.isoformat(),
                        "to": week_to.isoformat(),
                        "token": api_key,
                    },
                )
            if resp.status_code == 200:
                payload = resp.json()
                events = payload.get("economicCalendar", []) or []
                result = []
                for ev in events:
                    impact_raw = (ev.get("impact") or "low").lower()
                    if impact and impact.lower() != impact_raw:
                        continue
                    country_raw = ev.get("country", "")
                    if country and country.upper() not in country_raw.upper():
                        continue
                    # Finnhub date format: "2026-04-18 13:30:00"
                    date_str = ev.get("time", "")
                    date_part = date_str[:10]
                    time_part = date_str[11:16] if len(date_str) > 11 else ""
                    result.append({
                        "event": ev.get("event", ""),
                        "country": country_raw,
                        "impact": impact_raw,
                        "actual": ev.get("actual"),
                        "estimate": ev.get("estimate"),
                        "previous": ev.get("prev"),
                        "time": time_part,
                        "date": date_part,
                        "unit": ev.get("unit", ""),
                        "currency": country_raw,  # Finnhub doesn't return currency separately
                    })
                # Sort by date+time ascending (upcoming first)
                result.sort(key=lambda x: f"{x['date']} {x['time']}")
                return {"events": result, "count": len(result), "source": "finnhub"}
        except Exception as e:
            # Fall through to TE fallback
            pass

    # Fallback: Trading Economics (often stale in 2026)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.tradingeconomics.com/calendar",
            params={"c": "guest:guest"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Economic calendar unavailable: {resp.status_code}")

    raw = resp.json()
    if not isinstance(raw, list):
        raw = []

    imp_map = {1: "low", 2: "medium", 3: "high"}
    result = []
    for ev in raw:
        event_country = ev.get("Country", "")
        importance = ev.get("Importance", 1)
        event_impact = imp_map.get(importance, "low")
        if country and country.upper() not in event_country.upper():
            continue
        if impact and event_impact != impact.lower():
            continue
        date_str = ev.get("Date", "")
        date_part = date_str[:10] if date_str else ""
        time_part = date_str[11:16] if len(date_str) > 11 else ""
        result.append({
            "event": ev.get("Event", ev.get("Category", "")),
            "country": event_country,
            "impact": event_impact,
            "actual": ev.get("Actual"),
            "estimate": ev.get("Forecast"),
            "previous": ev.get("Previous"),
            "time": time_part,
            "date": date_part,
            "unit": ev.get("Unit", ""),
            "currency": ev.get("Currency", ""),
        })
    result.sort(key=lambda x: f"{x['date']} {x['time']}", reverse=True)

    return {
        "events": result,
        "count": len(result),
        "source": "tradingeconomics_fallback",
        "warning": "Trading Economics free tier is stale. Add a Finnhub API key in Settings → Providers for fresh data.",
    }


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
