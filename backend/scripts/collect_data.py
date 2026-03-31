"""
Collect historical candle data from Oanda or generate synthetic data.
Run: python -m scripts.collect_data [--synthetic]

Saves CSV files to backend/data/ with columns: time, open, high, low, close, volume
"""
import os
import sys
import asyncio
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SYMBOLS = ["XAUUSD", "BTCUSD", "US30"]
TIMEFRAMES = ["M5", "H1", "H4", "D1"]

# Oanda max candles per request
OANDA_MAX_COUNT = 5000


def generate_synthetic_data(symbol: str, timeframe: str, bars: int = 100000) -> pd.DataFrame:
    """Generate realistic synthetic OHLCV data for testing."""
    np.random.seed(hash(symbol + timeframe) % 2**32)

    # Base prices per symbol
    base_prices = {"XAUUSD": 2000.0, "BTCUSD": 50000.0, "US30": 35000.0}
    base = base_prices.get(symbol, 1000.0)

    # Timeframe to seconds
    tf_seconds = {"M5": 300, "H1": 3600, "H4": 14400, "D1": 86400}
    interval = tf_seconds.get(timeframe, 300)

    # Generate times (going back from now)
    end_time = int(datetime.now(timezone.utc).timestamp())
    start_time = end_time - (bars * interval)
    times = np.arange(start_time, end_time, interval)[:bars]

    # Random walk for close prices
    returns = np.random.normal(0, 0.001, len(times))
    # Add some trending behavior
    trend = np.sin(np.linspace(0, 8 * np.pi, len(times))) * 0.0002
    returns += trend
    log_prices = np.log(base) + np.cumsum(returns)
    closes = np.exp(log_prices)

    # Generate OHLV from close
    volatility = base * 0.002  # 0.2% typical range
    highs = closes + np.abs(np.random.normal(0, volatility, len(times)))
    lows = closes - np.abs(np.random.normal(0, volatility, len(times)))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = np.random.randint(100, 5000, len(times))

    df = pd.DataFrame({
        "time": times.astype(int),
        "open": np.round(opens, 2),
        "high": np.round(highs, 2),
        "low": np.round(lows, 2),
        "close": np.round(closes, 2),
        "volume": volumes,
    })
    return df


async def collect_from_oanda(symbol: str, timeframe: str, count: int = 50000) -> pd.DataFrame:
    """Fetch historical candles from Oanda API."""
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("OANDA_API_KEY", "")
    account_id = os.getenv("OANDA_ACCOUNT_ID", "")

    if not api_key or api_key.startswith("your-"):
        print(f"  [WARN] No Oanda credentials — generating synthetic data for {symbol} {timeframe}")
        return generate_synthetic_data(symbol, timeframe, count)

    from app.services.broker.oanda import OandaAdapter
    adapter = OandaAdapter()

    try:
        await adapter.connect({"api_key": api_key, "account_id": account_id, "practice": True})

        all_candles = []
        remaining = count
        # Oanda limits to 5000 per request, paginate
        while remaining > 0:
            batch_size = min(remaining, OANDA_MAX_COUNT)
            candles = await adapter.get_candles(symbol, timeframe, batch_size)
            if not candles:
                break
            for c in candles:
                all_candles.append({
                    "time": c.time,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                })
            remaining -= len(candles)
            if len(candles) < batch_size:
                break  # No more data available

        await adapter.disconnect()

        if not all_candles:
            print(f"  [WARN] No data from Oanda — generating synthetic for {symbol} {timeframe}")
            return generate_synthetic_data(symbol, timeframe, count)

        df = pd.DataFrame(all_candles)
        df = df.sort_values("time").drop_duplicates(subset="time").reset_index(drop=True)
        return df

    except Exception as e:
        print(f"  [ERROR] Oanda fetch failed: {e} — generating synthetic data")
        await adapter.disconnect()
        return generate_synthetic_data(symbol, timeframe, count)


def save_csv(df: pd.DataFrame, symbol: str, timeframe: str):
    """Save dataframe to CSV, supporting incremental appends."""
    os.makedirs(DATA_DIR, exist_ok=True)
    filepath = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.csv")

    if os.path.exists(filepath):
        existing = pd.read_csv(filepath)
        df = pd.concat([existing, df]).drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)

    df.to_csv(filepath, index=False)
    print(f"  Saved {len(df)} bars to {filepath}")


async def main(synthetic: bool = False):
    print("=" * 50)
    print("Flowrex Algo — Data Collection")
    print("=" * 50)

    for symbol in SYMBOLS:
        print(f"\n[{symbol}]")
        for tf in TIMEFRAMES:
            if synthetic:
                bars = {"M5": 100000, "H1": 10000, "H4": 3000, "D1": 1000}.get(tf, 10000)
                print(f"  Generating {bars} synthetic {tf} bars...")
                df = generate_synthetic_data(symbol, tf, bars)
            else:
                bars = {"M5": 50000, "H1": 10000, "H4": 3000, "D1": 1000}.get(tf, 5000)
                print(f"  Collecting {bars} {tf} bars from Oanda...")
                df = await collect_from_oanda(symbol, tf, bars)
            save_csv(df, symbol, tf)

    print("\nDone!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", action="store_true", help="Generate synthetic data instead of fetching from broker")
    args = parser.parse_args()
    asyncio.run(main(synthetic=args.synthetic))
