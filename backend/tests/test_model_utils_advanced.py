"""Tests for the new model_utils functions: purged CV, SHAP filter, divergence check."""
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.model_utils import (
    purged_walk_forward_splits,
    check_train_test_divergence,
    check_min_signals,
    grade_model,
)


# ── purged_walk_forward_splits ─────────────────────────────────────────


def test_purged_splits_returns_list():
    splits = purged_walk_forward_splits(n=10000, n_folds=3, embargo_bars=50)
    assert isinstance(splits, list)
    assert len(splits) > 0


def test_purged_splits_count(capsys):
    splits = purged_walk_forward_splits(n=10000, n_folds=3, embargo_bars=50, warmup_bars=200)
    # First fold is skipped (train_end < warmup + 50), so we get at least 2 valid folds
    assert len(splits) >= 2


def test_purged_splits_no_overlap():
    """Train and test indices for each fold must not overlap."""
    splits = purged_walk_forward_splits(n=10000, n_folds=3, embargo_bars=50)
    for train_idx, test_idx in splits:
        overlap = np.intersect1d(train_idx, test_idx)
        assert len(overlap) == 0, "Train/test indices overlap within a fold"


def test_purged_splits_embargo_gap():
    """Test indices must start at least embargo_bars after train indices end."""
    embargo = 50
    splits = purged_walk_forward_splits(n=10000, n_folds=3, embargo_bars=embargo)
    for train_idx, test_idx in splits:
        gap = test_idx[0] - train_idx[-1]
        assert gap >= embargo, f"Embargo gap {gap} < {embargo}"


def test_purged_splits_train_before_test():
    """All train indices must precede all test indices (temporal order)."""
    splits = purged_walk_forward_splits(n=10000, n_folds=3, embargo_bars=50)
    for train_idx, test_idx in splits:
        assert train_idx[-1] < test_idx[0]


def test_purged_splits_fallback_small():
    """Very small dataset → falls back to single 80/20 split."""
    splits = purged_walk_forward_splits(n=100, n_folds=3, embargo_bars=50)
    assert len(splits) == 1


def test_purged_splits_indices_in_bounds():
    n = 5000
    splits = purged_walk_forward_splits(n=n, n_folds=3, embargo_bars=50)
    for train_idx, test_idx in splits:
        assert np.all(train_idx >= 0)
        assert np.all(test_idx < n)


# ── check_train_test_divergence ────────────────────────────────────────


def test_divergence_no_overfit():
    train = {"sharpe": 1.5, "win_rate": 57.0}
    test  = {"sharpe": 1.3, "win_rate": 55.0}
    result = check_train_test_divergence(train, test)
    assert result["overfit"] is False


def test_divergence_detects_sharpe_overfit():
    train = {"sharpe": 2.0, "win_rate": 60.0}
    test  = {"sharpe": 0.5, "win_rate": 55.0}  # Sharpe ratio = 0.25 < 0.80
    result = check_train_test_divergence(train, test)
    assert result["overfit"] is True
    assert result["sharpe_ratio"] < 0.80


def test_divergence_detects_wr_overfit():
    train = {"sharpe": 1.5, "win_rate": 70.0}
    test  = {"sharpe": 1.4, "win_rate": 50.0}  # WR ratio = 0.71 < 0.80
    result = check_train_test_divergence(train, test)
    assert result["overfit"] is True
    assert result["wr_ratio"] < 0.80


def test_divergence_zero_train_sharpe():
    """Zero train Sharpe → ratio should not blow up."""
    train = {"sharpe": 0.0, "win_rate": 0.0}
    test  = {"sharpe": 1.0, "win_rate": 55.0}
    result = check_train_test_divergence(train, test)
    assert isinstance(result, dict)
    assert "overfit" in result


def test_divergence_returns_warnings_list():
    train = {"sharpe": 2.0, "win_rate": 60.0}
    test  = {"sharpe": 0.3, "win_rate": 40.0}
    result = check_train_test_divergence(train, test)
    assert isinstance(result["warnings"], list)
    assert len(result["warnings"]) >= 1


# ── check_min_signals ──────────────────────────────────────────────────


def test_check_min_signals_pass():
    y = np.array([0] * 40 + [2] * 40 + [1] * 20)
    result = check_min_signals(y, min_signals=75)
    assert result is True


def test_check_min_signals_fail():
    y = np.array([0] * 20 + [2] * 20 + [1] * 60)
    result = check_min_signals(y, min_signals=75)
    assert result is False


def test_check_min_signals_all_hold():
    y = np.ones(200, dtype=int)
    result = check_min_signals(y, min_signals=75)
    assert result is False


def test_check_min_signals_exactly_at_threshold():
    y = np.array([0] * 38 + [2] * 37 + [1] * 25)
    result = check_min_signals(y, min_signals=75)
    assert result is True


# ── grade_model ────────────────────────────────────────────────────────


def test_grade_a():
    m = {"sharpe": 1.8, "win_rate": 58, "max_drawdown": 10, "total_return": 20}
    assert grade_model(m) == "A"


def test_grade_b():
    m = {"sharpe": 1.2, "win_rate": 52, "max_drawdown": 18, "total_return": 10}
    assert grade_model(m) == "B"


def test_grade_c():
    m = {"sharpe": 0.7, "win_rate": 47, "max_drawdown": 22, "total_return": 5}
    assert grade_model(m) == "C"


def test_grade_d():
    m = {"sharpe": 0.3, "win_rate": 48, "max_drawdown": 30, "total_return": 2}
    assert grade_model(m) == "D"


def test_grade_f():
    m = {"sharpe": -0.5, "win_rate": 40, "max_drawdown": 40, "total_return": -5}
    assert grade_model(m) == "F"


def test_grade_a_requires_all_criteria():
    """Sharpe > 1.5 alone is not enough for grade A."""
    m = {"sharpe": 2.0, "win_rate": 48, "max_drawdown": 10, "total_return": 10}
    assert grade_model(m) != "A"
