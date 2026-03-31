"""
HMM regime detector — classifies market state.
Regimes: trending_up, trending_down, ranging, volatile
"""
import os
import numpy as np
import pandas as pd
import joblib
from dataclasses import dataclass

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")

REGIME_NAMES = {0: "trending_up", 1: "trending_down", 2: "ranging", 3: "volatile"}


@dataclass
class RegimeResult:
    regime: str = "unknown"
    confidence: float = 0.0
    state_id: int = -1


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
