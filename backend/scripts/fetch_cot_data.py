"""
fetch_cot_data.py — Download and process CFTC Commitment of Traders data.

Downloads weekly disaggregated COT reports from CFTC, extracts positions
for US30 (DJIA futures) and XAUUSD (Gold futures), computes ML-ready
features, and saves to backend/data/cot/cot_features_{symbol}.csv.

Usage:
    python scripts/fetch_cot_data.py

Features produced per symbol:
    cot_comm_net        — Commercial net position (long - short)
    cot_spec_net        — Large speculator net position (long - short)
    cot_comm_index_26w  — Williams COT Index over 26 weeks
    cot_comm_index_52w  — Williams COT Index over 52 weeks
    cot_comm_pct_oi     — Commercial net as % of total open interest
    cot_comm_change     — Week-over-week change in commercial net
    cot_extreme_bull    — Binary: comm_index_52w > 90
    cot_extreme_bear    — Binary: comm_index_52w < 10
"""
from __future__ import annotations

import io
import logging
import os
import zipfile
from datetime import datetime

import numpy as np
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cot")
os.makedirs(_DATA_DIR, exist_ok=True)

# CFTC contract codes and search patterns
SYMBOL_CONFIG = {
    "US30": {
        "cftc_code": "124603",
        "name_patterns": ["DOW JONES", "DJIA", "DJ INDUSTRIAL"],
    },
    "XAUUSD": {
        "cftc_code": "088691",
        "name_patterns": ["GOLD", "COMEX GOLD"],
    },
}

# CFTC URLs
_CURRENT_YEAR_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
_HISTORY_URL_TEMPLATE = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"

# Disaggregated report column mapping (positional names from CFTC schema)
# We use column names that appear in the CFTC disaggregated futures TXT files
_COL_MAP = {
    "Market_and_Exchange_Names": "market_name",
    "CFTC_Contract_Market_Code": "cftc_code",
    "As_of_Date_In_Form_YYMMDD": "date_raw",
    "Report_Date_as_YYYY-MM-DD": "date",
    "Open_Interest_All": "oi_all",
    "Prod_Merc_Positions_Long_All": "comm_long",
    "Prod_Merc_Positions_Short_All": "comm_short",
    "M_Money_Positions_Long_All": "spec_long",
    "M_Money_Positions_Short_All": "spec_short",
}


def _download_current_year() -> pd.DataFrame | None:
    """Download current year disaggregated COT data from CFTC."""
    logger.info("Downloading current year COT data from CFTC...")
    try:
        resp = requests.get(_CURRENT_YEAR_URL, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), low_memory=False)
        logger.info(f"Downloaded {len(df)} rows from current year report")
        return df
    except Exception as e:
        logger.warning(f"Failed to download current year COT data: {e}")
        return None


def _download_historical_year(year: int) -> pd.DataFrame | None:
    """Download a single historical year of disaggregated COT data."""
    url = _HISTORY_URL_TEMPLATE.format(year=year)
    logger.info(f"Downloading COT history for {year}...")
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            # The zip contains a single txt/csv file
            names = zf.namelist()
            txt_name = [n for n in names if n.endswith(".txt") or n.endswith(".csv")]
            if not txt_name:
                txt_name = names
            with zf.open(txt_name[0]) as f:
                df = pd.read_csv(f, low_memory=False)
        logger.info(f"Downloaded {len(df)} rows for {year}")
        return df
    except Exception as e:
        logger.warning(f"Failed to download COT history for {year}: {e}")
        return None


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise CFTC column names — strip whitespace and map to our names."""
    df.columns = [c.strip() for c in df.columns]
    rename = {}
    for orig, new in _COL_MAP.items():
        # Try exact match first, then case-insensitive
        if orig in df.columns:
            rename[orig] = new
        else:
            for c in df.columns:
                if c.lower().replace(" ", "_") == orig.lower().replace(" ", "_"):
                    rename[c] = new
                    break
    if rename:
        df = df.rename(columns=rename)
    return df


def _filter_symbol(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Filter COT data to rows matching a specific symbol."""
    cfg = SYMBOL_CONFIG.get(symbol)
    if cfg is None:
        return pd.DataFrame()

    mask = pd.Series(False, index=df.index)

    # Match by CFTC code
    if "cftc_code" in df.columns:
        code_col = df["cftc_code"].astype(str).str.strip()
        mask |= code_col == cfg["cftc_code"]

    # Match by market name patterns
    if "market_name" in df.columns:
        name_col = df["market_name"].astype(str).str.upper()
        for pat in cfg["name_patterns"]:
            mask |= name_col.str.contains(pat, na=False)

    return df[mask].copy()


def _parse_date(df: pd.DataFrame) -> pd.DataFrame:
    """Parse date column from COT data."""
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    elif "date_raw" in df.columns:
        df["date"] = pd.to_datetime(df["date_raw"], format="%y%m%d", errors="coerce")
    else:
        # Try to find any date-like column
        for col in df.columns:
            if "date" in col.lower():
                df["date"] = pd.to_datetime(df[col], errors="coerce")
                break

    if "date" not in df.columns:
        logger.warning("No date column found in COT data")
        return pd.DataFrame()

    df = df.dropna(subset=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def compute_cot_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute COT features from raw filtered COT data.

    Expects columns: date, comm_long, comm_short, spec_long, spec_short, oi_all
    Returns DataFrame with date index and 8 feature columns.
    """
    required = ["date", "comm_long", "comm_short", "spec_long", "spec_short", "oi_all"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Missing required column: {col}")
            return pd.DataFrame()

    # Ensure numeric
    for col in required[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    result = pd.DataFrame()
    result["date"] = df["date"].values

    # Net positions
    result["cot_comm_net"] = (df["comm_long"].values - df["comm_short"].values).astype(float)
    result["cot_spec_net"] = (df["spec_long"].values - df["spec_short"].values).astype(float)

    # Williams COT Index: (net - min(net, window)) / (max(net, window) - min(net, window)) * 100
    comm_net = result["cot_comm_net"].values.copy()

    result["cot_comm_index_26w"] = _williams_cot_index(comm_net, 26)
    result["cot_comm_index_52w"] = _williams_cot_index(comm_net, 52)

    # Commercial net as % of open interest
    oi = df["oi_all"].values.astype(float)
    result["cot_comm_pct_oi"] = np.where(oi > 0, comm_net / oi * 100.0, 0.0)

    # Week-over-week change
    result["cot_comm_change"] = np.diff(comm_net, prepend=comm_net[0] if len(comm_net) > 0 else 0)

    # Extreme signals
    idx_52 = result["cot_comm_index_52w"].values
    result["cot_extreme_bull"] = (idx_52 > 90).astype(int)
    result["cot_extreme_bear"] = (idx_52 < 10).astype(int)

    result = result.set_index("date").sort_index()
    return result


def _williams_cot_index(net: np.ndarray, window: int) -> np.ndarray:
    """
    Williams COT Index: (net - min(net, w)) / (max(net, w) - min(net, w)) * 100
    Returns array of same length. First `window-1` values are NaN-filled with 50.
    """
    n = len(net)
    result = np.full(n, 50.0)
    for i in range(window - 1, n):
        w = net[i - window + 1: i + 1]
        lo = np.min(w)
        hi = np.max(w)
        rng = hi - lo
        if rng > 1e-10:
            result[i] = (net[i] - lo) / rng * 100.0
        else:
            result[i] = 50.0
    return result


def _load_local_raw(symbol: str) -> pd.DataFrame | None:
    """Try to load raw COT data from local CSV (fallback)."""
    path = os.path.join(_DATA_DIR, f"cot_raw_{symbol}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, low_memory=False)
        logger.info(f"Loaded {len(df)} rows from local {path}")
        return df
    except Exception as e:
        logger.warning(f"Failed to load local COT data for {symbol}: {e}")
        return None


def _save_raw(df: pd.DataFrame, symbol: str) -> None:
    """Save raw filtered COT data locally for future use."""
    path = os.path.join(_DATA_DIR, f"cot_raw_{symbol}.csv")
    df.to_csv(path, index=False)
    logger.info(f"Saved raw COT data to {path}")


def load_cot_features(symbol: str) -> pd.DataFrame | None:
    """
    Load pre-computed COT features for a symbol.

    Returns DataFrame with DatetimeIndex and 8 feature columns, or None if
    the data file does not exist.
    """
    path = os.path.join(_DATA_DIR, f"cot_features_{symbol}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        return df.sort_index()
    except Exception as e:
        logger.warning(f"Failed to load COT features for {symbol}: {e}")
        return None


def process_symbol(symbol: str, raw_dfs: list[pd.DataFrame] | None = None) -> bool:
    """
    Process COT data for a single symbol.

    Args:
        symbol: "US30" or "XAUUSD"
        raw_dfs: list of raw DataFrames from CFTC downloads (optional).
                 If None, tries to load from local cache.

    Returns True if features were successfully computed and saved.
    """
    filtered = pd.DataFrame()

    # Try downloaded data first
    if raw_dfs:
        parts = []
        for df in raw_dfs:
            df = _normalise_columns(df)
            filt = _filter_symbol(df, symbol)
            if len(filt) > 0:
                parts.append(filt)
        if parts:
            filtered = pd.concat(parts, ignore_index=True)
            filtered = _parse_date(filtered)
            if len(filtered) > 0:
                _save_raw(filtered, symbol)

    # Fallback to local cache
    if len(filtered) == 0:
        local = _load_local_raw(symbol)
        if local is not None:
            filtered = _normalise_columns(local)
            filtered = _parse_date(filtered)

    if len(filtered) == 0:
        logger.warning(f"No COT data available for {symbol}")
        return False

    # Deduplicate by date (keep last)
    filtered = filtered.drop_duplicates(subset=["date"], keep="last")
    filtered = filtered.sort_values("date").reset_index(drop=True)

    logger.info(f"{symbol}: {len(filtered)} weekly COT observations "
                f"({filtered['date'].min().date()} to {filtered['date'].max().date()})")

    # Compute features
    features = compute_cot_features(filtered)
    if len(features) == 0:
        logger.warning(f"Feature computation failed for {symbol}")
        return False

    # Save
    out_path = os.path.join(_DATA_DIR, f"cot_features_{symbol}.csv")
    features.to_csv(out_path)
    logger.info(f"Saved {len(features)} rows of COT features to {out_path}")
    return True


def main():
    """Download COT data from CFTC and process for all symbols."""
    current_year = datetime.now().year

    # Download data
    raw_dfs = []

    # Current year
    df = _download_current_year()
    if df is not None:
        raw_dfs.append(df)

    # Historical years (last 15 years for deep history)
    for year in range(current_year - 15, current_year):
        df = _download_historical_year(year)
        if df is not None:
            raw_dfs.append(df)

    if not raw_dfs:
        logger.warning("No COT data downloaded from CFTC. "
                        "Will try local fallback files in data/cot/.")

    # Process each symbol
    for symbol in SYMBOL_CONFIG:
        success = process_symbol(symbol, raw_dfs if raw_dfs else None)
        if success:
            logger.info(f"{symbol}: COT features ready")
        else:
            logger.warning(f"{symbol}: COT features NOT available")


if __name__ == "__main__":
    main()
