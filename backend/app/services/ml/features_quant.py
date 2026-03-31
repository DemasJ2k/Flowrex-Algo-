"""
Quantitative features module — ~15 features from Donchian, Turtle, RenTech,
AQR, Ernest Chan, and Lopez de Prado methodologies.

Entry point: compute_quant_features(opens, highs, lows, closes, volumes, ...)
"""
import numpy as np
import pandas as pd


def compute_quant_features(
    opens: np.ndarray, highs: np.ndarray, lows: np.ndarray,
    closes: np.ndarray, volumes: np.ndarray,
    h4_highs: np.ndarray | None = None, h4_lows: np.ndarray | None = None,
    h4_closes: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Compute ~15 quant features. All outputs are float32, same length as input, no NaN/Inf."""
    n = len(closes)
    c = pd.Series(closes, dtype=np.float64)
    h = pd.Series(highs, dtype=np.float64)
    lo = pd.Series(lows, dtype=np.float64)
    feat: dict[str, np.ndarray] = {}

    # ── 1. Donchian Channel Features (5) ────────────────────────────
    for period, label in [(20, "20"), (55, "55")]:
        dh = h.rolling(period).max()
        dl = lo.rolling(period).min()
        width = dh - dl
        pos = (c - dl) / width.replace(0, np.nan)
        feat[f"donch_{label}_position"] = pos.fillna(0.5).values

    # Breakout: close vs previous bar's 20-period high/low
    dh20 = h.rolling(20).max().shift(1)
    dl20 = lo.rolling(20).min().shift(1)
    breakout = np.where(closes > dh20.values, 1.0,
               np.where(closes < dl20.values, -1.0, 0.0))
    feat["donch_20_breakout"] = breakout

    # Squeeze: 20-bar width percentile < 25th over 100 bars
    w20 = (h.rolling(20).max() - lo.rolling(20).min())
    pctile = w20.rolling(100).apply(
        lambda x: (x.iloc[-1] <= np.percentile(x, 25)).astype(float), raw=False
    )
    feat["donch_squeeze"] = pctile.fillna(0).values

    # Width ROC: 5-bar rate of change of 20-bar width
    w20_shifted = w20.shift(5)
    width_roc = (w20 - w20_shifted) / w20_shifted.replace(0, np.nan)
    feat["donch_width_roc"] = width_roc.fillna(0).values

    # ── 2. Mean Reversion — RenTech-inspired (3) ────────────────────
    for period, label in [(24, "24"), (96, "96")]:
        mu = c.rolling(period).mean()
        sigma = c.rolling(period).std(ddof=0)
        z = (c - mu) / sigma.replace(0, np.nan)
        feat[f"zscore_{label}"] = z.fillna(0).values

    rets = c.pct_change().fillna(0)
    autocorr = rets.rolling(50).apply(
        lambda x: pd.Series(x).autocorr(lag=1) if len(x) >= 2 else 0, raw=False
    )
    feat["return_autocorr"] = autocorr.fillna(0).values

    # ── 3. Momentum — AQR-inspired (3) ──────────────────────────────
    for period, label in [(48, "48"), (96, "96")]:
        shifted = c.shift(period)
        mom = (c.shift(1) - shifted) / shifted.replace(0, np.nan)
        feat[f"tsmom_{label}"] = mom.fillna(0).values

    rvol48 = rets.rolling(48).std(ddof=0).replace(0, np.nan)
    feat["volscaled_mom"] = (pd.Series(feat["tsmom_48"]) / rvol48).fillna(0).values

    # ── 4. Hurst Exponent — Chan-inspired (2) ───────────────────────
    log_c = np.log(np.maximum(closes, 1e-10))
    log_rets = pd.Series(np.diff(log_c, prepend=log_c[0]))
    lags = [2, 4, 8, 16, 32]
    log_lags = np.log(np.array(lags, dtype=np.float64))

    # Pre-compute variance series for each lag
    var_series = {}
    for lag in lags:
        # Variance of non-overlapping returns at this lag
        lagged_rets = pd.Series(log_c).diff(lag)
        var_series[lag] = lagged_rets.rolling(100).var(ddof=0)

    def _hurst_row(idx):
        if idx < 100:
            return 0.5
        log_vars = []
        for lag in lags:
            v = var_series[lag].iloc[idx]
            if v is None or np.isnan(v) or v <= 0:
                return 0.5
            log_vars.append(np.log(v))
        log_vars = np.array(log_vars)
        # Linear regression: log(var) = slope * log(lag) + intercept
        # H = slope / 2
        slope = np.polyfit(log_lags, log_vars, 1)[0]
        return np.clip(slope / 2.0, 0.0, 1.0)

    hurst_vals = np.array([_hurst_row(i) for i in range(n)], dtype=np.float64)
    feat["hurst_100"] = hurst_vals

    hurst_regime = np.where(hurst_vals < 0.45, -1.0,
                   np.where(hurst_vals > 0.55, 1.0, 0.0))
    feat["hurst_regime"] = hurst_regime

    # ── 5. Key Level Features (2) ───────────────────────────────────
    atr14 = (pd.Series(highs) - pd.Series(lows)).rolling(14).mean().replace(0, np.nan)
    prev_day_high = h.rolling(288).max().shift(1)
    prev_day_low = lo.rolling(288).min().shift(1)
    feat["dist_prev_day_high_atr"] = ((c - prev_day_high) / atr14).fillna(0).values
    feat["dist_prev_day_low_atr"] = ((c - prev_day_low) / atr14).fillna(0).values

    # ── Cast to float32, clean NaN/Inf ──────────────────────────────
    for k in feat:
        feat[k] = np.nan_to_num(feat[k].astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

    return feat
