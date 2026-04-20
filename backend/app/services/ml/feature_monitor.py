"""
Feature drift detection for live agents.

Compares live feature distributions against training baselines.
Logs a WARNING if any feature's z-score exceeds ±5σ from training mean.
Does NOT block trading — alerting only.

Training baseline is saved as:
  backend/data/ml_models/flowrex_{SYMBOL}_feature_stats.json

Usage:
  from app.services.ml.feature_monitor import check_drift
  check_drift(feature_vector_2d, feature_names, symbol)
"""
import os
import json
import logging
import numpy as np
from typing import Optional

logger = logging.getLogger("flowrex.feature_monitor")

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")
MODEL_DIR = os.path.normpath(MODEL_DIR)

# Alert threshold: feature value > this many σ from training mean → log warning
DRIFT_SIGMA_THRESHOLD = 5.0

# Cache loaded baselines in memory (loaded once per symbol)
_baselines: dict[str, dict] = {}


def _load_baseline(symbol: str) -> Optional[dict]:
    """Load the training feature stats baseline for a symbol."""
    if symbol in _baselines:
        return _baselines[symbol]

    path = os.path.join(MODEL_DIR, f"flowrex_{symbol}_feature_stats.json")
    if not os.path.exists(path):
        # Also try potential agent prefix
        path = os.path.join(MODEL_DIR, f"potential_{symbol}_feature_stats.json")
    if not os.path.exists(path):
        return None

    try:
        with open(path) as f:
            stats = json.load(f)
        _baselines[symbol] = stats
        return stats
    except Exception as e:
        logger.warning(f"Failed to load feature stats for {symbol}: {e}")
        return None


def check_drift(
    feature_vector: np.ndarray,
    feature_names: list[str],
    symbol: str,
    agent_id: int = 0,
) -> list[str]:
    """
    Check if the live feature vector has drifted from the training distribution.

    Args:
        feature_vector: 1D array of feature values (last bar's features)
        feature_names: list of feature names matching the vector
        symbol: trading symbol (e.g., "XAUUSD")
        agent_id: for log context

    Returns:
        List of drift warning strings (empty if no drift detected).
    """
    baseline = _load_baseline(symbol)
    if baseline is None:
        return []  # No baseline saved yet — skip silently

    warnings = []
    for i, fname in enumerate(feature_names):
        if fname not in baseline:
            continue

        stats = baseline[fname]
        mean = stats.get("mean", 0)
        std = stats.get("std", 1)
        if std <= 0:
            std = 1  # avoid division by zero

        value = float(feature_vector[i]) if i < len(feature_vector) else 0
        if np.isnan(value) or np.isinf(value):
            warnings.append(f"{fname}: NaN/Inf in live features")
            continue

        z = abs(value - mean) / std
        if z > DRIFT_SIGMA_THRESHOLD:
            warnings.append(
                f"{fname}: z={z:.1f}σ (live={value:.4f}, train_mean={mean:.4f}, train_std={std:.4f})"
            )

    if warnings:
        logger.warning(
            f"Feature drift detected for {symbol} (agent {agent_id}): "
            f"{len(warnings)} features outside {DRIFT_SIGMA_THRESHOLD}σ — "
            + "; ".join(warnings[:5])  # cap log length
        )

    return warnings


def clip_to_training_distribution(
    feature_vector: np.ndarray,
    feature_names: list[str],
    symbol: str,
    sigma: float = 5.0,
) -> tuple[np.ndarray, list[str]]:
    """
    Clip a live feature vector to [mean - sigma*std, mean + sigma*std] using the
    training baseline. Prevents scale-drift spikes (e.g. unnormalised dollar
    slopes on BTC at new all-time-highs, or ROC divisions by near-zero) from
    pushing the model into never-seen input regions.

    Returns (clipped_vector, list_of_clipped_feature_names). Vector is a copy —
    the input is never mutated. If no baseline exists, input is returned as-is.

    Safe to call on shape (N,) or (1, N); returns the same shape.
    """
    baseline = _load_baseline(symbol)
    if baseline is None:
        return feature_vector, []

    clipped = np.asarray(feature_vector, dtype=np.float64).copy()
    one_d = clipped.ndim == 1
    view = clipped if one_d else clipped[0]
    clipped_names: list[str] = []

    for i, fname in enumerate(feature_names):
        if fname not in baseline or i >= len(view):
            continue
        stats = baseline[fname]
        mean = float(stats.get("mean", 0.0))
        std = float(stats.get("std", 1.0))
        if std <= 0:
            continue  # degenerate — leave alone
        lo = mean - sigma * std
        hi = mean + sigma * std
        original = float(view[i])
        if np.isnan(original) or np.isinf(original):
            view[i] = mean
            clipped_names.append(fname)
            continue
        if original < lo or original > hi:
            view[i] = max(lo, min(hi, original))
            clipped_names.append(fname)

    if not one_d:
        clipped[0] = view
    return clipped, clipped_names


def save_training_stats(
    feature_names: list[str],
    X_train: np.ndarray,
    symbol: str,
    prefix: str = "flowrex",
):
    """
    Save feature distribution stats from training for drift detection.

    Called at the end of train_flowrex.py or train_potential.py.
    Saves {feature_name: {mean, std, min, max}} to a JSON file.
    """
    stats = {}
    for i, fname in enumerate(feature_names):
        col = X_train[:, i]
        valid = col[~np.isnan(col)]
        if len(valid) == 0:
            stats[fname] = {"mean": 0, "std": 1, "min": 0, "max": 0}
            continue
        stats[fname] = {
            "mean": float(np.mean(valid)),
            "std": float(np.std(valid)),
            "min": float(np.min(valid)),
            "max": float(np.max(valid)),
        }

    path = os.path.join(MODEL_DIR, f"{prefix}_{symbol}_feature_stats.json")
    with open(path, "w") as f:
        json.dump(stats, f, indent=2)
    logger.info(f"Saved training feature stats for {symbol}: {len(stats)} features → {path}")
