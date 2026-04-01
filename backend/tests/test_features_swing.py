"""Tests for swing trading feature pipeline."""
import numpy as np
import pandas as pd
from app.services.ml.features_swing import compute_swing_features


def _make_h4(n=500):
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(n) * 10) + 40000
    df = pd.DataFrame({
        "time": np.arange(1700000000, 1700000000 + n * 14400, 14400)[:n],
        "open": np.roll(closes, 1),
        "high": closes + np.abs(np.random.randn(n) * 20),
        "low": closes - np.abs(np.random.randn(n) * 20),
        "close": closes,
        "volume": np.random.randint(100, 5000, n),
    })
    df.iloc[0, df.columns.get_loc("open")] = df.iloc[0]["close"]
    return df


def test_returns_tuple():
    names, X = compute_swing_features(_make_h4())
    assert isinstance(names, list)
    assert isinstance(X, np.ndarray)


def test_feature_count():
    names, X = compute_swing_features(_make_h4())
    assert len(names) >= 60, f"Only {len(names)} features"
    assert X.shape[1] == len(names)


def test_array_lengths():
    df = _make_h4(300)
    names, X = compute_swing_features(df)
    assert X.shape[0] == 300


def test_no_nan_inf():
    names, X = compute_swing_features(_make_h4())
    assert not np.any(np.isnan(X))
    assert not np.any(np.isinf(X))


def test_with_d1():
    h4 = _make_h4(500)
    np.random.seed(99)
    d1_closes = np.cumsum(np.random.randn(100) * 30) + 40000
    d1 = pd.DataFrame({
        "time": np.arange(1700000000, 1700000000 + 100 * 86400, 86400)[:100],
        "open": np.roll(d1_closes, 1),
        "high": d1_closes + 50,
        "low": d1_closes - 50,
        "close": d1_closes,
        "volume": np.random.randint(1000, 50000, 100),
    })
    d1.iloc[0, d1.columns.get_loc("open")] = d1.iloc[0]["close"]
    names, X = compute_swing_features(h4, d1, symbol="US30")
    d1_feats = [n for n in names if n.startswith("d1_")]
    assert len(d1_feats) >= 4


def test_small_data():
    df = _make_h4(100)
    names, X = compute_swing_features(df)
    assert X.shape[0] == 100
    assert not np.any(np.isnan(X))


def test_large_data():
    df = _make_h4(3000)
    names, X = compute_swing_features(df)
    assert X.shape[0] == 3000


def test_ict_features_present():
    names, X = compute_swing_features(_make_h4())
    ict = [n for n in names if n.startswith("ict_")]
    assert len(ict) >= 10


def test_williams_features_present():
    names, X = compute_swing_features(_make_h4())
    lw = [n for n in names if n.startswith("lw_")]
    assert len(lw) >= 5


def test_donchian_features_present():
    names, X = compute_swing_features(_make_h4())
    dc = [n for n in names if n.startswith("donch_")]
    assert len(dc) >= 3
