"""Momentum feature module — 20 features prefixed `mom_`."""

import numpy as np
import pandas as pd


def _safe(arr: np.ndarray) -> np.ndarray:
    """Replace NaN/Inf with 0 and cast to float32."""
    out = np.asarray(arr, dtype=np.float32)
    out[~np.isfinite(out)] = 0.0
    return out


def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(arr).rolling(n, min_periods=1).mean().values
    return s


def _roc(closes: np.ndarray, n: int) -> np.ndarray:
    out = np.zeros_like(closes)
    out[n:] = (closes[n:] - closes[:-n]) / np.where(closes[:-n] == 0, 1.0, closes[:-n])
    return out


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, n: int = 14) -> np.ndarray:
    tr = np.maximum(highs - lows,
                    np.maximum(np.abs(highs - np.roll(closes, 1)),
                               np.abs(lows - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]
    return pd.Series(tr).rolling(n, min_periods=1).mean().values


def _obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    signs = np.sign(np.diff(closes, prepend=closes[0]))
    return np.cumsum(signs * volumes)


def compute_momentum_features(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    times=None,
) -> dict[str, np.ndarray]:
    """Compute 20 momentum features. All outputs float32, no NaN/Inf."""
    closes = np.asarray(closes, dtype=np.float64)
    opens = np.asarray(opens, dtype=np.float64)
    highs = np.asarray(highs, dtype=np.float64)
    lows = np.asarray(lows, dtype=np.float64)
    volumes = np.asarray(volumes, dtype=np.float64)
    n = len(closes)
    rets = np.diff(closes, prepend=closes[0]) / np.where(np.roll(closes, 1) == 0, 1.0, np.roll(closes, 1))
    rets[0] = 0.0
    atr = _atr(highs, lows, closes)
    atr_safe = np.where(atr == 0, 1.0, atr)

    # --- ROC Cascades ---
    roc3 = _roc(closes, 3)
    roc12 = _roc(closes, 12)
    roc48 = _roc(closes, 48)
    roc_cascade = np.sign(roc3) + np.sign(roc12) + np.sign(roc48)

    # --- Acceleration ---
    d1 = np.diff(closes, prepend=closes[0])
    accel = np.diff(d1, prepend=d1[0])
    accel_smooth = _sma(accel, 5)
    jerk = np.diff(accel, prepend=accel[0])

    # --- Volume-Weighted ---
    # VWAP: session resets every 288 bars
    session_len = 288
    vwap_dist = np.zeros(n)
    for start in range(0, n, session_len):
        end = min(start + session_len, n)
        cum_vp = np.cumsum(closes[start:end] * volumes[start:end])
        cum_v = np.cumsum(volumes[start:end])
        cum_v_safe = np.where(cum_v == 0, 1.0, cum_v)
        vwap_seg = cum_vp / cum_v_safe
        vwap_dist[start:end] = (closes[start:end] - vwap_seg) / atr_safe[start:end]

    # vol_momentum: volume * sign(return) cumulated 20 bars
    signed_vol = volumes * np.sign(rets)
    vol_momentum = pd.Series(signed_vol).rolling(20, min_periods=1).sum().values

    # vol_breakout: binary vol > 2x avg AND |return| > 1 ATR
    vol_avg = pd.Series(volumes).rolling(20, min_periods=1).mean().values
    abs_ret_price = np.abs(np.diff(closes, prepend=closes[0]))
    vol_breakout = ((volumes > 2.0 * vol_avg) & (abs_ret_price > atr)).astype(np.float64)

    # obv_slope: 10-bar difference of OBV
    obv = _obv(closes, volumes)
    obv_slope = np.zeros(n)
    obv_slope[10:] = obv[10:] - obv[:-10]

    # --- Divergence ---
    # RSI 14
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=1).mean().values
    avg_loss = pd.Series(loss).rolling(14, min_periods=1).mean().values
    rs = avg_gain / np.where(avg_loss == 0, 1.0, avg_loss)
    rsi = 100.0 - 100.0 / (1.0 + rs)

    # price_rsi_div: price new 20-bar high but RSI lower than previous 20-bar high's RSI
    roll_hi = pd.Series(closes).rolling(20, min_periods=1).max().values
    price_new_hi = (closes >= roll_hi).astype(np.float64)
    rsi_roll_hi = pd.Series(rsi).rolling(20, min_periods=1).max().values
    price_rsi_div = (price_new_hi * (rsi < rsi_roll_hi)).astype(np.float64)

    # price_vol_div: price up but volume declining (both over 10 bars)
    price_chg_10 = np.zeros(n)
    price_chg_10[10:] = closes[10:] - closes[:-10]
    vol_chg_10 = np.zeros(n)
    vol_chg_10[10:] = volumes[10:] - volumes[:-10]
    price_vol_div = ((price_chg_10 > 0) & (vol_chg_10 < 0)).astype(np.float64)

    # macd_div: price new 20-bar high but MACD hist declining
    ema12 = pd.Series(closes).ewm(span=12, min_periods=1).mean().values
    ema26 = pd.Series(closes).ewm(span=26, min_periods=1).mean().values
    macd_line = ema12 - ema26
    macd_signal = pd.Series(macd_line).ewm(span=9, min_periods=1).mean().values
    macd_hist = macd_line - macd_signal
    macd_hist_prev = np.roll(macd_hist, 1); macd_hist_prev[0] = 0.0
    macd_div = (price_new_hi * (macd_hist < macd_hist_prev)).astype(np.float64)

    # --- Quality ---
    consistency_12 = pd.Series((rets > 0).astype(np.float64)).rolling(12, min_periods=1).mean().values
    consistency_48 = pd.Series((rets > 0).astype(np.float64)).rolling(48, min_periods=1).mean().values

    # streak: consecutive same-direction bars
    dirs = np.sign(rets)
    streak = np.zeros(n)
    for i in range(1, n):
        if dirs[i] != 0 and dirs[i] == dirs[i - 1]:
            streak[i] = streak[i - 1] + dirs[i]
        else:
            streak[i] = dirs[i]

    # --- Relative Strength ---
    ret12 = _roc(closes, 12)
    ret48 = _roc(closes, 48)
    rs_12_48 = ret12 / np.where(np.abs(ret48) < 1e-10, 1.0, ret48)

    up_sum = pd.Series(np.where(rets > 0, rets, 0.0)).rolling(20, min_periods=1).sum().values
    dn_sum = pd.Series(np.where(rets < 0, -rets, 0.0)).rolling(20, min_periods=1).sum().values
    up_down_ratio = up_sum / np.where(dn_sum == 0, 1.0, dn_sum)

    # efficiency: net move / gross move over 20 bars
    net_move = np.zeros(n)
    net_move[20:] = closes[20:] - closes[:-20]
    gross_move = pd.Series(np.abs(np.diff(closes, prepend=closes[0]))).rolling(20, min_periods=1).sum().values
    efficiency = net_move / np.where(gross_move == 0, 1.0, gross_move)

    return {
        "mom_roc_3": _safe(roc3),
        "mom_roc_12": _safe(roc12),
        "mom_roc_48": _safe(roc48),
        "mom_roc_cascade": _safe(roc_cascade),
        "mom_accel": _safe(accel),
        "mom_accel_smooth": _safe(accel_smooth),
        "mom_jerk": _safe(jerk),
        "mom_vwap_dist": _safe(vwap_dist),
        "mom_vol_momentum": _safe(vol_momentum),
        "mom_vol_breakout": _safe(vol_breakout),
        "mom_obv_slope": _safe(obv_slope),
        "mom_price_rsi_div": _safe(price_rsi_div),
        "mom_price_vol_div": _safe(price_vol_div),
        "mom_macd_div": _safe(macd_div),
        "mom_consistency_12": _safe(consistency_12),
        "mom_consistency_48": _safe(consistency_48),
        "mom_streak": _safe(streak),
        "mom_rs_12_48": _safe(rs_12_48),
        "mom_up_down_ratio": _safe(up_down_ratio),
        "mom_efficiency": _safe(efficiency),
    }
