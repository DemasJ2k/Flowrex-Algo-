"""Tests for features_calendar.py: FOMC, OPEX, BTC halving, gold seasonality, etc."""
import numpy as np
import pytest
import sys
import os
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_calendar import (
    add_calendar_features,
    _third_friday,
    _last_friday,
    _FOMC_SET,
    _OPEX_SET,
    _CRYPTO_OPEX_SET,
)

# ── Fixtures ───────────────────────────────────────────────────────────

N = 500


def _make_times(start_date: str = "2024-01-01", n: int = N, interval_secs: int = 300):
    """Generate N unix timestamps starting from start_date at interval_secs intervals."""
    start_ts = int(datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc).timestamp())
    return np.arange(start_ts, start_ts + n * interval_secs, interval_secs, dtype=int)


@pytest.fixture
def times():
    return _make_times()


# ── Helper function tests ──────────────────────────────────────────────


def test_third_friday_is_friday():
    d = _third_friday(2024, 1)
    assert d.weekday() == 4  # Friday


def test_third_friday_is_third():
    """Third Friday must be between day 15 and 21."""
    d = _third_friday(2024, 3)
    assert 15 <= d.day <= 21


def test_last_friday_is_friday():
    d = _last_friday(2024, 1)
    assert d.weekday() == 4


def test_fomc_set_non_empty():
    assert len(_FOMC_SET) > 30


def test_opex_set_non_empty():
    assert len(_OPEX_SET) > 50


def test_crypto_opex_set_non_empty():
    assert len(_CRYPTO_OPEX_SET) > 50


# ── add_calendar_features — all symbols ───────────────────────────────


def test_fomc_drift_flag_shape(times):
    features = {}
    add_calendar_features(features, times, symbol="US30")
    assert "fomc_drift_flag" in features
    assert features["fomc_drift_flag"].shape == (N,)


def test_fomc_drift_flag_binary(times):
    features = {}
    add_calendar_features(features, times, symbol="US30")
    vals = np.unique(features["fomc_drift_flag"])
    assert set(vals.tolist()).issubset({0.0, 1.0})


def test_opex_week_flag_binary(times):
    features = {}
    add_calendar_features(features, times, symbol="US30")
    vals = np.unique(features["opex_week_flag"])
    assert set(vals.tolist()).issubset({0.0, 1.0})


def test_days_to_opex_norm_bounded(times):
    features = {}
    add_calendar_features(features, times, symbol="US30")
    arr = features["days_to_opex_norm"]
    assert np.all(arr >= 0)
    assert np.all(arr <= 1.0)


def test_quad_witching_binary(times):
    features = {}
    add_calendar_features(features, times, symbol="US30")
    vals = np.unique(features["quad_witching_flag"])
    assert set(vals.tolist()).issubset({0.0, 1.0})


# ── BTCUSD-specific ────────────────────────────────────────────────────


def test_btc_halving_phase_range(times):
    features = {}
    add_calendar_features(features, times, symbol="BTCUSD")
    assert "halving_cycle_phase" in features
    phase = features["halving_cycle_phase"]
    assert np.all(phase >= 0)
    assert np.all(phase <= 1.0)


def test_btc_halving_days_to_next_bounded(times):
    features = {}
    add_calendar_features(features, times, symbol="BTCUSD")
    arr = features["halving_days_to_next_norm"]
    assert np.all(arr >= 0)
    assert np.all(arr <= 1.0)


def test_crypto_opex_flag_binary(times):
    features = {}
    add_calendar_features(features, times, symbol="BTCUSD")
    vals = np.unique(features["crypto_opex_flag"])
    assert set(vals.tolist()).issubset({0.0, 1.0})


def test_days_to_crypto_opex_bounded(times):
    features = {}
    add_calendar_features(features, times, symbol="BTCUSD")
    arr = features["days_to_crypto_opex_norm"]
    assert np.all(arr >= 0)
    assert np.all(arr <= 1.0)


# ── XAUUSD-specific ────────────────────────────────────────────────────


def test_gold_seasonal_bias_range(times):
    features = {}
    add_calendar_features(features, times, symbol="XAUUSD")
    assert "gold_seasonal_bias" in features
    bias = features["gold_seasonal_bias"]
    assert np.all(bias >= -1)
    assert np.all(bias <= 1.0)


def test_futures_roll_flag_binary(times):
    features = {}
    add_calendar_features(features, times, symbol="XAUUSD")
    vals = np.unique(features["futures_roll_flag"])
    assert set(vals.tolist()).issubset({0.0, 1.0})


def test_days_to_roll_bounded(times):
    features = {}
    add_calendar_features(features, times, symbol="XAUUSD")
    arr = features["days_to_roll_norm"]
    assert np.all(arr >= 0)
    assert np.all(arr <= 1.0)


# ── US30-specific ──────────────────────────────────────────────────────


def test_buyback_blackout_binary(times):
    features = {}
    add_calendar_features(features, times, symbol="US30")
    vals = np.unique(features["buyback_blackout_flag"])
    assert set(vals.tolist()).issubset({0.0, 1.0})


# ── FOMC flag fires on known dates ────────────────────────────────────


def test_fomc_flag_fires_on_known_fomc():
    """2024-09-18 was an FOMC day — flag should be 1 on 2024-09-17 (pre-window)."""
    # 2024-09-17 18:00 UTC (one day before)
    ts = int(datetime(2024, 9, 17, 18, 0, 0, tzinfo=timezone.utc).timestamp())
    times_single = np.array([ts], dtype=int)
    features = {}
    add_calendar_features(features, times_single, symbol="US30")
    # The flag should be 1 either on the 17th (day before) or 18th (meeting day)
    assert features["fomc_drift_flag"][0] == 1.0


# ── No NaN / Inf in any feature ────────────────────────────────────────


@pytest.mark.parametrize("symbol", ["BTCUSD", "XAUUSD", "US30"])
def test_no_nan_in_calendar_features(symbol, times):
    features = {}
    add_calendar_features(features, times, symbol=symbol)
    for key, arr in features.items():
        assert not np.any(np.isnan(arr)), f"NaN in {key} for {symbol}"
        assert not np.any(np.isinf(arr)), f"Inf in {key} for {symbol}"
