"""
Scout agent — PotentialAgent with a lookback + pullback/BOS entry state
machine.

User-requested behaviour (2026-04-21):
> when the agent receives a signal, it will do a 40 candle stick look back
> and then setup it's entry point by either waiting for a pullback, or if
> confidence is over 0.85 it can instantly enter. Or maybe a confirmation
> of break of structure when it looks back.
>
> the look back can also check if it is continuing to make identical trades,
> and it can also have a look at the trend.

Implementation
--------------
ScoutAgent inherits from PotentialAgent (same 85 features, same deployed
`potential_{SYMBOL}_M5_*.joblib` models, same regime / session / correlation
filters — no retraining needed). The only behavioural difference is the
entry path:

Normal PotentialAgent
    signal → enter immediately at close[i]

Scout
    signal at bar i →
        stash as PENDING with a reference bar snapshot
        next tick:
            if confidence ≥ instant_entry_confidence:  enter at current close
            elif pullback confirmed:                   enter at current close
            elif break-of-structure confirmed:         enter at current close
            elif bars_waited > max_pending_bars:       discard
            elif duplicate-trade filter:               discard
            else:                                      keep waiting

SL/TP are computed FRESH at trigger time using current-bar ATR so they
match the actual entry price, not the stale pending-bar price.

Config knobs (all optional — sensible defaults apply):
    lookback_bars              default 40     bars in the reference window
    instant_entry_confidence   default 0.85   skip pullback, enter now
    max_pending_bars           default 10     expire pending if no trigger
    pullback_atr_fraction      default 0.50   how far price must retrace
    dedupe_window_bars         default 20     skip if same-dir trade recent
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd

from app.services.agent.potential_agent import PotentialAgent, PotentialSignal


class ScoutAgent(PotentialAgent):
    """Lookback + pullback/BOS entry variant of PotentialAgent."""

    def __init__(self, agent_id: int, symbol: str, broker_name: str, config: dict = None):
        super().__init__(agent_id, symbol, broker_name, config)

        self.lookback_bars: int = int(self.config.get("lookback_bars", 40))
        self.instant_entry_confidence: float = float(
            self.config.get("instant_entry_confidence", 0.85)
        )
        self.max_pending_bars: int = int(self.config.get("max_pending_bars", 10))
        self.pullback_atr_fraction: float = float(
            self.config.get("pullback_atr_fraction", 0.50)
        )
        self.dedupe_window_bars: int = int(self.config.get("dedupe_window_bars", 20))

        # State machine — a single pending signal at a time. Stash enough to
        # re-evaluate trigger conditions each tick without re-running the
        # model. Direction is ±1 to match PotentialSignal convention.
        self._pending: Optional[dict] = None  # keys: direction, confidence, ref_close, ref_high, ref_low, ref_atr, ref_time, bars_waited

    async def evaluate(
        self,
        m5_bars: list[dict],
        broker_adapter=None,
        balance: float = 10000.0,
        daily_pnl: float = 0.0,
        daily_trade_count: int = 0,
        current_bar_index: int = 0,
    ) -> Optional[dict]:
        if len(m5_bars) < max(self.MIN_BARS, self.lookback_bars + 5):
            self._log_reject("Insufficient bars for scout lookback")
            return None

        current_bar = m5_bars[-1]

        # ── 1. If a signal is pending, check trigger conditions FIRST ─────
        # We do this before calling super() so a valid pullback/BOS doesn't
        # get stomped by a fresh same-direction signal from super that would
        # re-stash and reset the bars_waited counter.
        if self._pending is not None:
            self._pending["bars_waited"] = int(self._pending.get("bars_waited", 0)) + 1

            # Expire stale pendings. The model signal was generated at a
            # specific bar's feature state; after N bars the regime may have
            # shifted and the signal no longer reflects current conditions.
            if self._pending["bars_waited"] > self.max_pending_bars:
                self._log("info",
                    f"Scout: pending {self._dir_name(self._pending['direction'])} expired "
                    f"after {self._pending['bars_waited']} bars (no trigger)")
                self._pending = None
                # Fall through to look for a fresh signal below.
            else:
                # Duplicate-trade filter — skip if the last closed trade was
                # the same direction within the dedupe window. Reduces
                # over-trading on re-emerging same-direction signals.
                if self._is_duplicate_direction(self._pending["direction"]):
                    self._log_reject(
                        f"Scout dedupe: last trade was same direction within "
                        f"{self.dedupe_window_bars} bars"
                    )
                    self._pending = None
                else:
                    trigger = self._check_triggers(m5_bars)
                    if trigger is not None:
                        self._log("info",
                            f"Scout trigger: {trigger} → enter "
                            f"{self._dir_name(self._pending['direction'])} "
                            f"after {self._pending['bars_waited']} bar(s)")
                        pending = self._pending
                        self._pending = None
                        # Build a fresh trade dict at the current bar using the
                        # pending signal's direction + confidence. SL/TP are
                        # recomputed from current ATR so they match entry.
                        return await self._enter_from_pending(
                            pending, m5_bars, broker_adapter,
                            balance, daily_pnl, daily_trade_count,
                            current_bar_index, trigger,
                        )
                    # No trigger yet — wait.
                    return None

        # ── 2. No pending (or just expired) — run super to look for a signal
        # PotentialAgent.evaluate returns either a trade dict (immediate
        # entry per its normal logic) OR None. We intercept the trade dict
        # and stash it as pending instead of trading now.
        result = await super().evaluate(
            m5_bars, broker_adapter=broker_adapter,
            balance=balance, daily_pnl=daily_pnl,
            daily_trade_count=daily_trade_count,
            current_bar_index=current_bar_index,
        )
        if result is None:
            return None

        # Super returned a trade dict. Stash as pending + decline to enter.
        # Instant-entry short-circuit: if confidence is already over the
        # threshold, enter immediately without waiting for a pullback.
        conf = float(result.get("confidence", 0.0))
        direction = 1 if result.get("direction", "").upper() == "BUY" else -1

        if conf >= self.instant_entry_confidence:
            self._log("info",
                f"Scout instant-entry: conf {conf:.3f} ≥ {self.instant_entry_confidence:.2f}")
            return result

        try:
            from app.services.backtest.indicators import atr as compute_atr
            highs = np.asarray([b["high"] for b in m5_bars], dtype=float)
            lows  = np.asarray([b["low"]  for b in m5_bars], dtype=float)
            closes = np.asarray([b["close"] for b in m5_bars], dtype=float)
            atr_arr = compute_atr(highs, lows, closes, 14)
            ref_atr = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else 0.0
        except Exception:
            ref_atr = 0.0

        self._pending = {
            "direction": direction,
            "confidence": conf,
            "ref_close": float(current_bar["close"]),
            "ref_high": float(current_bar["high"]),
            "ref_low": float(current_bar["low"]),
            "ref_atr": ref_atr,
            "ref_time": int(current_bar["time"]),
            "bars_waited": 0,
        }
        self._log("info",
            f"Scout: {self._dir_name(direction)} signal stashed (conf {conf:.3f}) — "
            f"waiting for pullback / BOS / expiry")
        return None

    # ── Trigger logic ─────────────────────────────────────────────────────

    def _check_triggers(self, m5_bars: list[dict]) -> Optional[str]:
        """
        Returns the name of the first trigger that fires, or None.

        Order matters — instant-entry beats pullback beats BOS.
        """
        if self._pending is None:
            return None

        conf = float(self._pending["confidence"])
        if conf >= self.instant_entry_confidence:
            return "instant_confidence"

        direction = int(self._pending["direction"])
        ref_close = float(self._pending["ref_close"])
        ref_atr = float(self._pending["ref_atr"]) or 1.0
        current = m5_bars[-1]

        # Pullback: price has moved against pending direction by at least
        # `pullback_atr_fraction` × ATR, and the current bar is closing
        # back in the pending direction (reversal candle).
        pullback_distance = ref_atr * self.pullback_atr_fraction
        if direction > 0:  # BUY pending
            lowest_since = min(float(b["low"]) for b in m5_bars[-self._pending["bars_waited"] - 1:])
            if (ref_close - lowest_since) >= pullback_distance and float(current["close"]) > float(m5_bars[-2]["close"]):
                return "pullback"
        else:  # SELL pending
            highest_since = max(float(b["high"]) for b in m5_bars[-self._pending["bars_waited"] - 1:])
            if (highest_since - ref_close) >= pullback_distance and float(current["close"]) < float(m5_bars[-2]["close"]):
                return "pullback"

        # Break-of-structure: current bar extends past the lookback window's
        # extreme in the pending direction (higher-high for BUY, lower-low
        # for SELL).
        window = m5_bars[-self.lookback_bars:-1]
        if not window:
            return None
        if direction > 0:
            prior_high = max(float(b["high"]) for b in window)
            if float(current["high"]) > prior_high:
                return "break_of_structure"
        else:
            prior_low = min(float(b["low"]) for b in window)
            if float(current["low"]) < prior_low:
                return "break_of_structure"

        return None

    def _is_duplicate_direction(self, direction: int) -> bool:
        """
        Check if the agent's most recent closed trade was the same direction
        within the last `dedupe_window_bars` × 5min of wall time. Keeps the
        scout from stacking up same-side entries in a chop zone.
        """
        try:
            from app.core.database import SessionLocal
            from app.models.agent import AgentTrade
            from datetime import datetime, timezone, timedelta
            db = SessionLocal()
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(
                    minutes=5 * self.dedupe_window_bars
                )
                last = (
                    db.query(AgentTrade)
                    .filter(
                        AgentTrade.agent_id == self.agent_id,
                        AgentTrade.status == "closed",
                        AgentTrade.entry_time >= cutoff,
                    )
                    .order_by(AgentTrade.entry_time.desc())
                    .first()
                )
                if last is None:
                    return False
                dir_str = "BUY" if direction > 0 else "SELL"
                return (last.direction or "").upper() == dir_str
            finally:
                db.close()
        except Exception:
            return False

    # ── Entry builder ─────────────────────────────────────────────────────

    async def _enter_from_pending(
        self,
        pending: dict,
        m5_bars: list[dict],
        broker_adapter,
        balance: float,
        daily_pnl: float,
        daily_trade_count: int,
        current_bar_index: int,
        trigger: str,
    ) -> Optional[dict]:
        """
        Build a fresh trade dict at the current bar using the pending
        signal's direction. Reuses PotentialAgent's normal evaluate path by
        delegating via a synthetic call — simplest cleanest approach.

        The synthetic trick: temporarily shrink max_pending (already 0 since
        we cleared self._pending) and call super().evaluate(). If super
        emits a same-direction trade at this tick, use it. Otherwise, build
        a minimal trade dict manually — this covers the case where the
        model no longer emits a fresh signal on the pullback bar (which is
        common and expected; the model saw an exit signal at the pending
        bar, pullback is the structural confirmation, not a fresh signal).
        """
        # Try super() first. If the model still agrees with pending direction
        # at this tick, use its fresh entry price / SL / TP / sizing output.
        try:
            result = await super().evaluate(
                m5_bars, broker_adapter=broker_adapter,
                balance=balance, daily_pnl=daily_pnl,
                daily_trade_count=daily_trade_count,
                current_bar_index=current_bar_index,
            )
        except Exception:
            result = None
        if result is not None:
            expected = "BUY" if pending["direction"] > 0 else "SELL"
            if result.get("direction") == expected:
                result["entry_reason"] = trigger
                return result

        # Super didn't re-emit (cooldown, confidence slipped below threshold,
        # etc.). Build the trade from scratch using the pending direction.
        return self._manual_entry(pending, m5_bars, balance, trigger)

    def _manual_entry(
        self, pending: dict, m5_bars: list[dict], balance: float, trigger: str,
    ) -> Optional[dict]:
        """Fallback entry builder — mirrors PotentialAgent's SL/TP/sizing math
        without re-running the model. Used when a pullback triggers but super
        didn't re-emit a signal this tick (common & intended).
        """
        try:
            from app.services.backtest.indicators import atr as compute_atr
            from app.services.agent.instrument_specs import (
                calc_lot_size, get_spec, get_oanda_price_decimals,
            )
            highs = np.asarray([b["high"] for b in m5_bars], dtype=float)
            lows  = np.asarray([b["low"]  for b in m5_bars], dtype=float)
            closes = np.asarray([b["close"] for b in m5_bars], dtype=float)
            atr_arr = compute_atr(highs, lows, closes, 14)
            atr_value = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else 0.0
            if atr_value <= 0:
                self._log_reject("Scout manual entry: ATR is zero")
                return None

            direction = int(pending["direction"])
            current = m5_bars[-1]
            entry_price = float(current["close"])
            spec = get_spec(self.symbol)

            sl_distance = atr_value * self.sl_atr_mult
            tp_distance = atr_value * self.tp_atr_mult
            from app.services.ml.symbol_config import get_symbol_config
            sym_cfg = get_symbol_config(self.symbol)
            spread = sym_cfg.get("spread_pips", 1.0)
            pip_size = spec.pip_size if spec.pip_size > 0 else 0.01
            min_distance = spread * pip_size * 3
            sl_distance = max(sl_distance, min_distance)
            tp_distance = max(tp_distance, min_distance)

            if direction > 0:
                stop_loss = entry_price - sl_distance
                take_profit = entry_price + tp_distance
            else:
                stop_loss = entry_price + sl_distance
                take_profit = entry_price - tp_distance
            price_digits = get_oanda_price_decimals(self.symbol) if self.broker_name == "oanda" else 5
            stop_loss = round(stop_loss, price_digits)
            take_profit = round(take_profit, price_digits)

            risk_pct = self.risk_config.get("risk_per_trade_pct", 0.001)
            risk_amount = balance * risk_pct
            lot_size = calc_lot_size(self.symbol, risk_amount, sl_distance, self.broker_name)
            if self.broker_name == "oanda":
                lot_size = max(1, int(round(lot_size)))

            direction_str = "BUY" if direction > 0 else "SELL"
            self._log("info",
                f"Scout manual entry ({trigger}): {direction_str} @ {entry_price:.4f} "
                f"SL:{stop_loss:.4f} TP:{take_profit:.4f} Lots:{lot_size}")

            return {
                "direction": direction_str,
                "confidence": float(pending["confidence"]),
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "lot_size": lot_size,
                "atr": atr_value,
                "entry_reason": trigger,
                "signal": {"direction": direction, "confidence": float(pending["confidence"])},
            }
        except Exception as e:
            self._log("warn", f"Scout manual entry failed: {e}")
            return None

    @staticmethod
    def _dir_name(direction: int) -> str:
        return "BUY" if direction > 0 else "SELL"
