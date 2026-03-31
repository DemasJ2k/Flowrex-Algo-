"""
External macro/market features for the ML pipeline.

Loads pre-fetched macro data from backend/data/macro/ and aligns it to
M5 bar timestamps via forward-fill (no lookahead — each bar only sees
data available at or before its timestamp).

Files expected (produced by scripts/fetch_macro_data.py):
  backend/data/macro/fred_daily.csv      — VIX, TIPS 10yr, 2s10s spread
  backend/data/macro/btc_funding_rate.csv — BTC perp funding rate (8h)
  backend/data/macro/btc_dominance.csv   — BTC dominance proxy (daily)
  backend/data/macro/eth_btc_ratio.csv   — ETH/BTC ratio (daily)

Usage:
    from app.services.ml.features_external import add_external_features
    add_external_features(features, times, symbol="BTCUSD")
"""
import os
import numpy as np
import pandas as pd
from functools import lru_cache

_MACRO_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "macro")


# ── Data Loading (cached per process) ─────────────────────────────────


def _load_csv(filename: str) -> pd.DataFrame | None:
    path = os.path.join(_MACRO_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        return df.sort_index()
    except Exception:
        return None


@lru_cache(maxsize=1)
def _load_fred() -> pd.DataFrame | None:
    return _load_csv("fred_daily.csv")


@lru_cache(maxsize=1)
def _load_funding_rate() -> pd.DataFrame | None:
    return _load_csv("btc_funding_rate.csv")


@lru_cache(maxsize=1)
def _load_btc_dominance() -> pd.DataFrame | None:
    return _load_csv("btc_dominance.csv")


@lru_cache(maxsize=1)
def _load_eth_btc() -> pd.DataFrame | None:
    return _load_csv("eth_btc_ratio.csv")


# ── Alignment helper ──────────────────────────────────────────────────


def _align_to_bars(
    series: pd.Series,
    bar_timestamps: np.ndarray,
) -> np.ndarray:
    """
    Forward-fill a macro series (daily/8h resolution) to M5 bar timestamps.
    Only uses data available at or before each bar's timestamp (no lookahead bias).
    Fully vectorised via np.searchsorted on the full bar array.
    """
    if series is None or len(series) == 0:
        return np.zeros(len(bar_timestamps))

    macro_ts   = series.index.view("int64") // 10**9  # UTC unix seconds (numpy array)
    macro_vals = series.values.astype(float)

    # For each bar timestamp, find the last macro obs <= bar time
    idx = np.searchsorted(macro_ts, bar_timestamps.astype("int64"), side="right") - 1
    valid = idx >= 0
    result = np.zeros(len(bar_timestamps))
    idx_clipped = np.clip(idx, 0, len(macro_vals) - 1)
    result[valid] = np.where(
        np.isnan(macro_vals[idx_clipped[valid]]),
        0.0,
        macro_vals[idx_clipped[valid]],
    )
    return result


def _align_df_cols(
    df: pd.DataFrame,
    cols: list[str],
    bar_timestamps: np.ndarray,
) -> dict[str, np.ndarray]:
    """Align multiple columns from a DataFrame, return dict of arrays."""
    out = {}
    for col in cols:
        if col in df.columns:
            out[col] = _align_to_bars(df[col], bar_timestamps)
        else:
            out[col] = np.zeros(len(bar_timestamps))
    return out


# ── Z-score normalisation ─────────────────────────────────────────────


def _rolling_zscore(arr: np.ndarray, window: int = 252) -> np.ndarray:
    """Rolling z-score to de-mean and normalise macro series."""
    n = len(arr)
    result = np.zeros(n)
    for i in range(window, n):
        w = arr[i - window : i + 1]
        mu = np.mean(w)
        sigma = np.std(w)
        result[i] = (arr[i] - mu) / sigma if sigma > 1e-10 else 0.0
    return result


# ── Main feature addition function ────────────────────────────────────


def add_external_features(
    features: dict,
    times: np.ndarray,
    symbol: str = "BTCUSD",
    normalise: bool = True,
) -> dict:
    """
    Load cached macro data and align to bar timestamps.
    Adds features to `features` dict in-place; returns the same dict.

    All features are forward-filled (no lookahead bias).
    Missing files silently zero-fill — training still works, just without
    that signal.

    Features added (all symbols):
      vix_norm              — VIX z-score (252-day rolling)
      tips_10y_norm         — TIPS 10yr real yield z-score
      spread_2s10s_norm     — 2s10s yield curve z-score
      vix_raw               — raw VIX level (clipped 0-80)
      spread_2s10s_raw      — raw spread (can be negative = inversion)

    BTCUSD additional:
      funding_rate          — raw 8h funding rate
      funding_rate_zscore   — 30-day rolling z-score (pre-computed)
      funding_rate_roc      — 24h rate-of-change
      btc_dominance_norm    — BTC.D proxy z-score
      eth_btc_ratio_norm    — ETH/BTC ratio z-score

    XAUUSD additional:
      tips_10y_norm         — (same as global but highest signal for gold)
      vix_norm              — (same, gold = safe-haven flow)
    """
    n = len(times)

    # ── FRED: VIX, TIPS, 2s10s ────────────────────────────────────────
    fred = _load_fred()
    if fred is not None:
        aligned_fred = _align_df_cols(
            fred,
            ["vix", "tips_10y", "spread_2s10s"],
            times,
        )

        vix_raw  = aligned_fred["vix"]
        tips_raw = aligned_fred["tips_10y"]
        spr_raw  = aligned_fred["spread_2s10s"]

        features["vix_raw"]  = np.clip(vix_raw, 0, 80)
        features["spread_2s10s_raw"] = np.clip(spr_raw, -3, 5)

        if normalise:
            # 252-bar window at M5 resolution ≈ 252 × 288 = 72k bars —
            # far too slow in a pure Python loop; use daily proxy:
            # align daily FRED values then z-score at daily resolution
            # before aligning to M5.
            features["vix_norm"]          = _zscore_via_daily(fred["vix"],  times, window=252)
            features["tips_10y_norm"]     = _zscore_via_daily(fred["tips_10y"], times, window=252)
            features["spread_2s10s_norm"] = _zscore_via_daily(fred["spread_2s10s"], times, window=252)
        else:
            features["vix_norm"]          = vix_raw / 40.0  # rough normalise to [0, 2]
            features["tips_10y_norm"]     = tips_raw / 3.0
            features["spread_2s10s_norm"] = spr_raw / 2.0
    else:
        for k in ["vix_raw", "vix_norm", "tips_10y_norm", "spread_2s10s_raw", "spread_2s10s_norm"]:
            features[k] = np.zeros(n)

    # ── BTCUSD-specific external features ─────────────────────────────
    if symbol == "BTCUSD":
        # BTC funding rate (8-hourly)
        funding_df = _load_funding_rate()
        if funding_df is not None:
            funding_aligned = _align_df_cols(
                funding_df,
                ["funding_rate", "funding_rate_zscore", "funding_rate_roc"],
                times,
            )
            features["btc_funding_rate"]       = np.clip(funding_aligned["funding_rate"], -0.01, 0.01)
            features["btc_funding_zscore"]     = np.clip(funding_aligned["funding_rate_zscore"], -5, 5)
            features["btc_funding_roc"]        = np.clip(funding_aligned["funding_rate_roc"], -0.005, 0.005)
        else:
            for k in ["btc_funding_rate", "btc_funding_zscore", "btc_funding_roc"]:
                features[k] = np.zeros(n)

        # BTC dominance proxy
        dom_df = _load_btc_dominance()
        if dom_df is not None:
            dom_raw = _align_to_bars(dom_df["btc_dominance"], times)
            features["btc_dominance_norm"] = _zscore_via_daily(dom_df["btc_dominance"], times, window=90)
        else:
            features["btc_dominance_norm"] = np.zeros(n)

        # ETH/BTC ratio
        eth_df = _load_eth_btc()
        if eth_df is not None:
            features["eth_btc_ratio_norm"] = _zscore_via_daily(eth_df["eth_btc_ratio"], times, window=90)
        else:
            features["eth_btc_ratio_norm"] = np.zeros(n)

    return features


# ── Efficient daily z-score then align to M5 ──────────────────────────


def _zscore_via_daily(
    series: pd.Series,
    bar_timestamps: np.ndarray,
    window: int = 252,
) -> np.ndarray:
    """
    Compute rolling z-score at daily resolution (fast), then align to M5.
    Avoids looping over 300k+ M5 bars for the z-score calculation.
    """
    if series is None or len(series) == 0:
        return np.zeros(len(bar_timestamps))

    # Resample to daily if needed (already daily for FRED)
    daily = series.resample("1D").last().ffill().dropna()

    mu   = daily.rolling(window, min_periods=max(window // 4, 10)).mean()
    sig  = daily.rolling(window, min_periods=max(window // 4, 10)).std()
    z    = ((daily - mu) / sig.clip(lower=1e-8)).fillna(0).clip(-5, 5)

    return _align_to_bars(z, bar_timestamps)
