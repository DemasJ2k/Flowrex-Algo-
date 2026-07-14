"""
SwingAgent — rules-first H1/H4/D1 swing agent (2026-07-13 reorg, Phase 2).

No ML model. Every decision comes from app/services/agent/swing_rules.py,
the same module scripts/backtest_swing.py simulates with — backtest-live
parity by construction.

The engine feeds this agent H1 bars (primary_timeframe = "H1"); the agent
fetches H4 + D1 context itself through the broker adapter and caches it.
Positions are expected to hold overnight (default_max_hold_hours = 240 —
ten days — instead of the intraday default of 24h).
"""
import logging
import time as _wall_time
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from app.services.agent.filters import classify_session
from app.services.agent.swing_rules import (
    SwingConfig, ZoneTracker, check_entry, d1_trend, h4_atr_series,
)

logger = logging.getLogger("flowrex.swing_agent")


class SwingAgent:
    """Rules-based swing agent. Interface-compatible with PotentialAgent."""

    primary_timeframe = "H1"
    default_max_hold_hours = 240   # 10 days — swing trades hold overnight

    MIN_H1_BARS = 30
    H4_FETCH = 320    # zone lookback 250 + ATR/EMA warmup
    D1_FETCH = 60     # EMA(50) warmup + 10

    def __init__(self, agent_id: int, symbol: str, broker_name: str, config: dict):
        self.agent_id = agent_id
        self.symbol = symbol
        self.broker_name = broker_name
        self.config = config or {}
        self.rules = SwingConfig.from_dict(self.config)

        self.risk_config = {
            "risk_per_trade_pct": self.config.get("risk_per_trade", 0.005),
            "daily_loss_limit_pct": self.config.get("max_daily_loss_pct", 0.03),
            "max_trades_per_day": self.config.get("max_trades_per_day", 2),
            "max_drawdown_pct": self.config.get("max_drawdown_pct", 0.10),
        }
        self.allow_buy = bool(self.config.get("allow_buy", True))
        self.allow_sell = bool(self.config.get("allow_sell", True))
        self.news_filter = bool(self.config.get("news_filter", False))

        self._log_fn = None
        self._risk_manager = None      # engine probes this for should_flatten()
        self._eval_count = 0
        self._signal_count = 0
        self._peak_equity = 0.0
        self._last_trade_wall_time = 0.0
        # H4/D1 context cache (broker fetches once per hour at most)
        self._h4_bars: list[dict] = []
        self._d1_bars: list[dict] = []
        self._htf_fetch_time = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────

    def load(self) -> bool:
        """Rules-based — nothing to load from disk. Always ready."""
        return True

    def reload_config(self, config: dict):
        self.__init__(self.agent_id, self.symbol, self.broker_name, config)

    # ── Evaluation ─────────────────────────────────────────────────────

    async def evaluate(
        self,
        m5_bars: list[dict],       # engine's primary-bar buffer — H1 for this agent
        broker_adapter=None,
        balance: float = 10000.0,
        daily_pnl: float = 0.0,
        daily_trade_count: int = 0,
        current_bar_index: int = 0,
    ) -> Optional[dict]:
        self._eval_count += 1
        h1_bars = m5_bars

        if len(h1_bars) < self.MIN_H1_BARS:
            self._log_reject("Insufficient H1 bars", len(h1_bars))
            return None

        # Cooldown — wall time so a restart doesn't reset it
        cooldown_sec = self.rules.cooldown_h1_bars * 3600
        if self._last_trade_wall_time == 0.0:
            self._last_trade_wall_time = self._load_last_trade_time_from_db()
        if self._last_trade_wall_time > 0 and (_wall_time.time() - self._last_trade_wall_time) < cooldown_sec:
            self._log_reject("Cooldown")
            return None

        if self.news_filter:
            try:
                from app.services.news.newsapi_provider import check_high_impact_news
                news = check_high_impact_news(self.symbol)
                if not news.should_trade:
                    self._log_reject(f"News filter: {news.reason}")
                    return None
            except Exception as e:
                self._log("warn", f"News filter unavailable: {e}")

        if not self._check_risk(balance, daily_pnl, daily_trade_count):
            return None

        # H4 + D1 context
        await self._refresh_htf(broker_adapter)
        if len(self._h4_bars) < self.rules.atr_period + self.rules.displacement_span + 5:
            self._log_reject("Insufficient H4 bars", len(self._h4_bars))
            return None
        if len(self._d1_bars) < self.rules.d1_ema_period + 1:
            self._log_reject("Insufficient D1 bars", len(self._d1_bars))
            return None

        h4_h = np.array([b["high"] for b in self._h4_bars], dtype=np.float64)
        h4_l = np.array([b["low"] for b in self._h4_bars], dtype=np.float64)
        h4_c = np.array([b["close"] for b in self._h4_bars], dtype=np.float64)
        h4_o = np.array([b["open"] for b in self._h4_bars], dtype=np.float64)
        atr_h4 = h4_atr_series(h4_h, h4_l, h4_c, self.rules.atr_period)

        tracker = ZoneTracker(self.rules)
        for k in range(len(h4_c)):
            tracker.on_h4_bar(h4_o[k], h4_h[k], h4_l[k], h4_c[k], atr_h4[k])

        d1_c = np.array([b["close"] for b in self._d1_bars], dtype=np.float64)
        trend = d1_trend(d1_c, self.rules.d1_ema_period, self.rules.d1_slope_bars)
        if trend == 0:
            self._log_reject("No D1 trend (insufficient history)")
            return None
        if trend > 0 and not self.allow_buy:
            self._log_reject("BUY direction disabled")
            return None
        if trend < 0 and not self.allow_sell:
            self._log_reject("SELL direction disabled")
            return None

        h1_o = np.array([b["open"] for b in h1_bars], dtype=np.float64)
        h1_h = np.array([b["high"] for b in h1_bars], dtype=np.float64)
        h1_l = np.array([b["low"] for b in h1_bars], dtype=np.float64)
        h1_c = np.array([b["close"] for b in h1_bars], dtype=np.float64)

        atr_now = float(atr_h4[-1])
        sig = check_entry(
            h1_o, h1_h, h1_l, h1_c, len(h1_c) - 1,
            tracker, trend, atr_now, self.rules,
        )
        if sig is None:
            self._log_reject("No rule signal")
            return None

        return self._build_signal(sig, balance, atr_now)

    # ── Signal assembly ────────────────────────────────────────────────

    def _build_signal(self, sig: dict, balance: float, atr_value: float) -> Optional[dict]:
        from app.services.agent.instrument_specs import calc_lot_size, get_oanda_price_decimals

        direction_str = "BUY" if sig["direction"] == 1 else "SELL"
        entry_price = float(sig["entry_ref"])
        sl_distance = float(sig["sl_dist"])

        risk_amount = balance * self.risk_config["risk_per_trade_pct"]
        lot_size = calc_lot_size(self.symbol, risk_amount, sl_distance, self.broker_name)
        lot_size = max(1, int(round(lot_size)))
        max_lot_size = self.config.get("max_lot_size")
        if max_lot_size and lot_size > max_lot_size:
            lot_size = int(max_lot_size)

        decimals = get_oanda_price_decimals(self.symbol) if self.broker_name == "oanda" else 2
        stop_loss = round(float(sig["stop_loss"]), decimals)
        take_profit = round(float(sig["take_profit"]), decimals)

        signal_dict = {
            "direction": direction_str,
            "confidence": float(sig["confidence"]),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "lot_size": lot_size,
            "reason": sig["reason"],
            "atr": atr_value,
            "agent_type": "swing",
            # Analytics enrichment
            "session_name": classify_session(datetime.now(timezone.utc).hour),
            "atr_at_entry": atr_value,
            "model_name": sig["reason"],
            "zone": {
                "kind": sig["zone_kind"], "source": sig["zone_source"],
                "top": sig["zone_top"], "bottom": sig["zone_bottom"],
            },
        }
        self._signal_count += 1
        self._last_trade_wall_time = _wall_time.time()
        self._log("signal",
                  f"{direction_str} {self.symbol} @ {entry_price:.{2}f} | "
                  f"SL:{stop_loss} TP:{take_profit} | Lots:{lot_size} | "
                  f"{sig['reason']}", signal_dict)
        return signal_dict

    # ── Context / risk helpers ─────────────────────────────────────────

    async def _refresh_htf(self, broker_adapter):
        """Fetch H4 + D1 bars, cached for 30 minutes."""
        now = _wall_time.time()
        if broker_adapter is None:
            return
        if now - self._htf_fetch_time < 1800 and self._h4_bars and self._d1_bars:
            return
        try:
            for tf, attr, count in (("H4", "_h4_bars", self.H4_FETCH), ("D1", "_d1_bars", self.D1_FETCH)):
                candles = await broker_adapter.get_candles(self.symbol, tf, count)
                if candles:
                    setattr(self, attr, [
                        {"time": c.time, "open": c.open, "high": c.high,
                         "low": c.low, "close": c.close, "volume": c.volume}
                        for c in candles
                    ])
            self._htf_fetch_time = now
        except Exception as e:
            self._log("warn", f"HTF fetch failed: {e}")

    def _check_risk(self, balance: float, daily_pnl: float, daily_trade_count: int) -> bool:
        if daily_trade_count >= self.risk_config["max_trades_per_day"]:
            self._log_reject("Daily trade limit reached")
            return False
        daily_limit = balance * self.risk_config["daily_loss_limit_pct"]
        if daily_pnl < -daily_limit:
            self._log_reject(f"Daily loss limit ({daily_pnl:.2f} < -{daily_limit:.2f})")
            return False
        equity = balance + daily_pnl
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            dd = (self._peak_equity - equity) / self._peak_equity
            if dd > self.risk_config["max_drawdown_pct"]:
                self._log_reject(f"Max DD exceeded ({dd*100:.1f}%)")
                return False
        return True

    def _load_last_trade_time_from_db(self) -> float:
        try:
            from app.core.database import SessionLocal
            from app.models.agent import AgentTrade
            db = SessionLocal()
            try:
                t = (
                    db.query(AgentTrade)
                    .filter(AgentTrade.agent_id == self.agent_id)
                    .order_by(AgentTrade.entry_time.desc())
                    .first()
                )
                return t.entry_time.timestamp() if t and t.entry_time else 0.0
            finally:
                db.close()
        except Exception:
            return 0.0

    # ── Engine hooks ───────────────────────────────────────────────────

    def on_position_opened(self):
        pass

    def on_position_closed(self, pnl: float):
        pass

    # ── Logging ────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str, data: dict = None):
        if self._log_fn:
            try:
                self._log_fn(level, msg, data)
                return
            except Exception:
                pass
        getattr(logger, "info" if level in ("signal", "info") else "warning")(msg)

    def _log_reject(self, reason: str, detail=None):
        # Quiet by default — one line per rejection at debug level
        logger.debug(f"[swing {self.symbol}] reject: {reason} {detail if detail is not None else ''}")
