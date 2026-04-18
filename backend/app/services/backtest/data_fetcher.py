"""
Backtest Data Fetcher — always draws fresh data from Dukascopy.

User requirement (2026-04-15): "For backtest, always draw backtest data from
Dukascopy. I do not want the file they fetch to stay on the database for a long
period of time."

Design:
- On each backtest run, spawn the existing Node.js Dukascopy fetcher writing
  into a per-run tempdir under /tmp/flowrex-backtest/{run_id}/.
- Training DOES NOT use this module — it still reads the persistent
  /opt/flowrex/History Data/data/ files. Only backtests fetch fresh.
- Tempdir is cleaned up by the caller (via `fetcher.cleanup(run_id)`) OR by
  the daily housekeeping task if the caller crashes before cleanup.
- A short-lived in-memory cache (default 10 min TTL) keyed on
  (symbol, timeframe_tuple, days) allows concurrent backtests for the same
  parameters to reuse one fetch. Cache entries are DataFrames loaded from the
  tempdir, so the tempdir can be cleaned up immediately after load.

Files tempfiles are NEVER written to the database. No `/opt/flowrex/backend/data`
writes. Pure filesystem tempdir → pandas DataFrame → in-memory cache.
"""
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import pandas as pd

BACKTEST_TMP_ROOT = "/tmp/flowrex-backtest"
DEFAULT_DAYS = 2500
DEFAULT_TIMEFRAMES = ["M5", "H1", "H4", "D1"]
CACHE_TTL_SEC = 10 * 60  # 10 minutes

# Path to the Node.js fetcher
_FETCHER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "scripts", "fetch_dukascopy_node.js"
)
_FETCHER_PATH = os.path.normpath(_FETCHER_PATH)

# Node binary paths — try /snap first (droplet), fall back to PATH
_NODE_PATHS = ["/snap/node/current/bin/node", "node"]


def _resolve_node_bin() -> str:
    for p in _NODE_PATHS:
        if p == "node" or os.path.exists(p):
            return p
    return "node"


@dataclass
class BacktestDataBundle:
    """A set of OHLCV DataFrames for one symbol across multiple timeframes."""
    symbol: str
    m5: Optional[pd.DataFrame]
    h1: Optional[pd.DataFrame]
    h4: Optional[pd.DataFrame]
    d1: Optional[pd.DataFrame]
    fetched_at: float
    run_id: str


class BacktestDataFetcher:
    """
    Fetches Dukascopy data on-demand for backtests.

    Usage:
        fetcher = get_backtest_fetcher()
        bundle = fetcher.fetch("XAUUSD", days=1000)
        # run backtest using bundle.m5, bundle.h1, etc.
        fetcher.cleanup(bundle.run_id)  # remove tempdir
    """

    def __init__(self):
        self._cache: dict[tuple, tuple[BacktestDataBundle, float]] = {}
        self._node_bin = _resolve_node_bin()
        os.makedirs(BACKTEST_TMP_ROOT, exist_ok=True)

    def fetch(
        self,
        symbol: str,
        days: int = DEFAULT_DAYS,
        timeframes: list = None,
    ) -> BacktestDataBundle:
        timeframes = timeframes or DEFAULT_TIMEFRAMES
        cache_key = (symbol.upper(), tuple(sorted(timeframes)), days)
        now = time.time()

        # Check cache (memory only, no DB touch)
        cached = self._cache.get(cache_key)
        if cached:
            bundle, ts = cached
            if now - ts < CACHE_TTL_SEC:
                return bundle
            else:
                self._cache.pop(cache_key, None)

        # Miss — spawn the fetcher into a fresh tempdir
        run_id = uuid.uuid4().hex[:12]
        tempdir = os.path.join(BACKTEST_TMP_ROOT, run_id)
        os.makedirs(tempdir, exist_ok=True)

        self._run_node_fetcher(symbol, days, tempdir)

        bundle = BacktestDataBundle(
            symbol=symbol.upper(),
            m5=self._load_if_exists(tempdir, symbol, "M5"),
            h1=self._load_if_exists(tempdir, symbol, "H1"),
            h4=self._load_if_exists(tempdir, symbol, "H4"),
            d1=self._load_if_exists(tempdir, symbol, "D1"),
            fetched_at=now,
            run_id=run_id,
        )

        # Cache in memory, then delete the tempdir immediately — the in-memory
        # DataFrames are the canonical reference now.
        self._cache[cache_key] = (bundle, now)
        try:
            shutil.rmtree(tempdir, ignore_errors=True)
        except Exception:
            pass

        return bundle

    def _run_node_fetcher(self, symbol: str, days: int, tempdir: str):
        env = os.environ.copy()
        # Ensure node is on PATH for subprocess
        if os.path.exists("/snap/node/current/bin"):
            env["PATH"] = f"/snap/node/current/bin:{env.get('PATH', '')}"

        cmd = [self._node_bin, _FETCHER_PATH, symbol, str(days), tempdir]
        try:
            # 5 minutes max — fetches on 2500 days * 4 timeframes should finish well under this
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                # Exit code 2 = critical M5 failure per our fetcher; any non-zero is a problem
                raise RuntimeError(
                    f"Dukascopy fetcher exited with code {result.returncode}. "
                    f"stdout tail: {result.stdout[-500:]}\n"
                    f"stderr tail: {result.stderr[-500:]}"
                )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Dukascopy fetcher timed out after 5 minutes")
        except FileNotFoundError:
            raise RuntimeError(
                f"Node binary not found at {self._node_bin}. "
                "Install Node 20+ or adjust _NODE_PATHS in data_fetcher.py."
            )

    def _load_if_exists(self, tempdir: str, symbol: str, tf: str) -> Optional[pd.DataFrame]:
        path = os.path.join(tempdir, f"{symbol.upper()}_{tf}.csv")
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_csv(path)
            # Canonical columns from the fetcher: time,open,high,low,close,volume
            if "time" not in df.columns:
                return None
            return df
        except Exception:
            return None

    def cleanup(self, run_id: str):
        """Best-effort tempdir cleanup. Usually already done in fetch()."""
        path = os.path.join(BACKTEST_TMP_ROOT, run_id)
        shutil.rmtree(path, ignore_errors=True)

    def invalidate_cache(self, symbol: Optional[str] = None):
        """Drop cache — per-symbol if given, else all."""
        if symbol is None:
            self._cache.clear()
            return
        sym = symbol.upper()
        to_drop = [k for k in self._cache if k[0] == sym]
        for k in to_drop:
            self._cache.pop(k, None)


# ── Module-level singleton ─────────────────────────────────────────

_fetcher: Optional[BacktestDataFetcher] = None


def get_backtest_fetcher() -> BacktestDataFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = BacktestDataFetcher()
    return _fetcher
