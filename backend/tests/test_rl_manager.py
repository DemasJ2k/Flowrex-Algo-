"""Tests for RL Trade Manager."""
import pytest
import numpy as np
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.ml.rl_trade_manager import (
    RLTradeManager, RLTradeDecision, build_rl_observation, ACTION_CONFIGS,
)


def test_fallback_without_model():
    """Without a trained model, should return NORMAL action."""
    mgr = RLTradeManager("US30")
    decision = mgr.decide(
        signal_direction=1, signal_confidence=0.6,
        feature_vector=np.zeros(210), feature_names=["f" + str(i) for i in range(210)],
    )
    assert decision.action == 2  # NORMAL
    assert decision.action_name == "NORMAL"
    assert decision.lot_multiplier == 1.0


def test_observation_shape():
    obs = build_rl_observation(
        signal_direction=1, signal_confidence=0.7,
        feature_vector=np.random.randn(210),
        feature_names=["atr_ratio", "htf_alignment", "ict_confluence",
                       "inst_vwap_dist_atr", "return_1", "return_3",
                       "return_5", "return_10", "return_20"] + ["f" + str(i) for i in range(201)],
        regime_id=2, regime_confidence=0.8,
        hour_utc=14,
        recent_trade_results=[0.01, -0.005, 0.02],
    )
    assert obs.shape == (20,)
    assert obs.dtype == np.float32


def test_observation_bounded():
    obs = build_rl_observation(
        signal_direction=-1, signal_confidence=0.9,
        feature_vector=np.random.randn(210) * 100,  # extreme values
        feature_names=["f" + str(i) for i in range(210)],
    )
    assert np.all(obs >= -5.0)
    assert np.all(obs <= 5.0)


def test_action_configs_complete():
    assert len(ACTION_CONFIGS) == 4
    for i in range(4):
        cfg = ACTION_CONFIGS[i]
        assert "name" in cfg
        assert "lot_mult" in cfg
        assert "sl_atr" in cfg
        assert "tp_atr" in cfg


def test_decision_dataclass():
    d = RLTradeDecision(action=1, action_name="SMALL",
                        lot_multiplier=0.5, sl_atr_mult=0.8, tp_atr_mult=1.5)
    assert d.action == 1
    assert d.lot_multiplier == 0.5


def test_regime_onehot():
    """Regime ID should create proper one-hot encoding."""
    for rid in range(4):
        obs = build_rl_observation(1, 0.5, np.zeros(10), [], regime_id=rid)
        # Check one-hot at indices 6-9
        assert obs[6 + rid] == 1.0
        for j in range(4):
            if j != rid:
                assert obs[6 + j] == 0.0


def test_session_timing_cyclical():
    """Hour encoding should be cyclical (sin/cos)."""
    obs_0h = build_rl_observation(1, 0.5, np.zeros(10), [], hour_utc=0)
    obs_12h = build_rl_observation(1, 0.5, np.zeros(10), [], hour_utc=12)
    obs_24h = build_rl_observation(1, 0.5, np.zeros(10), [], hour_utc=24)
    # 0h and 24h should be approximately equal (cyclical)
    np.testing.assert_allclose(obs_0h[11:13], obs_24h[11:13], atol=1e-5)
    # 0h and 12h should be different
    assert not np.allclose(obs_0h[11:13], obs_12h[11:13])
