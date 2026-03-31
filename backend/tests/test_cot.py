"""Tests for COT data download, feature computation, and ML integration."""
import os
import sys
import tempfile

import numpy as np
import pandas as pd
import pytest

# Add backend root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.fetch_cot_data import (
    compute_cot_features,
    load_cot_features,
    _williams_cot_index,
    _DATA_DIR,
)
from app.services.ml.features_cot import (
    add_cot_features,
    _align_cot_to_bars,
    _shift_to_release_time,
    COT_FEATURE_NAMES,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_synthetic_cot(n_weeks: int = 60) -> pd.DataFrame:
    """Create synthetic raw COT data for testing."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-02", periods=n_weeks, freq="W-TUE")
    comm_long = np.random.randint(50000, 200000, n_weeks).astype(float)
    comm_short = np.random.randint(50000, 200000, n_weeks).astype(float)
    spec_long = np.random.randint(100000, 300000, n_weeks).astype(float)
    spec_short = np.random.randint(100000, 300000, n_weeks).astype(float)
    oi = comm_long + comm_short + spec_long + spec_short + np.random.randint(10000, 50000, n_weeks)

    return pd.DataFrame({
        "date": dates,
        "comm_long": comm_long,
        "comm_short": comm_short,
        "spec_long": spec_long,
        "spec_short": spec_short,
        "oi_all": oi,
    })


def _make_cot_features_df(n_weeks: int = 60) -> pd.DataFrame:
    """Create a COT features DataFrame (as if loaded from CSV)."""
    raw = _make_synthetic_cot(n_weeks)
    return compute_cot_features(raw)


# ── Test 1: load_cot_features returns None when no file ──────────────────


def test_load_cot_features_returns_none_when_no_data():
    """load_cot_features returns None when no data file exists."""
    result = load_cot_features("NONEXISTENT_SYMBOL_XYZ")
    assert result is None


# ── Test 2: add_cot_features doesn't crash with no data ─────────────────


def test_add_cot_features_no_crash_without_data():
    """add_cot_features silently returns when no COT data is available."""
    features = {}
    times = np.arange(1700000000, 1700000000 + 1000 * 300, 300)
    # Should not raise, should not add features
    add_cot_features(features, times, symbol="NONEXISTENT_SYMBOL")
    assert len(features) == 0


# ── Test 3: Williams COT Index computation ───────────────────────────────


def test_williams_cot_index_known_values():
    """Williams COT Index produces correct values for known input."""
    # Simple case: linearly increasing net positions
    net = np.array([10.0, 20.0, 30.0, 40.0, 50.0])

    # With window=5, at position 4 (last element):
    # min = 10, max = 50, net = 50
    # index = (50 - 10) / (50 - 10) * 100 = 100.0
    result = _williams_cot_index(net, window=5)
    assert result[4] == pytest.approx(100.0)

    # At position 4 with window=3:
    # window covers indices 2,3,4 -> values 30,40,50
    # min=30, max=50, net=50
    # index = (50-30)/(50-30)*100 = 100.0
    result3 = _williams_cot_index(net, window=3)
    assert result3[4] == pytest.approx(100.0)

    # Test with value in the middle of the range
    net2 = np.array([0.0, 100.0, 50.0])
    result2 = _williams_cot_index(net2, window=3)
    # At index 2: min=0, max=100, net=50 -> (50-0)/(100-0)*100 = 50.0
    assert result2[2] == pytest.approx(50.0)


def test_williams_cot_index_flat():
    """Williams COT Index returns 50 when range is zero."""
    net = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    result = _williams_cot_index(net, window=5)
    # All same -> range is 0 -> should return 50 (default)
    assert result[4] == pytest.approx(50.0)


# ── Test 4: Forward-fill has no lookahead bias ───────────────────────────


def test_no_lookahead_bias():
    """COT data (Tuesday) is only available after Friday release."""
    # Create a single COT observation for Tuesday 2024-01-02
    cot_df = pd.DataFrame({
        "cot_comm_net": [1000.0],
        "cot_spec_net": [500.0],
        "cot_comm_index_26w": [75.0],
        "cot_comm_index_52w": [80.0],
        "cot_comm_pct_oi": [5.0],
        "cot_comm_change": [100.0],
        "cot_extreme_bull": [0],
        "cot_extreme_bear": [0],
    }, index=pd.DatetimeIndex(
        [pd.Timestamp("2024-01-02", tz="UTC")],  # Tuesday
        name="date",
    ))

    # Check release time: Tuesday Jan 2 -> Friday Jan 5 at 21:00 UTC
    release_ts = _shift_to_release_time(cot_df.index)
    release_dt = pd.Timestamp(release_ts[0], unit="s", tz="UTC")
    assert release_dt.weekday() == 4  # Friday
    assert release_dt.day == 5  # Jan 5
    assert release_dt.hour == 21

    # Bar on Wednesday Jan 3 should NOT see this COT data
    wed_ts = np.array([int(pd.Timestamp("2024-01-03 12:00", tz="UTC").timestamp())])
    aligned = _align_cot_to_bars(cot_df, wed_ts)
    assert aligned["cot_comm_net"][0] == 0.0  # Not yet available

    # Bar on Thursday Jan 4 should NOT see this COT data
    thu_ts = np.array([int(pd.Timestamp("2024-01-04 18:00", tz="UTC").timestamp())])
    aligned = _align_cot_to_bars(cot_df, thu_ts)
    assert aligned["cot_comm_net"][0] == 0.0  # Not yet available

    # Bar on Friday Jan 5 at 20:00 should NOT see it yet (before 21:00)
    fri_early_ts = np.array([int(pd.Timestamp("2024-01-05 20:00", tz="UTC").timestamp())])
    aligned = _align_cot_to_bars(cot_df, fri_early_ts)
    assert aligned["cot_comm_net"][0] == 0.0  # Not yet released

    # Bar on Friday Jan 5 at 22:00 SHOULD see it (after 21:00 release)
    fri_late_ts = np.array([int(pd.Timestamp("2024-01-05 22:00", tz="UTC").timestamp())])
    aligned = _align_cot_to_bars(cot_df, fri_late_ts)
    assert aligned["cot_comm_net"][0] == 1000.0  # Now available

    # Bar on Monday Jan 8 should still see it (forward-filled)
    mon_ts = np.array([int(pd.Timestamp("2024-01-08 14:00", tz="UTC").timestamp())])
    aligned = _align_cot_to_bars(cot_df, mon_ts)
    assert aligned["cot_comm_net"][0] == 1000.0


# ── Test 5: All features have correct names ──────────────────────────────


def test_feature_names():
    """All 8 expected COT features are produced with correct names."""
    raw = _make_synthetic_cot(60)
    features = compute_cot_features(raw)

    expected = [
        "cot_comm_net",
        "cot_spec_net",
        "cot_comm_index_26w",
        "cot_comm_index_52w",
        "cot_comm_pct_oi",
        "cot_comm_change",
        "cot_extreme_bull",
        "cot_extreme_bear",
    ]
    for name in expected:
        assert name in features.columns, f"Missing feature: {name}"

    assert len(features.columns) == 8


def test_cot_feature_names_constant():
    """COT_FEATURE_NAMES matches the expected feature set."""
    expected = [
        "cot_comm_net",
        "cot_spec_net",
        "cot_comm_index_26w",
        "cot_comm_index_52w",
        "cot_comm_pct_oi",
        "cot_comm_change",
        "cot_extreme_bull",
        "cot_extreme_bear",
    ]
    assert COT_FEATURE_NAMES == expected


# ── Test 6: No NaN in computed features ──────────────────────────────────


def test_no_nan_in_features():
    """Computed features contain no NaN values."""
    raw = _make_synthetic_cot(60)
    features = compute_cot_features(raw)
    assert not features.isnull().any().any(), f"NaN found in features: {features.isnull().sum()}"


def test_no_nan_in_aligned_features():
    """Aligned (forward-filled) features contain no NaN."""
    cot_df = _make_cot_features_df(60)

    # Create M5 bar timestamps spanning the COT data range
    start_ts = int(cot_df.index[0].timestamp())
    end_ts = int(cot_df.index[-1].timestamp()) + 7 * 86400
    bar_ts = np.arange(start_ts, end_ts, 300)  # M5 bars

    aligned = _align_cot_to_bars(cot_df, bar_ts)
    for name, arr in aligned.items():
        assert not np.any(np.isnan(arr)), f"NaN found in aligned feature {name}"


# ── Additional edge case tests ───────────────────────────────────────────


def test_compute_features_with_minimal_data():
    """Feature computation handles very short datasets."""
    raw = _make_synthetic_cot(5)
    features = compute_cot_features(raw)
    assert len(features) == 5
    assert not features.isnull().any().any()


def test_extreme_signals():
    """Extreme bull/bear signals are correctly computed."""
    # Create data where commercial index is very high or very low
    net = np.concatenate([
        np.linspace(-100000, 100000, 52),  # Trending up for 52 weeks
    ])
    raw = pd.DataFrame({
        "date": pd.date_range("2024-01-02", periods=52, freq="W-TUE"),
        "comm_long": net + 200000,
        "comm_short": np.full(52, 200000.0),
        "spec_long": np.full(52, 150000.0),
        "spec_short": np.full(52, 150000.0),
        "oi_all": np.full(52, 700000.0),
    })
    features = compute_cot_features(raw)

    # Last value has highest net -> index should be 100 -> extreme bull
    assert features["cot_extreme_bull"].iloc[-1] == 1
    # First usable value (at index 51) has lowest period values behind it
    # but is itself at the top, so it should be bull
    assert features["cot_extreme_bear"].iloc[-1] == 0


def test_add_cot_features_with_saved_data(tmp_path):
    """add_cot_features integrates correctly when data file exists."""
    # Create and save synthetic COT features
    cot_df = _make_cot_features_df(60)
    csv_path = tmp_path / "cot_features_TESTSYM.csv"
    cot_df.to_csv(csv_path)

    # Monkey-patch the load function to use our temp file
    import app.services.ml.features_cot as cot_module
    original_load = cot_module._load_cot_features

    def mock_load(symbol):
        if symbol == "TESTSYM":
            df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df.sort_index()
        return None

    cot_module._load_cot_features = mock_load
    try:
        features = {}
        # Create timestamps that fall within the COT data range
        start_ts = int(cot_df.index[10].timestamp()) + 4 * 86400  # After Friday release
        times = np.arange(start_ts, start_ts + 500 * 300, 300)

        add_cot_features(features, times, symbol="TESTSYM")

        # All 8 features should be present
        assert len(features) == 8
        for name in COT_FEATURE_NAMES:
            assert name in features
            assert len(features[name]) == len(times)
            assert not np.any(np.isnan(features[name]))
    finally:
        cot_module._load_cot_features = original_load
