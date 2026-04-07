"""Market data provider CRUD endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.encryption import get_fernet
from app.models.market_data import MarketDataProvider

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

SUPPORTED_PROVIDERS = ["databento", "alphavantage", "finnhub", "polygon"]


class ProviderCreate(BaseModel):
    provider_name: str
    api_key: str
    data_type: str = "ohlcv"  # ohlcv or tick


class ProviderUpdate(BaseModel):
    api_key: Optional[str] = None
    data_type: Optional[str] = None
    is_active: Optional[bool] = None


def _mask_key(encrypted: str) -> str:
    try:
        decrypted = get_fernet().decrypt(encrypted.encode()).decode()
        if len(decrypted) <= 8:
            return "****" + decrypted[-4:]
        return "****" + decrypted[-8:]
    except Exception:
        return "****"


@router.get("/providers")
def list_providers(user=Depends(get_current_user), db: Session = Depends(get_db)):
    providers = db.query(MarketDataProvider).filter(
        MarketDataProvider.user_id == user.id
    ).order_by(MarketDataProvider.created_at.desc()).all()
    return [
        {
            "id": p.id,
            "provider_name": p.provider_name,
            "api_key_masked": _mask_key(p.api_key_encrypted),
            "data_type": p.data_type,
            "is_active": p.is_active,
            "created_at": str(p.created_at),
        }
        for p in providers
    ]


@router.post("/providers")
def add_provider(body: ProviderCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if body.provider_name not in SUPPORTED_PROVIDERS:
        raise HTTPException(400, f"Unsupported provider. Choose from: {SUPPORTED_PROVIDERS}")
    if body.data_type not in ("ohlcv", "tick"):
        raise HTTPException(400, "data_type must be 'ohlcv' or 'tick'")

    # Check for duplicate
    existing = db.query(MarketDataProvider).filter(
        MarketDataProvider.user_id == user.id,
        MarketDataProvider.provider_name == body.provider_name,
    ).first()
    if existing:
        raise HTTPException(400, f"Provider '{body.provider_name}' already configured. Update it instead.")

    encrypted = get_fernet().encrypt(body.api_key.encode()).decode()
    provider = MarketDataProvider(
        user_id=user.id,
        provider_name=body.provider_name,
        api_key_encrypted=encrypted,
        data_type=body.data_type,
    )
    db.add(provider)
    db.commit()
    return {"message": f"Provider '{body.provider_name}' added.", "id": provider.id}


@router.put("/providers/{provider_id}")
def update_provider(provider_id: int, body: ProviderUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(MarketDataProvider).filter(
        MarketDataProvider.id == provider_id,
        MarketDataProvider.user_id == user.id,
    ).first()
    if not p:
        raise HTTPException(404, "Provider not found")

    if body.api_key is not None:
        p.api_key_encrypted = get_fernet().encrypt(body.api_key.encode()).decode()
    if body.data_type is not None:
        p.data_type = body.data_type
    if body.is_active is not None:
        p.is_active = body.is_active

    db.commit()
    return {"message": "Provider updated."}


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(MarketDataProvider).filter(
        MarketDataProvider.id == provider_id,
        MarketDataProvider.user_id == user.id,
    ).first()
    if not p:
        raise HTTPException(404, "Provider not found")
    db.delete(p)
    db.commit()
    return {"message": "Provider deleted."}


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(MarketDataProvider).filter(
        MarketDataProvider.id == provider_id,
        MarketDataProvider.user_id == user.id,
    ).first()
    if not p:
        raise HTTPException(404, "Provider not found")

    api_key = get_fernet().decrypt(p.api_key_encrypted.encode()).decode()

    # Basic connectivity test per provider
    try:
        if p.provider_name == "databento":
            import httpx
            r = httpx.get("https://hist.databento.com/v0/metadata.list_datasets",
                         auth=(api_key, ""), timeout=10)
            if r.status_code == 200:
                return {"status": "ok", "message": "Databento connection successful"}
            return {"status": "error", "message": f"Databento returned {r.status_code}"}

        elif p.provider_name == "finnhub":
            import httpx
            r = httpx.get(f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={api_key}", timeout=10)
            if r.status_code == 200:
                return {"status": "ok", "message": "Finnhub connection successful"}
            return {"status": "error", "message": f"Finnhub returned {r.status_code}"}

        elif p.provider_name == "alphavantage":
            import httpx
            r = httpx.get(f"https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol=IBM&interval=5min&apikey={api_key}", timeout=10)
            if r.status_code == 200:
                return {"status": "ok", "message": "Alpha Vantage connection successful"}
            return {"status": "error", "message": f"Alpha Vantage returned {r.status_code}"}

        elif p.provider_name == "polygon":
            import httpx
            r = httpx.get(f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-02?apiKey={api_key}", timeout=10)
            if r.status_code == 200:
                return {"status": "ok", "message": "Polygon connection successful"}
            return {"status": "error", "message": f"Polygon returned {r.status_code}"}

        return {"status": "error", "message": "Unknown provider"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/sources")
def get_data_sources(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Return available data sources for the current user."""
    from app.services.data.databento_adapter import SYMBOL_MAP as DB_SYMBOLS

    sources = [{"name": "broker", "label": "Broker", "symbols": "*", "has_ticks": False}]

    # Check for configured providers
    providers = db.query(MarketDataProvider).filter(
        MarketDataProvider.user_id == user.id,
        MarketDataProvider.is_active == True,
    ).all()

    for p in providers:
        if p.provider_name == "databento":
            sources.append({
                "name": "databento",
                "label": "Databento",
                "symbols": list(DB_SYMBOLS.keys()),
                "has_ticks": True,
                "data_type": p.data_type,
            })

    return sources
