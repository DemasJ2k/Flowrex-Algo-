"""Tests for Smart Money Concepts features."""
import numpy as np
from app.services.ml.smc_features import compute_smc_features


def _make_ohlc(n=500):
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(n) * 0.5) + 2000
    highs = closes + np.abs(np.random.randn(n) * 2)
    lows = closes - np.abs(np.random.randn(n) * 2)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    return opens, highs, lows, closes


def test_smc_returns_dict():
    opens, highs, lows, closes = _make_ohlc()
    result = compute_smc_features(opens, highs, lows, closes)
    assert isinstance(result, dict)
    assert len(result) >= 10


def test_smc_feature_lengths():
    opens, highs, lows, closes = _make_ohlc(300)
    result = compute_smc_features(opens, highs, lows, closes)
    for key, values in result.items():
        assert len(values) == 300, f"{key} has wrong length {len(values)}"


def test_smc_no_nan():
    opens, highs, lows, closes = _make_ohlc()
    result = compute_smc_features(opens, highs, lows, closes)
    for key, values in result.items():
        assert not np.any(np.isnan(values)), f"{key} has NaN"


def test_smc_bos_values():
    """BOS should only be -1, 0, or 1."""
    opens, highs, lows, closes = _make_ohlc()
    result = compute_smc_features(opens, highs, lows, closes)
    bos = result["smc_bos"]
    unique = set(np.unique(bos))
    assert unique.issubset({-1.0, 0.0, 1.0})


def test_smc_choch_values():
    """CHoCH should only be -1, 0, or 1."""
    opens, highs, lows, closes = _make_ohlc()
    result = compute_smc_features(opens, highs, lows, closes)
    choch = result["smc_choch"]
    unique = set(np.unique(choch))
    assert unique.issubset({-1.0, 0.0, 1.0})


def test_smc_premium_discount_range():
    """Premium/discount zone should be between 0 and 1."""
    opens, highs, lows, closes = _make_ohlc()
    result = compute_smc_features(opens, highs, lows, closes)
    pd = result["smc_premium_discount"]
    valid = pd[pd != 0]  # non-zero values
    if len(valid) > 0:
        assert np.all(valid >= 0) and np.all(valid <= 1), f"Range: {valid.min()}-{valid.max()}"


def test_enhanced_feature_count():
    """Full feature pipeline should produce ~98 features."""
    import pandas as pd
    from app.services.ml.features_mtf import compute_expert_features

    np.random.seed(42)
    n = 500
    closes = np.cumsum(np.random.randn(n) * 0.5) + 2000
    df = pd.DataFrame({
        "time": np.arange(1700000000, 1700000000 + n * 300, 300)[:n],
        "open": np.roll(closes, 1),
        "high": closes + np.abs(np.random.randn(n) * 2),
        "low": closes - np.abs(np.random.randn(n) * 2),
        "close": closes,
        "volume": np.random.randint(100, 5000, n),
    })
    names, X = compute_expert_features(df)
    assert len(names) >= 95, f"Only {len(names)} features (expected ~98)"
    assert not np.any(np.isnan(X))
    # Check SMC features are present
    smc_names = [n for n in names if n.startswith("smc_")]
    assert len(smc_names) >= 10, f"Only {len(smc_names)} SMC features"


def test_symbol_config_exists():
    from app.services.ml.symbol_config import get_symbol_config, get_all_symbols
    config = get_symbol_config("XAUUSD")
    assert config["asset_class"] == "commodity"
    assert config["label_atr_mult"] == 1.5
    symbols = get_all_symbols()
    assert "XAUUSD" in symbols
    assert "BTCUSD" in symbols
    assert len(symbols) >= 5
