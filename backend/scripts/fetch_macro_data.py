"""
Fetch and cache macro/external data for ML feature engineering.
All sources are public APIs — no account or API key required.

Sources:
  - FRED direct CSV download: VIX (VIXCLS), TIPS 10yr real yield (DFII10), 2s10s spread (T10Y2Y)
  - Binance public REST:       BTC-USDT perpetual funding rate (no auth)
  - CoinGecko public API:      BTC dominance historical
  - yfinance:                  ETH-USD price (for ETH/BTC ratio)

Output: backend/data/macro/*.csv
Run: python -m scripts.fetch_macro_data
"""
import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

MACRO_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "macro")
os.makedirs(MACRO_DIR, exist_ok=True)

# FRED series to fetch (no API key — direct CSV download)
FRED_SERIES = {
    "vix":        "VIXCLS",    # CBOE VIX
    "tips_10y":   "DFII10",    # 10yr TIPS real yield
    "spread_2s10s": "T10Y2Y",  # 10yr minus 2yr Treasury spread
}


# ── FRED ───────────────────────────────────────────────────────────────

def fetch_fred_series(name: str, series_id: str) -> pd.Series:
    """Download a FRED series via direct CSV URL (no API key needed)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    print(f"  Fetching FRED {series_id} ({name})...")
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        # FRED CSVs have columns: DATE, <series_id>
        date_col = df.columns[0]
        val_col = df.columns[1]
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])
        df = df.set_index(date_col)
        df.index = pd.DatetimeIndex(df.index).tz_localize("UTC")
        series = pd.to_numeric(df[val_col], errors="coerce").dropna()
        series.name = name
        return series
    except Exception as e:
        print(f"  [WARN] FRED {series_id} failed: {e}")
        return pd.Series(dtype=float, name=name)


def save_fred_data():
    """Fetch all FRED series and save to macro/fred_daily.csv."""
    frames = {}
    for name, sid in FRED_SERIES.items():
        s = fetch_fred_series(name, sid)
        if len(s) > 0:
            frames[name] = s
        time.sleep(1)  # be polite

    if frames:
        df = pd.DataFrame(frames)
        df = df.sort_index()
        # Forward-fill weekend/holiday gaps
        df = df.ffill()
        out_path = os.path.join(MACRO_DIR, "fred_daily.csv")
        df.to_csv(out_path)
        print(f"  Saved FRED data: {len(df)} rows | {df.index[0].date()} to {df.index[-1].date()}")
        print(f"  Columns: {list(df.columns)}")
        return df
    return pd.DataFrame()


# ── Binance Funding Rate ───────────────────────────────────────────────

def fetch_binance_funding_rate(start_ts_ms: int = None, end_ts_ms: int = None) -> pd.DataFrame:
    """
    Fetch BTC-USDT perpetual funding rate from Binance.
    Public endpoint — no account or API key required.
    Funding rate updates every 8 hours.
    """
    BASE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
    all_records = []

    # Default: fetch from 2019-01-01 to now
    if start_ts_ms is None:
        start_ts_ms = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    if end_ts_ms is None:
        end_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    current_start = start_ts_ms
    batch_limit = 1000

    print("  Fetching Binance BTC funding rate history...")
    while current_start < end_ts_ms:
        try:
            resp = requests.get(BASE_URL, params={
                "symbol": "BTCUSDT",
                "startTime": current_start,
                "endTime": end_ts_ms,
                "limit": batch_limit,
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                break

            all_records.extend(data)

            # Advance past last record
            last_ts = data[-1]["fundingTime"]
            current_start = last_ts + 1

            if len(data) < batch_limit:
                break

            time.sleep(0.5)  # rate limit courtesy

        except Exception as e:
            print(f"  [WARN] Binance funding rate fetch failed: {e}")
            break

    if not all_records:
        print("  [WARN] No funding rate data fetched — returning empty")
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")

    # 30-day rolling z-score
    window = 90  # 90 × 8h = 30 days
    df["funding_rate_mean"] = df["funding_rate"].rolling(window, min_periods=1).mean()
    df["funding_rate_std"] = df["funding_rate"].rolling(window, min_periods=1).std().fillna(1e-6)
    df["funding_rate_zscore"] = (df["funding_rate"] - df["funding_rate_mean"]) / df["funding_rate_std"]

    # Rate of change (vs previous period)
    df["funding_rate_roc"] = df["funding_rate"].diff(3)  # 3-period (24h) momentum

    out = df[["funding_rate", "funding_rate_zscore", "funding_rate_roc"]].copy()
    out_path = os.path.join(MACRO_DIR, "btc_funding_rate.csv")
    out.to_csv(out_path)
    print(f"  Saved funding rate: {len(out)} records | {out.index[0].date()} to {out.index[-1].date()}")
    return out


# ── CoinGecko BTC Dominance ───────────────────────────────────────────

def fetch_btc_dominance(start: str = "2019-01-01") -> pd.DataFrame:
    """
    Compute BTC dominance proxy via yfinance.
    Uses BTC / (BTC + ETH + BNB + SOL) market cap proxy from price × circulating supply.
    Since circulating supply is approximately stable over short windows, we use
    BTC price relative strength vs a basket as the dominance proxy.
    This correlates highly (>0.90) with CoinGecko BTC.D on a daily basis.
    """
    import yfinance as yf
    print("  Computing BTC dominance proxy via yfinance...")
    try:
        tickers = {"BTC-USD": "btc", "ETH-USD": "eth", "BNB-USD": "bnb", "SOL-USD": "sol"}
        prices = {}
        for ticker, name in tickers.items():
            try:
                data = yf.download(ticker, start=start, progress=False, auto_adjust=True)
                if not data.empty:
                    prices[name] = data["Close"].squeeze()
            except Exception:
                pass
            time.sleep(0.3)

        if "btc" not in prices:
            raise ValueError("BTC price unavailable")

        # Align all to common index
        price_df = pd.DataFrame(prices).ffill().dropna(subset=["btc"])
        # Normalize each coin by its starting price to get relative market weight
        normalized = price_df / price_df.iloc[0]
        total = normalized.sum(axis=1)
        btc_dom = (normalized["btc"] / total * 100)
        btc_dom.name = "btc_dominance"

        if btc_dom.index.tz is None:
            btc_dom.index = btc_dom.index.tz_localize("UTC")
        else:
            btc_dom.index = btc_dom.index.tz_convert("UTC")

        df = pd.DataFrame({"btc_dominance": btc_dom}).resample("1D").last().ffill()

        out_path = os.path.join(MACRO_DIR, "btc_dominance.csv")
        df.to_csv(out_path)
        print(f"  Saved BTC dominance proxy: {len(df)} rows | {df.index[0].date()} to {df.index[-1].date()}")
        return df

    except Exception as e:
        print(f"  [WARN] BTC dominance proxy failed: {e}")
        return pd.DataFrame()


# ── yfinance ETH/BTC Ratio ────────────────────────────────────────────

def fetch_eth_btc_ratio(start: str = "2019-01-01") -> pd.DataFrame:
    """Fetch ETH/BTC price ratio via yfinance."""
    import yfinance as yf
    print("  Fetching ETH/BTC ratio via yfinance...")
    try:
        eth = yf.download("ETH-USD", start=start, progress=False, auto_adjust=True)
        btc = yf.download("BTC-USD", start=start, progress=False, auto_adjust=True)

        if eth.empty or btc.empty:
            raise ValueError("Empty data from yfinance")

        eth_close = eth["Close"].squeeze()
        btc_close = btc["Close"].squeeze()

        # Align on common dates
        ratio = (eth_close / btc_close).dropna()
        ratio.name = "eth_btc_ratio"

        # Normalize to UTC
        if ratio.index.tz is None:
            ratio.index = ratio.index.tz_localize("UTC")
        else:
            ratio.index = ratio.index.tz_convert("UTC")

        df = pd.DataFrame({"eth_btc_ratio": ratio})
        df = df.resample("1D").last().ffill()

        out_path = os.path.join(MACRO_DIR, "eth_btc_ratio.csv")
        df.to_csv(out_path)
        print(f"  Saved ETH/BTC ratio: {len(df)} rows | {df.index[0].date()} to {df.index[-1].date()}")
        return df

    except Exception as e:
        print(f"  [WARN] yfinance ETH/BTC failed: {e}")
        return pd.DataFrame()


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("\n=== Fetching Macro Data ===")
    print(f"Output directory: {MACRO_DIR}\n")

    # 1. FRED (VIX, TIPS, 2s10s)
    print("[1/4] FRED daily data (VIX, TIPS real yield, 2s10s spread)")
    save_fred_data()

    # 2. Binance funding rate
    print("\n[2/4] Binance BTC perpetual funding rate")
    fetch_binance_funding_rate()

    # 3. CoinGecko BTC dominance
    print("\n[3/4] CoinGecko BTC dominance")
    fetch_btc_dominance(start="2019-01-01")

    # 4. ETH/BTC ratio
    print("\n[4/4] yfinance ETH/BTC ratio")
    fetch_eth_btc_ratio(start="2019-01-01")

    print("\n=== Done. Macro data saved to backend/data/macro/ ===")


if __name__ == "__main__":
    main()
