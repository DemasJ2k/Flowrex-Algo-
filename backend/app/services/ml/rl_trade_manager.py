"""
PPO Reinforcement Learning Trade Manager
=========================================
Decides SKIP / SMALL / NORMAL / AGGRESSIVE for each signal.
Replaces fixed TP/SL with dynamic, learned trade sizing.

Action mapping:
  0: SKIP       — don't take this trade (0x lot)
  1: SMALL      — quick scalp (0.5x lot, SL=0.8×ATR, TP=1.5×ATR)
  2: NORMAL     — standard trade (1.0x lot, SL=1.2×ATR, TP=2.5×ATR)
  3: AGGRESSIVE — high conviction (1.5x lot, SL=1.5×ATR, TP=4.0×ATR)

Observation space: 20-dim focused summary of market state.
"""
import os
import numpy as np
from dataclasses import dataclass
from typing import Optional

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")


# ── Action Configurations ───────────��────────────────────────────────────

ACTION_CONFIGS = {
    0: {"name": "SKIP",       "lot_mult": 0.0, "sl_atr": 0.0, "tp_atr": 0.0},
    1: {"name": "SMALL",      "lot_mult": 0.5, "sl_atr": 0.8, "tp_atr": 1.5},
    2: {"name": "NORMAL",     "lot_mult": 1.0, "sl_atr": 1.2, "tp_atr": 2.5},
    3: {"name": "AGGRESSIVE", "lot_mult": 1.5, "sl_atr": 1.5, "tp_atr": 4.0},
}


@dataclass
class RLTradeDecision:
    """Output of the RL trade manager."""
    action: int
    action_name: str
    lot_multiplier: float
    sl_atr_mult: float
    tp_atr_mult: float
    confidence: float = 0.0


class RLTradeManager:
    """
    PPO-based trade sizing agent.
    Falls back to NORMAL (action=2) when no trained model is loaded.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._model = None
        self._loaded = False

    def load(self) -> bool:
        """Load trained PPO model from disk."""
        path = os.path.join(MODEL_DIR, f"rl_trade_manager_{self.symbol}.zip")
        if not os.path.exists(path):
            self._loaded = False
            return False
        try:
            from stable_baselines3 import PPO
            self._model = PPO.load(path)
            self._loaded = True
            return True
        except Exception:
            self._loaded = False
            return False

    def decide(
        self,
        signal_direction: int,
        signal_confidence: float,
        feature_vector: np.ndarray,
        feature_names: list[str],
        regime_id: int = 0,
        regime_confidence: float = 0.5,
        hour_utc: int = 12,
        recent_trade_results: list[float] = None,
    ) -> RLTradeDecision:
        """
        Decide trade sizing given current market state.

        Returns RLTradeDecision with action, lot_mult, SL, TP.
        Falls back to NORMAL when no model is loaded.
        """
        obs = build_rl_observation(
            signal_direction, signal_confidence,
            feature_vector, feature_names,
            regime_id, regime_confidence,
            hour_utc, recent_trade_results or [],
        )

        if not self._loaded or self._model is None:
            # No RL model — default to NORMAL
            cfg = ACTION_CONFIGS[2]
            return RLTradeDecision(
                action=2, action_name=cfg["name"],
                lot_multiplier=cfg["lot_mult"],
                sl_atr_mult=cfg["sl_atr"], tp_atr_mult=cfg["tp_atr"],
                confidence=1.0,
            )

        try:
            action, _ = self._model.predict(obs, deterministic=True)
            action = int(action)
        except Exception:
            action = 2  # fallback to NORMAL

        cfg = ACTION_CONFIGS.get(action, ACTION_CONFIGS[2])
        return RLTradeDecision(
            action=action, action_name=cfg["name"],
            lot_multiplier=cfg["lot_mult"],
            sl_atr_mult=cfg["sl_atr"], tp_atr_mult=cfg["tp_atr"],
            confidence=0.8,  # RL doesn't expose confidence easily
        )

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ── Observation Builder ────��─────────────────────────────────────────────

def build_rl_observation(
    signal_direction: int,
    signal_confidence: float,
    feature_vector: np.ndarray,
    feature_names: list[str],
    regime_id: int = 0,
    regime_confidence: float = 0.5,
    hour_utc: int = 12,
    recent_trade_results: list[float] = None,
) -> np.ndarray:
    """
    Build 20-dim observation for the PPO agent.
    Extracts key features from the full 210-feature vector.
    """
    if recent_trade_results is None:
        recent_trade_results = []

    def _get_feature(name: str, default: float = 0.0) -> float:
        if name in feature_names:
            idx = feature_names.index(name)
            if idx < len(feature_vector):
                val = float(feature_vector[idx])
                if np.isfinite(val):
                    return val
        return default

    obs = np.zeros(20, dtype=np.float32)

    # Signal info (2)
    obs[0] = float(signal_direction)           # +1 buy, -1 sell
    obs[1] = float(signal_confidence)          # 0-1

    # ICT/Strategy context (2)
    obs[2] = _get_feature("ict_confluence", 0.0)
    obs[3] = _get_feature("htf_alignment", 0.0)

    # Volatility & VWAP (2)
    obs[4] = _get_feature("atr_ratio", 0.0)
    obs[5] = _get_feature("inst_vwap_dist_atr", 0.0)

    # Regime one-hot (4)
    regime_id = max(0, min(3, regime_id))
    obs[6 + regime_id] = 1.0

    # Regime confidence (1)
    obs[10] = float(regime_confidence)

    # Session timing (2)
    hour_rad = 2 * np.pi * hour_utc / 24.0
    obs[11] = np.sin(hour_rad)
    obs[12] = np.cos(hour_rad)

    # Recent returns (5) — last 5 bars
    obs[13] = _get_feature("return_1", 0.0)
    obs[14] = _get_feature("return_3", 0.0)
    obs[15] = _get_feature("return_5", 0.0)
    obs[16] = _get_feature("return_10", 0.0)
    obs[17] = _get_feature("return_20", 0.0)

    # Recent trade performance (2)
    if len(recent_trade_results) > 0:
        obs[18] = np.mean(recent_trade_results[-20:])   # avg PnL of last 20
        obs[19] = sum(1 for r in recent_trade_results[-20:] if r > 0) / max(len(recent_trade_results[-20:]), 1)  # win rate
    else:
        obs[18] = 0.0
        obs[19] = 0.5  # neutral assumption

    return np.clip(obs, -5.0, 5.0)
