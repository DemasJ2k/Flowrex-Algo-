"""
Telegram notifications for FlowrexAlgo.

Uses raw httpx to Telegram Bot API (no extra dependencies).
Messages: trade executions, alerts, daily summaries.
"""
import httpx
from typing import Optional


TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """Send notifications via Telegram Bot API."""

    def __init__(self):
        self._bot_token: Optional[str] = None
        self._chat_id: Optional[str] = None

    def configure(self, bot_token: str, chat_id: str):
        """Set Telegram bot credentials."""
        self._bot_token = bot_token
        self._chat_id = chat_id

    @property
    def is_configured(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured Telegram chat."""
        if not self.is_configured:
            return False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def notify_trade_opened(self, trade: dict):
        """Send trade opened notification."""
        direction = trade.get("direction", "?")
        symbol = trade.get("symbol", "?")
        entry = trade.get("entry_price", 0)
        sl = trade.get("stop_loss", 0)
        tp = trade.get("take_profit", 0)
        conf = trade.get("confidence", 0)
        lots = trade.get("lot_size", 0)

        msg = (
            f"<b>NEW TRADE</b>\n"
            f"{'🟢' if direction == 'BUY' else '🔴'} {direction} {symbol}\n"
            f"Entry: {entry}\n"
            f"SL: {sl} | TP: {tp}\n"
            f"Size: {lots} | Conf: {conf:.3f}"
        )
        await self.send(msg)

    async def notify_trade_closed(self, trade: dict):
        """Send trade closed notification."""
        direction = trade.get("direction", "?")
        symbol = trade.get("symbol", "?")
        pnl = trade.get("pnl", 0)
        reason = trade.get("exit_reason", "?")

        emoji = "✅" if pnl >= 0 else "❌"
        sign = "+" if pnl >= 0 else ""

        msg = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"{direction} {symbol}\n"
            f"P&L: {sign}${pnl:.2f}\n"
            f"Reason: {reason}"
        )
        await self.send(msg)

    async def notify_alert(self, alert: str):
        """Send alert notification."""
        msg = f"⚠️ <b>ALERT</b>\n{alert}"
        await self.send(msg)

    async def notify_daily_summary(self, summary: dict):
        """Send daily performance summary."""
        pnl = summary.get("total_pnl", 0)
        trades = summary.get("trade_count", 0)
        wr = summary.get("win_rate", 0)
        emoji = "📈" if pnl >= 0 else "📉"
        sign = "+" if pnl >= 0 else ""

        msg = (
            f"{emoji} <b>DAILY SUMMARY</b>\n"
            f"P&L: {sign}${pnl:.2f}\n"
            f"Trades: {trades}\n"
            f"Win Rate: {wr:.1f}%"
        )
        await self.send(msg)


# Singleton
_notifier: Optional[TelegramNotifier] = None


def get_telegram_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
