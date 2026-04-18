"""
News filter — checks for high-impact economic events before trading.
Uses Finnhub (economic calendar) -> NewsAPI (headlines) -> AlphaVantage (fallback).
Fails open: if all APIs unavailable, allows trading.
"""
import time
import httpx
from datetime import datetime, timezone, timedelta

from app.core.config import settings

# Per-symbol keywords that indicate high-impact news
SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "XAUUSD": ["gold", "fed", "fomc", "inflation", "cpi", "nfp", "interest rate", "ppi", "pce"],
    "XAGUSD": ["silver", "fed", "fomc", "inflation", "cpi", "interest rate"],
    "BTCUSD": ["bitcoin", "crypto", "sec", "etf", "regulation", "binance", "coinbase"],
    "ETHUSD": ["ethereum", "crypto", "sec", "etf", "defi"],
    "US30":   ["dow", "jobs", "gdp", "fed", "fomc", "earnings", "nfp", "unemployment"],
    "NAS100": ["nasdaq", "tech", "fed", "fomc", "jobs", "gdp", "earnings"],
    "ES":     ["s&p", "sp500", "fed", "jobs", "gdp", "earnings", "inflation"],
    "SPX500": ["s&p", "fed", "fomc", "jobs", "gdp", "cpi", "earnings"],
    "EURUSD": ["ecb", "euro", "fed", "fomc", "nfp", "inflation", "gdp"],
    "GBPUSD": ["boe", "pound", "sterling", "fed", "fomc", "nfp"],
    "USDJPY": ["boj", "yen", "fed", "fomc", "nfp", "japan"],
}

# Finnhub country codes for economic calendar by symbol
SYMBOL_COUNTRIES: dict[str, str] = {
    "XAUUSD": "US", "XAGUSD": "US", "BTCUSD": "US", "ETHUSD": "US",
    "US30": "US", "NAS100": "US", "SPX500": "US",
    "EURUSD": "EU", "GBPUSD": "GB", "USDJPY": "JP",
}

# Cache — increased from 5 min to 30 min (Batch J, V4-C4). Economic calendar
# data doesn't change within 30 min, and the old 5-min TTL was burning through
# free-tier API quotas (100 calls/day on NewsAPI) within 2 hours.
_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 1800  # 30 minutes

# Per-API rate limiting (Batch J, V4-C4) — minimum seconds between calls
_last_api_call: dict[str, float] = {}
MIN_API_INTERVAL = 2.0  # 2 seconds between calls to the same API


class NewsResult:
    def __init__(self, should_trade: bool = True, reason: str = "", source: str = ""):
        self.should_trade = should_trade
        self.reason = reason
        self.source = source


def check_high_impact_news(
    symbol: str,
    window_minutes: int = 15,
) -> NewsResult:
    """
    Check if high-impact news is imminent for this symbol.
    Tries APIs in order: Finnhub -> NewsAPI -> AlphaVantage -> fail open.
    """
    cache_key = f"{symbol}_{window_minutes}"

    # Check cache
    if cache_key in _cache:
        cached_time, cached_result = _cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            return NewsResult(**cached_result)

    # Try each provider in order
    result = None

    if settings.FINNHUB_API_KEY and not settings.FINNHUB_API_KEY.startswith("your-"):
        result = _check_finnhub(symbol, window_minutes)

    if result is None and settings.NEWSAPI_API_KEY and not settings.NEWSAPI_API_KEY.startswith("your-"):
        result = _check_newsapi(symbol, window_minutes)

    if result is None and settings.ALPHAVANTAGE_API_KEY and not settings.ALPHAVANTAGE_API_KEY.startswith("your-"):
        result = _check_alphavantage(symbol)

    # Fail open if all APIs unavailable
    if result is None:
        result = NewsResult(should_trade=True, reason="No news APIs configured (fail-open)", source="none")

    # Cache
    _cache[cache_key] = (time.time(), {
        "should_trade": result.should_trade,
        "reason": result.reason,
        "source": result.source,
    })

    return result


def _check_finnhub(symbol: str, window_minutes: int) -> NewsResult | None:
    """
    Check Finnhub economic calendar for high-impact events.
    Returns None if API call fails (triggers fallback).
    """
    try:
        now = datetime.now(timezone.utc)
        from_date = now.strftime("%Y-%m-%d")
        to_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        resp = httpx.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": from_date, "to": to_date, "token": settings.FINNHUB_API_KEY},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        events = data.get("economicCalendar", [])
        keywords = get_keywords(symbol)
        country = SYMBOL_COUNTRIES.get(symbol, "US")

        for event in events:
            event_impact = event.get("impact", "").lower()
            event_country = event.get("country", "")
            event_name = event.get("event", "").lower()
            event_time_str = event.get("time", "")

            # Only care about high impact events
            if event_impact not in ("high", "3"):
                continue

            # Check country relevance (Batch J fix — V4-H3).
            # Previously missed EUR events because German "DE" != "EU".
            # EU instruments should match DE, FR, IT, ES events + US (Fed always matters).
            eu_countries = {"DE", "FR", "IT", "ES", "NL", "BE", "AT"}
            relevant = {country, "US"}
            if country == "EU":
                relevant |= eu_countries
            if country == "GB":
                relevant.add("EU")  # BoE + ECB both affect GBPUSD
            if event_country not in relevant:
                continue

            # Check keyword match
            matches_keyword = any(kw in event_name for kw in keywords)
            if not matches_keyword:
                continue

            # Check if event is within the window
            try:
                event_time = datetime.strptime(f"{from_date} {event_time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                time_until = (event_time - now).total_seconds() / 60
                if -5 <= time_until <= window_minutes:
                    return NewsResult(
                        should_trade=False,
                        reason=f"High-impact event in {int(time_until)}min: {event.get('event', 'Unknown')}",
                        source="finnhub",
                    )
            except (ValueError, TypeError):
                continue

        return NewsResult(should_trade=True, reason="No high-impact events (Finnhub)", source="finnhub")

    except Exception as e:
        import logging
        logging.getLogger("flowrex.news").warning(f"Finnhub API failed: {e}")
        return None  # Trigger fallback


def _check_newsapi(symbol: str, window_minutes: int) -> NewsResult | None:
    """
    Check NewsAPI for breaking news headlines matching symbol keywords.
    Returns None if API call fails.
    """
    try:
        keywords = get_keywords(symbol)
        if not keywords:
            return NewsResult(should_trade=True, reason="No keywords for symbol", source="newsapi")

        # Search for recent high-impact headlines
        query = " OR ".join(keywords[:5])  # NewsAPI limits query length

        resp = httpx.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "sortBy": "publishedAt",
                "pageSize": 5,
                "language": "en",
                "apiKey": settings.NEWSAPI_API_KEY,
            },
            timeout=5.0,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        articles = data.get("articles", [])

        # Check if any article is very recent (within window)
        now = datetime.now(timezone.utc)
        for article in articles:
            pub_str = article.get("publishedAt", "")
            try:
                pub_time = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                minutes_ago = (now - pub_time).total_seconds() / 60
                if minutes_ago <= window_minutes:
                    title = article.get("title", "")
                    # Check if title matches our keywords
                    title_lower = title.lower()
                    if any(kw in title_lower for kw in keywords):
                        return NewsResult(
                            should_trade=False,
                            reason=f"Breaking news ({int(minutes_ago)}min ago): {title[:80]}",
                            source="newsapi",
                        )
            except (ValueError, TypeError):
                continue

        return NewsResult(should_trade=True, reason="No breaking news (NewsAPI)", source="newsapi")

    except Exception:
        return None


def _check_alphavantage(symbol: str) -> NewsResult | None:
    """
    Check AlphaVantage news sentiment as final fallback.
    Returns None if API call fails.
    """
    try:
        keywords = get_keywords(symbol)
        if not keywords:
            return NewsResult(should_trade=True, reason="No keywords", source="alphavantage")

        tickers = {"XAUUSD": "GOLD", "BTCUSD": "CRYPTO:BTC", "US30": "DJI"}.get(symbol, "")

        params = {
            "function": "NEWS_SENTIMENT",
            "apikey": settings.ALPHAVANTAGE_API_KEY,
        }
        if tickers:
            params["tickers"] = tickers
        else:
            params["topics"] = keywords[0]

        resp = httpx.get("https://www.alphavantage.co/query", params=params, timeout=5.0)
        if resp.status_code != 200:
            return None

        data = resp.json()
        feed = data.get("feed", [])

        now = datetime.now(timezone.utc)
        for item in feed[:5]:
            pub_str = item.get("time_published", "")
            try:
                pub_time = datetime.strptime(pub_str[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                minutes_ago = (now - pub_time).total_seconds() / 60
                if minutes_ago <= 15:
                    title = item.get("title", "")
                    title_lower = title.lower()
                    if any(kw in title_lower for kw in keywords):
                        return NewsResult(
                            should_trade=False,
                            reason=f"Recent news ({int(minutes_ago)}min): {title[:80]}",
                            source="alphavantage",
                        )
            except (ValueError, TypeError):
                continue

        return NewsResult(should_trade=True, reason="No recent news (AlphaVantage)", source="alphavantage")

    except Exception:
        return None


def get_keywords(symbol: str) -> list[str]:
    """Get news keywords for a symbol."""
    return SYMBOL_KEYWORDS.get(symbol, [])


def clear_cache():
    """Clear the news cache (for testing)."""
    _cache.clear()
