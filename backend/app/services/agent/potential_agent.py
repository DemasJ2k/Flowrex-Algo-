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
    MIN_BARS = 300
    EXPECTED_FEATURE_COUNT = 85

    def __init__(self, agent_id: int, symbol: str, broker_name: str, config: dict = None):
        self.agent_id = agent_id
        self.symbol = symbol
        self.broker_name = broker_name
        self.config = config or {}

        self.models: dict[str, dict] = {}
        self.feature_names: list[str] = []

        # Read from config (wizard/settings), with sensible defaults
        # Safer default 0.001 (0.10%) instead of 0.01 (1.00%) — if config is
        # missing the key, agent won't silently 10x its position sizing.
        _risk_per_trade = self.config.get("risk_per_trade")
        if _risk_per_trade is None:
            _risk_per_trade = 0.001
            self._risk_default_warn = True
        else:
            self._risk_default_warn = False
        self.risk_config = {
            "max_drawdown_pct": self.config.get("max_drawdown_pct", 0.10),
            "daily_loss_limit_pct": self.config.get("max_daily_loss_pct", 0.03),
            "risk_per_trade_pct": _risk_per_trade,
            "max_trades_per_day": self.config.get("max_trades_per_day", 10),
        }
        self.cooldown_bars = self.config.get("cooldown_bars", 3)
        self.session_filter = self.config.get("session_filter", False)
        self.news_filter = self.config.get("news_filter_enabled", False)

        # Allowed sessions: subset of {asian, london, ny_open, ny_close, off_hours}
        # Empty list = all allowed. Only applies when session_filter=True.
        self.allowed_sessions: list[str] = self.config.get("allowed_sessions") or []
        # Direction gates
        self.allow_buy: bool = self.config.get("allow_buy", True)
        self.allow_sell: bool = self.config.get("allow_sell", True)

        # Optional prop-firm RiskManager (tiered DD + anti-martingale).
        self.prop_firm_enabled = self.config.get("prop_firm_enabled", False)
        self._risk_manager = None
        if self.prop_firm_enabled:
            try:
                from app.services.agent.risk_manager import RiskManager
                override_keys = [
                    "max_daily_dd_pct", "max_total_dd_pct", "daily_dd_yellow",
                    "daily_dd_red", "daily_dd_hard_stop", "max_trades_per_day",
                    "max_concurrent_positions", "base_risk_per_trade_pct",
                ]
                overrides = {k: self.config[k] for k in override_keys if k in self.config}
                self._risk_manager = RiskManager(config=overrides)
            except Exception:
                self._risk_manager = None

        self._log_fn: Optional[Callable] = None
        self._eval_count = 0
        self._signal_count = 0
        self._reject_count = 0
        self._reject_last_log_time: dict[str, float] = {}  # reason -> last log timestamp
        self._last_trade_bar = -999
        self._last_trade_time = 0.0  # monotonic seconds — robust to pause/resume
        self._last_trade_wall_time = 0.0  # persisted across restarts
        self._h1_bars = None
        self._h4_bars = None
        self._d1_bars = None
        self._htf_fetch_time = 0
        self._peak_equity = 0.0
        self._daily_pnl = 0.0
        self._feature_cache_key = None
        self._feature_cache_result = None
        self._last_prediction: Optional[dict] = None

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

            # Validate feature count matches expected — abort if mismatch
            if feat_count != self.EXPECTED_FEATURE_COUNT:
                self._log("error",
                    f"Feature count mismatch for {mtype}: model has {feat_count} features, "
                    f"expected {self.EXPECTED_FEATURE_COUNT} from compute_potential_features")
                return False

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

        # 2. Cooldown — wall time so restart doesn't reset it
        cooldown_sec = self.cooldown_bars * 300
        if self._last_trade_wall_time == 0.0:
            self._last_trade_wall_time = self._load_last_trade_time_from_db()
        import time as _wall_time
        now_wall = _wall_time.time()
        elapsed_sec = now_wall - self._last_trade_wall_time
        if self._last_trade_wall_time > 0 and elapsed_sec < cooldown_sec:
            self._log_reject("Cooldown", f"{int(elapsed_sec)}s/{cooldown_sec}s")
            return None

        # 2b. News filter (if enabled in config)
        if self.news_filter:
            try:
                from app.services.news.newsapi_provider import check_high_impact_news
                news = check_high_impact_news(self.symbol)
                if not news.should_trade:
                    self._log_reject(f"News filter: {news.reason}")
                    return None
            except Exception as e:
                self._log("warn", f"News filter unavailable: {e}")

        # 3. Risk checks (simple)
        if not self._check_risk(balance, daily_pnl, daily_trade_count):
            return None

        # 4. Fetch HTF context
        if broker_adapter:
            await self._refresh_htf_context(broker_adapter)

        # 5. Compute features (with caching to avoid redundant recomputation)
        from app.services.ml.features_potential import compute_potential_features

        cache_key = (m5_bars[-1]["time"], len(m5_bars))
        if self._feature_cache_key == cache_key and self._feature_cache_result is not None:
            feat_names, X = self._feature_cache_result
        else:
            import pandas as pd
            m5_df = pd.DataFrame(m5_bars)
            h1_df = pd.DataFrame(self._h1_bars) if self._h1_bars else None
            h4_df = pd.DataFrame(self._h4_bars) if self._h4_bars else None
            d1_df = pd.DataFrame(self._d1_bars) if self._d1_bars else None
            feat_names, X = compute_potential_features(m5_df, h1_df, h4_df, d1_df, symbol=self.symbol)
            self._feature_cache_key = cache_key
            self._feature_cache_result = (feat_names, X)

        if X.shape[0] == 0:
            self._log_reject("Empty feature matrix")
            return None

        # Use last bar's features — validate for NaN/Inf
        feature_vector = X[-1].reshape(1, -1)
        if np.any(np.isnan(feature_vector)) or np.any(np.isinf(feature_vector)):
            feature_vector = np.nan_to_num(feature_vector, nan=0.0, posinf=0.0, neginf=0.0)

        # Drift check (first eval + every 50 evals)
        if self._eval_count == 1 or self._eval_count % 50 == 0:
            try:
                from app.services.ml.feature_monitor import check_drift
                drift = check_drift(feature_vector[0], feat_names, self.symbol, self.agent_id)
                if drift:
                    self._log("warn",
                        f"Feature drift: {len(drift)} features outside training distribution "
                        f"(first 3: {'; '.join(drift[:3])})")
            except Exception:
                pass

        # 6. Ensemble prediction — best confidence wins
        signal = self._predict_ensemble(feature_vector, feat_names)

        if signal is None or signal.direction == 0:
            self._log_reject("No signal")
            return None

        # Direction filter — user can disable BUY or SELL.
        if signal.direction == 1 and not self.allow_buy:
            self._log_reject("BUY direction disabled for this agent")
            return None
        if signal.direction == -1 and not self.allow_sell:
            self._log_reject("SELL direction disabled for this agent")
            return None

        # Session filter — only trade during user-selected sessions.
        if self.session_filter and self.allowed_sessions:
            _hour = datetime.now(timezone.utc).hour
            if _hour < 8:         _sess = "asian"
            elif _hour < 13:      _sess = "london"
            elif _hour < 17:      _sess = "ny_open"
            elif _hour < 21:      _sess = "ny_close"
            else:                 _sess = "off_hours"
            if _sess not in self.allowed_sessions:
                self._log_reject(f"Session '{_sess}' not in allowed {self.allowed_sessions}")
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

        # Use 1.5x ATR for TP, 1.0x ATR for SL (wider to avoid spread rejection)
        sl_distance = atr_value * 1.0
        tp_distance = atr_value * 1.5

        # Ensure minimum distance using symbol-specific spread (3x spread for safety)
        from app.services.ml.symbol_config import get_symbol_config
        sym_cfg = get_symbol_config(self.symbol)
        spread = sym_cfg.get("spread_pips", 1.0)
        pip_size = spec.pip_size if spec.pip_size > 0 else 0.01
        min_distance = spread * pip_size * 3  # 3x spread for safety
        sl_distance = max(sl_distance, min_distance)
        tp_distance = max(tp_distance, min_distance)

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

        # 8. Position sizing — strictly % of balance
        sizing_mode = self.config.get("sizing_mode", "risk_pct")
        max_lot_size = self.config.get("max_lot_size", None)

        # Deferred warning if risk_per_trade was missing from config
        if getattr(self, "_risk_default_warn", False):
            self._log("warn", "risk_per_trade not in config, using 0.10% default")
            self._risk_default_warn = False

        if sizing_mode == "max_lots" and max_lot_size:
            # Mode 2: Max Lot Size — scale by confidence within user's cap
            conf = signal.confidence
            conf_pct = min(1.0, max(0.2, (conf - 0.52) / (0.95 - 0.52)))
            lot_size = max(1, int(round(max_lot_size * conf_pct)))
        else:
            # Mode 1: Risk % of Balance (DEFAULT)
            risk_pct = self.risk_config.get("risk_per_trade_pct", 0.001)
            risk_amount = balance * risk_pct
            lot_size = calc_lot_size(self.symbol, risk_amount, sl_distance, self.broker_name)
            self._log("info",
                f"Sizing: balance={balance:.0f} x risk={risk_pct*100:.2f}% = ${risk_amount:.2f}, "
                f"SL_dist={sl_distance:.2f}, units={lot_size:.1f}")

        # Oanda uses integer units
        if self.broker_name == "oanda":
            lot_size = max(1, int(round(lot_size)))

        # Hard cap — apply in BOTH sizing modes (was only max_lots before, which
        # left risk_pct mode without an upper bound and created oversized trades
        # when wide stops + high risk% collided).
        if max_lot_size and lot_size > max_lot_size:
            lot_size = int(max_lot_size)

        # Safety cap: never risk more than 5% of balance
        max_safe = int(balance * 0.05 / max(sl_distance, 1))
        if max_safe > 0 and lot_size > max_safe:
            self._log("warn", f"Lot size capped: {lot_size} → {max_safe} (safety)")
            lot_size = max_safe

        # 9. Build signal dict
        hour_utc = datetime.now(timezone.utc).hour
        direction_str = "BUY" if signal.direction == 1 else "SELL"

        # Determine trading session
        if hour_utc < 8:
            session_name = "asian"
        elif hour_utc < 13:
            session_name = "london"
        elif hour_utc < 17:
            session_name = "ny_open"
        elif hour_utc < 21:
            session_name = "ny_close"
        else:
            session_name = "off_hours"

        # Prop-firm final gate
        if self._risk_manager:
            try:
                approved, approved_risk_pct, reason = self._risk_manager.approve_trade(
                    symbol=self.symbol,
                    direction=direction_str,
                    confidence=float(signal.confidence),
                    atr=float(atr_value),
                    current_price=float(entry_price),
                    hour_utc=float(hour_utc),
                )
                if not approved:
                    self._log_reject(f"Prop-firm gate: {reason}")
                    return None
                if approved_risk_pct > 0 and approved_risk_pct < self.risk_config["risk_per_trade_pct"]:
                    self.risk_config["risk_per_trade_pct"] = approved_risk_pct
                    self._log("info", f"Prop-firm sized down: risk_pct -> {approved_risk_pct*100:.3f}%")
            except Exception as e:
                self._log("warn", f"RiskManager.approve_trade failed (non-fatal): {e}")

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
            # Analytics enrichment
            "session_name": session_name,
            "atr_at_entry": atr_value,
            "model_name": signal.reason,
        }

        self._signal_count += 1
        self._last_trade_bar = current_bar_index
        self._last_trade_time = _time.monotonic()
        import time as _wall_time
        self._last_trade_wall_time = _wall_time.time()

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
        prob_sum = np.zeros(3)
        prob_count = 0

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

            # Accumulate probs for HOLD logging
            if len(proba) == 3:
                prob_sum += proba
                prob_count += 1

            # Only consider directional predictions above threshold
            if direction != 0 and conf >= self.CONFIDENCE_THRESHOLD and conf > best_conf:
                best_conf = conf
                best_signal = PotentialSignal(
                    direction=direction,
                    confidence=conf,
                    reason=f"potential_{mtype}",
                    votes=votes,
                )

        # Persist prediction distribution for HOLD logging
        if prob_count > 0:
            avg = prob_sum / prob_count
            self._last_prediction = {
                "sell_prob": float(avg[0]),
                "hold_prob": float(avg[1]),
                "buy_prob": float(avg[2]),
                "votes": votes,
            }

        if best_signal is not None:
            best_signal.votes = votes

        return best_signal

    def _load_last_trade_time_from_db(self) -> float:
        """Recover last-trade wall-clock time from DB after restart."""
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
                if t and t.entry_time:
                    return t.entry_time.timestamp()
                return 0.0
            finally:
                db.close()
        except Exception:
            return 0.0

    # ── Hooks called by engine ─────────────────────────────────────────
    def on_position_opened(self):
        if self._risk_manager:
            try:
                self._risk_manager.open_position()
            except Exception:
                pass

    def on_position_closed(self, pnl: float):
        if self._risk_manager:
            try:
                self._risk_manager.close_position()
                self._risk_manager.record_trade_result(pnl)
            except Exception:
                pass

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
