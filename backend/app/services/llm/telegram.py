"""
Telegram notifications for FlowrexAlgo.

Two modes supported:
1. **Central bot** (preferred): set TELEGRAM_BOT_TOKEN env var. Users connect
   by running `/start <code>` in the bot chat. Messages go to their chat_id.
2. **Per-user bot**: user supplies their own bot_token in Settings. Stored
   encrypted. Mostly backward-compat for early adopters.

The notifier picks the token in this order for each send:
- User's personal token (if set) with their chat_id
- Global TELEGRAM_BOT_TOKEN with their chat_id
"""
import httpx
from typing import Optional

from app.core.config import settings


TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """
    Per-user telegram notifier. Legacy singleton API preserved via `configure`
    for the already-connected admin account; new users route through `send_to_user`
    which reads chat_id from DB and uses the global bot token by default.
    """

    def __init__(self):
        # Legacy singleton state (for already-configured admin user)
        self._bot_token: Optional[str] = None
        self._chat_id: Optional[str] = None

    def configure(self, bot_token: str, chat_id: str):
        """Legacy: set Telegram bot credentials for the default singleton."""
        self._bot_token = bot_token
        self._chat_id = chat_id

    @property
    def is_configured(self) -> bool:
        return bool(self._bot_token and self._chat_id) or bool(settings.TELEGRAM_BOT_TOKEN)

    # ── Raw send ──────────────────────────────────────────────────────

    async def _raw_send(self, bot_token: str, chat_id: str, message: str,
                        parse_mode: str = "HTML") -> bool:
        """Low-level send via Telegram Bot API."""
        if not bot_token or not chat_id:
            return False
        # Telegram limits messages to 4096 chars; truncate long ones.
        if len(message) > 4000:
            message = message[:3997] + "..."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code != 200:
                    return False
                return True
        except Exception:
            return False

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Legacy: send to the singleton's configured chat."""
        token = self._bot_token or settings.TELEGRAM_BOT_TOKEN
        return await self._raw_send(token, self._chat_id or "", message, parse_mode)

    async def send_to_user(self, chat_id: str, message: str,
                           user_bot_token: Optional[str] = None,
                           parse_mode: str = "HTML") -> bool:
        """
        Send a message to a specific user's chat_id.
        Prefers user_bot_token (legacy per-user mode) if provided,
        else falls back to the global TELEGRAM_BOT_TOKEN.
        """
        token = user_bot_token or settings.TELEGRAM_BOT_TOKEN
        if not token or not chat_id:
            return False
        return await self._raw_send(token, chat_id, message, parse_mode)

    # ── High-level notifications (legacy singleton) ───────────────────

    async def notify_trade_opened(self, trade: dict):
        msg = _format_trade_opened(trade)
        await self.send(msg)

    async def notify_trade_closed(self, trade: dict):
        msg = _format_trade_closed(trade)
        await self.send(msg)

    async def notify_alert(self, alert: str):
        await self.send(f"⚠️ <b>ALERT</b>\n{alert}")

    async def notify_daily_summary(self, summary: dict):
        await self.send(_format_daily_summary(summary))


# ── Formatters (shared between legacy and per-user paths) ─────────────

def _format_trade_opened(trade: dict) -> str:
    direction = trade.get("direction", "?")
    symbol = trade.get("symbol", "?")
    return (
        f"<b>🎯 NEW TRADE</b>\n"
        f"{'🟢' if direction == 'BUY' else '🔴'} {direction} {symbol}\n"
        f"Entry: {trade.get('entry_price', 0)}\n"
        f"SL: {trade.get('stop_loss', 0)} | TP: {trade.get('take_profit', 0)}\n"
        f"Size: {trade.get('lot_size', 0)} | Conf: {trade.get('confidence', 0):.3f}"
    )


def _format_trade_closed(trade: dict) -> str:
    direction = trade.get("direction", "?")
    symbol = trade.get("symbol", "?")
    pnl = trade.get("pnl", 0) or 0
    reason = trade.get("exit_reason", "?")
    emoji = "✅" if pnl >= 0 else "❌"
    sign = "+" if pnl >= 0 else ""
    return (
        f"{emoji} <b>TRADE CLOSED</b>\n"
        f"{direction} {symbol}\n"
        f"P&L: {sign}${pnl:.2f}\n"
        f"Reason: {reason}"
    )


def _format_daily_summary(summary: dict) -> str:
    pnl = summary.get("total_pnl", 0) or 0
    trades = summary.get("trade_count", 0)
    wr = summary.get("win_rate", 0)
    emoji = "📈" if pnl >= 0 else "📉"
    sign = "+" if pnl >= 0 else ""
    return (
        f"{emoji} <b>DAILY SUMMARY</b>\n"
        f"P&L: {sign}${pnl:.2f}\n"
        f"Trades: {trades}\n"
        f"Win Rate: {wr:.1f}%"
    )


# Singleton (legacy; new code should use send_to_user directly)
_notifier: Optional[TelegramNotifier] = None


def get_telegram_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
