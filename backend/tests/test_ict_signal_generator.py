"""Tests for ICT rule-based signal generator."""
import numpy as np
import pandas as pd
from app.services.agent.ict_signal_generator import generate_swing_signals


def _make_h4(n=500, trend="up"):
    np.random.seed(42)
    if trend == "up":
        closes = np.cumsum(np.abs(np.random.randn(n)) * 5 + 2) + 40000
    elif trend == "down":
        closes = 45000 - np.cumsum(np.abs(np.random.randn(n)) * 5 + 2)
    else:
        closes = np.cumsum(np.random.randn(n) * 10) + 40000
    df = pd.DataFrame({
        "time": np.arange(1700000000, 1700000000 + n * 14400, 14400)[:n],
        "open": np.roll(closes, 1),
        "high": closes + np.abs(np.random.randn(n) * 30),
        "low": closes - np.abs(np.random.randn(n) * 30),
        "close": closes,
        "volume": np.random.randint(100, 5000, n),
    })
    df.iloc[0, df.columns.get_loc("open")] = df.iloc[0]["close"]
    return df


def test_returns_dataframe():
    result = generate_swing_signals(_make_h4())
    assert isinstance(result, pd.DataFrame)
    assert "signal" in result.columns
    assert "strength" in result.columns
    assert "rules" in result.columns


def test_signal_values():
    result = generate_swing_signals(_make_h4(300, "mixed"))
    unique = set(result["signal"].unique())
    assert unique.issubset({-1, 0, 1})


def test_strength_range():
    result = generate_swing_signals(_make_h4())
    assert result["strength"].min() >= 0
    assert result["strength"].max() <= 7


def test_generates_some_signals():
    result = generate_swing_signals(_make_h4(1000, "mixed"), min_rules=2)
    n_signals = (result["signal"] != 0).sum()
    assert n_signals > 0, "Expected at least some signals with min_rules=2"


def test_min_rules_filter():
    result_low = generate_swing_signals(_make_h4(500, "mixed"), min_rules=2)
    result_high = generate_swing_signals(_make_h4(500, "mixed"), min_rules=4)
    assert (result_low["signal"] != 0).sum() >= (result_high["signal"] != 0).sum()


def test_tp_sl_set_for_signals():
    result = generate_swing_signals(_make_h4(500, "mixed"), min_rules=2)
    signal_mask = result["signal"] != 0
    if signal_mask.sum() > 0:
        assert result.loc[signal_mask, "tp_price"].notna().all()
        assert result.loc[signal_mask, "sl_price"].notna().all()


def test_with_d1_data():
    h4 = _make_h4(500, "mixed")
    np.random.seed(99)
    d1_c = np.cumsum(np.random.randn(100) * 30) + 40000
    d1 = pd.DataFrame({
        "time": np.arange(1700000000, 1700000000 + 100 * 86400, 86400)[:100],
        "open": np.roll(d1_c, 1), "high": d1_c + 50,
        "low": d1_c - 50, "close": d1_c,
        "volume": np.random.randint(1000, 50000, 100),
    })
    d1.iloc[0, d1.columns.get_loc("open")] = d1.iloc[0]["close"]
    result = generate_swing_signals(h4, d1, min_rules=2)
    assert len(result) == 500


def test_rules_string_populated():
    result = generate_swing_signals(_make_h4(500, "mixed"), min_rules=2)
    signal_mask = result["signal"] != 0
    if signal_mask.sum() > 0:
        rules = result.loc[signal_mask, "rules"]
        assert all(len(r) > 0 for r in rules)


def test_small_data():
    result = generate_swing_signals(_make_h4(50, "mixed"), min_rules=2)
    assert len(result) == 50


def test_output_length_matches_input():
    h4 = _make_h4(300)
    result = generate_swing_signals(h4)
    assert len(result) == len(h4)
