"""
features_correlation.py - Cross-symbol correlation & lead-lag features.

For each symbol, computes features based on other symbols' price data:
  - Lead-lag returns (1, 5, 20 bars of other symbols)
  - Rolling correlations (50, 100, 200-bar)
  - Relative performance z-scores
  - Risk-on / Risk-off composite indicator

These features give the model information about the macro regime
that is not visible from a single instrument's OHLCV data.

Usage:
    from app.services.ml.features_correlation import compute_correlation_features

    other_m5 = {
        "US30":   us30_m5_df,
        "XAUUSD": xauusd_m5_df,
    }
    feat_names, feat_matrix = compute_correlation_features(
        symbol="BTCUSD",
        m5=btc_m5_df,
        other_m5=other_m5,
    )

Returns (feature_names: list[str], X: np.ndarray[n, k]).
All features are NaN-safe (NaN -> 0 fill, with validity mask check).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


# ── Symbol correlation groups ────────────────────────────────────────────────
# Key: symbol  -> list of symbols it can benefit from
CORRELATION_PEERS: dict[str, list[str]] = {
    "BTCUSD": ["US30", "XAUUSD"],
    "XAUUSD": ["US30", "BTCUSD"],
    "US30":   ["XAUUSD", "BTCUSD"],
    "NAS100": ["US30", "BTCUSD"],
    "ES":     ["US30", "XAUUSD"],
    "EURUSD": ["XAUUSD", "US30"],
    "GBPUSD": ["XAUUSD", "US30"],
}

# Rolling windows (in M5 bars)
CORR_WINDOWS    = [50, 100, 200]   # ~4h, ~8h, ~17h
PERF_WINDOWS    = [1, 5, 20, 60]   # 5m, 25m, 1h40, 5h
ZSCORE_WINDOW   = 50


def _align_to_timestamps(
    base_m5: pd.DataFrame,
    other_m5: pd.DataFrame,
) -> pd.Series:
    """
    Align other_m5 close prices to base_m5 timestamps.
    Uses forward-fill for minor gaps (holiday/weekend mismatches).
    Returns a Series indexed same as base_m5.
    """
    base_ts  = base_m5["time"].astype(np.int64)
    other_ts = other_m5["time"].astype(np.int64)
    other_close = other_m5["close"].values

    # Build Series with timestamp as index for fast reindex
    other_ser = pd.Series(other_close, index=other_ts, dtype=np.float64)
    # Reindex to base timestamps, forward-fill gaps (max 12 bars = 1h)
    aligned = other_ser.reindex(base_ts).ffill(limit=12)
    return aligned.values  # np.ndarray aligned to base_m5


def compute_correlation_features(
    symbol: str,
    m5: pd.DataFrame,
    other_m5: Optional[dict[str, pd.DataFrame]] = None,
) -> tuple[list[str], np.ndarray]:
    """
    Compute cross-symbol correlation features for `symbol`.

    Parameters
    ----------
    symbol : str
        The symbol being modelled (e.g. "BTCUSD")
    m5 : pd.DataFrame
        M5 OHLCV data for `symbol` with columns: time, open, high, low, close, volume
    other_m5 : dict[str, pd.DataFrame] | None
        M5 OHLCV data for peer symbols. Keys are symbol names.
        If None or empty, returns empty (0-column) feature matrix.

    Returns
    -------
    feature_names : list[str]
    X             : np.ndarray  shape (n, k)  float32
    """
    n = len(m5)
    feature_names: list[str] = []
    columns: list[np.ndarray] = []

    if not other_m5:
        return feature_names, np.empty((n, 0), dtype=np.float32)

    peers = CORRELATION_PEERS.get(symbol, list(other_m5.keys()))
    base_close = m5["close"].values.astype(np.float64)
    base_ret   = np.concatenate([[0.0], np.diff(np.log(np.maximum(base_close, 1e-10)))])

    for peer_sym, peer_df in other_m5.items():
        if peer_sym not in peers:
            continue
        if peer_df is None or len(peer_df) < 10:
            continue

        tag   = peer_sym.lower().replace("usd", "")  # "us30", "xau", "btc"
        p_raw = _align_to_timestamps(m5, peer_df)
        # Replace 0 / NaN prices with forward-filled values
        p_close = pd.Series(p_raw).replace(0, np.nan).ffill().bfill().values
        p_ret   = np.concatenate([[0.0], np.diff(np.log(np.maximum(p_close, 1e-10)))])

        # ── 1. Lead-lag returns of peer ──────────────────────────────────
        for lag in PERF_WINDOWS:
            col_name = f"corr_{tag}_ret_{lag}b"
            if lag == 1:
                feature_names.append(col_name)
                columns.append(p_ret.astype(np.float32))
            else:
                roll_ret = pd.Series(p_ret).rolling(lag, min_periods=lag).sum().values
                feature_names.append(col_name)
                columns.append(roll_ret.astype(np.float32))

        # ── 2. Rolling return correlation between base and peer ──────────
        base_s = pd.Series(base_ret)
        peer_s = pd.Series(p_ret)
        for win in CORR_WINDOWS:
            col_name = f"corr_{symbol.lower().replace('usd','')}_{tag}_corr_{win}"
            roll_corr = base_s.rolling(win, min_periods=win // 2).corr(peer_s).values
            feature_names.append(col_name)
            columns.append(roll_corr.astype(np.float32))

        # ── 3. Relative performance z-score ──────────────────────────────
        # Z-score of (base_return - peer_return) vs recent history
        diff_ret  = base_s - peer_s
        roll_mean = diff_ret.rolling(ZSCORE_WINDOW, min_periods=10).mean()
        roll_std  = diff_ret.rolling(ZSCORE_WINDOW, min_periods=10).std()
        z_score   = ((diff_ret - roll_mean) / (roll_std + 1e-10)).values
        feature_names.append(f"corr_{symbol.lower().replace('usd','')}_{tag}_relperf_z")
        columns.append(z_score.astype(np.float32))

        # ── 4. Peer volatility ratio ──────────────────────────────────────
        # Is peer more or less volatile than usual right now?
        peer_vol_short = peer_s.rolling(20,  min_periods=5).std()
        peer_vol_long  = peer_s.rolling(100, min_periods=20).std()
        vol_ratio = (peer_vol_short / (peer_vol_long + 1e-10)).values
        feature_names.append(f"corr_{tag}_vol_ratio")
        columns.append(vol_ratio.astype(np.float32))

    # ── 5. Risk-on / risk-off composite indicator ─────────────────────────
    # Only if we have both US30 and XAUUSD
    us30_in  = "US30"   in other_m5 and other_m5["US30"]   is not None
    xau_in   = "XAUUSD" in other_m5 and other_m5["XAUUSD"] is not None

    if us30_in and xau_in:
        us30_close = pd.Series(_align_to_timestamps(m5, other_m5["US30"])).ffill().bfill().values
        xau_close  = pd.Series(_align_to_timestamps(m5, other_m5["XAUUSD"])).ffill().bfill().values

        us30_ret_20 = pd.Series(us30_close).pct_change().rolling(20, min_periods=5).sum().values
        xau_ret_20  = pd.Series(xau_close).pct_change().rolling(20, min_periods=5).sum().values

        # Risk-on = equities rising, gold falling
        risk_on_raw = us30_ret_20 - xau_ret_20
        # Normalise to [-1, 1] via rolling z-score
        ro_ser    = pd.Series(risk_on_raw)
        ro_mean   = ro_ser.rolling(100, min_periods=10).mean()
        ro_std    = ro_ser.rolling(100, min_periods=10).std()
        risk_on_z = ((ro_ser - ro_mean) / (ro_std + 1e-10)).values

        feature_names.append("corr_risk_on_z")
        columns.append(risk_on_z.astype(np.float32))

        # Regime signal: rolling percentile rank of risk-on z (0=risk-off, 1=risk-on)
        risk_on_rank = ro_ser.rolling(200, min_periods=20).apply(
            lambda x: (x[-1] > x[:-1]).sum() / max(len(x) - 1, 1), raw=True
        ).values
        feature_names.append("corr_risk_on_rank")
        columns.append(risk_on_rank.astype(np.float32))

    # ── Assemble matrix ───────────────────────────────────────────────────
    if not columns:
        return [], np.empty((n, 0), dtype=np.float32)

    X = np.column_stack(columns).astype(np.float32)
    # NaN -> 0 fill (safe default: no signal when data unavailable)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    return feature_names, X
