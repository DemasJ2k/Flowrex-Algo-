"""
Tests for BacktestDataFetcher (Batch 5 — Dukascopy-direct backtest).

User requirement: backtests draw fresh from Dukascopy, files don't persist.
These tests cover the cache and tempdir lifecycle WITHOUT actually hitting
Dukascopy — the Node subprocess is mocked.
"""
import os
import shutil
import time
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd

from app.services.backtest.data_fetcher import (
    BacktestDataFetcher, BacktestDataBundle, BACKTEST_TMP_ROOT, CACHE_TTL_SEC,
)


@pytest.fixture
def fetcher(tmp_path):
    """A fresh BacktestDataFetcher (with module singleton bypassed)."""
    return BacktestDataFetcher()


def _make_csv(path, rows=10):
    """Write a minimal valid OHLCV CSV."""
    import pandas as pd
    df = pd.DataFrame({
        "time": list(range(1700000000, 1700000000 + rows * 300, 300)),
        "open": [100.0] * rows,
        "high": [101.0] * rows,
        "low": [99.0] * rows,
        "close": [100.5] * rows,
        "volume": [1000] * rows,
    })
    df.to_csv(path, index=False)


def test_cache_hit_returns_same_bundle(fetcher, tmp_path, monkeypatch):
    """Two consecutive fetches for the same params should hit the cache."""

    def mock_run_node(symbol, days, tempdir):
        os.makedirs(tempdir, exist_ok=True)
        _make_csv(os.path.join(tempdir, f"{symbol}_M5.csv"))
        _make_csv(os.path.join(tempdir, f"{symbol}_H1.csv"))
        _make_csv(os.path.join(tempdir, f"{symbol}_H4.csv"))
        _make_csv(os.path.join(tempdir, f"{symbol}_D1.csv"))

    fetcher._run_node_fetcher = mock_run_node

    bundle1 = fetcher.fetch("XAUUSD", days=100)
    bundle2 = fetcher.fetch("XAUUSD", days=100)

    # Both bundles share the same in-memory data
    assert bundle1 is bundle2
    assert bundle1.m5 is not None
    assert len(bundle1.m5) == 10


def test_cache_miss_after_ttl(fetcher, tmp_path):
    """After CACHE_TTL_SEC, a re-fetch should bypass the cache."""

    call_count = [0]

    def mock_run_node(symbol, days, tempdir):
        call_count[0] += 1
        os.makedirs(tempdir, exist_ok=True)
        _make_csv(os.path.join(tempdir, f"{symbol}_M5.csv"))

    fetcher._run_node_fetcher = mock_run_node

    fetcher.fetch("XAUUSD", days=100, timeframes=["M5"])
    assert call_count[0] == 1

    # Manually expire the cache by rewriting the timestamp
    cache_key = ("XAUUSD", ("M5",), 100)
    bundle, _ts = fetcher._cache[cache_key]
    fetcher._cache[cache_key] = (bundle, time.time() - CACHE_TTL_SEC - 10)

    fetcher.fetch("XAUUSD", days=100, timeframes=["M5"])
    assert call_count[0] == 2


def test_tempdir_cleaned_up_after_fetch(fetcher, tmp_path):
    """Tempdir must be deleted immediately after data is loaded into memory."""

    captured_tempdir = [None]

    def mock_run_node(symbol, days, tempdir):
        captured_tempdir[0] = tempdir
        os.makedirs(tempdir, exist_ok=True)
        _make_csv(os.path.join(tempdir, f"{symbol}_M5.csv"))

    fetcher._run_node_fetcher = mock_run_node

    bundle = fetcher.fetch("XAUUSD", days=100, timeframes=["M5"])

    # Tempdir should be gone (DataFrames are in memory)
    assert not os.path.exists(captured_tempdir[0])
    # But the data is still accessible
    assert bundle.m5 is not None
    assert len(bundle.m5) == 10


def test_fetcher_propagates_node_failure(fetcher, tmp_path):
    """If the Node fetcher fails, fetch() must raise (not silently return empty)."""

    def mock_run_node(symbol, days, tempdir):
        raise RuntimeError("Dukascopy unreachable")

    fetcher._run_node_fetcher = mock_run_node

    with pytest.raises(RuntimeError, match="Dukascopy unreachable"):
        fetcher.fetch("XAUUSD", days=100)


def test_invalidate_cache_per_symbol(fetcher, tmp_path):
    def mock_run_node(symbol, days, tempdir):
        os.makedirs(tempdir, exist_ok=True)
        _make_csv(os.path.join(tempdir, f"{symbol}_M5.csv"))

    fetcher._run_node_fetcher = mock_run_node

    fetcher.fetch("XAUUSD", days=100, timeframes=["M5"])
    fetcher.fetch("BTCUSD", days=100, timeframes=["M5"])

    assert ("XAUUSD", ("M5",), 100) in fetcher._cache
    assert ("BTCUSD", ("M5",), 100) in fetcher._cache

    fetcher.invalidate_cache("XAUUSD")
    assert ("XAUUSD", ("M5",), 100) not in fetcher._cache
    assert ("BTCUSD", ("M5",), 100) in fetcher._cache


def test_cleanup_removes_tempdir():
    fetcher = BacktestDataFetcher()
    run_id = "test-cleanup-12345"
    tempdir = os.path.join(BACKTEST_TMP_ROOT, run_id)
    os.makedirs(tempdir, exist_ok=True)
    with open(os.path.join(tempdir, "marker.txt"), "w") as f:
        f.write("")
    assert os.path.exists(tempdir)

    fetcher.cleanup(run_id)
    assert not os.path.exists(tempdir)


def test_cleanup_handles_missing_dir():
    """cleanup() on a nonexistent run_id should not raise."""
    fetcher = BacktestDataFetcher()
    fetcher.cleanup("does-not-exist-99999")  # must not raise
