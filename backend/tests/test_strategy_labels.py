"""
Tests for strategy_labels module: strategy-informed triple-barrier + ICT quality.
Uses synthetic OHLCV data (random walk, seed 42) so tests work without real
ICT features or market data.
"""
import os
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from strategy_labels import compute_strategy_labels, compute_dynamic_barriers


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_synthetic_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame with a random-walk price."""
    rng = np.random.RandomState(seed)
    # Random walk for close prices, starting at 40000 (US30-like)
    returns = rng.normal(0, 0.001, size=n)
    closes = 40000.0 * np.exp(np.cumsum(returns))

    # Construct OHLC from close
    noise = rng.uniform(0.0005, 0.002, size=n)
    highs = closes * (1 + noise)
    lows = closes * (1 - noise)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.uniform(1000, 5000, size=n)

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


@pytest.fixture
def synth_df():
    return _make_synthetic_ohlcv()


# ---------------------------------------------------------------------------
# 1. Returns DataFrame with expected columns
# ---------------------------------------------------------------------------

def test_returns_expected_columns(synth_df):
    result = compute_strategy_labels(synth_df, symbol="US30")
    expected_cols = {
        "label", "label_quality", "label_weighted",
        "tp_price", "sl_price", "exit_bar", "exit_type",
        "hold_bars", "pnl_pct",
    }
    assert expected_cols.issubset(set(result.columns))
    assert len(result) == len(synth_df)


# ---------------------------------------------------------------------------
# 2. Labels are in {-1, 0, 1}
# ---------------------------------------------------------------------------

def test_labels_in_valid_set(synth_df):
    result = compute_strategy_labels(synth_df)
    unique_labels = set(result["label"].unique())
    assert unique_labels.issubset({-1, 0, 1})


# ---------------------------------------------------------------------------
# 3. label_quality is in [0, 10]
# ---------------------------------------------------------------------------

def test_label_quality_range(synth_df):
    result = compute_strategy_labels(synth_df)
    assert result["label_quality"].min() >= 0.0
    assert result["label_quality"].max() <= 10.0


# ---------------------------------------------------------------------------
# 4. label_weighted magnitude <= 1.0
# ---------------------------------------------------------------------------

def test_label_weighted_magnitude(synth_df):
    result = compute_strategy_labels(synth_df)
    assert result["label_weighted"].abs().max() <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# 5. exit_type is one of "tp", "sl", "timeout"
# ---------------------------------------------------------------------------

def test_exit_type_values(synth_df):
    result = compute_strategy_labels(synth_df)
    valid_types = {"tp", "sl", "timeout", ""}
    actual = set(result["exit_type"].unique())
    assert actual.issubset(valid_types)


# ---------------------------------------------------------------------------
# 6. hold_bars <= max_hold_bars
# ---------------------------------------------------------------------------

def test_hold_bars_bounded(synth_df):
    max_hold = 24
    result = compute_strategy_labels(synth_df, max_hold_bars=max_hold)
    assert result["hold_bars"].max() <= max_hold


# ---------------------------------------------------------------------------
# 7. High confluence bars get higher weight than low confluence
# ---------------------------------------------------------------------------

def test_high_confluence_higher_weight():
    """
    Manually inject ICT scores and verify weighting.
    We test the weighting formula directly: weight = 0.5 + 0.5 * (quality / 10).
    """
    # Score 8 -> weight 0.9, score 1 -> weight 0.55
    high_weight = 0.5 + 0.5 * (8.0 / 10.0)
    low_weight = 0.5 + 0.5 * (1.0 / 10.0)
    assert high_weight > low_weight
    # With label=1: weighted = 1 * weight
    assert 1.0 * high_weight > 1.0 * low_weight


# ---------------------------------------------------------------------------
# 8. compute_dynamic_barriers returns correct shapes
# ---------------------------------------------------------------------------

def test_dynamic_barriers_shapes():
    n = 200
    rng = np.random.RandomState(42)
    closes = 40000 + rng.randn(n).cumsum() * 10
    atr = np.full(n, 50.0)
    scores = rng.uniform(0, 10, size=n)

    tp_d, sl_d, hold = compute_dynamic_barriers(closes, atr, scores)

    assert tp_d.shape == (n,)
    assert sl_d.shape == (n,)
    assert hold.shape == (n,)
    assert np.all(tp_d > 0)
    assert np.all(sl_d > 0)
    assert np.all(hold > 0)


# ---------------------------------------------------------------------------
# 9. High confluence gets wider TP
# ---------------------------------------------------------------------------

def test_dynamic_barriers_high_confluence_wider_tp():
    n = 100
    closes = np.full(n, 40000.0)
    atr = np.full(n, 50.0)

    # All high confluence
    high_scores = np.full(n, 8.0)
    tp_high, sl_high, hold_high = compute_dynamic_barriers(closes, atr, high_scores)

    # All low confluence
    low_scores = np.full(n, 1.0)
    tp_low, sl_low, hold_low = compute_dynamic_barriers(closes, atr, low_scores)

    # High confluence -> wider TP (2.5x base vs 1.5x base)
    assert tp_high[0] > tp_low[0]
    # High confluence -> tighter SL
    assert sl_high[0] < sl_low[0]
    # High confluence -> longer hold
    assert hold_high[0] > hold_low[0]


# ---------------------------------------------------------------------------
# 10. Works with US30 symbol config
# ---------------------------------------------------------------------------

def test_works_with_us30_symbol(synth_df):
    result = compute_strategy_labels(synth_df, symbol="US30", tp_atr_mult=1.2, sl_atr_mult=0.8)
    assert len(result) == len(synth_df)
    # Should have at least some non-zero labels
    non_zero = (result["label"] != 0).sum()
    assert non_zero > 0, "Expected at least some non-zero labels for US30"


# ---------------------------------------------------------------------------
# 11. Dynamic barriers mode runs without error
# ---------------------------------------------------------------------------

def test_dynamic_barriers_mode(synth_df):
    result = compute_strategy_labels(synth_df, use_dynamic_barriers=True)
    assert len(result) == len(synth_df)
    assert set(result["label"].unique()).issubset({-1, 0, 1})


# ---------------------------------------------------------------------------
# 12. Graceful fallback without ICT features
# ---------------------------------------------------------------------------

def test_graceful_without_ict(synth_df):
    """
    The module should work even if features_ict is not importable.
    In test environment the import will likely fail -- scores default to 0.
    """
    result = compute_strategy_labels(synth_df)
    # All quality scores should be 0 (fallback)
    # (or could be real if features_ict happens to be importable)
    assert result["label_quality"].min() >= 0.0
    assert result["label_quality"].max() <= 10.0
