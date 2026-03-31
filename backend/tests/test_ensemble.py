"""Unit tests for ensemble voting logic."""
import numpy as np
from app.services.ml.ensemble_engine import EnsembleSignalEngine, Signal


class FakeModel:
    """Fake sklearn-like model for testing voting logic."""
    def __init__(self, pred_class: int, confidence: float):
        self._pred = pred_class
        self._conf = confidence

    def predict_proba(self, X):
        proba = np.zeros((1, 3))
        proba[0, self._pred] = self._conf
        remaining = 1 - self._conf
        for i in range(3):
            if i != self._pred:
                proba[0, i] = remaining / 2
        return proba


def _make_engine(pipeline: str, models: dict) -> EnsembleSignalEngine:
    engine = EnsembleSignalEngine("TEST", pipeline)
    for name, (pred, conf) in models.items():
        engine.models[name] = {"model": FakeModel(pred, conf), "feature_names": ["f1"]}
    return engine


def test_scalping_single_model_fires():
    """Scalping: one model with >= 55% on buy should fire."""
    engine = _make_engine("scalping", {"xgb": (2, 0.60)})  # buy at 60%
    features = np.array([1.0])
    signal = engine.predict(features)
    assert signal is not None
    assert signal.direction == 1  # buy
    assert signal.confidence >= 0.55


def test_scalping_low_confidence_rejected():
    """Scalping: confidence < 55% should be rejected."""
    engine = _make_engine("scalping", {"xgb": (2, 0.50)})
    signal = engine.predict(np.array([1.0]))
    assert signal is None


def test_scalping_hold_no_signal():
    """Scalping: hold prediction (class 1) should not fire."""
    engine = _make_engine("scalping", {"xgb": (1, 0.80)})
    signal = engine.predict(np.array([1.0]))
    assert signal is None


def test_expert_2_of_3_agreement():
    """Expert: 2/3 models agreeing on buy should fire."""
    engine = _make_engine("expert", {
        "xgb": (2, 0.60),  # buy
        "lgb": (2, 0.58),  # buy
        "lstm": (0, 0.55),  # sell (disagrees)
    })
    signal = engine.predict(np.array([1.0]))
    assert signal is not None
    assert signal.direction == 1  # buy
    assert signal.agreement == 2


def test_expert_no_consensus():
    """Expert: all different directions -> no signal."""
    engine = _make_engine("expert", {
        "xgb": (2, 0.60),  # buy
        "lgb": (0, 0.58),  # sell
        "other": (1, 0.70),  # hold
    })
    signal = engine.predict(np.array([1.0]))
    assert signal is None


def test_expert_low_weighted_confidence():
    """Expert: agreement but low confidence -> rejected."""
    engine = _make_engine("expert", {
        "xgb": (2, 0.40),  # buy but low conf
        "lgb": (2, 0.45),  # buy but low conf
    })
    signal = engine.predict(np.array([1.0]))
    assert signal is None


def test_nan_features_rejected():
    """NaN features should be rejected."""
    engine = _make_engine("scalping", {"xgb": (2, 0.60)})
    signal = engine.predict(np.array([np.nan]))
    assert signal is None
    assert engine.get_rejection_stats()["nan_features"] == 1


def test_no_models_loaded():
    """Empty engine should reject everything."""
    engine = EnsembleSignalEngine("TEST", "scalping")
    signal = engine.predict(np.array([1.0]))
    assert signal is None
