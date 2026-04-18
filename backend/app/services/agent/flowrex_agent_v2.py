"""
Flowrex Agent v2 — 4-layer MTF agent with 3-model ensemble.

Architecture:
    D1 bias → H4 momentum → H1 setup zone → M5 entry trigger
        ↓          ↓              ↓               ↓
      trend     direction      confluence       120 features
      filter    confirm        + key levels     → 3 models vote

Ensemble: XGBoost + LightGBM + CatBoost
  - Majority vote (2/3 agreement)
  - Weighted confidence averaging
  - All-3 agreement → +5% confidence bonus
  - Minimum threshold: 0.55

Position sizing: strictly % of balance
  - lot_size = (balance * risk_pct) / sl_distance
  - Integer units for Oanda
  - Safety cap: 5% of balance max per trade
"""
import os
import time as _time
import numpy as np
import joblib
from typing import Optional, Callable
from datetime import datetime, timezone
from dataclasses import dataclass, field

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")

MODEL_TYPES = ["xgboost", "lightgbm", "catboost"]  # catboost optional
MODEL_WEIGHTS = {"xgboost": 1.0, "lightgbm": 1.0, "catboost": 1.0}


@dataclass
class FlowrexSignal:
    direction: int  # 1=buy, -1=sell, 0=no signal
    confidence: float = 0.0
    reason: str = ""
    votes: dict = field(default_factory=dict)
    mtf_layers: dict = field(default_factory=dict)
    top_features: list = field(default_factory=list)


class FlowrexAgentV2:
    """
    Flowrex v2 trading agent — 4-layer MTF with 3-model ensemble.
    """

    CONFIDENCE_THRESHOLD = 0.55
    MIN_BARS = 300
    EXPECTED_FEATURE_COUNT = 120

    def __init__(self, agent_id: int, symbol: str, broker_name: str, config: dict = None):
        self.agent_id = agent_id
        self.symbol = symbol
        self.broker_name = broker_name
        self.config = config or {}

        self.models: dict[str, dict] = {}
        self.feature_names: list[str] = []

        # Safer default 0.001 (0.10%) instead of 0.01 (1.00%) — if config is missing
        # the key, the agent won't silently 10x its position sizing.
        _risk_per_trade = self.config.get("risk_per_trade")
        if _risk_per_trade is None:
            _risk_per_trade = 0.001
            # Deferred warning — _log_fn isn't set yet in __init__
            self._risk_default_warn = True
        else:
            self._risk_default_warn = False
        self.risk_config = {
            "max_drawdown_pct": self.config.get("max_drawdown_pct", 0.10),
            "daily_loss_limit_pct": self.config.get("max_daily_loss_pct", 0.03),
            "risk_per_trade_pct": _risk_per_trade,
            "max_trades_per_day": self.config.get("max_trades_per_day", 5),
        }
        self.cooldown_bars = self.config.get("cooldown_bars", 3)
        self.session_filter = self.config.get("session_filter", False)
        self.news_filter = self.config.get("news_filter_enabled", False)

        # Prop-firm tiered risk manager (opt-in via config).
        # When enabled, approve_trade() gates every signal with FTMO-grade
        # drawdown tiers, anti-martingale sizing, and session windows.
        self.prop_firm_enabled = self.config.get("prop_firm_enabled", False)
        self._risk_manager = None
        if self.prop_firm_enabled:
            try:
                from app.services.agent.risk_manager import RiskManager
                # Merge any user overrides onto the prop-firm defaults.
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
        self._reject_last_log_time: dict[str, float] = {}
        self._last_trade_bar = -999
        self._last_trade_time = 0.0  # monotonic seconds — robust to pause/resume
        # Wall-clock time of last trade (for cooldown persistence across restarts).
        # Loaded from DB on start; set on each trade open.
        self._last_trade_wall_time = 0.0
        self._h1_bars = None
        self._h4_bars = None
        self._d1_bars = None
        self._htf_fetch_time = 0
        self._peak_equity = 0.0
        self._daily_pnl = 0.0
        self._feature_cache_key = None
        self._feature_cache_result = None
        self._last_prediction: Optional[dict] = None  # for HOLD logging + drift

    def load(self) -> bool:
        """Load 3 ML models (XGBoost + LightGBM + CatBoost) from disk."""
        self.models.clear()
        loaded_any = False

        for mtype in MODEL_TYPES:
            path = os.path.join(MODEL_DIR, f"flowrex_{self.symbol}_M5_{mtype}.joblib")

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
            pipeline_version = data.get("pipeline_version", "?")

            if feat_count != self.EXPECTED_FEATURE_COUNT:
                self._log("error",
                    f"Feature count mismatch for {mtype}: model has {feat_count}, "
                    f"expected {self.EXPECTED_FEATURE_COUNT}")
                continue

            self.models[mtype] = data
            loaded_any = True

            if not self.feature_names and model_features:
                self.feature_names = model_features

            self._log("info",
                f"Loaded flowrex_v2 model: {mtype} "
                f"(grade={grade}, features={feat_count}, "
                f"pipeline={pipeline_version})")

        if not loaded_any:
            available = []
            try:
                for f in os.listdir(MODEL_DIR):
                    if f.startswith("flowrex_") and f.endswith(".joblib"):
                        available.append(f)
            except OSError:
                pass
            if available:
                self._log("error",
                    f"No models found for {self.symbol}. "
                    f"Available: {', '.join(sorted(available))}")
            else:
                self._log("error",
                    f"No flowrex models found in {MODEL_DIR}")

        self._log("info", f"Ensemble: {len(self.models)}/{len(MODEL_TYPES)} models loaded")
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
        """Evaluate current bars through 4-layer MTF filter + 3-model ensemble."""
        self._eval_count += 1

        # 1. Minimum bars
        if len(m5_bars) < self.MIN_BARS:
            self._log_reject("Insufficient bars", len(m5_bars))
            return None

        # 2. Cooldown — use wall time so restart doesn't reset it.
        cooldown_sec = self.cooldown_bars * 300  # M5 bars × 300s
        # Lazy-load last trade time from DB on first eval after start
        if self._last_trade_wall_time == 0.0:
            self._last_trade_wall_time = self._load_last_trade_time_from_db()
        import time as _wall_time
        now_wall = _wall_time.time()
        elapsed_sec = now_wall - self._last_trade_wall_time
        if self._last_trade_wall_time > 0 and elapsed_sec < cooldown_sec:
            self._log_reject("Cooldown", f"{int(elapsed_sec)}s/{cooldown_sec}s")
            return None

        # 3. News filter
        if self.news_filter:
            try:
                from app.services.news.newsapi_provider import check_high_impact_news
                news = check_high_impact_news(self.symbol)
                if not news.should_trade:
                    self._log_reject(f"News filter: {news.reason}")
                    return None
            except Exception:
                pass

        # 4. Risk checks
        if not self._check_risk(balance, daily_pnl, daily_trade_count):
            return None

        # 5. Fetch HTF context
        if broker_adapter:
            await self._refresh_htf_context(broker_adapter)

        # 6. 4-layer MTF filter
        import pandas as pd
        m5_df = pd.DataFrame(m5_bars)
        h1_df = pd.DataFrame(self._h1_bars) if self._h1_bars else None
        h4_df = pd.DataFrame(self._h4_bars) if self._h4_bars else None
        d1_df = pd.DataFrame(self._d1_bars) if self._d1_bars else None

        mtf_result = self._mtf_filter(m5_df, h1_df, h4_df, d1_df)
        if mtf_result["blocked"]:
            self._log_reject(f"MTF filter: {mtf_result['reason']}")
            return None

        # 7. Compute features
        from app.services.ml.features_flowrex import compute_flowrex_features

        cache_key = (m5_bars[-1]["time"], len(m5_bars))
        if self._feature_cache_key == cache_key and self._feature_cache_result is not None:
            feat_names, X = self._feature_cache_result
        else:
            feat_names, X = compute_flowrex_features(
                m5_df, h1_df, h4_df, d1_df, symbol=self.symbol
            )
            self._feature_cache_key = cache_key
            self._feature_cache_result = (feat_names, X)

        if X.shape[0] == 0:
            self._log_reject("Empty feature matrix")
            return None

        feature_vector = X[-1].reshape(1, -1)
        if np.any(np.isnan(feature_vector)) or np.any(np.isinf(feature_vector)):
            feature_vector = np.nan_to_num(feature_vector, nan=0.0, posinf=0.0, neginf=0.0)

        # Drift check (first eval after start + every 50 evals).
        # Warnings go to agent_logs; does NOT block trading.
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

        # 8. 3-model ensemble prediction with majority vote
        signal = self._predict_ensemble(feature_vector, mtf_result)

        if signal is None or signal.direction == 0:
            self._log_reject("No signal")
            return None

        # 9. (Gate B removed 2026-04-15) The D1-hard-veto was double-filtering because
        # fx_d1_bias is already a feature the model trains on, and the MTF score check
        # in _mtf_filter already requires 2-of-3 layers to agree before signal generation.
        # Removing this unblocks trading while preserving the score-based filter.

        # 10. ATR-based SL/TP
        from app.services.backtest.indicators import atr as compute_atr
        bar_highs = m5_df["high"].values.astype(float)
        bar_lows = m5_df["low"].values.astype(float)
        bar_closes = m5_df["close"].values.astype(float)
        atr_arr = compute_atr(bar_highs, bar_lows, bar_closes, 14)
        atr_value = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else 0.0

        if atr_value <= 0:
            self._log_reject("ATR is zero")
            return None

        from app.services.agent.instrument_specs import (
            calc_lot_size, get_spec, get_oanda_price_decimals,
        )

        last_bar = m5_bars[-1]
        entry_price = float(last_bar["close"])
        spec = get_spec(self.symbol)

        sl_distance = atr_value * 1.0
        tp_distance = atr_value * 1.5

        # Ensure minimum distance
        from app.services.ml.symbol_config import get_symbol_config
        sym_cfg = get_symbol_config(self.symbol)
        spread = sym_cfg.get("spread_pips", 1.0)
        pip_size = spec.pip_size if spec.pip_size > 0 else 0.01
        min_distance = spread * pip_size * 3
        sl_distance = max(sl_distance, min_distance)
        tp_distance = max(tp_distance, min_distance)

        if signal.direction == 1:  # BUY
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SELL
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance

        # Round prices
        price_digits = (
            get_oanda_price_decimals(self.symbol)
            if self.broker_name == "oanda"
            else max(0, len(str(spec.pip_size).rstrip("0").split(".")[-1]))
        )
        stop_loss = round(stop_loss, price_digits)
        take_profit = round(take_profit, price_digits)
        entry_price = round(entry_price, price_digits)

        # 11. Position sizing — strictly % of balance
        # Deferred warning for missing risk_per_trade key
        if getattr(self, "_risk_default_warn", False):
            self._log("warn", "risk_per_trade not in config, using 0.10% default")
            self._risk_default_warn = False
        risk_amount = balance * self.risk_config.get("risk_per_trade_pct", 0.001)
        lot_size = calc_lot_size(self.symbol, risk_amount, sl_distance, self.broker_name)

        if self.broker_name == "oanda":
            lot_size = max(1, int(round(lot_size)))

        # Hard cap from config
        max_lot_size = self.config.get("max_lot_size")
        if max_lot_size and lot_size > max_lot_size:
            lot_size = int(max_lot_size)

        # Safety cap: 5% of balance
        max_safe = int(balance * 0.05 / max(sl_distance, 1))
        if max_safe > 0 and lot_size > max_safe:
            self._log("warn", f"Lot size capped: {lot_size} -> {max_safe} (safety)")
            lot_size = max_safe

        # 12. Build signal dict
        direction_str = "BUY" if signal.direction == 1 else "SELL"

        hour_utc = datetime.now(timezone.utc).hour
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

        # Prop-firm final gate: approve_trade() runs the tiered DD + session + anti-martingale check.
        # If disabled (default), skip silently — preserves legacy behavior.
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
                # Let RiskManager override the risk_pct when it recommends a lower size
                # (e.g., after losses or in DD yellow tier).
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
            "agent_type": "flowrex_v2",
            "votes": signal.votes,
            "mtf_layers": signal.mtf_layers,
            # Analytics enrichment
            "session_name": session_name,
            "atr_at_entry": atr_value,
            "model_name": signal.reason,
            "mtf_score": mtf_result["score"],
            "top_features": signal.top_features if hasattr(signal, "top_features") else None,
        }

        self._signal_count += 1
        self._last_trade_bar = current_bar_index
        self._last_trade_time = _time.monotonic()
        import time as _wall_time
        self._last_trade_wall_time = _wall_time.time()

        self._log("signal",
            f"{direction_str} {self.symbol} @ {entry_price:.2f} | "
            f"SL:{stop_loss:.2f} TP:{take_profit:.2f} | "
            f"Lots:{lot_size} | Conf:{signal.confidence:.3f} | "
            f"MTF:{mtf_result['score']}/3",
            signal_dict)

        return signal_dict

    def _mtf_filter(self, m5_df, h1_df, h4_df, d1_df) -> dict:
        """
        4-layer MTF filter: D1 bias -> H4 momentum -> H1 setup -> M5 entry.
        Returns dict with bias, score, and whether signal is blocked.
        """
        from app.services.backtest.indicators import ema as calc_ema, rsi as calc_rsi

        result = {
            "d1_bias": 0, "h4_momentum": 0, "h1_setup": 0,
            "score": 0, "blocked": False, "reason": "",
        }

        # Layer 1: D1 bias (EMA21 direction)
        if d1_df is not None and len(d1_df) > 25:
            d1_closes = d1_df["close"].values.astype(float)
            d1_ema21 = calc_ema(d1_closes, 21)
            result["d1_bias"] = 1 if d1_closes[-1] > d1_ema21[-1] else -1

        # Layer 2: H4 momentum (RSI above/below 50 + EMA21 direction)
        if h4_df is not None and len(h4_df) > 25:
            h4_closes = h4_df["close"].values.astype(float)
            h4_ema21 = calc_ema(h4_closes, 21)
            h4_rsi = calc_rsi(h4_closes, 14)
            h4_trend = 1 if h4_closes[-1] > h4_ema21[-1] else -1
            h4_rsi_dir = 1 if h4_rsi[-1] > 50 else -1
            result["h4_momentum"] = h4_trend if h4_trend == h4_rsi_dir else 0

        # Layer 3: H1 setup (EMA21 trend + not overbought/oversold)
        if h1_df is not None and len(h1_df) > 25:
            h1_closes = h1_df["close"].values.astype(float)
            h1_ema21 = calc_ema(h1_closes, 21)
            h1_rsi = calc_rsi(h1_closes, 14)
            h1_trend = 1 if h1_closes[-1] > h1_ema21[-1] else -1
            # Block if RSI is extreme (overbought for buys, oversold for sells)
            if h1_trend == 1 and h1_rsi[-1] > 80:
                h1_trend = 0  # overbought, don't buy
            elif h1_trend == -1 and h1_rsi[-1] < 20:
                h1_trend = 0  # oversold, don't sell
            result["h1_setup"] = h1_trend

        # Score: how many layers agree
        layers = [result["d1_bias"], result["h4_momentum"], result["h1_setup"]]
        bull_count = sum(1 for l in layers if l == 1)
        bear_count = sum(1 for l in layers if l == -1)
        result["score"] = max(bull_count, bear_count)

        # Block if fewer than 2 layers agree on a direction
        if bull_count < 2 and bear_count < 2:
            result["blocked"] = True
            result["reason"] = f"Insufficient MTF alignment (bull={bull_count}, bear={bear_count})"

        return result

    def _predict_ensemble(self, X: np.ndarray, mtf_result: dict) -> Optional[FlowrexSignal]:
        """
        3-model majority vote ensemble.
        Requires 2/3 agreement on direction.
        All-3 agreement gets +5% confidence bonus.
        """
        if len(self.models) < 2:
            return None

        votes = {}
        directions = []
        confidences = []
        weights = []

        for mtype, data in self.models.items():
            model = data.get("model")
            if model is None:
                continue

            try:
                proba = model.predict_proba(X)[0]
            except Exception:
                continue

            pred = int(np.argmax(proba))
            conf = float(proba[pred])
            direction = {0: -1, 1: 0, 2: 1}.get(pred, 0)

            votes[mtype] = {"direction": direction, "confidence": round(conf, 4), "pred": pred}

            if direction != 0:
                directions.append(direction)
                confidences.append(conf)
                weights.append(MODEL_WEIGHTS.get(mtype, 1.0))

        # Persist ensemble-averaged class probabilities for HOLD logging + drift analysis.
        # Label encoding: 0=sell, 1=hold, 2=buy (matches create_labels).
        try:
            import numpy as _np
            prob_sum = _np.zeros(3)
            count = 0
            for mtype, data in self.models.items():
                model = data.get("model")
                if model is None:
                    continue
                try:
                    p = model.predict_proba(X)[0]
                    if len(p) == 3:
                        prob_sum += p
                        count += 1
                except Exception:
                    continue
            if count > 0:
                avg = prob_sum / count
                self._last_prediction = {
                    "sell_prob": float(avg[0]),
                    "hold_prob": float(avg[1]),
                    "buy_prob": float(avg[2]),
                    "votes": votes,
                }
        except Exception:
            pass

        if not directions:
            return None

        # Majority vote: need 2/3 on same direction
        buy_count = sum(1 for d in directions if d == 1)
        sell_count = sum(1 for d in directions if d == -1)

        if buy_count >= 2:
            chosen_dir = 1
            agreement = buy_count
        elif sell_count >= 2:
            chosen_dir = -1
            agreement = sell_count
        else:
            return None  # No majority

        # Weighted confidence of agreeing models
        agree_confs = []
        agree_weights = []
        for d, c, w in zip(directions, confidences, weights):
            if d == chosen_dir:
                agree_confs.append(c)
                agree_weights.append(w)

        total_w = sum(agree_weights)
        avg_conf = sum(c * w for c, w in zip(agree_confs, agree_weights)) / total_w if total_w > 0 else 0

        # All-3 agreement bonus
        if agreement == 3:
            avg_conf = min(avg_conf + 0.05, 0.99)

        if avg_conf < self.CONFIDENCE_THRESHOLD:
            return None

        dir_str = "buy" if chosen_dir == 1 else "sell"
        return FlowrexSignal(
            direction=chosen_dir,
            confidence=round(avg_conf, 4),
            reason=f"flowrex_v2_{dir_str}_{agreement}/3",
            votes=votes,
            mtf_layers={
                "d1_bias": mtf_result["d1_bias"],
                "h4_momentum": mtf_result["h4_momentum"],
                "h1_setup": mtf_result["h1_setup"],
                "mtf_score": mtf_result["score"],
            },
        )

    def _load_last_trade_time_from_db(self) -> float:
        """
        Recover the last-trade wall-clock time from DB after a restart.
        Returns 0.0 if no trade found — which means cooldown is immediately available.
        """
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
        """Called by engine after a trade is opened."""
        if self._risk_manager:
            try:
                self._risk_manager.open_position()
            except Exception:
                pass

    def on_position_closed(self, pnl: float):
        """Called by engine after a trade closes."""
        if self._risk_manager:
            try:
                self._risk_manager.close_position()
                self._risk_manager.record_trade_result(pnl)
            except Exception:
                pass

    def _check_risk(self, balance: float, daily_pnl: float, daily_trade_count: int) -> bool:
        """Risk checks."""
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
                self._log_reject(f"Max DD exceeded ({dd * 100:.1f}%)")
                return False

        # If prop-firm mode is enabled, sync state with RiskManager and
        # run its tiered DD / session / anti-martingale checks.
        if self._risk_manager:
            try:
                rm = self._risk_manager
                rm._daily_pnl = daily_pnl
                rm._trades_today = daily_trade_count
                rm._account_size = balance
                if rm.should_close_all():
                    self._log_reject("Prop-firm hard stop (daily DD hit limit)")
                    return False
            except Exception:
                pass

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
            "agent_type": "flowrex_v2",
            "symbol": self.symbol,
            "evaluations": self._eval_count,
            "signals": self._signal_count,
            "rejections": self._reject_count,
            "models_loaded": list(self.models.keys()),
            "feature_count": len(self.feature_names),
            "ensemble_size": len(self.models),
        }

    def _log(self, level: str, message: str, data: dict = None):
        if self._log_fn:
            self._log_fn(level, message, data)

    def _log_reject(self, reason: str, extra=None):
        self._reject_count += 1
        now = _time.monotonic()
        last_time = self._reject_last_log_time.get(reason, 0.0)
        if now - last_time >= 60.0:
            self._reject_last_log_time[reason] = now
            msg = f"[FlowrexV2] Rejected: {reason}"
            if extra is not None:
                msg += f" ({extra})"
            self._log("reject", msg)
