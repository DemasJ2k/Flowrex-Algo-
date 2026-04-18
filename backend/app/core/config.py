from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./flowrex_algo.db"
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    DEBUG: bool = False  # MUST be False in production — True bypasses auth
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]
    ENCRYPTION_KEY: str = ""

    # Oanda
    OANDA_API_KEY: str = ""
    OANDA_ACCOUNT_ID: str = ""
    OANDA_PRACTICE: bool = True

    # cTrader
    CTRADER_CLIENT_ID: str = ""
    CTRADER_CLIENT_SECRET: str = ""
    CTRADER_ACCESS_TOKEN: str = ""
    CTRADER_ACCOUNT_ID: str = ""
    CTRADER_REFRESH_TOKEN: str = ""

    # MT5
    MT5_PATH: str = ""
    MT5_LOGIN: str = ""
    MT5_PASSWORD: str = ""
    MT5_SERVER: str = ""

    # News / Data APIs
    FINNHUB_API_KEY: str = ""
    ALPHAVANTAGE_API_KEY: str = ""
    NEWSAPI_API_KEY: str = ""

    # Monitoring
    SENTRY_DSN: str = ""  # Sentry error tracking — set in .env for production
    LOG_LEVEL: str = ""   # DEBUG / INFO / WARNING / ERROR — overrides DEBUG-based default

    # Telegram — central bot for all users (users connect via /connect flow)
    TELEGRAM_BOT_TOKEN: str = ""  # Global bot token (from BotFather)
    TELEGRAM_BOT_USERNAME: str = "FlowrexAlgoBot"  # Without @ prefix
    TELEGRAM_WEBHOOK_SECRET: str = ""  # Validates Telegram webhook authenticity
    APP_BASE_URL: str = "https://flowrexalgo.com"  # Used for webhook URL

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
