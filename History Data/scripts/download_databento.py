"""
Download historical OHLCV data from Databento and resample to multiple timeframes.

Symbols:
  XAUUSD -> GC (COMEX Gold futures, continuous front-month)
  ES     -> ES (CME E-mini S&P 500 futures)
  NAS100 -> NQ (CME E-mini Nasdaq 100 futures)
  US30   -> YM (CME E-mini Dow futures)
  BTCUSD -> BTC (CME Bitcoin futures)

Timeframes: M1, M5, M15, H1, H4
"""

import os
import sys
from pathlib import Path
from datetime import datetime

import databento as db
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Map user symbols to Databento dataset + symbol
SYMBOLS = {
    "XAUUSD": {"dataset": "GLBX.MDP3", "symbol": "GC.c.0"},
    "ES":     {"dataset": "GLBX.MDP3", "symbol": "ES.c.0"},
    "NAS100": {"dataset": "GLBX.MDP3", "symbol": "NQ.c.0"},
    "US30":   {"dataset": "GLBX.MDP3", "symbol": "YM.c.0"},
    "BTCUSD": {"dataset": "GLBX.MDP3", "symbol": "BTC.c.0"},
}

# Download range — GLBX.MDP3 available from 2010-06-06
START = "2010-06-06"
END = "2025-03-25"

RESAMPLE_MAP = {
    "M1":  None,       # already 1-minute
    "M5":  "5min",
    "M15": "15min",
    "H1":  "1h",
    "H4":  "4h",
}


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV dataframe to a larger timeframe."""
    return df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])


def download_and_save(name: str, cfg: dict, client: db.Historical):
    """Download 1-minute OHLCV and resample to all timeframes."""
    print(f"\n{'='*60}")
    print(f"Downloading {name} ({cfg['symbol']}) from {cfg['dataset']}")
    print(f"  Range: {START} to {END}")
    print(f"{'='*60}")

    # First, get the cost estimate
    try:
        cost = client.metadata.get_cost(
            dataset=cfg["dataset"],
            symbols=[cfg["symbol"]],
            schema="ohlcv-1m",
            stype_in="continuous",
            start=START,
            end=END,
        )
        print(f"  Estimated cost: ${cost:.2f}")
    except Exception as e:
        print(f"  Could not estimate cost: {e}")

    # Download 1-minute OHLCV
    try:
        data = client.timeseries.get_range(
            dataset=cfg["dataset"],
            symbols=[cfg["symbol"]],
            schema="ohlcv-1m",
            stype_in="continuous",
            start=START,
            end=END,
        )
    except Exception as e:
        print(f"  ERROR downloading {name}: {e}")
        return

    df = data.to_df()
    if df.empty:
        print(f"  No data returned for {name}")
        return

    print(f"  Downloaded {len(df):,} 1-minute bars")

    # Ensure proper column names
    col_map = {}
    for col in df.columns:
        lc = col.lower()
        if lc in ("open", "high", "low", "close", "volume"):
            col_map[col] = lc
    if col_map:
        df = df.rename(columns=col_map)

    # Save each timeframe
    sym_dir = DATA_DIR / name
    sym_dir.mkdir(parents=True, exist_ok=True)

    for tf_name, rule in RESAMPLE_MAP.items():
        if rule is None:
            tf_df = df[["open", "high", "low", "close", "volume"]].copy()
        else:
            tf_df = resample_ohlcv(df[["open", "high", "low", "close", "volume"]], rule)

        out_path = sym_dir / f"{name}_{tf_name}.parquet"
        tf_df.to_parquet(out_path)
        print(f"  {tf_name}: {len(tf_df):,} bars -> {out_path.name}")

    # Also save M1 as CSV for easy inspection
    csv_path = sym_dir / f"{name}_M1.csv"
    df[["open", "high", "low", "close", "volume"]].to_csv(csv_path)
    print(f"  CSV: {csv_path.name}")


def main():
    client = db.Historical()  # reads DATABENTO_API_KEY from env

    # Optionally download only specific symbols via CLI args
    targets = sys.argv[1:] if len(sys.argv) > 1 else list(SYMBOLS.keys())

    for name in targets:
        if name not in SYMBOLS:
            print(f"Unknown symbol: {name}")
            continue
        download_and_save(name, SYMBOLS[name], client)

    print("\nDone!")


if __name__ == "__main__":
    main()
