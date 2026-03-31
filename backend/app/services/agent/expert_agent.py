"""
Expert agent — multi-model ensemble with regime detection and meta-labeler.
Uses 2/3 voting (XGBoost + LightGBM + LSTM), HMM regime, meta-labeler gate.
"""
import time
import numpy as np
from typing import Optional, Callable
from datetime import datetime, timezone

from app.services.ml.ensemble_engine import EnsembleSignalEngine
from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.meta_labeler import MetaLabeler
from app.services.ml.regime_detector import RegimeDetector
from app.services.agent.instrument_specs import calc_sl_tp, calc_lot_size, get_session_multiplier
from app.services.agent.risk_manager import RiskManager
from app.services.news.newsapi_provider import check_high_impact_news


def _get_session(hour_utc: int) -> str:
    if 0 <= hour_utc < 8:
        return "asian"
    elif 8 <= hour_utc < 13:
        return "london"
    elif 13 <= hour_utc < 21:
        return "ny"
    else:
        return "dead_zone"


class ExpertAgent:
    """
    Expert trading agent with full multi-model ensemble pipeline.
    Stages: bars→daily gate→HTF fetch→features→news→session→regime→ensemble→meta→cooldown→SL/TP→sizing→signal
    """

    def __init__(self, agent_id: int, symbol: str, broker_name: str, config: dict):
        self.agent_id = agent_id
        self.symbol = symbol
        self.broker_name = broker_name
        self.config = config

        self._risk_manager = RiskManager(config)
        self._ensemble = EnsembleSignalEngine(symbol, "expert")
        self._meta_labeler = MetaLabeler(symbol)
        self._regime_detector = RegimeDetector(symbol)
        self._log_fn: Optional[Callable] = None

        # Config with defaults
        self._news_enabled = config.get("news_filter_enabled", True)
        self._news_window = config.get("news_window_minutes", 15)
        self._session_filter = config.get("session_filter", True)
        self._regime_filter = config.get("regime_filter", True)

        self._eval_count = 0
        self._signal_count = 0
        self._reject_count = 0
        self._last_trade_bar = -999

        # HTF bar caches
        self._h1_bars = None
        self._h1_fetch_time = 0
        self._h4_bars = None
        self._h4_fetch_time = 0
        self._d1_bars = None
        self._d1_fetch_time = 0

    def load(self) -> bool:
        """Load all ML models: ensemble, meta-labeler, regime detector."""
        loaded = self._ensemble.load_models()
        meta_loaded = self._meta_labeler.load()
        regime_loaded = self._regime_detector.load()

        if loaded:
            self._log("info", f"Expert models loaded: {list(self._ensemble.models.keys())}" +
                      (", meta-labeler" if meta_loaded else "") +
                      (", regime detector" if regime_loaded else ""))
        else:
            self._log("warn", "No ensemble models found for expert agent")
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
        """Full expert evaluation pipeline."""
        self._eval_count += 1

        # 1. Basic checks
        if len(m5_bars) < 60:
            self._log_reject("Insufficient bars")
            return None

        # Daily loss gate
        bars_since_last = current_bar_index - self._last_trade_bar
        now = datetime.now(timezone.utc)
        hour_utc = now.hour
        session = _get_session(hour_utc)
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

        # 2. Fetch HTF context bars
        await self._refresh_htf_bars(broker_adapter)

        # 3. Compute expert features (M5 + H1 + H4 + D1)
        import pandas as pd
        m5_df = pd.DataFrame(m5_bars)
        h1_df = pd.DataFrame(self._h1_bars) if self._h1_bars else None
        h4_df = pd.DataFrame(self._h4_bars) if self._h4_bars else None
        d1_df = pd.DataFrame(self._d1_bars) if self._d1_bars else None

        feature_names, X = compute_expert_features(m5_df, h1_df, h4_df, d1_df)

        if X.shape[0] == 0:
            self._log_reject("Empty features")
            return None

        feature_vector = X[-1]

        # Build LSTM sequence (last 60 bars)
        seq_len = min(60, X.shape[0])
        feature_sequence = X[-seq_len:] if seq_len >= 60 else None

        # 4. News filter
        if self._news_enabled:
            news = check_high_impact_news(self.symbol, self._news_window)
            if not news.should_trade:
                self._log("info", f"Trade blocked by news filter: {news.reason} (source: {news.source})")
                return None

        # 5. Session awareness
        if self._session_filter:
            is_crypto = self.symbol in ("BTCUSD", "ETHUSD")
            if session == "dead_zone" and not is_crypto:
                self._log_reject("Dead zone session")
                return None
            if session == "asian" and not is_crypto:
                session_mult = 0.5

        # 6. Regime detection
        regime_mult = 1.0
        regime_name = "unknown"
        if self._regime_filter:
            closes = np.array([b["close"] for b in m5_bars[-100:]], dtype=float)
            volumes = np.array([b.get("volume", 1) for b in m5_bars[-100:]], dtype=float)
            regime = self._regime_detector.predict_regime(closes, volumes)
            regime_name = regime.regime
            if regime.confidence > 0.6:
                if regime.regime == "volatile":
                    regime_mult = 0.6
                elif regime.regime == "ranging":
                    regime_mult = 0.8
                elif regime.regime in ("trending_up", "trending_down"):
                    regime_mult = 1.1

        # 7. Ensemble vote (2/3 agreement)
        signal = self._ensemble.predict(feature_vector, feature_sequence)

        if signal is None or signal.direction == 0:
            self._log_reject("No ensemble consensus")
            return None

        # 8. Meta-labeler gate
        meta_approved = self._meta_labeler.should_trade(feature_vector, signal.direction, signal.confidence)
        if not meta_approved:
            self._log_reject("Meta-labeler rejected")
            return None

        # 9. Compute SL/TP (wider than scalping: 2.0 ATR SL, 3.0 ATR TP)
        atr_idx = feature_names.index("atr_14") if "atr_14" in feature_names else None
        atr_value = float(feature_vector[atr_idx]) if atr_idx is not None else 0

        if atr_value <= 0:
            self._log_reject("ATR is zero")
            return None

        last_bar = m5_bars[-1]
        entry_price = float(last_bar["close"])
        stop_loss, take_profit = calc_sl_tp(entry_price, signal.direction, atr_value, sl_multiplier=2.0, tp_multiplier=3.0)

        # 10. Position sizing with regime multiplier
        effective_risk = self._risk_manager.risk_per_trade * session_mult * regime_mult
        risk_amount = balance * effective_risk
        sl_distance = abs(entry_price - stop_loss)
        lot_size = calc_lot_size(self.symbol, risk_amount, sl_distance, self.broker_name)

        # 11. Build signal
        direction_str = "BUY" if signal.direction == 1 else "SELL"

        signal_dict = {
            "direction": direction_str,
            "confidence": signal.confidence,
            "agreement": signal.agreement,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "lot_size": lot_size,
            "reason": signal.reason,
            "session": session,
            "session_multiplier": session_mult,
            "regime": regime_name,
            "regime_multiplier": regime_mult,
            "meta_approved": True,
            "atr": atr_value,
            "agent_type": "expert",
            "votes": {k: v["confidence"] for k, v in signal.votes.items()},
        }

        self._signal_count += 1
        self._last_trade_bar = current_bar_index

        self._log("signal",
            f"SIGNAL: {direction_str} {self.symbol} @ {entry_price} | "
            f"SL={stop_loss} TP={take_profit} | conf={signal.confidence:.1%} | "
            f"regime={regime_name} session={session} | "
            f"{signal.agreement}/{len(signal.votes)} models agreed",
            signal_dict,
        )

        return signal_dict

    async def _refresh_htf_bars(self, broker_adapter):
        """Fetch HTF bars with caching: H1 (1hr), H4 (4hr), D1 (daily)."""
        now = time.time()

        async def _fetch(tf, count, cache_attr, time_attr, ttl):
            if now - getattr(self, time_attr) < ttl and getattr(self, cache_attr):
                return
            try:
                candles = await broker_adapter.get_candles(self.symbol, tf, count)
                bars = [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
                setattr(self, cache_attr, bars)
                setattr(self, time_attr, now)
            except Exception:
                pass

        await _fetch("H1", 200, "_h1_bars", "_h1_fetch_time", 3600)
        await _fetch("H4", 100, "_h4_bars", "_h4_fetch_time", 14400)
        await _fetch("D1", 50, "_d1_bars", "_d1_fetch_time", 86400)

    def _log(self, level: str, message: str, data: dict = None):
        if self._log_fn:
            self._log_fn(level, message, data)

    def _log_reject(self, reason: str):
        self._reject_count += 1
        if self._reject_count % 10 == 1:
            self._log("info", f"Expert signal rejected: {reason}")
