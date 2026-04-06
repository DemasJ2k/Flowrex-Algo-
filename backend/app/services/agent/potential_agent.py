"""
Potential Agent — institutional strategy ML inference.
Uses XGBoost/LightGBM ensemble trained on 76 features (VWAP, Volume Profile,
ADX, ORB, EMA structure, Donchian, RSI, MACD, CVD, HTF alignment + LSTM diversity).
Simple risk: 10% max DD, 3% daily loss, 1% per trade. No prop firm filters.
"""
import os
import numpy as np
import joblib
from typing import Optional, Callable
from datetime import datetime, timezone
from dataclasses import dataclass, field

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")


@dataclass
class PotentialSignal:
    direction: int  # 1=buy, -1=sell, 0=no signal
    confidence: float = 0.0
    reason: str = ""
    votes: dict = field(default_factory=dict)


class PotentialAgent:
    """
    Potential trading agent — institutional strategies.
    Uses XGBoost + LightGBM ensemble with best-confidence voting.
    """

    CONFIDENCE_THRESHOLD = 0.52
    MIN_BARS = 300  # need enough for Volume Profile (288-bar window)
    COOLDOWN_BARS = 3

    def __init__(self, agent_id: int, symbol: str, broker_name: str, config: dict = None):
        self.agent_id = agent_id
        self.symbol = symbol
        self.broker_name = broker_name
        self.config = config or {}

        self.models: dict[str, dict] = {}
        self.feature_names: list[str] = []
        self.risk_config = {
            "max_drawdown_pct": 0.10,
            "daily_loss_limit_pct": 0.03,
            "risk_per_trade_pct": 0.01,
            "max_trades_per_day": 10,
        }

        self._log_fn: Optional[Callable] = None
        self._eval_count = 0
        self._signal_count = 0
        self._reject_count = 0
        self._last_trade_bar = -999
        self._h1_bars = None
        self._h4_bars = None
        self._d1_bars = None
        self._htf_fetch_time = 0
        self._peak_equity = 0.0
        self._daily_pnl = 0.0

    def load(self) -> bool:
        """Load ML models from disk."""
        self.models.clear()
        for mtype in ["xgboost", "lightgbm"]:
            path = os.path.join(MODEL_DIR, f"potential_{self.symbol}_M5_{mtype}.joblib")
            if os.path.exists(path):
                data = joblib.load(path)
                self.models[mtype] = data
                if not self.feature_names and "feature_names" in data:
                    self.feature_names = data["feature_names"]
                if "risk_config" in data:
                    self.risk_config = data["risk_config"]
                self._log("info", f"Loaded potential model: {mtype} "
                          f"(grade={data.get('grade', '?')}, "
                          f"features={len(data.get('feature_names', []))})")
        return len(self.models) > 0

    async def evaluate(
        self,
        m5_bars: list[dict],
        broker_adapter=None,
        balance: float = 10000.0,
        daily_pnl: float = 0.0,
        daily_trade_count: int = 0,
        current_bar_index: int = 0,
    ) -> Optional[dict]:
        """Evaluate current M5 bars for trading signal. Returns signal dict or None."""
        self._eval_count += 1

        # 1. Minimum bars
        if len(m5_bars) < self.MIN_BARS:
            self._log_reject("Insufficient bars", len(m5_bars))
            return None

        # 2. Cooldown
        bars_since_last = current_bar_index - self._last_trade_bar
        if bars_since_last < self.COOLDOWN_BARS:
            self._log_reject("Cooldown", bars_since_last)
            return None

        # 3. Risk checks (simple)
        if not self._check_risk(balance, daily_pnl, daily_trade_count):
            return None

        # 4. Fetch HTF context
        if broker_adapter:
            await self._refresh_htf_context(broker_adapter)

        # 5. Compute features
        import pandas as pd
        from app.services.ml.features_potential import compute_potential_features

        m5_df = pd.DataFrame(m5_bars)
        h1_df = pd.DataFrame(self._h1_bars) if self._h1_bars else None
        h4_df = pd.DataFrame(self._h4_bars) if self._h4_bars else None
        d1_df = pd.DataFrame(self._d1_bars) if self._d1_bars else None

        feat_names, X = compute_potential_features(m5_df, h1_df, h4_df, d1_df, symbol=self.symbol)

        if X.shape[0] == 0:
            self._log_reject("Empty feature matrix")
            return None

        # Use last bar's features
        feature_vector = X[-1].reshape(1, -1)

        # 6. Ensemble prediction — best confidence wins
        signal = self._predict_ensemble(feature_vector, feat_names)

        if signal is None or signal.direction == 0:
            self._log_reject("No signal")
            return None

        # 7. ATR-based SL/TP
        atr_idx = feat_names.index("pot_atr_14") if "pot_atr_14" in feat_names else None
        atr_value = float(feature_vector[0, atr_idx]) if atr_idx is not None else 0

        if atr_value <= 0:
            self._log_reject("ATR is zero")
            return None

        from app.services.agent.instrument_specs import calc_sl_tp, calc_lot_size

        last_bar = m5_bars[-1]
        entry_price = float(last_bar["close"])
        # Use 1.2x ATR for TP, 0.8x ATR for SL (from training config)
        sl_distance = atr_value * 0.8
        tp_distance = atr_value * 1.2
        if signal.direction == 1:  # BUY
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SELL
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance

        # 8. Position sizing (1% risk)
        risk_amount = balance * self.risk_config["risk_per_trade_pct"]
        lot_size = calc_lot_size(self.symbol, risk_amount, sl_distance, self.broker_name)

        # 9. Build signal dict
        hour_utc = datetime.now(timezone.utc).hour
        direction_str = "BUY" if signal.direction == 1 else "SELL"

        signal_dict = {
            "direction": direction_str,
            "confidence": signal.confidence,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "lot_size": lot_size,
            "reason": signal.reason,
            "atr": atr_value,
            "agent_type": "potential",
            "votes": signal.votes,
        }

        self._signal_count += 1
        self._last_trade_bar = current_bar_index

        self._log("signal", f"{direction_str} {self.symbol} @ {entry_price:.2f} | "
                  f"SL:{stop_loss:.2f} TP:{take_profit:.2f} | "
                  f"Lots:{lot_size} | Conf:{signal.confidence:.3f}", signal_dict)

        return signal_dict

    def _predict_ensemble(self, X: np.ndarray, feat_names: list) -> Optional[PotentialSignal]:
        """Run both models, pick highest confidence directional signal."""
        if not self.models:
            return None

        votes = {}
        best_signal = None
        best_conf = 0.0

        for mtype, data in self.models.items():
            model = data.get("model")
            if model is None:
                continue

            try:
                proba = model.predict_proba(X)[0]
            except (ValueError, Exception):
                continue

            pred = int(np.argmax(proba))
            conf = float(proba[pred])
            direction = {0: -1, 1: 0, 2: 1}.get(pred, 0)

            votes[mtype] = {"direction": direction, "confidence": round(conf, 4)}

            # Only consider directional predictions above threshold
            if direction != 0 and conf >= self.CONFIDENCE_THRESHOLD and conf > best_conf:
                best_conf = conf
                best_signal = PotentialSignal(
                    direction=direction,
                    confidence=conf,
                    reason=f"potential_{mtype}",
                    votes=votes,
                )

        if best_signal is not None:
            best_signal.votes = votes

        return best_signal

    def _check_risk(self, balance: float, daily_pnl: float, daily_trade_count: int) -> bool:
        """Simple risk checks — no prop firm tiers."""
        # Daily trade limit
        if daily_trade_count >= self.risk_config["max_trades_per_day"]:
            self._log_reject("Daily trade limit reached")
            return False

        # Daily loss limit
        daily_limit = balance * self.risk_config["daily_loss_limit_pct"]
        if daily_pnl < -daily_limit:
            self._log_reject(f"Daily loss limit ({daily_pnl:.2f} < -{daily_limit:.2f})")
            return False

        # Max drawdown (track peak equity)
        equity = balance + daily_pnl
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            dd = (self._peak_equity - equity) / self._peak_equity
            if dd > self.risk_config["max_drawdown_pct"]:
                self._log_reject(f"Max DD exceeded ({dd*100:.1f}% > {self.risk_config['max_drawdown_pct']*100:.0f}%)")
                return False

        return True

    async def _refresh_htf_context(self, broker_adapter):
        """Fetch H1/H4/D1 bars, cache for 1 hour."""
        import time
        now = time.time()
        if now - self._htf_fetch_time < 3600 and self._h1_bars:
            return
        try:
            for tf, attr, count in [("H1", "_h1_bars", 200), ("H4", "_h4_bars", 100), ("D1", "_d1_bars", 50)]:
                candles = await broker_adapter.get_candles(self.symbol, tf, count)
                bars = [{"time": c.time, "open": c.open, "high": c.high,
                         "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
                setattr(self, attr, bars)
            self._htf_fetch_time = now
        except Exception:
            pass

    def get_stats(self) -> dict:
        """Return agent statistics."""
        return {
            "agent_type": "potential",
            "symbol": self.symbol,
            "evaluations": self._eval_count,
            "signals": self._signal_count,
            "rejections": self._reject_count,
            "models_loaded": list(self.models.keys()),
            "feature_count": len(self.feature_names),
        }

    def _log(self, level: str, message: str, data: dict = None):
        if self._log_fn:
            self._log_fn(level, message, data)

    def _log_reject(self, reason: str, extra=None):
        self._reject_count += 1
        if self._reject_count % 10 == 1:
            msg = f"[Potential] Rejected: {reason}"
            if extra is not None:
                msg += f" ({extra})"
            self._log("info", msg)
