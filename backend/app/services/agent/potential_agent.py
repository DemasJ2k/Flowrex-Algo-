"""
Potential Agent — institutional strategy ML inference.
Uses XGBoost/LightGBM ensemble trained on 76 features (VWAP, Volume Profile,
ADX, ORB, EMA structure, Donchian, RSI, MACD, CVD, HTF alignment + LSTM diversity).
Simple risk: 10% max DD, 3% daily loss, 1% per trade. No prop firm filters.
"""
import os
import time as _time
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
    EXPECTED_FEATURE_COUNT = 85

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
        self._reject_last_log_time: dict[str, float] = {}  # reason -> last log timestamp
        self._last_trade_bar = -999
        self._h1_bars = None
        self._h4_bars = None
        self._d1_bars = None
        self._htf_fetch_time = 0
        self._peak_equity = 0.0
        self._daily_pnl = 0.0

    def load(self) -> bool:
        """Load ML models from disk with validation."""
        self.models.clear()
        loaded_any = False

        for mtype in ["xgboost", "lightgbm"]:
            path = os.path.join(MODEL_DIR, f"potential_{self.symbol}_M5_{mtype}.joblib")

            if not os.path.exists(path):
                self._log("warn", f"Model file not found: {os.path.basename(path)}")
                continue

            try:
                data = joblib.load(path)
            except Exception as e:
                self._log("error", f"Failed to load model {os.path.basename(path)}: {e}")
                continue

            model_features = data.get("feature_names", [])
            feat_count = len(model_features)
            grade = data.get("grade", "?")
            pipeline_version = data.get("pipeline_version", data.get("version", "?"))

            # Validate feature count matches expected
            if feat_count != self.EXPECTED_FEATURE_COUNT:
                self._log("warn",
                    f"Feature count mismatch for {mtype}: model has {feat_count} features, "
                    f"expected {self.EXPECTED_FEATURE_COUNT} from compute_potential_features")

            self.models[mtype] = data
            loaded_any = True

            if not self.feature_names and model_features:
                self.feature_names = model_features
            if "risk_config" in data:
                self.risk_config = data["risk_config"]

            self._log("info",
                f"Loaded potential model: {mtype} "
                f"(grade={grade}, features={feat_count}, "
                f"pipeline_version={pipeline_version})")

        if not loaded_any:
            # List available model files so user can diagnose
            available = []
            try:
                for f in os.listdir(MODEL_DIR):
                    if f.startswith("potential_") and f.endswith(".joblib"):
                        available.append(f)
            except OSError:
                pass

            if available:
                self._log("error",
                    f"No models found for symbol={self.symbol}. "
                    f"Available potential models: {', '.join(sorted(available))}")
            else:
                self._log("error",
                    f"No models found for symbol={self.symbol}. "
                    f"No potential model files exist in {MODEL_DIR}")

        return loaded_any

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

        # Use last bar's features — validate for NaN/Inf
        feature_vector = X[-1].reshape(1, -1)
        if np.any(np.isnan(feature_vector)) or np.any(np.isinf(feature_vector)):
            feature_vector = np.nan_to_num(feature_vector, nan=0.0, posinf=0.0, neginf=0.0)

        # 6. Ensemble prediction — best confidence wins
        signal = self._predict_ensemble(feature_vector, feat_names)

        if signal is None or signal.direction == 0:
            self._log_reject("No signal")
            return None

        # 7. ATR-based SL/TP — compute ATR directly from bars (not from features)
        from app.services.backtest.indicators import atr as compute_atr
        bar_highs = m5_df["high"].values.astype(float)
        bar_lows = m5_df["low"].values.astype(float)
        bar_closes = m5_df["close"].values.astype(float)
        atr_arr = compute_atr(bar_highs, bar_lows, bar_closes, 14)
        atr_value = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else 0.0

        if atr_value <= 0:
            self._log_reject("ATR is zero")
            return None

        from app.services.agent.instrument_specs import calc_lot_size, get_spec, get_oanda_price_decimals

        last_bar = m5_bars[-1]
        entry_price = float(last_bar["close"])
        spec = get_spec(self.symbol)

        # Use 1.2x ATR for TP, 0.8x ATR for SL (from training config)
        sl_distance = atr_value * 0.8
        tp_distance = atr_value * 1.2
        if signal.direction == 1:  # BUY
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SELL
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance

        # Round prices to instrument precision (Oanda rejects excess decimals)
        # Use hardcoded decimal map — the pip_size formula breaks for US30/ES/NAS100
        price_digits = get_oanda_price_decimals(self.symbol) if self.broker_name == "oanda" else max(0, len(str(spec.pip_size).rstrip('0').split('.')[-1]))
        stop_loss = round(stop_loss, price_digits)
        take_profit = round(take_profit, price_digits)
        entry_price = round(entry_price, price_digits)

        # 8. Position sizing (1% risk)
        risk_amount = balance * self.risk_config["risk_per_trade_pct"]
        lot_size = calc_lot_size(self.symbol, risk_amount, sl_distance, self.broker_name)

        # Oanda uses integer units — round to whole number
        if self.broker_name == "oanda":
            lot_size = max(1, int(round(lot_size)))

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
        now = _time.monotonic()
        last_time = self._reject_last_log_time.get(reason, 0.0)
        if now - last_time >= 60.0:  # Rate limit: 1 log per minute per reason
            self._reject_last_log_time[reason] = now
            msg = f"[Potential] Rejected: {reason}"
            if extra is not None:
                msg += f" ({extra})"
            self._log("reject", msg)
