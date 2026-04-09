"""
Fetch historical OHLCV data from Dukascopy (FREE) for all 5 symbols.
Dukascopy provides CFD data matching Oanda execution — ideal for training.

Usage: cd backend && python -m scripts.fetch_dukascopy --symbol US30 --days 365
       cd backend && python -m scripts.fetch_dukascopy --all --days 730
"""
import os
import sys
import argparse
import csv
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)

# Dukascopy instrument codes
SYMBOL_MAP = {
    "US30": "usa30idxusd",
    "BTCUSD": "btcusd",
    "XAUUSD": "xauusd",
    "ES": "usa500idxusd",
    "NAS100": "usatechidxusd",
}

# Timeframe mapping to dukascopy intervals
TF_MAP = {
    "M5": "m5",
    "H1": "h1",
    "H4": "h4",
    "D1": "d1",
}

MAX_BARS = 500_000  # Cap per symbol to fit in 2GB RAM


def fetch_symbol(symbol: str, timeframe: str, days: int):
    """Fetch OHLCV data from Dukascopy for a single symbol/timeframe."""
    import dukascopy_python

    duka_code = SYMBOL_MAP.get(symbol)
    if not duka_code:
        print(f"  Symbol {symbol} not available on Dukascopy. Available: {list(SYMBOL_MAP.keys())}")
        return None

    duka_tf = TF_MAP.get(timeframe)
    if not duka_tf:
        print(f"  Timeframe {timeframe} not supported. Available: {list(TF_MAP.keys())}")
        return None

    end = datetime.utcnow()
    start = end - timedelta(days=days)

    print(f"  Fetching {symbol} ({duka_code}) {timeframe} from {start.date()} to {end.date()}...", flush=True)

    try:
        # Map timeframe to dukascopy interval constant
        interval_map = {
            "m5": dukascopy_python.INTERVAL_MIN_5,
            "h1": dukascopy_python.INTERVAL_HOUR_1,
            "h4": dukascopy_python.INTERVAL_HOUR_4,
            "d1": dukascopy_python.INTERVAL_DAY_1,
        }
        interval = interval_map[duka_tf]

        df = dukascopy_python.fetch(
            duka_code,
            interval,
            dukascopy_python.OFFER_SIDE_BID,
            start,
            end,
        )

        if df is None or df.empty:
            print(f"  No data returned for {symbol} {timeframe}")
            return None

        # Normalize columns
        df = df.reset_index()
        # Dukascopy returns: timestamp, open, high, low, close, volume
        if "timestamp" in df.columns:
            df["time"] = df["timestamp"].astype(np.int64) // 10**9  # ns → seconds
        elif "date" in df.columns:
            df["time"] = pd.to_datetime(df["date"]).astype(np.int64) // 10**9
        else:
            # Try index
            df["time"] = df.index.astype(np.int64) // 10**9

        # Keep only OHLCV columns
        for col in ["open", "high", "low", "close"]:
            if col not in df.columns:
                print(f"  Missing column '{col}' in {symbol} {timeframe} data")
                return None

        if "volume" not in df.columns:
            df["volume"] = 0

        df = df[["time", "open", "high", "low", "close", "volume"]].copy()
        df = df.sort_values("time").reset_index(drop=True)

        # Cap bars
        if len(df) > MAX_BARS:
            print(f"  Capping from {len(df):,} to {MAX_BARS:,} bars")
            df = df.iloc[-MAX_BARS:].reset_index(drop=True)

        print(f"  Got {len(df):,} bars ({df['time'].iloc[0]} to {df['time'].iloc[-1]})")
        return df

    except Exception as e:
        print(f"  ERROR fetching {symbol} {timeframe}: {e}")
        return None


def save_data(symbol: str, timeframe: str, df: pd.DataFrame):
    """Save to History Data directory, merging with existing data."""
    out_dir = os.path.join(HIST_DATA_DIR, symbol)
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, f"{symbol}_{timeframe}.csv")

    # Merge with existing
    if os.path.exists(out_path):
        existing = pd.read_csv(out_path)
        if "ts_event" in existing.columns and "time" not in existing.columns:
            existing["time"] = pd.to_datetime(existing["ts_event"]).astype(np.int64) // 10**9
        if "time" in existing.columns:
            existing["time"] = existing["time"].astype(np.int64)
            merged = pd.concat([existing[["time", "open", "high", "low", "close", "volume"]], df])
            merged = merged.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
            print(f"  Merged: {len(existing):,} existing + {len(df):,} new = {len(merged):,} total")
            df = merged

    df.to_csv(out_path, index=False)
    print(f"  Saved: {out_path}")


def fetch_all_timeframes(symbol: str, days: int):
    """Fetch all timeframes for a symbol."""
    for tf in ["M5", "H1", "H4", "D1"]:
        df = fetch_symbol(symbol, tf, days)
        if df is not None and not df.empty:
            save_data(symbol, tf, df)


def main():
    parser = argparse.ArgumentParser(description="Fetch Dukascopy historical data")
    parser.add_argument("--symbol", type=str, help="Symbol to fetch (US30, BTCUSD, etc.)")
    parser.add_argument("--all", action="store_true", help="Fetch all 5 symbols")
    parser.add_argument("--days", type=int, default=365, help="Days of history (default 365)")
    parser.add_argument("--timeframe", type=str, default=None, help="Single timeframe (M5, H1, H4, D1)")
    args = parser.parse_args()

    if args.all:
        symbols = list(SYMBOL_MAP.keys())
    elif args.symbol:
        symbols = [args.symbol.upper()]
    else:
        print("Specify --symbol or --all")
        return

    print(f"\n{'='*60}")
    print(f"  Dukascopy Data Downloader")
    print(f"  Symbols: {symbols}")
    print(f"  Days: {args.days}")
    print(f"{'='*60}\n")

    for sym in symbols:
        print(f"\n--- {sym} ---")
        if args.timeframe:
            df = fetch_symbol(sym, args.timeframe, args.days)
            if df is not None:
                save_data(sym, args.timeframe, df)
        else:
            fetch_all_timeframes(sym, args.days)

    print(f"\n{'='*60}")
    print("  Download complete!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
