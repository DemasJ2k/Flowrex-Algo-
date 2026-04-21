"""
Regime detection — classifies current market state for agent gating.

Two classifiers available:
- `classify_regime_simple()` — deterministic, rule-based using ATR, ADX, EMA
  slope. Works for any symbol with no training required. This is what the
  V2 and Potential agents use for the regime filter gate.
- `RegimeDetector` — trained HMM, used by the deprecated FlowrexAgent.

Regimes: trending_up, trending_down, ranging, volatile
"""
import os
import numpy as np
import pandas as pd
import joblib
from dataclasses import dataclass
from typing import Sequence

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")

REGIME_NAMES = {0: "trending_up", 1: "trending_down", 2: "ranging", 3: "volatile"}


@dataclass
class RegimeResult:
    regime: str = "unknown"
    confidence: float = 0.0
    state_id: int = -1


def classify_regime_simple(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    atr_window: int = 14,
    adx_window: int = 14,
    atr_vol_lookback: int = 100,
    atr_vol_percentile: float = 75.0,
    ema_period: int = 50,
    ema_slope_lookback: int = 20,
    adx_trend_threshold: float = 25.0,
    adx_range_threshold: float = 20.0,
) -> RegimeResult:
    """
    Rule-based market-regime classifier. Deterministic, no training required.

    Decision tree, in order:
      1. Current ATR > `atr_vol_percentile` of the last `atr_vol_lookback`
         bars → **volatile**. One-way gate: volatile markets shouldn't
         trade trend-following or mean-reverting strategies without first
         confirming the regime has calmed.
      2. ADX < `adx_range_threshold` → **ranging**. Weak trend strength =
         chop; mean-reversion edge may exist but breakout/trend edge won't.
      3. Otherwise, sign of EMA(`ema_period`) slope over `ema_slope_lookback`:
           positive → **trending_up**
           negative → **trending_down**
      4. If we have insufficient data for any of the above, return
         "unknown" with confidence 0.

    Confidence is heuristic:
      - volatile / ranging: 1.0 when threshold is clearly exceeded,
        scales down near the boundary
      - trending_*: scales with |EMA slope| normalised by ATR

    Returns `RegimeResult`. Callers that need the deprecated HMM-based
    classifier can still use `RegimeDetector`.
    """
    from app.services.backtest.indicators import atr as atr_fn
    from app.services.backtest.indicators import adx as adx_fn
    from app.services.backtest.indicators import ema as ema_fn

    try:
        h_arr = np.asarray(highs, dtype=np.float64)
        l_arr = np.asarray(lows, dtype=np.float64)
        c_arr = np.asarray(closes, dtype=np.float64)
    except Exception:
        return RegimeResult()

    min_bars = max(atr_vol_lookback, ema_period + ema_slope_lookback, adx_window * 2) + 5
    if len(c_arr) < min_bars:
        return RegimeResult()

    # ── ATR + volatility percentile ──────────────────────────────────
    try:
        atr_series = atr_fn(h_arr, l_arr, c_arr, atr_window)
    except Exception:
        return RegimeResult()
    current_atr = float(atr_series[-1])
    if np.isnan(current_atr):
        return RegimeResult()

    recent_atr = atr_series[-atr_vol_lookback:]
    recent_atr = recent_atr[~np.isnan(recent_atr)]
    if len(recent_atr) < 20:
        return RegimeResult()
    atr_threshold = float(np.percentile(recent_atr, atr_vol_percentile))
    if atr_threshold > 0 and current_atr >= atr_threshold:
        # Confidence: how far above threshold we are, capped at 1.0
        over_pct = (current_atr - atr_threshold) / atr_threshold
        conf = min(1.0, 0.6 + over_pct)
        return RegimeResult(regime="volatile", confidence=conf, state_id=3)

    # ── ADX ──────────────────────────────────────────────────────────
    try:
        adx_val, _plus_di, _minus_di = adx_fn(h_arr, l_arr, c_arr, adx_window)
    except Exception:
        return RegimeResult()
    current_adx = float(adx_val[-1])
    if np.isnan(current_adx):
        current_adx = 0.0

    if current_adx < adx_range_threshold:
        conf = min(1.0, (adx_range_threshold - current_adx) / adx_range_threshold + 0.5)
        return RegimeResult(regime="ranging", confidence=conf, state_id=2)

    # ── EMA slope ────────────────────────────────────────────────────
    try:
        ema_series = ema_fn(c_arr, ema_period)
    except Exception:
        return RegimeResult()
    if len(ema_series) < ema_slope_lookback + 1:
        return RegimeResult()
    ema_now = float(ema_series[-1])
    ema_past = float(ema_series[-1 - ema_slope_lookback])
    if np.isnan(ema_now) or np.isnan(ema_past) or ema_past == 0:
        return RegimeResult()

    slope = (ema_now - ema_past) / max(current_atr, 1e-9)
    # normalise slope by ATR to get an "ATR-units per bar" figure; >0.1
    # over the lookback is a meaningful trend on M5.
    if slope > 0:
        conf = min(1.0, abs(slope) / 2.0 + 0.5) if current_adx > adx_trend_threshold else 0.5
        return RegimeResult(regime="trending_up", confidence=conf, state_id=0)
    else:
        conf = min(1.0, abs(slope) / 2.0 + 0.5) if current_adx > adx_trend_threshold else 0.5
        return RegimeResult(regime="trending_down", confidence=conf, state_id=1)


def regime_size_multiplier(regime: str) -> float:
    """Soft-sizing multiplier applied AFTER the hard-gate check passes.

    The agent's base risk is multiplied by this. Designed so a regime gate
    set to allow all four still de-risks when conditions are unfavourable.
    """
    return {
        "trending_up":   1.10,
        "trending_down": 1.10,
        "ranging":       0.80,
        "volatile":      0.60,
        "unknown":       1.00,
    }.get(regime, 1.0)


class RegimeDetector:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.model = None

    def load(self) -> bool:
        path = os.path.join(MODEL_DIR, f"expert_{self.symbol}_M5_regime.joblib")
        if not os.path.exists(path):
            return False
        data = joblib.load(path)
        self.model = data.get("model")
        return self.model is not None

    def predict_regime(self, closes: np.ndarray, volumes: np.ndarray) -> RegimeResult:
        """
        Predict current market regime from recent price/volume data.
        Needs at least 30 bars.
        """
        if self.model is None:
            return RegimeResult()

        if len(closes) < 30:
            return RegimeResult()

        try:
            returns = np.diff(np.log(closes))
            vol = pd.Series(returns).rolling(20).std().values
            vol_ratio = volumes[1:] / pd.Series(volumes[1:]).rolling(20).mean().values

            X = np.column_stack([returns, vol, vol_ratio])
            mask = ~np.any(np.isnan(X), axis=1) & ~np.any(np.isinf(X), axis=1)
            X_clean = X[mask]

            if len(X_clean) < 5:
                return RegimeResult()

            # Predict most likely state for latest observation
            states = self.model.predict(X_clean)
            current_state = int(states[-1])

            # Confidence from posterior probabilities
            posteriors = self.model.predict_proba(X_clean)
            confidence = float(posteriors[-1, current_state])

            # Map state to regime name
            # Determine regime names by analyzing the state means
            means = self.model.means_
            return_means = means[:, 0]  # first feature is returns
            vol_means = means[:, 1]     # second feature is volatility

            # Sort states by return mean to assign names
            sorted_states = np.argsort(return_means)
            regime_map = {}
            n_states = len(sorted_states)
            if n_states >= 4:
                regime_map[sorted_states[0]] = "trending_down"
                regime_map[sorted_states[1]] = "ranging"
                regime_map[sorted_states[2]] = "ranging"  # second ranging if 4 states
                regime_map[sorted_states[3]] = "trending_up"
                # Override: highest volatility state is "volatile"
                vol_state = np.argmax(vol_means)
                regime_map[vol_state] = "volatile"
            else:
                for i, s in enumerate(sorted_states):
                    regime_map[s] = REGIME_NAMES.get(i, "unknown")

            regime_name = regime_map.get(current_state, "unknown")

            return RegimeResult(
                regime=regime_name,
                confidence=confidence,
                state_id=current_state,
            )
        except Exception:
            return RegimeResult()
