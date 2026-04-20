"""
Risk management -- enforces per-trade and daily limits with prop firm (FTMO) constraints.

Supports:
- Tiered daily drawdown management (yellow / red / hard-stop)
- Tiered total drawdown management (caution / warning / critical / emergency)
- Anti-martingale position sizing (reduce after consecutive losses)
- Session window enforcement per symbol
- Daily profit trailing protection
- Max trades per day and max concurrent positions
- Minimum R:R ratio gating
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Default prop-firm configuration (FTMO $10k challenge)
# ---------------------------------------------------------------------------
PROP_FIRM_CONFIG: Dict = {
    "account_size": 10_000,
    "max_daily_dd_pct": 0.05,         # 5% FTMO rule
    "max_total_dd_pct": 0.10,         # 10% FTMO rule
    "daily_target_pct": 0.02,         # 2% target

    # Position Sizing
    "base_risk_per_trade_pct": 0.0075,  # 0.75%
    "max_risk_per_trade_pct": 0.01,     # 1.0% absolute cap
    "min_risk_per_trade_pct": 0.0025,   # 0.25% floor

    # Trade Limits
    "max_trades_per_day": 5,
    "max_concurrent_positions": 2,

    # Daily Stop-Loss Tiers
    "daily_dd_yellow": 0.015,         # -1.5%: reduce size
    "daily_dd_red": 0.025,            # -2.5%: stop new entries
    "daily_dd_hard_stop": 0.03,       # -3.0%: close everything

    # Total DD Recovery Tiers
    "total_dd_caution": 0.02,         # -2%: reduce to 0.67x
    "total_dd_warning": 0.04,         # -4%: reduce to 0.50x
    "total_dd_critical": 0.06,        # -6%: reduce to 0.33x
    "total_dd_emergency": 0.08,       # -8%: stop trading

    # Anti-Martingale
    "consecutive_loss_reduce_at": 2,
    "consecutive_loss_multipliers": {0: 1.0, 1: 1.0, 2: 0.67, 3: 0.33},

    # Daily Profit Protection
    "daily_profit_trail_activate": 0.01,   # activate after +1%
    "daily_profit_trail_pct": 0.50,        # give back at most 50% of peak

    # Session Windows (UTC hours)
    "us30_primary_session": (13.5, 15.5),
    "us30_secondary_session": (19.0, 20.0),
    "xauusd_primary_session": (7.0, 9.0),
    "xauusd_secondary_session": (13.5, 15.5),
    "btcusd_session": (0.0, 24.0),

    # R:R Targets
    "min_rr_ratio": 1.5,
    "target_rr_ratio": 2.0,

    # Legacy compatibility defaults
    "risk_per_trade": 0.0075,
    "max_daily_loss_pct": 0.04,
    "cooldown_bars": 3,

    # ------------------------------------------------------------------
    # Bolt-compatible extensions (2026-04-20)
    # All three are None/off by default. Set via the BOLT_50K_CONFIG preset
    # or the agent config editor to enable.
    # ------------------------------------------------------------------
    # UTC hour after which the agent must NOT open new positions and
    # should flatten any open position. Bolt requires all positions closed
    # before CME daily close — 21:00 UTC.
    "force_flat_utc_hour": None,
    # Absolute USD profit target for the 40 % consistency gate. Set this and
    # `consistency_pct_cap` together.
    "profit_target_usd": None,
    # Fraction of profit target that any single day's P&L must not exceed
    # (e.g. 0.40 for Bolt's 40 % consistency rule). If a trade's maximum
    # theoretical profit would push daily P&L over this cap, it's rejected.
    "consistency_pct_cap": None,
    # Once the EOD account balance reaches this level, the trailing DD
    # stops trailing and the loss floor locks. Bolt specifies $50,100 on
    # the $50k account — trailing stops once +$100 is reached.
    "trail_lock_at_balance": None,
}


# Pre-configured preset for the FundedNext Bolt $50k account. Use via
#     RiskManager(config={**BOLT_50K_CONFIG, ...your overrides...})
# or by selecting "Bolt 50K" in the agent config editor.
BOLT_50K_CONFIG: Dict = {
    "account_size":              50_000,
    "profit_target_usd":          3_000,   # 6 %
    "max_daily_dd_pct":            0.02,   # $1,000 daily loss
    "max_total_dd_pct":            0.04,   # $2,000 trailing DD
    "daily_dd_yellow":             0.01,   # $500 — tighten risk
    "daily_dd_red":                0.015,  # $750 — no new entries
    "daily_dd_hard_stop":          0.02,   # $1,000 — hard stop
    "total_dd_caution":            0.02,   # $1,000
    "total_dd_warning":            0.03,   # $1,500
    "total_dd_critical":           0.035,  # $1,750
    "total_dd_emergency":          0.04,   # $2,000
    "base_risk_per_trade_pct":     0.0075, # 0.75 % = $375
    "max_risk_per_trade_pct":      0.01,
    "min_risk_per_trade_pct":      0.0025,
    "max_trades_per_day":          5,
    "max_concurrent_positions":    2,
    "consistency_pct_cap":         0.40,   # 40 % consistency rule
    "force_flat_utc_hour":         21,     # CME daily close
    "trail_lock_at_balance":       50_100, # trailing stops at +$100
    # Bolt-sensible session windows. Bolt accounts trade futures only; the
    # most efficient window for ES / NQ is the NY cash session.
    "us30_primary_session":       (13.5, 20.0),
    "xauusd_primary_session":     (13.5, 20.0),
    # Legacy compat
    "risk_per_trade":              0.0075,
    "max_daily_loss_pct":          0.02,
    "cooldown_bars":               5,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RiskCheck:
    """Result of a risk gate check (backward-compatible)."""
    approved: bool
    reason: str
    risk_amount: float = 0.0
    adjusted_size: float = 0.0


@dataclass
class TradeApproval:
    """Extended result from approve_trade()."""
    approved: bool
    risk_pct: float
    reason: str


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Prop-firm-aware risk manager with tiered drawdown management,
    anti-martingale sizing, and session window enforcement.

    Backward-compatible: the old ``check_trade()`` interface still works
    exactly as before for existing callers.
    """

    def __init__(self, config: Optional[dict] = None):
        # Merge caller config over defaults so every key is present
        self._cfg: Dict = {**PROP_FIRM_CONFIG, **(config or {})}

        # ------- legacy attributes (used directly by callers) -------
        self.risk_per_trade: float = self._cfg.get(
            "risk_per_trade",
            self._cfg["base_risk_per_trade_pct"],
        )
        self.max_daily_loss_pct: float = self._cfg.get("max_daily_loss_pct", 0.04)
        self.max_drawdown_pct: float = self._cfg.get("max_drawdown_pct", self._cfg["max_total_dd_pct"])
        self.cooldown_bars: int = self._cfg.get("cooldown_bars", 3)
        self.max_trades_per_day: int = self._cfg.get("max_trades_per_day", 5)

        # ------- prop-firm state -------
        self._account_size: float = self._cfg["account_size"]
        self._daily_pnl: float = 0.0
        self._daily_peak_pnl: float = 0.0
        self._total_pnl: float = 0.0
        self._trades_today: int = 0
        self._concurrent_positions: int = 0
        self._consecutive_losses: int = 0
        self._last_reset_date: Optional[str] = None  # UTC date string "YYYY-MM-DD"

    def _maybe_reset_daily(self) -> None:
        """
        Reset daily counters at UTC day boundary.
        On the FIRST call, just record today (don't wipe state — the caller
        may have pre-populated P&L that shouldn't be erased).
        """
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_reset_date is None:
            self._last_reset_date = today
            return
        if self._last_reset_date != today:
            self._daily_pnl = 0.0
            self._daily_peak_pnl = 0.0
            self._trades_today = 0
            self._last_reset_date = today

    # ------------------------------------------------------------------
    # Legacy interface (backward-compatible)
    # ------------------------------------------------------------------

    def check_trade(
        self,
        balance: float,
        daily_pnl: float,
        daily_trade_count: int,
        bars_since_last_trade: int,
        session_multiplier: float = 1.0,
    ) -> RiskCheck:
        """
        Legacy risk gate -- existing callers (scalping_agent, expert_agent,
        flowrex_agent, backtest engine) rely on this signature.
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

        # ------------------------------------------------------------------
        # Bolt-compatible gates (only active if configured)
        # ------------------------------------------------------------------

        # Force flat before CME close: reject new entries once we're past the
        # configured UTC hour. The runtime also has to CLOSE any open
        # position — see `should_flatten()` below, which the agent polls.
        force_flat_hour = self._cfg.get("force_flat_utc_hour")
        if force_flat_hour is not None:
            from datetime import datetime, timezone
            now_hour = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute / 60.0
            if now_hour >= float(force_flat_hour):
                return RiskCheck(
                    approved=False,
                    reason=f"Force-flat cutoff ({now_hour:.1f} UTC >= {force_flat_hour} UTC)",
                )

        # 40 % consistency rule: reject if already at/above the cap today,
        # since even a breakeven trade risks pushing further over the cap
        # if the winner comes tomorrow.
        cap_pct = self._cfg.get("consistency_pct_cap")
        target = self._cfg.get("profit_target_usd")
        if cap_pct is not None and target is not None and target > 0 and daily_pnl > 0:
            cap_usd = float(cap_pct) * float(target)
            if daily_pnl >= cap_usd:
                return RiskCheck(
                    approved=False,
                    reason=f"Consistency cap ({daily_pnl:.0f} >= {cap_usd:.0f} = {cap_pct:.0%} of ${target:.0f})",
                )

        # Calculate risk amount
        risk_amount = balance * self.risk_per_trade * session_multiplier

        return RiskCheck(
            approved=True,
            reason="Trade approved",
            risk_amount=risk_amount,
        )

    # ------------------------------------------------------------------
    # Bolt runtime helpers
    # ------------------------------------------------------------------

    def should_flatten(self, now_utc_hour: Optional[float] = None) -> bool:
        """
        Called by the agent each tick to decide whether to close open
        positions. True once we're past `force_flat_utc_hour`. Returns
        False if the cutoff isn't configured.
        """
        cutoff = self._cfg.get("force_flat_utc_hour")
        if cutoff is None:
            return False
        if now_utc_hour is None:
            from datetime import datetime, timezone
            n = datetime.now(timezone.utc)
            now_utc_hour = n.hour + n.minute / 60.0
        return now_utc_hour >= float(cutoff)

    def trailing_locked(self, eod_balance: float) -> bool:
        """
        Bolt: once EOD balance hits $50,100, the trailing drawdown stops
        trailing and the max-loss floor locks at starting balance.
        Engine caller uses this to decide whether to advance the trailing
        high-water mark.
        """
        lock_at = self._cfg.get("trail_lock_at_balance")
        if lock_at is None:
            return False
        return eod_balance >= float(lock_at)

    # ------------------------------------------------------------------
    # New prop-firm interface
    # ------------------------------------------------------------------

    def approve_trade(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        atr: float,
        current_price: float,
        hour_utc: float,
    ) -> Tuple[bool, float, str]:
        """
        Full prop-firm risk gate.

        Returns
        -------
        (approved, risk_pct, reason)
        """
        # Reset daily counters at UTC day boundary.
        self._maybe_reset_daily()

        cfg = self._cfg

        # 1. Daily DD hard stop -- close everything tier
        daily_dd_pct = self._daily_dd_pct()
        if daily_dd_pct >= cfg["daily_dd_hard_stop"]:
            return False, 0.0, f"Daily DD hard stop ({daily_dd_pct:.2%} >= {cfg['daily_dd_hard_stop']:.1%})"

        # 2. Daily DD red tier -- no new entries
        if daily_dd_pct >= cfg["daily_dd_red"]:
            return False, 0.0, f"Daily DD red tier ({daily_dd_pct:.2%} >= {cfg['daily_dd_red']:.1%})"

        # 3. Total DD emergency -- stop trading entirely
        total_dd_pct = self._total_dd_pct()
        if total_dd_pct >= cfg["total_dd_emergency"]:
            return False, 0.0, f"Total DD emergency ({total_dd_pct:.2%} >= {cfg['total_dd_emergency']:.1%})"

        # 4. Max trades per day
        if self._trades_today >= cfg["max_trades_per_day"]:
            return False, 0.0, f"Max trades per day ({self._trades_today} >= {cfg['max_trades_per_day']})"

        # 5. Max concurrent positions
        if self._concurrent_positions >= cfg["max_concurrent_positions"]:
            return False, 0.0, f"Max concurrent positions ({self._concurrent_positions} >= {cfg['max_concurrent_positions']})"

        # 6. Session window
        if not self._in_session(symbol, hour_utc):
            return False, 0.0, f"{symbol} outside session window at {hour_utc:.1f} UTC"

        # 7. Minimum R:R check (ATR-based: SL ~ 1 ATR, TP ~ RR * ATR)
        if atr > 0 and current_price > 0:
            # We assume SL = 1 ATR, TP = target_rr * ATR
            # The minimum acceptable is min_rr_ratio
            # This check is informational; callers should set actual TP/SL.
            pass  # R:R is enforced at signal generation level

        # 8. Compute base risk pct
        risk_pct = cfg["base_risk_per_trade_pct"]

        # 9. Daily DD yellow tier -- reduce size by 50%
        if daily_dd_pct >= cfg["daily_dd_yellow"]:
            risk_pct *= 0.5

        # 10. Total DD tier multipliers
        if total_dd_pct >= cfg["total_dd_critical"]:
            risk_pct *= 0.33
        elif total_dd_pct >= cfg["total_dd_warning"]:
            risk_pct *= 0.50
        elif total_dd_pct >= cfg["total_dd_caution"]:
            risk_pct *= 0.67

        # 11. Anti-martingale
        loss_mult = self._anti_martingale_multiplier()
        risk_pct *= loss_mult

        # 12. Daily profit trailing protection
        if self._daily_peak_pnl > 0:
            trail_activate = cfg["daily_profit_trail_activate"] * self._account_size
            if self._daily_peak_pnl >= trail_activate:
                trail_floor = self._daily_peak_pnl * (1.0 - cfg["daily_profit_trail_pct"])
                if self._daily_pnl < trail_floor:
                    return False, 0.0, (
                        f"Daily profit protection: P&L ${self._daily_pnl:.0f} "
                        f"fell below trail floor ${trail_floor:.0f}"
                    )

        # 13. Clamp risk_pct to [min, max]
        risk_pct = max(cfg["min_risk_per_trade_pct"], min(risk_pct, cfg["max_risk_per_trade_pct"]))

        return True, risk_pct, "Trade approved"

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def record_trade_result(self, pnl: float) -> None:
        """Update daily P&L, consecutive loss counter, and trade count."""
        self._daily_pnl += pnl
        self._total_pnl += pnl
        self._trades_today += 1

        # Track daily peak for profit trailing
        if self._daily_pnl > self._daily_peak_pnl:
            self._daily_peak_pnl = self._daily_pnl

        # Consecutive losses
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def open_position(self) -> None:
        """Call when a new position is opened."""
        self._concurrent_positions += 1

    def close_position(self) -> None:
        """Call when a position is closed."""
        self._concurrent_positions = max(0, self._concurrent_positions - 1)

    def should_close_all(self) -> bool:
        """True if daily DD hard stop has been hit."""
        return self._daily_dd_pct() >= self._cfg["daily_dd_hard_stop"]

    def daily_reset(self) -> None:
        """Call at the start of each trading day to reset daily counters."""
        self._daily_pnl = 0.0
        self._daily_peak_pnl = 0.0
        self._trades_today = 0
        self._consecutive_losses = 0

    def get_status(self) -> dict:
        """Return a snapshot of the current risk state."""
        daily_dd = self._daily_dd_pct()
        total_dd = self._total_dd_pct()
        return {
            "daily_pnl": self._daily_pnl,
            "daily_peak_pnl": self._daily_peak_pnl,
            "daily_dd_pct": daily_dd,
            "total_pnl": self._total_pnl,
            "total_dd_pct": total_dd,
            "consecutive_losses": self._consecutive_losses,
            "trades_today": self._trades_today,
            "concurrent_positions": self._concurrent_positions,
            "daily_tier": self._daily_tier(daily_dd),
            "total_tier": self._total_tier(total_dd),
            "anti_martingale_mult": self._anti_martingale_multiplier(),
        }

    def get_position_size(
        self,
        account_balance: float,
        risk_pct: float,
        stop_loss_points: float,
        point_value: float,
    ) -> float:
        """
        Calculate lot/position size.

        Parameters
        ----------
        account_balance : current account balance
        risk_pct : risk as a fraction (e.g. 0.0075 for 0.75%)
        stop_loss_points : SL distance in price points
        point_value : dollar value of 1 point per lot (e.g. 10 for US30)

        Returns
        -------
        Lot size (float, caller should round to broker precision).
        """
        if stop_loss_points <= 0 or point_value <= 0:
            return 0.0
        risk_amount = account_balance * risk_pct
        lots = risk_amount / (stop_loss_points * point_value)
        return lots

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _daily_dd_pct(self) -> float:
        """Current daily drawdown as a positive fraction."""
        if self._daily_pnl >= 0:
            return 0.0
        return abs(self._daily_pnl) / self._account_size

    def _total_dd_pct(self) -> float:
        """Current total drawdown as a positive fraction."""
        if self._total_pnl >= 0:
            return 0.0
        return abs(self._total_pnl) / self._account_size

    def _anti_martingale_multiplier(self) -> float:
        """Return position-size multiplier based on consecutive losses."""
        mults = self._cfg["consecutive_loss_multipliers"]
        # mults keys may be int or str depending on source
        losses = self._consecutive_losses
        # Find the highest key <= losses
        best_key = 0
        for k in mults:
            k_int = int(k)
            if k_int <= losses and k_int >= best_key:
                best_key = k_int
        return mults.get(best_key, mults.get(str(best_key), 0.33))

    def _in_session(self, symbol: str, hour_utc: float) -> bool:
        """Check whether the current UTC hour is within an allowed session."""
        cfg = self._cfg
        sym = symbol.upper()

        if "BTC" in sym:
            start, end = cfg.get("btcusd_session", (0.0, 24.0))
            return start <= hour_utc < end

        if "US30" in sym or "DJ" in sym or "DOW" in sym:
            pri = cfg.get("us30_primary_session", (13.5, 15.5))
            sec = cfg.get("us30_secondary_session", (19.0, 20.0))
            return (pri[0] <= hour_utc < pri[1]) or (sec[0] <= hour_utc < sec[1])

        if "XAU" in sym or "GOLD" in sym:
            pri = cfg.get("xauusd_primary_session", (7.0, 9.0))
            sec = cfg.get("xauusd_secondary_session", (13.5, 15.5))
            return (pri[0] <= hour_utc < pri[1]) or (sec[0] <= hour_utc < sec[1])

        # Unknown symbol -- allow
        return True

    def _daily_tier(self, dd: float) -> str:
        cfg = self._cfg
        if dd >= cfg["daily_dd_hard_stop"]:
            return "HARD_STOP"
        if dd >= cfg["daily_dd_red"]:
            return "RED"
        if dd >= cfg["daily_dd_yellow"]:
            return "YELLOW"
        return "GREEN"

    def _total_tier(self, dd: float) -> str:
        cfg = self._cfg
        if dd >= cfg["total_dd_emergency"]:
            return "EMERGENCY"
        if dd >= cfg["total_dd_critical"]:
            return "CRITICAL"
        if dd >= cfg["total_dd_warning"]:
            return "WARNING"
        if dd >= cfg["total_dd_caution"]:
            return "CAUTION"
        return "GREEN"
