"""
Tests for features_correlation.py — cross-symbol correlation feature engineering.
"""
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_correlation import compute_correlation_features


def make_m5(n=500, seed=0, start_price=100.0):
    """Create synthetic M5 OHLCV DataFrame."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) * 300
    close = start_price + np.cumsum(rng.standard_normal(n) * 0.5)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "time":   t,
        "open":   close,
        "high":   close * 1.002,
        "low":    close * 0.998,
        "close":  close,
        "volume": np.ones(n) * 1000,
    })


# ── Basic shape / type tests ─────────────────────────────────────────────────

def test_no_peers_returns_empty():
    m5 = make_m5()
    names, X = compute_correlation_features("BTCUSD", m5, other_m5=None)
    assert names == []
    assert X.shape == (len(m5), 0)


def test_no_peers_empty_dict():
    m5 = make_m5()
    names, X = compute_correlation_features("BTCUSD", m5, other_m5={})
    assert names == []
    assert X.shape == (len(m5), 0)


def test_single_peer_shape():
    m5  = make_m5(n=600, seed=1)
    us30 = make_m5(n=600, seed=2, start_price=35000)
    names, X = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30})
    assert X.shape[0] == len(m5)
    assert len(names) == X.shape[1]
    assert X.shape[1] > 0


def test_two_peers_more_features_than_one():
    m5   = make_m5(n=600, seed=1)
    us30 = make_m5(n=600, seed=2)
    xau  = make_m5(n=600, seed=3)
    _, X1 = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30})
    _, X2 = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30, "XAUUSD": xau})
    assert X2.shape[1] > X1.shape[1]


def test_no_nan_in_output():
    m5   = make_m5(n=1000, seed=10)
    us30 = make_m5(n=1000, seed=11)
    xau  = make_m5(n=1000, seed=12)
    _, X = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30, "XAUUSD": xau})
    assert not np.any(np.isnan(X)), "NaN values found in correlation features"
    assert not np.any(np.isinf(X)), "Inf values found in correlation features"


def test_dtype_is_float32():
    m5   = make_m5(n=300, seed=5)
    us30 = make_m5(n=300, seed=6)
    _, X = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30})
    assert X.dtype == np.float32


def test_feature_names_are_strings():
    m5   = make_m5(n=300)
    us30 = make_m5(n=300, seed=1)
    names, _ = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30})
    assert all(isinstance(n, str) for n in names)


def test_risk_on_indicator_present_when_both_peers_available():
    m5   = make_m5(n=800)
    us30 = make_m5(n=800, seed=1)
    xau  = make_m5(n=800, seed=2)
    names, _ = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30, "XAUUSD": xau})
    assert "corr_risk_on_z"    in names
    assert "corr_risk_on_rank" in names


def test_risk_on_indicator_absent_when_only_one_peer():
    m5   = make_m5(n=800)
    us30 = make_m5(n=800, seed=1)
    names, _ = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30})
    assert "corr_risk_on_z"    not in names
    assert "corr_risk_on_rank" not in names


def test_mismatched_timestamps_handled():
    """Peer data may have different timestamps — should forward-fill safely."""
    m5 = make_m5(n=500, seed=0)
    # Peer has half the timestamps (sparse data — e.g. less liquid instrument)
    us30_sparse = make_m5(n=250, seed=1)
    # Use every 2nd timestamp from m5
    us30_sparse["time"] = m5["time"].values[::2]
    names, X = compute_correlation_features("BTCUSD", m5, other_m5={"US30": us30_sparse})
    assert X.shape[0] == len(m5)
    assert not np.any(np.isnan(X))


def test_peer_not_in_correlation_group_skipped():
    """EURUSD is not a natural peer of BTCUSD in CORRELATION_PEERS."""
    m5   = make_m5(n=300)
    eur  = make_m5(n=300, seed=7)
    # When only an off-group peer is supplied, features may be 0
    names, X = compute_correlation_features("BTCUSD", m5, other_m5={"EURUSD": eur})
    # Should not crash; output may be empty (EURUSD not in BTCUSD peers)
    assert isinstance(names, list)
    assert X.shape[0] == len(m5)


# ── Integration with compute_expert_features ─────────────────────────────────

def test_integration_with_compute_expert_features():
    import warnings
    warnings.filterwarnings("ignore")
    from app.services.ml.features_mtf import compute_expert_features

    m5   = make_m5(n=600, seed=20)
    us30 = make_m5(n=600, seed=21, start_price=35000)
    xau  = make_m5(n=600, seed=22, start_price=1900)

    names0, X0 = compute_expert_features(m5, symbol="BTCUSD", include_external=False)
    names1, X1 = compute_expert_features(
        m5, symbol="BTCUSD", include_external=False,
        other_m5={"US30": us30, "XAUUSD": xau},
    )

    assert len(names1) > len(names0), "Correlation features should increase feature count"
    assert X1.shape[0] == X0.shape[0], "Row count should be unchanged"
    assert not np.any(np.isnan(X1)), "No NaN in integrated output"

    # Verify new names start with 'corr_'
    new_names = set(names1) - set(names0)
    assert all(n.startswith("corr_") for n in new_names)
