"""Tests for the monthly retrain pipeline."""
import pytest
import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.retrain_monthly import should_swap_model, GRADE_ORDER


# ── should_swap_model tests ─────────────────────────────────────────────────

def test_swap_first_model():
    """No old model → always swap."""
    swap, reason = should_swap_model(None, {"sharpe": 0.5}, None, "C")
    assert swap is True
    assert "first model" in reason


def test_swap_grade_improved():
    """New model has better grade → swap."""
    swap, reason = should_swap_model(
        {"sharpe": 1.0}, {"sharpe": 0.8},
        "C", "B",
    )
    assert swap is True
    assert "grade improved" in reason


def test_swap_sharpe_within_tolerance():
    """New Sharpe within 80% of old → swap."""
    swap, reason = should_swap_model(
        {"sharpe": 2.0}, {"sharpe": 1.7},
        "B", "B", sharpe_tolerance=0.8,
    )
    assert swap is True
    assert "tolerance" in reason


def test_no_swap_sharpe_too_low():
    """New Sharpe below 80% of old → don't swap."""
    swap, reason = should_swap_model(
        {"sharpe": 2.0}, {"sharpe": 1.0},
        "B", "C", sharpe_tolerance=0.8,
    )
    assert swap is False
    assert "did not pass" in reason


def test_swap_old_negative_new_positive():
    """Old model negative Sharpe, new positive → swap."""
    swap, reason = should_swap_model(
        {"sharpe": -1.0}, {"sharpe": 0.5},
        "F", "D",
    )
    assert swap is True


def test_no_swap_both_negative():
    """Both negative Sharpe, same grade → don't swap."""
    swap, reason = should_swap_model(
        {"sharpe": -0.5}, {"sharpe": -1.0},
        "F", "F",
    )
    assert swap is False
    assert "both models negative" in reason


def test_grade_order_completeness():
    """GRADE_ORDER covers all expected grades."""
    for g in ["A", "B", "C", "D", "F", None]:
        assert g in GRADE_ORDER


def test_swap_same_grade_higher_sharpe():
    """Same grade but higher Sharpe → swap."""
    swap, reason = should_swap_model(
        {"sharpe": 1.0}, {"sharpe": 1.5},
        "B", "B",
    )
    assert swap is True


def test_swap_tolerance_edge_case():
    """Exactly at tolerance boundary → swap."""
    swap, reason = should_swap_model(
        {"sharpe": 1.0}, {"sharpe": 0.8},
        "C", "C", sharpe_tolerance=0.8,
    )
    assert swap is True


# ── Rolling window index tests ──────────────────────────────────────────────

def test_rolling_window_indices():
    """Verify rolling window computation is correct."""
    # Simulate 1 year of M5 data at 288 bars/day
    n_bars = 288 * 365
    timestamps = np.arange(n_bars) * 300 + 1609459200  # 300s intervals from 2021-01-01

    now_ts = timestamps[-1]
    holdout_days = 14
    train_months = 6

    holdout_ts = now_ts - holdout_days * 86400
    train_ts = holdout_ts - int(train_months * 30.44 * 86400)

    holdout_idx = int(np.searchsorted(timestamps, holdout_ts))
    train_idx = int(np.searchsorted(timestamps, train_ts))

    n_train = holdout_idx - train_idx
    n_holdout = len(timestamps) - holdout_idx

    # 6 months ≈ 182 days × 288 bars = ~52,416 bars
    assert n_train > 40000, f"Train window too small: {n_train}"
    assert n_holdout > 3000, f"Holdout too small: {n_holdout}"
    assert train_idx < holdout_idx, "Train must come before holdout"
    assert holdout_idx < len(timestamps), "Holdout index in bounds"
