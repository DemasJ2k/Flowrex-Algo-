"""
Convert real historical parquet data to pipeline-ready CSVs.

Steps:
  1. Read parquet from 'History Data/data/<symbol>/<symbol>_<tf>.parquet'
  2. Filter to symbol-specific training window
  3. Convert to (time, open, high, low, close, volume) CSV format
     - time = Unix integer seconds (UTC)
     - XAUUSD: normalize tick volume (volume / 20-bar rolling mean)
  4. Resample H4 -> D1 (no D1 parquet provided)
  5. Save to backend/data/<symbol>_<tf>.csv  (overwrites synthetic data)
  6. Print data quality summary

Run: python -m scripts.prepare_real_data
     python -m scripts.prepare_real_data --symbol BTCUSD
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HIST_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Training windows per symbol (inclusive)
WINDOWS = {
    "BTCUSD": ("2020-01-01", "2025-03-24"),   # full crypto cycle: COVID, 2021 ATH, 2022 bear, 2024 halving
    "XAUUSD": ("2022-01-01", "2025-03-24"),   # full rate-hike cycle
    "US30":   ("2022-01-01", "2025-03-24"),   # 2022 bear + 2023-24 bull
}

# OOS test window (same for all — genuinely unseen during feature dev)
OOS_START = "2024-10-01"  # post-US election BTC rally, gold ATH run

TIMEFRAMES = ["M5", "H1", "H4"]


def load_parquet(symbol: str, tf: str) -> pd.DataFrame:
    path = os.path.join(HIST_DIR, symbol, f"{symbol}_{tf}.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing: {path}")
    df = pd.read_parquet(path)
    # Index should be DatetimeIndex[UTC]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def normalize_volume(volumes: pd.Series, window: int = 20) -> pd.Series:
    """
    Normalize tick volume to [0, ~3] range using rolling mean.
    Handles XAUUSD tick volume (1-5 range) and larger volumes equally.
    Forward-fill zeros to avoid Amihud division-by-zero.
    """
    rolling_mean = volumes.rolling(window, min_periods=1).mean()
    normalized = volumes / rolling_mean.clip(lower=1e-8)
    normalized = normalized.clip(lower=0.01)  # floor at 1% of mean
    return normalized


def to_pipeline_csv(df: pd.DataFrame, symbol: str, normalize_vol: bool = False) -> pd.DataFrame:
    """
    Convert DataFrame with DatetimeIndex to pipeline format:
    columns = [time (unix int), open, high, low, close, volume]
    """
    out = pd.DataFrame()
    out["time"] = df.index.astype("int64") // 10**9   # nanoseconds -> seconds
    out["open"]  = df["open"].values
    out["high"]  = df["high"].values
    out["low"]   = df["low"].values
    out["close"] = df["close"].values

    if normalize_vol:
        out["volume"] = normalize_volume(df["volume"]).values
    else:
        out["volume"] = df["volume"].values

    return out


def resample_to_d1(h4_df: pd.DataFrame) -> pd.DataFrame:
    """Resample H4 OHLCV to D1 by aggregating 6 bars per day."""
    d1 = h4_df.resample("1D").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    return d1


def process_symbol(symbol: str) -> None:
    start, end = WINDOWS[symbol]
    normalize_vol = (symbol == "XAUUSD")   # tick volume normalization only for gold

    print(f"\n{'='*55}")
    print(f"  Processing {symbol}")
    print(f"  Window: {start} to {end}  |  OOS from: {OOS_START}")
    print(f"{'='*55}")

    for tf in TIMEFRAMES:
        try:
            raw = load_parquet(symbol, tf)

            # Filter to training window
            raw = raw[start:end]

            if len(raw) == 0:
                print(f"  [WARN] {tf}: no data in window after filter")
                continue

            # Quality check: drop rows with zero close price
            raw = raw[raw["close"] > 0]

            # Quality check: fill any NaN OHLC with forward fill
            raw[["open", "high", "low", "close"]] = (
                raw[["open", "high", "low", "close"]].ffill()
            )

            # Normalize volume
            if normalize_vol:
                raw["volume"] = normalize_volume(raw["volume"])

            # Convert to pipeline format
            out = to_pipeline_csv(raw, symbol, normalize_vol=False)  # already normalized above

            # Save
            out_path = os.path.join(DATA_DIR, f"{symbol}_{tf}.csv")
            out.to_csv(out_path, index=False)

            t_start = pd.Timestamp(raw.index[0]).strftime("%Y-%m-%d")
            t_end   = pd.Timestamp(raw.index[-1]).strftime("%Y-%m-%d")
            n_train = len(raw[raw.index < OOS_START])
            n_oos   = len(raw[raw.index >= OOS_START])

            print(f"  {tf}: {len(raw):>7} bars | {t_start} to {t_end} | "
                  f"train={n_train} OOS={n_oos} | saved to {os.path.basename(out_path)}")

        except FileNotFoundError as e:
            print(f"  [SKIP] {tf}: {e}")
        except Exception as e:
            print(f"  [ERROR] {tf}: {e}")
            import traceback; traceback.print_exc()

    # Generate D1 from H4
    try:
        h4_raw = load_parquet(symbol, "H4")[start:end]
        if normalize_vol:
            h4_raw["volume"] = normalize_volume(h4_raw["volume"])

        d1_raw = resample_to_d1(h4_raw)
        d1_out = to_pipeline_csv(d1_raw, symbol, normalize_vol=False)
        d1_path = os.path.join(DATA_DIR, f"{symbol}_D1.csv")
        d1_out.to_csv(d1_path, index=False)

        n_train = len(d1_raw[d1_raw.index < OOS_START])
        n_oos   = len(d1_raw[d1_raw.index >= OOS_START])
        t_start = pd.Timestamp(d1_raw.index[0]).strftime("%Y-%m-%d")
        t_end   = pd.Timestamp(d1_raw.index[-1]).strftime("%Y-%m-%d")
        print(f"  D1: {len(d1_raw):>7} bars | {t_start} to {t_end} | "
              f"train={n_train} OOS={n_oos} | saved to {os.path.basename(d1_path)}")
    except Exception as e:
        print(f"  [WARN] D1 generation failed: {e}")

    # Summary stats
    _print_quality_summary(symbol, start, end)


def _print_quality_summary(symbol: str, start: str, end: str) -> None:
    """Print price range and volume stats for the saved M5 CSV."""
    try:
        m5_path = os.path.join(DATA_DIR, f"{symbol}_M5.csv")
        df = pd.read_csv(m5_path)
        df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)

        train = df[df["dt"] < OOS_START]
        oos   = df[df["dt"] >= OOS_START]

        print(f"\n  Quality summary ({symbol}):")
        print(f"    Price range: {df['close'].min():.2f} - {df['close'].max():.2f}")
        print(f"    Volume (normalized) mean: {df['volume'].mean():.4f}, std: {df['volume'].std():.4f}")
        print(f"    NaN in close: {df['close'].isna().sum()}")
        print(f"    Train bars: {len(train):,}  |  OOS bars: {len(oos):,}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default=None, help="Process single symbol")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else list(WINDOWS.keys())

    print("\n=== Preparing Real Historical Data ===")
    print(f"Source: {HIST_DIR}")
    print(f"Output: {DATA_DIR}")
    print(f"OOS test window starts: {OOS_START}")

    for sym in symbols:
        if sym not in WINDOWS:
            print(f"[SKIP] Unknown symbol: {sym}")
            continue
        process_symbol(sym)

    print("\n=== Data preparation complete ===")
    print("Next step: run fetch_macro_data.py if not already done, then train.")


if __name__ == "__main__":
    main()
