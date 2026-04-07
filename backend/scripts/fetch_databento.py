"""
Fetch historical OHLCV data from Databento and save as CSV.
Usage: cd backend && python -m scripts.fetch_databento --symbol ES --timeframe M5 --days 365
"""
import os, sys, argparse, csv, io
import httpx
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = "https://hist.databento.com/v0"
DATASET = "GLBX.MDP3"

SYMBOL_MAP = {
    "ES": "ES.v.0",
    "NAS100": "NQ.v.0",
    "US30": "YM.v.0",
}

SCHEMA_MAP = {
    "M5": "ohlcv-1m",    # fetch 1m, aggregate to 5m
    "M1": "ohlcv-1m",
    "H1": "ohlcv-1h",
    "H4": "ohlcv-1h",    # fetch 1h, aggregate to 4h
    "D1": "ohlcv-1d",
}

AGG_FACTORS = {"M5": 5, "H4": 4}

HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)


def fetch_and_save(symbol: str, timeframe: str, days: int, api_key: str):
    db_symbol = SYMBOL_MAP.get(symbol)
    if not db_symbol:
        print(f"Symbol {symbol} not available on Databento. Available: {list(SYMBOL_MAP.keys())}")
        return

    schema = SCHEMA_MAP.get(timeframe)
    if not schema:
        print(f"Timeframe {timeframe} not supported. Available: {list(SCHEMA_MAP.keys())}")
        return

    agg_factor = AGG_FACTORS.get(timeframe, 1)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    print(f"Fetching {symbol} ({db_symbol}) {timeframe} from {start.date()} to {end.date()}...")
    print(f"Schema: {schema}, aggregation: {agg_factor}x")

    # Fetch in chunks to avoid timeout (max 30 days per request)
    all_rows = []
    chunk_days = 30
    current_start = start

    while current_start < end:
        chunk_end = min(current_start + timedelta(days=chunk_days), end)
        print(f"  Fetching {current_start.date()} to {chunk_end.date()}...", end=" ", flush=True)

        params = {
            "dataset": DATASET,
            "symbols": db_symbol,
            "schema": schema,
            "start": current_start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "end": chunk_end.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "encoding": "csv",
        }

        resp = httpx.get(
            f"{BASE_URL}/timeseries.get_range",
            params=params,
            auth=(api_key, ""),
            timeout=60.0,
        )

        if resp.status_code not in (200, 206):
            print(f"ERROR: {resp.status_code} {resp.text[:200]}")
            current_start = chunk_end
            continue

        reader = csv.DictReader(io.StringIO(resp.text))
        chunk_count = 0
        for row in reader:
            if not row.get("ts_event"):
                continue
            ts = int(row["ts_event"])
            if ts > 1e15:
                ts = int(ts / 1e9)

            # Fixed-point prices
            def parse_price(v):
                v = int(v)
                return v / 1e9 if abs(v) > 1e6 else float(v)

            all_rows.append({
                "time": ts,
                "open": parse_price(row["open"]),
                "high": parse_price(row["high"]),
                "low": parse_price(row["low"]),
                "close": parse_price(row["close"]),
                "volume": int(row.get("volume", 0)),
            })
            chunk_count += 1

        print(f"{chunk_count} bars")
        current_start = chunk_end

    if not all_rows:
        print("No data fetched!")
        return

    # Sort by time
    all_rows.sort(key=lambda r: r["time"])

    # Aggregate if needed
    if agg_factor > 1:
        aggregated = []
        for i in range(0, len(all_rows), agg_factor):
            chunk = all_rows[i:i + agg_factor]
            if len(chunk) < agg_factor:
                break
            aggregated.append({
                "time": chunk[0]["time"],
                "open": chunk[0]["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(c["volume"] for c in chunk),
            })
        all_rows = aggregated
        print(f"Aggregated {agg_factor}x: {len(all_rows)} bars")

    # Save to History Data folder
    out_dir = os.path.join(HIST_DATA_DIR, symbol)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{symbol}_{timeframe}.csv")

    # If file exists, merge (append new data, dedupe by time)
    existing = {}
    if os.path.exists(out_path):
        import pandas as pd
        df = pd.read_csv(out_path)
        for _, row in df.iterrows():
            existing[int(row["time"])] = row.to_dict()
        print(f"Existing data: {len(existing)} bars")

    for row in all_rows:
        existing[row["time"]] = row

    # Write merged
    merged = sorted(existing.values(), key=lambda r: r["time"])

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(merged)

    print(f"Saved: {out_path} ({len(merged)} total bars)")

    # Also save to backend/data/ for training
    backend_path = os.path.join(os.path.dirname(__file__), "..", "data", f"{symbol}_{timeframe}.csv")
    with open(backend_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(merged)
    print(f"Saved: {backend_path} ({len(merged)} total bars)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        print("Provide --api-key or set DATABENTO_API_KEY env var")
        sys.exit(1)

    fetch_and_save(args.symbol, args.timeframe, args.days, api_key)
