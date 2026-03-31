"""
Risk management — enforces per-trade and daily limits.
"""
from dataclasses import dataclass


@dataclass
class RiskCheck:
    approved: bool
    reason: str
    risk_amount: float = 0.0
    adjusted_size: float = 0.0


class RiskManager:
    """
    Enforces:
    - Per-trade risk limit (default 0.5%)
    - Daily loss limit (default 4%)
    - Max drawdown limit (default 8%)
    - Cooldown between trades
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.risk_per_trade = cfg.get("risk_per_trade", 0.005)
        self.max_daily_loss_pct = cfg.get("max_daily_loss_pct", 0.04)
        self.max_drawdown_pct = cfg.get("max_drawdown_pct", 0.08)
        self.cooldown_bars = cfg.get("cooldown_bars", 3)
        self.max_trades_per_day = cfg.get("max_trades_per_day", 20)

    def check_trade(
        self,
        balance: float,
        daily_pnl: float,
        daily_trade_count: int,
        bars_since_last_trade: int,
        session_multiplier: float = 1.0,
    ) -> RiskCheck:
        """
        Check whether a new trade is allowed.
        Returns RiskCheck with approval status and reason.
        """
        if balance <= 0:
            return RiskCheck(approved=False, reason="Zero or negative balance")

        # Daily loss limit
        daily_loss_pct = abs(daily_pnl) / balance if daily_pnl < 0 else 0
        if daily_loss_pct >= self.max_daily_loss_pct:
            return RiskCheck(
                approved=False,
                reason=f"Daily loss limit hit ({daily_loss_pct:.1%} >= {self.max_daily_loss_pct:.1%})",
            )

        # Daily trade count limit
        if daily_trade_count >= self.max_trades_per_day:
            return RiskCheck(
                approved=False,
                reason=f"Daily trade limit hit ({daily_trade_count} >= {self.max_trades_per_day})",
            )

        # Cooldown
        if bars_since_last_trade < self.cooldown_bars:
            return RiskCheck(
                approved=False,
                reason=f"Cooldown active ({bars_since_last_trade}/{self.cooldown_bars} bars)",
            )

        # Calculate risk amount
        risk_amount = balance * self.risk_per_trade * session_multiplier

        return RiskCheck(
            approved=True,
            reason="Trade approved",
            risk_amount=risk_amount,
        )
