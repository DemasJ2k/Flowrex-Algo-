"""
Claude AI Supervisor — event-driven LLM monitoring for trading agents.

Triggers:
  - Trade executed/closed → analyze quality
  - 3+ consecutive losses → recommend pause
  - Errors → diagnose
  - Hourly summary → health check
  - User chat → full context analysis

Autonomous actions: PAUSE_AGENT, STOP_AGENT, ADJUST_RISK, SEND_ALERT, LOG_RECOMMENDATION
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import httpx


MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-5-20241022",
    "opus": "claude-opus-4-20250514",
}

SYSTEM_PROMPT = """You are the AI Supervisor for FlowrexAlgo, an autonomous algorithmic trading platform.

Your responsibilities:
1. Monitor trading agent performance and health
2. Analyze trade quality and patterns
3. Detect issues (drawdown, losing streaks, errors)
4. Recommend or execute actions (pause agent, adjust risk)
5. Answer user questions about trading performance

You have access to:
- Agent configurations (symbol, risk settings, model grades)
- Recent trade history (last 50 trades with P&L)
- Daily P&L summary
- Agent logs (last 100 entries)
- Market regime indicators

When analyzing trades, consider:
- Win rate trends
- Risk/reward ratios
- Session performance (London, NY, Asian)
- Drawdown levels vs limits
- Model confidence patterns

Available autonomous actions (use JSON format):
- {"action": "PAUSE_AGENT", "agent_id": <id>, "reason": "<why>"}
- {"action": "ADJUST_RISK", "agent_id": <id>, "risk_per_trade": <pct>, "reason": "<why>"}
- {"action": "SEND_ALERT", "message": "<alert text>"}
- {"action": "LOG_RECOMMENDATION", "recommendation": "<text>"}

Only recommend PAUSE_AGENT after 3+ consecutive losses or drawdown > 50% of limit.
Be concise and data-driven. Use markdown formatting."""


class LLMSupervisor:
    """Event-driven AI supervisor using Claude API."""

    def __init__(self):
        self._api_key: Optional[str] = None
        self._model: str = MODELS["haiku"]
        self._enabled: bool = False
        self._autonomous: bool = False
        self._conversation: list[dict] = []
        self._max_history = 20
        self._consecutive_losses: dict[int, int] = {}  # agent_id -> count
        self._hourly_task: Optional[asyncio.Task] = None

    def configure(self, api_key: str, model: str = "haiku",
                  enabled: bool = True, autonomous: bool = False):
        """Configure the supervisor with API credentials."""
        self._api_key = api_key
        self._model = MODELS.get(model, MODELS["haiku"])
        self._enabled = enabled
        self._autonomous = autonomous
        self._conversation = []

    @property
    def is_enabled(self) -> bool:
        return self._enabled and bool(self._api_key)

    async def on_trade_opened(self, trade_data: dict, agent_data: dict):
        """Called when a new trade is opened."""
        if not self.is_enabled:
            return None

        prompt = (
            f"New trade opened:\n"
            f"- Agent: {agent_data.get('symbol')} ({agent_data.get('agent_type')})\n"
            f"- Direction: {trade_data.get('direction')}\n"
            f"- Entry: {trade_data.get('entry_price')}\n"
            f"- SL: {trade_data.get('stop_loss')} | TP: {trade_data.get('take_profit')}\n"
            f"- Confidence: {trade_data.get('confidence', 0):.3f}\n"
            f"- Lot size: {trade_data.get('lot_size')}\n\n"
            f"Briefly assess trade quality (1-2 sentences)."
        )
        return await self._query(prompt)

    async def on_trade_closed(self, trade_data: dict, agent_data: dict):
        """Called when a trade is closed (TP/SL/timeout)."""
        if not self.is_enabled:
            return None

        agent_id = agent_data.get("id", 0)
        pnl = trade_data.get("pnl", 0)
        exit_reason = trade_data.get("exit_reason", "unknown")

        # Track consecutive losses
        if pnl < 0:
            self._consecutive_losses[agent_id] = self._consecutive_losses.get(agent_id, 0) + 1
        else:
            self._consecutive_losses[agent_id] = 0

        losses = self._consecutive_losses.get(agent_id, 0)

        prompt = (
            f"Trade closed:\n"
            f"- Agent: {agent_data.get('symbol')} ({agent_data.get('agent_type')})\n"
            f"- Direction: {trade_data.get('direction')}\n"
            f"- Entry: {trade_data.get('entry_price')} -> Exit: {trade_data.get('exit_price')}\n"
            f"- P&L: ${pnl:.2f} | Reason: {exit_reason}\n"
            f"- Consecutive losses: {losses}\n"
        )

        if losses >= 3:
            prompt += (
                f"\n**WARNING**: {losses} consecutive losses detected.\n"
                f"Should we pause this agent? Autonomous mode: {self._autonomous}\n"
                f"If yes, respond with the PAUSE_AGENT action JSON."
            )

        return await self._query(prompt)

    async def on_error(self, error_msg: str, agent_data: dict):
        """Called on agent errors."""
        if not self.is_enabled:
            return None

        prompt = (
            f"Agent error detected:\n"
            f"- Agent: {agent_data.get('symbol')} ({agent_data.get('agent_type')})\n"
            f"- Error: {error_msg}\n\n"
            f"Diagnose the likely cause and suggest a fix (2-3 sentences)."
        )
        return await self._query(prompt)

    async def chat(self, user_message: str, context: dict = None) -> str:
        """Handle user chat query with full trading context."""
        if not self.is_enabled:
            return "AI Supervisor is not enabled. Configure your API key in Settings > AI Supervisor."

        context_block = ""
        if context:
            if context.get("agents"):
                context_block += "\n**Active Agents:**\n"
                for a in context["agents"]:
                    context_block += (
                        f"- {a['symbol']} ({a['agent_type']}) | "
                        f"Status: {a['status']} | "
                        f"Models: {a.get('models', 'N/A')}\n"
                    )

            if context.get("recent_trades"):
                context_block += "\n**Recent Trades (last 50):**\n"
                for t in context["recent_trades"][:50]:
                    pnl = t.get("pnl", 0)
                    emoji = "+" if pnl >= 0 else ""
                    context_block += (
                        f"- {t['direction']} {t['symbol']} | "
                        f"P&L: {emoji}${pnl:.2f} | {t.get('exit_reason', 'open')}\n"
                    )

            if context.get("daily_summary"):
                s = context["daily_summary"]
                context_block += (
                    f"\n**Today's Summary:**\n"
                    f"- Total P&L: ${s.get('total_pnl', 0):.2f}\n"
                    f"- Trades: {s.get('trade_count', 0)}\n"
                    f"- Win Rate: {s.get('win_rate', 0):.1f}%\n"
                )

        prompt = f"{context_block}\n\n**User Question:** {user_message}"
        response = await self._query(prompt)
        return response or "I couldn't generate a response. Please check your API key."

    async def hourly_health_check(self, context: dict) -> Optional[str]:
        """Hourly health check of all agents."""
        if not self.is_enabled:
            return None

        prompt = (
            f"Hourly health check.\n\n"
            f"Active agents: {json.dumps(context.get('agents', []), indent=2)}\n"
            f"Today's P&L: ${context.get('daily_pnl', 0):.2f}\n"
            f"Open positions: {context.get('open_positions', 0)}\n\n"
            f"Give a brief status report (3-5 bullet points). "
            f"Flag any concerns."
        )
        return await self._query(prompt)

    async def _query(self, user_prompt: str) -> Optional[str]:
        """Send a message to Claude API and return the response."""
        if not self._api_key:
            return None

        # Maintain conversation history (sliding window)
        self._conversation.append({"role": "user", "content": user_prompt})
        if len(self._conversation) > self._max_history * 2:
            self._conversation = self._conversation[-self._max_history * 2:]

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "max_tokens": 1024,
                        "system": SYSTEM_PROMPT,
                        "messages": self._conversation,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data.get("content", [{}])
                reply = content[0].get("text", "") if content else ""

                self._conversation.append({"role": "assistant", "content": reply})
                return reply

        except httpx.HTTPStatusError as e:
            return f"API error: {e.response.status_code} — {e.response.text[:200]}"
        except Exception as e:
            return f"Error: {str(e)}"

    def parse_actions(self, response: str) -> list[dict]:
        """Extract autonomous action JSON blocks from supervisor response."""
        if not self._autonomous or not response:
            return []

        actions = []
        import re
        # Find JSON blocks in response
        for match in re.finditer(r'\{[^{}]*"action"\s*:\s*"[^"]+?"[^{}]*\}', response):
            try:
                action = json.loads(match.group())
                if "action" in action:
                    actions.append(action)
            except json.JSONDecodeError:
                continue
        return actions

    def clear_history(self):
        """Clear conversation history."""
        self._conversation = []


# Singleton
_supervisor: Optional[LLMSupervisor] = None


def get_supervisor() -> LLMSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = LLMSupervisor()
    return _supervisor
