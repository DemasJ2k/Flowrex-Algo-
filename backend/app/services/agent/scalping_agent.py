"""
Scalping agent — evaluates M5 bars using XGBoost + LightGBM ensemble.
Fires signal when any ONE model has >= 55% confidence.
"""
import numpy as np
from typing import Optional, Callable
from datetime import datetime, timezone

from app.services.ml.ensemble_engine import EnsembleSignalEngine
from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.rl_trade_manager import RLTradeManager
from app.services.agent.instrument_specs import calc_sl_tp, calc_lot_size, get_session_multiplier
from app.services.agent.risk_manager import RiskManager
from app.services.news.newsapi_provider import check_high_impact_news


class ScalpingAgent:
    """
    Scalping trading agent.
    Uses XGBoost + LightGBM ensemble with single-model conviction voting.
    """

    def __init__(self, agent_id: int, symbol: str, broker_name: str, config: dict):
        self.agent_id = agent_id
        self.symbol = symbol
        self.broker_name = broker_name
        self.config = config

        self._risk_manager = RiskManager(config)
        self._ensemble = EnsembleSignalEngine(symbol, "scalping")
        self._rl_manager = RLTradeManager(symbol)
        self._recent_trade_results: list[float] = []
        self._log_fn: Optional[Callable] = None

        self._eval_count = 0
        self._signal_count = 0
        self._reject_count = 0
        self._last_trade_bar = -999  # bar index of last trade
        self._h1_bars = None
        self._h1_fetch_time = 0

    def load(self) -> bool:
        """Load ML models from disk."""
        loaded = self._ensemble.load_models()
        rl_loaded = self._rl_manager.load()
        if loaded:
            self._log("info", f"Loaded {len(self._ensemble.models)} models: {list(self._ensemble.models.keys())}"
                      + (", RL trade manager" if rl_loaded else ""))
        else:
            self._log("warn", "No models found for scalping agent")
        return loaded

    async def evaluate(
        self,
        m5_bars: list[dict],
        broker_adapter,
        balance: float = 10000.0,
        daily_pnl: float = 0.0,
        daily_trade_count: int = 0,
        current_bar_index: int = 0,
    ) -> Optional[dict]:
        """
        Evaluate current M5 bars for trading signal.
        Returns signal dict or None.
        """
        self._eval_count += 1

        # 1. Check minimum bars
        if len(m5_bars) < 60:
            self._log_reject("Insufficient bars", len(m5_bars))
            return None

        # 2. Check cooldown
        bars_since_last = current_bar_index - self._last_trade_bar

        # 3. Risk check
        hour_utc = datetime.now(timezone.utc).hour
        session_mult = get_session_multiplier(hour_utc, self.symbol)

        risk_check = self._risk_manager.check_trade(
            balance=balance,
            daily_pnl=daily_pnl,
            daily_trade_count=daily_trade_count,
            bars_since_last_trade=bars_since_last,
            session_multiplier=session_mult,
        )
        if not risk_check.approved:
            self._log_reject(risk_check.reason)
            return None

        # 4. News filter
        news = check_high_impact_news(self.symbol)
        if not news.should_trade:
            self._log_reject(f"News filter: {news.reason}")
            return None

        # 5. Fetch H1 context (cache for 1 hour)
        await self._refresh_h1_context(broker_adapter)

        # 6. Compute features
        import pandas as pd
        m5_df = pd.DataFrame(m5_bars)
        h1_df = pd.DataFrame(self._h1_bars) if self._h1_bars else None

        feature_names, X = compute_expert_features(m5_df, h1_df)

        if X.shape[0] == 0:
            self._log_reject("Empty feature matrix")
            return None

        # Use last bar's features
        feature_vector = X[-1]

        # 7-8. Ensemble prediction (scalping voting)
        signal = self._ensemble.predict(feature_vector)

        if signal is None or signal.direction == 0:
            self._log_reject("No ensemble signal")
            return None

        # 9. Compute ATR
        atr_idx = feature_names.index("atr_14") if "atr_14" in feature_names else None
        atr_value = float(feature_vector[atr_idx]) if atr_idx is not None else 0

        if atr_value <= 0:
            self._log_reject("ATR is zero")
            return None

        # 9b. RL Trade Manager: decides SKIP/SMALL/NORMAL/AGGRESSIVE
        rl_decision = self._rl_manager.decide(
            signal_direction=signal.direction,
            signal_confidence=signal.confidence,
            feature_vector=feature_vector,
            feature_names=feature_names,
            hour_utc=hour_utc,
            recent_trade_results=self._recent_trade_results,
        )

        if rl_decision.action == 0:  # SKIP
            self._log_reject(f"RL skipped")
            return None

        # Use RL-managed SL/TP (or defaults if no RL model)
        sl_mult = rl_decision.sl_atr_mult if self._rl_manager.is_loaded else 1.5
        tp_mult = rl_decision.tp_atr_mult if self._rl_manager.is_loaded else 2.5
        lot_mult = rl_decision.lot_multiplier if self._rl_manager.is_loaded else 1.0

        last_bar = m5_bars[-1]
        entry_price = float(last_bar["close"])
        stop_loss, take_profit = calc_sl_tp(entry_price, signal.direction, atr_value, sl_mult, tp_mult)

        # 10. Position sizing (scaled by RL)
        sl_distance = abs(entry_price - stop_loss)
        lot_size = calc_lot_size(self.symbol, risk_check.risk_amount * lot_mult, sl_distance, self.broker_name)

        # 11. Build signal dict
        direction_str = "BUY" if signal.direction == 1 else "SELL"
        session = "asian" if 0 <= hour_utc < 8 else "london" if 8 <= hour_utc < 16 else "ny"

        signal_dict = {
            "direction": direction_str,
            "confidence": signal.confidence,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "lot_size": lot_size,
            "reason": signal.reason,
            "session": session,
            "session_multiplier": session_mult,
            "atr": atr_value,
            "agent_type": "scalping",
            "rl_action": rl_decision.action_name,
            "rl_lot_mult": rl_decision.lot_multiplier,
            "votes": {k: v["confidence"] for k, v in signal.votes.items()},
        }

        self._signal_count += 1
        self._last_trade_bar = current_bar_index

        self._log("signal", f"{direction_str} {self.symbol} @ {entry_price} | SL:{stop_loss} TP:{take_profit} | Lots:{lot_size} | Conf:{signal.confidence:.2f}", signal_dict)

        return signal_dict

    async def _refresh_h1_context(self, broker_adapter):
        """Fetch H1 bars from broker, cache for 1 hour."""
        import time
        now = time.time()
        if now - self._h1_fetch_time < 3600 and self._h1_bars:
            return
        try:
            candles = await broker_adapter.get_candles(self.symbol, "H1", 100)
            self._h1_bars = [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
            self._h1_fetch_time = now
        except Exception:
            pass  # Keep old cache

    def _log(self, level: str, message: str, data: dict = None):
        if self._log_fn:
            self._log_fn(level, message, data)

    def _log_reject(self, reason: str, extra=None):
        self._reject_count += 1
        if self._reject_count % 10 == 1:  # Log every 10th rejection
            msg = f"Signal rejected: {reason}"
            if extra is not None:
                msg += f" ({extra})"
            self._log("info", msg)
