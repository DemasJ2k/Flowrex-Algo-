"""Unit tests for the v2 meta-labeling pipeline."""

import os
import tempfile

import numpy as np
import pytest

from app.services.ml.meta_labeler_v2 import MetaLabeler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_data(n=500, seed=42):
    """
    Generate synthetic features, primary signals, and actual outcomes.

    Returns:
        X: (n, 20) feature matrix
        primary_signals: array of +1, -1, 0
        actual_outcomes: array of +1, -1, 0
        feature_names: list of 20 feature names
    """
    rng = np.random.RandomState(seed)

    X = rng.randn(n, 20).astype(np.float64)

    # Primary signals: ~40% BUY, ~40% SELL, ~20% HOLD
    raw = rng.choice([1, -1, 0], size=n, p=[0.4, 0.4, 0.2])
    primary_signals = raw.astype(np.int8)

    # Actual outcomes: correlated with primary signals ~60% of the time
    actual_outcomes = np.copy(primary_signals)
    flip_mask = rng.rand(n) < 0.4  # flip 40% to simulate imperfect primary model
    actual_outcomes[flip_mask] = rng.choice([1, -1, 0], size=flip_mask.sum())

    feature_names = [f"feat_{i}" for i in range(18)] + ["regime_hmm", "atr_14"]

    return X, primary_signals, actual_outcomes, feature_names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMetaLabelerV2:
    """Tests for MetaLabeler v2."""

    def test_instantiation(self):
        """MetaLabeler can be instantiated with default and custom params."""
        ml = MetaLabeler()
        assert ml.threshold == 0.6
        assert ml.model_type == "lightgbm"

        ml2 = MetaLabeler(threshold=0.7, model_type="lightgbm")
        assert ml2.threshold == 0.7

    def test_fit_returns_metrics(self):
        """fit() trains on valid data and returns a metrics dict."""
        X, signals, outcomes, names = _synthetic_data()
        ml = MetaLabeler()
        metrics = ml.fit(X, signals, outcomes, feature_names=names)

        assert isinstance(metrics, dict)
        for key in ["accuracy", "precision", "recall", "f1", "auc"]:
            assert key in metrics
            assert 0.0 <= metrics[key] <= 1.0

        assert ml._is_fitted is True

    def test_predict_confidence_range(self):
        """predict_confidence returns probabilities in [0, 1]."""
        X, signals, outcomes, names = _synthetic_data()
        ml = MetaLabeler()
        ml.fit(X, signals, outcomes, feature_names=names)

        conf = ml.predict_confidence(X, signals)
        assert conf.shape == (len(X),)
        assert np.all(conf >= 0.0)
        assert np.all(conf <= 1.0)

    def test_predict_confidence_zero_for_hold(self):
        """predict_confidence returns 0 for HOLD signals."""
        X, signals, outcomes, names = _synthetic_data()
        ml = MetaLabeler()
        ml.fit(X, signals, outcomes, feature_names=names)

        hold_mask = signals == 0
        assert hold_mask.sum() > 0, "Test data should contain HOLD signals"

        conf = ml.predict_confidence(X, signals)
        assert np.all(conf[hold_mask] == 0.0)

    def test_filter_signals_removes_low_confidence(self):
        """filter_signals sets low-confidence signals to 0 (HOLD)."""
        X, signals, outcomes, names = _synthetic_data()
        ml = MetaLabeler(threshold=0.6)
        ml.fit(X, signals, outcomes, feature_names=names)

        filtered = ml.filter_signals(signals, X)

        # Some signals should have been filtered out
        active_orig = np.sum(signals != 0)
        active_filtered = np.sum(filtered != 0)
        assert active_filtered <= active_orig
        # With threshold=0.6, at least some should be removed
        assert active_filtered < active_orig, "Expected some signals to be filtered"

    def test_filter_signals_preserves_high_confidence(self):
        """filter_signals preserves signals where meta-model is confident."""
        X, signals, outcomes, names = _synthetic_data()
        ml = MetaLabeler(threshold=0.0)  # accept everything
        ml.fit(X, signals, outcomes, feature_names=names)

        filtered = ml.filter_signals(signals, X)

        # With threshold=0, all non-HOLD signals should be preserved
        active_mask = signals != 0
        assert np.array_equal(filtered[active_mask], signals[active_mask])

    def test_save_load_roundtrip(self):
        """save/load round-trip preserves model and produces same predictions."""
        X, signals, outcomes, names = _synthetic_data()
        ml = MetaLabeler(threshold=0.55)
        ml.fit(X, signals, outcomes, feature_names=names)

        conf_before = ml.predict_confidence(X, signals)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "meta_model.joblib")
            ml.save(path)
            assert os.path.exists(path)

            ml_loaded = MetaLabeler.load(path)
            assert ml_loaded.threshold == 0.55
            assert ml_loaded._is_fitted is True

            conf_after = ml_loaded.predict_confidence(X, signals)
            np.testing.assert_array_almost_equal(conf_before, conf_after, decimal=10)

    def test_feature_importance_after_fit(self):
        """get_feature_importance returns a dict after fitting."""
        X, signals, outcomes, names = _synthetic_data()
        ml = MetaLabeler()
        ml.fit(X, signals, outcomes, feature_names=names)

        importance = ml.get_feature_importance()
        assert isinstance(importance, dict)
        assert len(importance) > 0

        # Check meta-features are present
        assert "primary_signal_direction" in importance
        assert "primary_confidence" in importance

        # Values should sum to ~1.0 (normalised)
        total = sum(importance.values())
        assert abs(total - 1.0) < 0.01

    def test_feature_importance_none_before_fit(self):
        """get_feature_importance returns None before fitting."""
        ml = MetaLabeler()
        assert ml.get_feature_importance() is None

    def test_fit_all_same_direction(self):
        """fit() handles edge case: all signals are the same direction."""
        rng = np.random.RandomState(42)
        n = 200
        X = rng.randn(n, 10)
        signals = np.ones(n, dtype=np.int8)  # all BUY
        outcomes = rng.choice([1, -1], size=n)

        ml = MetaLabeler()
        metrics = ml.fit(X, signals, outcomes)

        assert isinstance(metrics, dict)
        assert ml._is_fitted is True
        # Should still produce valid confidences
        conf = ml.predict_confidence(X, signals)
        assert conf.shape == (n,)
        assert np.all(conf >= 0.0)
        assert np.all(conf <= 1.0)

    def test_fit_very_few_samples(self):
        """fit() handles edge case: very few training samples (< 50)."""
        rng = np.random.RandomState(42)
        n = 30
        X = rng.randn(n, 10)
        signals = rng.choice([1, -1], size=n).astype(np.int8)
        outcomes = rng.choice([1, -1], size=n).astype(np.int8)

        ml = MetaLabeler()
        metrics = ml.fit(X, signals, outcomes)

        assert isinstance(metrics, dict)
        assert ml._is_fitted is True

        conf = ml.predict_confidence(X, signals)
        assert len(conf) == n
        assert np.all(conf >= 0.0)
        assert np.all(conf <= 1.0)
