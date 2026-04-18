"""Market hours status endpoint — exposes is_market_open to the frontend."""
from fastapi import APIRouter, Depends
from app.core.auth import get_current_user
from app.services.market_hours import is_market_open, next_open, seconds_until_open

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/status/{symbol}")
async def market_status(symbol: str, user=Depends(get_current_user)):
    """Return open/closed status for a single symbol."""
    open_, reason = is_market_open(symbol)
    return {
        "symbol": symbol.upper(),
        "open": open_,
        "reason": reason,
        "seconds_until_open": seconds_until_open(symbol),
        "next_open_utc": (next_open(symbol).isoformat() if next_open(symbol) else None),
    }


@router.get("/status")
async def all_market_status(user=Depends(get_current_user)):
    """Return open/closed status for all supported symbols."""
    from app.services.market_hours import ASSET_CLASS
    return {
        sym: {
            "open": is_market_open(sym)[0],
            "reason": is_market_open(sym)[1],
            "asset_class": cls,
            "seconds_until_open": seconds_until_open(sym),
        }
        for sym, cls in ASSET_CLASS.items()
    }
