"""
Backtest Data Fetcher — delta-merge against persistent History Data.

2026-04-19 redesign:
  Previously every backtest run did a full 2,500-day Dukascopy fetch (M5 split
  into 14 × 6-month chunks), which routinely blew past the 5-minute subprocess
  timeout. Now we seed from the bind-mounted `History Data/data/` CSVs (~7
  years of history per symbol), fetch only the delta from the newest stored
  bar to now, and merge that delta in memory. The merged series is written
  back to the persistent CSV so the next run starts from an even fresher
  baseline.

  Typical costs after fix:
    - First-ever fetch for a symbol: ~2-3 min (full bootstrap)
    - Subsequent fetches: 5-20 s (delta only)
    - Up-to-date symbol: near-zero (reads CSV, no Dukascopy call)

Files tempfiles are NEVER written to the database. Delta fetches live in a
per-run tempdir and are merged into a pandas DataFrame before the tempdir
is removed. The persistent CSV at `/app/History Data/data/{SYMBOL}/...` is
the single source of truth.
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

# Persistent history data lives here in the container (bind-mounted from host
# `/opt/flowrex/History Data/data/`). Falls back to a local dev path.
_HIST_CANDIDATES = [
    "/app/History Data/data",
    "/History Data/data",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "History Data", "data")),
]


def _resolve_hist_dir() -> str:
    for p in _HIST_CANDIDATES:
        if os.path.isdir(p):
            return p
    # Fall back to the first candidate even if missing — we'll create it on write.
    return _HIST_CANDIDATES[0]


HIST_DATA_DIR = _resolve_hist_dir()

# Path to the Node.js fetcher
_FETCHER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "scripts", "fetch_dukascopy_node.js"
)
_FETCHER_PATH = os.path.normpath(_FETCHER_PATH)

# Node binary paths — try /snap first (droplet host), fall back to PATH
_NODE_PATHS = ["/snap/node/current/bin/node", "/usr/bin/node", "node"]

# Delta-fetch buffer: start N minutes before the newest stored bar so Dukascopy
# re-issues the boundary bars; duplicates are dropped during merge.
_DELTA_BUFFER_SEC = 15 * 60

# Don't write incremental updates smaller than this. Avoids hammering the CSV
# for tiny fetches during rapid backtest iterations.
_MIN_ROWS_TO_WRITE_BACK = 10


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
    # Delta stats so callers/UI can report something useful.
    bootstrap: bool = False
    new_rows: int = 0
    sources: dict = None  # {"M5": "persistent+delta", ...}


class BacktestDataFetcher:
    """
    Fetches and maintains a rolling Dukascopy OHLCV snapshot per symbol.

    Usage:
        fetcher = get_backtest_fetcher()
        bundle = fetcher.fetch("XAUUSD")
        # bundle.m5 / .h1 / .h4 / .d1 are DataFrames
        # bundle.bootstrap=True if we had no prior history for this symbol
        # bundle.new_rows = rows added by this run's delta fetch
    """

    def __init__(self):
        self._cache: dict[tuple, tuple[BacktestDataBundle, float]] = {}
        self._node_bin = _resolve_node_bin()
        os.makedirs(BACKTEST_TMP_ROOT, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────

    def fetch(
        self,
        symbol: str,
        days: int = DEFAULT_DAYS,
        timeframes: list = None,
    ) -> BacktestDataBundle:
        timeframes = [tf.upper() for tf in (timeframes or DEFAULT_TIMEFRAMES)]
        symbol = symbol.upper()
        cache_key = (symbol, tuple(sorted(timeframes)), days)
        now = time.time()

        cached = self._cache.get(cache_key)
        if cached:
            bundle, ts = cached
            if now - ts < CACHE_TTL_SEC:
                return bundle
            self._cache.pop(cache_key, None)

        # 1. Load persistent CSVs for each requested timeframe.
        persistent: dict[str, Optional[pd.DataFrame]] = {
            tf: self._load_persistent(symbol, tf) for tf in timeframes
        }
        have_any = any(df is not None and len(df) > 0 for df in persistent.values())
        bootstrap = not have_any

        # 2. Decide `since` for the Node fetcher. We pass the OLDEST max-ts
        #    across available timeframes, so the missing TF (if any) also
        #    gets caught up.
        since_ts: Optional[int] = None
        if have_any:
            max_times = [
                int(df["time"].max()) for df in persistent.values()
                if df is not None and len(df) > 0
            ]
            if max_times:
                since_ts = min(max_times) - _DELTA_BUFFER_SEC

        # 3. Spawn Node fetcher in either delta or bootstrap mode.
        run_id = uuid.uuid4().hex[:12]
        tempdir = os.path.join(BACKTEST_TMP_ROOT, run_id)
        os.makedirs(tempdir, exist_ok=True)

        try:
            self._run_node_fetcher(symbol, days, tempdir, since_ts=since_ts)
        except Exception as e:
            # Bootstrap failures are fatal; delta failures are recoverable if
            # we have persistent data to fall back on.
            shutil.rmtree(tempdir, ignore_errors=True)
            if bootstrap or not have_any:
                raise
            # Delta fetch failed — log and serve whatever we already have.
            import logging
            logging.getLogger("flowrex.backtest").warning(
                f"Dukascopy delta fetch failed for {symbol} — serving persistent data only: {e}"
            )
            bundle = self._bundle_from_frames(
                symbol, persistent, now, run_id,
                bootstrap=False, new_rows=0,
                sources={tf: "persistent (delta fetch failed)" for tf in timeframes},
            )
            self._cache[cache_key] = (bundle, now)
            return bundle

        # 4. Merge tempdir delta into persistent per timeframe.
        merged: dict[str, Optional[pd.DataFrame]] = {}
        total_new = 0
        sources: dict[str, str] = {}
        for tf in timeframes:
            delta = self._load_csv(os.path.join(tempdir, f"{symbol}_{tf}.csv"))
            base = persistent.get(tf)
            combined, added, src = self._merge(base, delta, since_ts)
            merged[tf] = combined
            total_new += added
            sources[tf] = src

            # 5. Write merged series back to persistent store so next run's
            #    `since` is even fresher.
            if combined is not None and added >= _MIN_ROWS_TO_WRITE_BACK:
                self._write_persistent(symbol, tf, combined)

        shutil.rmtree(tempdir, ignore_errors=True)

        bundle = self._bundle_from_frames(
            symbol, merged, now, run_id,
            bootstrap=bootstrap, new_rows=total_new, sources=sources,
        )
        self._cache[cache_key] = (bundle, now)
        return bundle

    def cleanup(self, run_id: str):
        """Best-effort tempdir cleanup. fetch() already removes it normally."""
        shutil.rmtree(os.path.join(BACKTEST_TMP_ROOT, run_id), ignore_errors=True)

    def invalidate_cache(self, symbol: Optional[str] = None):
        if symbol is None:
            self._cache.clear()
            return
        sym = symbol.upper()
        for k in list(self._cache.keys()):
            if k[0] == sym:
                self._cache.pop(k, None)

    # ── Helpers ─────────────────────────────────────────────────────────

    def _persistent_path(self, symbol: str, tf: str) -> str:
        return os.path.join(HIST_DATA_DIR, symbol, f"{symbol}_{tf}.csv")

    def _load_persistent(self, symbol: str, tf: str) -> Optional[pd.DataFrame]:
        return self._load_csv(self._persistent_path(symbol, tf))

    def _load_csv(self, path: str) -> Optional[pd.DataFrame]:
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_csv(path)
            if "time" not in df.columns:
                # Legacy files may store `ts_event` — normalise to `time`.
                if "ts_event" in df.columns:
                    df["time"] = pd.to_datetime(df["ts_event"]).astype("int64") // 10**9
                else:
                    return None
            # Coerce time column to integer seconds and drop garbage rows.
            df["time"] = pd.to_numeric(df["time"], errors="coerce")
            df = df.dropna(subset=["time"])
            df["time"] = df["time"].astype("int64")
            df = df[df["time"] > 10**9]  # sanity-check: post-2001 epoch
            for col in ("open", "high", "low", "close", "volume"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["open", "high", "low", "close"])
            df = df.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
            return df if len(df) > 0 else None
        except Exception:
            return None

    def _merge(
        self,
        base: Optional[pd.DataFrame],
        delta: Optional[pd.DataFrame],
        since_ts: Optional[int],
    ) -> tuple[Optional[pd.DataFrame], int, str]:
        """
        Merge a newly-fetched delta into the persistent base DataFrame.
        Returns (merged, rows_added, source_label).
        """
        if base is None and delta is None:
            return None, 0, "empty"
        if base is None:
            return delta, len(delta), "bootstrap"
        if delta is None or len(delta) == 0:
            return base, 0, "persistent (up-to-date)"

        base_times = set(base["time"].tolist())
        truly_new = delta[~delta["time"].isin(base_times)]
        added = len(truly_new)
        if added == 0:
            return base, 0, "persistent (0 new bars)"
        combined = pd.concat([base, truly_new], ignore_index=True)
        combined = combined.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
        return combined, added, f"persistent+delta ({added} new)"

    def _write_persistent(self, symbol: str, tf: str, df: pd.DataFrame):
        path = self._persistent_path(symbol, tf)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Keep canonical column order matching the Node fetcher's output
            cols = [c for c in ("time", "open", "high", "low", "close", "volume") if c in df.columns]
            df[cols].to_csv(path, index=False)
        except Exception as e:
            import logging
            logging.getLogger("flowrex.backtest").warning(
                f"Could not write back {path}: {e}"
            )

    def _bundle_from_frames(
        self, symbol: str, frames: dict, now: float, run_id: str,
        bootstrap: bool, new_rows: int, sources: dict,
    ) -> BacktestDataBundle:
        return BacktestDataBundle(
            symbol=symbol,
            m5=frames.get("M5"),
            h1=frames.get("H1"),
            h4=frames.get("H4"),
            d1=frames.get("D1"),
            fetched_at=now,
            run_id=run_id,
            bootstrap=bootstrap,
            new_rows=new_rows,
            sources=sources or {},
        )

    def _run_node_fetcher(
        self, symbol: str, days: int, tempdir: str, since_ts: Optional[int] = None,
    ):
        env = os.environ.copy()
        if os.path.exists("/snap/node/current/bin"):
            env["PATH"] = f"/snap/node/current/bin:{env.get('PATH', '')}"

        cmd = [self._node_bin, _FETCHER_PATH, symbol, str(days), tempdir]
        if since_ts is not None:
            cmd.append(f"--since={int(since_ts)}")

        # Delta fetches usually finish in 5-20s but Dukascopy can rate-limit
        # and trigger 1-2 retries (1s + 2s + 4s backoff per chunk). 120s leaves
        # room for that without masking a real hang. Bootstrap still gets 6min.
        timeout_sec = 120 if since_ts is not None else 360
        try:
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=timeout_sec
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Dukascopy fetcher exited with code {result.returncode}. "
                    f"stdout tail: {result.stdout[-500:]}\n"
                    f"stderr tail: {result.stderr[-500:]}"
                )
        except subprocess.TimeoutExpired:
            mode = "delta" if since_ts is not None else "bootstrap"
            raise RuntimeError(
                f"Dukascopy {mode} fetcher timed out after {timeout_sec}s"
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Node binary not found at {self._node_bin}. "
                "Install Node 20+ or adjust _NODE_PATHS in data_fetcher.py."
            )


# ── Module-level singleton ─────────────────────────────────────────

_fetcher: Optional[BacktestDataFetcher] = None


def get_backtest_fetcher() -> BacktestDataFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = BacktestDataFetcher()
    return _fetcher
