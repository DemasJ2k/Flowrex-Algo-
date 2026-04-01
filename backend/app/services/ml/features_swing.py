"""
Swing trading feature pipeline for H4/D1 timeframe.

Optimised for Expert/Swing agent: fewer, higher-signal features.
Focus on HTF ICT structure, Williams daily patterns, COT positioning,
Donchian trend context, and regime detection.

Entry point: compute_swing_features(h4_bars, d1_bars, symbol)
Returns ~80 features suitable for 1-8 day hold trades.
"""
import numpy as np
import pandas as pd


def _to_arrays(bars):
    """Convert bars to numpy arrays."""
    if isinstance(bars, pd.DataFrame):
        df = bars
    else:
        df = pd.DataFrame(bars)
    return (
        df["time"].values if "time" in df.columns else np.arange(len(df)),
        df["open"].values.astype(float),
        df["high"].values.astype(float),
        df["low"].values.astype(float),
        df["close"].values.astype(float),
        df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(df)),
    )


def _ema(values, period):
    """EMA via pandas."""
    return pd.Series(values).ewm(span=period, min_periods=period).mean().values


def _sma(values, period):
    """SMA via pandas."""
    return pd.Series(values).rolling(period, min_periods=period).mean().values


def _atr(highs, lows, closes, period=14):
    """ATR."""
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    return pd.Series(tr).rolling(period, min_periods=1).mean().values


def _rsi(closes, period=14):
    """RSI."""
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).ewm(span=period, min_periods=period).mean().values
    avg_loss = pd.Series(loss).ewm(span=period, min_periods=period).mean().values
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    return 100 - 100 / (1 + rs)


def _returns(closes, period):
    """Percentage returns."""
    r = np.zeros(len(closes))
    r[period:] = (closes[period:] - closes[:-period]) / np.where(closes[:-period] != 0, closes[:-period], 1)
    return r


def compute_swing_features(
    h4_bars,
    d1_bars=None,
    symbol: str = "US30",
    include_cot: bool = True,
) -> tuple[list[str], np.ndarray]:
    """
    Compute ~80 swing trading features from H4 bars + optional D1 context.

    Returns (feature_names, X_matrix) where X has shape (n_bars, n_features).
    """
    times, opens, highs, lows, closes, volumes = _to_arrays(h4_bars)
    n = len(closes)
    features = {}

    atr14 = _atr(highs, lows, closes, 14)
    atr50 = _atr(highs, lows, closes, 50)

    # ── Price-based (8) ─────────────────────────────────────────
    features["return_1"] = _returns(closes, 1)
    features["return_3"] = _returns(closes, 3)
    features["return_6"] = _returns(closes, 6)    # 1 day
    features["return_12"] = _returns(closes, 12)   # 2 days
    features["return_30"] = _returns(closes, 30)   # 5 days
    features["log_return"] = np.zeros(n)
    features["log_return"][1:] = np.log(closes[1:] / np.where(closes[:-1] > 0, closes[:-1], 1))
    hl_range = highs - lows
    features["hl_range_atr"] = np.where(atr14 > 0, hl_range / atr14, 0)
    body = closes - opens
    features["body_ratio"] = np.where(hl_range > 0, body / hl_range, 0)

    # ── Moving Averages (8) ──────────────────────────────────────
    for p in [10, 21, 50, 200]:
        e = _ema(closes, p)
        features[f"ema_{p}_dist"] = np.where(closes > 0, (closes - e) / closes, 0)
    features["ema_10_21_cross"] = np.sign(_ema(closes, 10) - _ema(closes, 21))
    features["ema_21_50_cross"] = np.sign(_ema(closes, 21) - _ema(closes, 50))
    features["price_above_ema200"] = np.where(closes > _ema(closes, 200), 1.0, -1.0)
    ema50 = _ema(closes, 50)
    features["ema_50_slope"] = np.zeros(n)
    features["ema_50_slope"][5:] = (ema50[5:] - ema50[:-5]) / np.where(np.abs(ema50[:-5]) > 0, np.abs(ema50[:-5]), 1)

    # ── Momentum (8) ─────────────────────────────────────────────
    features["rsi_14"] = _rsi(closes, 14)
    features["rsi_28"] = _rsi(closes, 28)
    macd_fast = _ema(closes, 12)
    macd_slow = _ema(closes, 26)
    macd_line = macd_fast - macd_slow
    macd_signal = _ema(macd_line, 9)
    features["macd_hist"] = macd_line - macd_signal
    features["macd_cross"] = np.sign(macd_line - macd_signal)
    # Williams %R multi-period
    for p in [14, 28]:
        hh = pd.Series(highs).rolling(p).max().values
        ll = pd.Series(lows).rolling(p).min().values
        denom = hh - ll
        features[f"wr_{p}"] = np.where(denom > 0, (hh - closes) / denom * -100, -50)
    features["rsi_slope_5"] = np.zeros(n)
    features["rsi_slope_5"][5:] = features["rsi_14"][5:] - features["rsi_14"][:-5]
    features["momentum_6"] = closes - np.roll(closes, 6)

    # ── Volatility (6) ──────────────────────────────────────────
    features["atr_14"] = atr14
    features["atr_ratio"] = np.where(closes > 0, atr14 / closes, 0)
    features["atr_14_50_ratio"] = np.where(atr50 > 0, atr14 / atr50, 1)
    bb_mid = _sma(closes, 20)
    bb_std = pd.Series(closes).rolling(20).std(ddof=0).values
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = bb_upper - bb_lower
    features["bb_pct_b"] = np.where(bb_width > 0, (closes - bb_lower) / bb_width, 0.5)
    features["bb_bandwidth"] = np.where(bb_mid > 0, bb_width / bb_mid, 0)
    features["atr_change_5"] = _returns(atr14, 5)

    # ── Donchian Channels (5) ────────────────────────────────────
    h_s = pd.Series(highs)
    l_s = pd.Series(lows)
    for p, label in [(20, "20"), (55, "55")]:
        dh = h_s.rolling(p).max()
        dl = l_s.rolling(p).min()
        width = dh - dl
        features[f"donch_{label}_pos"] = ((pd.Series(closes) - dl) / width.replace(0, np.nan)).fillna(0.5).values
    dh20 = h_s.rolling(20).max().shift(1)
    dl20 = l_s.rolling(20).min().shift(1)
    features["donch_breakout"] = np.where(closes > dh20.values, 1.0, np.where(closes < dl20.values, -1.0, 0.0))
    dcw = h_s.rolling(20).max() - l_s.rolling(20).min()
    features["donch_squeeze"] = (dcw.rolling(50, min_periods=20).rank(pct=True) < 0.25).astype(float).fillna(0).values
    features["donch_width_roc"] = dcw.pct_change(5).fillna(0).values

    # ── ICT/SMC HTF Features (12) ────────────────────────────────
    try:
        from app.services.ml.features_ict import compute_ict_features
        ict = compute_ict_features(opens, highs, lows, closes, volumes, swing_window=6)
        # Select the most important HTF ICT features
        ict_keys = [
            "ict_trend", "ict_choch_recent", "ict_bos_momentum",
            "ict_sweep_bull", "ict_sweep_bear",
            "ict_pd_position", "ict_in_discount_bull",
            "ict_fvg_bull_count", "ict_fvg_bear_count",
            "ict_disp_strength", "ict_disp_direction",
            "ict_confluence_score",
        ]
        for k in ict_keys:
            features[k] = ict.get(k, np.zeros(n))
    except Exception:
        for k in ["ict_trend", "ict_choch_recent", "ict_bos_momentum",
                   "ict_sweep_bull", "ict_sweep_bear", "ict_pd_position",
                   "ict_in_discount_bull", "ict_fvg_bull_count", "ict_fvg_bear_count",
                   "ict_disp_strength", "ict_disp_direction", "ict_confluence_score"]:
            features[k] = np.zeros(n)

    # ── Williams Swing Features (8) ──────────────────────────────
    try:
        from app.services.ml.features_williams import compute_williams_features
        wf = compute_williams_features(opens, highs, lows, closes, volumes)
        williams_keys = [
            "lw_stretch_up", "lw_stretch_down", "lw_above_stretch", "lw_below_stretch",
            "lw_range_expansion", "lw_nr7", "lw_smash_bull", "lw_smash_bear",
        ]
        for k in williams_keys:
            features[k] = wf.get(k, np.zeros(n))
    except Exception:
        for k in ["lw_stretch_up", "lw_stretch_down", "lw_above_stretch", "lw_below_stretch",
                   "lw_range_expansion", "lw_nr7", "lw_smash_bull", "lw_smash_bear"]:
            features[k] = np.zeros(n)

    # ── Quant Features (6) ───────────────────────────────────────
    try:
        from app.services.ml.features_quant import compute_quant_features
        qf = compute_quant_features(opens, highs, lows, closes, volumes)
        quant_keys = [
            "zscore_24", "zscore_96", "return_autocorr",
            "tsmom_48", "hurst_100", "hurst_regime",
        ]
        for k in quant_keys:
            features[k] = qf.get(k, np.zeros(n))
    except Exception:
        for k in ["zscore_24", "zscore_96", "return_autocorr", "tsmom_48", "hurst_100", "hurst_regime"]:
            features[k] = np.zeros(n)

    # ── Session / Time (4) ───────────────────────────────────────
    hours = pd.to_datetime(times, unit='s', utc=True).hour if np.issubdtype(type(times[0]), np.integer) else np.zeros(n)
    dow = pd.to_datetime(times, unit='s', utc=True).dayofweek if np.issubdtype(type(times[0]), np.integer) else np.zeros(n)
    features["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    features["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    features["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    features["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    # ── D1 context (6) ──────────────────────────────────────────
    if d1_bars is not None and len(d1_bars) > 50:
        _, d1_o, d1_h, d1_l, d1_c, _ = _to_arrays(d1_bars)
        d1_ema50 = _ema(d1_c, 50)
        d1_bias = np.where(d1_c > d1_ema50, 1.0, -1.0)
        d1_atr = _atr(d1_h, d1_l, d1_c, 14)
        d1_rsi = _rsi(d1_c, 14)
        # Align D1 → H4 (6 H4 bars per D1)
        ratio = max(1, n // len(d1_c))
        features["d1_bias"] = np.repeat(d1_bias, ratio)[:n]
        features["d1_atr_ratio"] = np.repeat(np.where(d1_c > 0, d1_atr / d1_c, 0), ratio)[:n]
        features["d1_rsi"] = np.repeat(d1_rsi, ratio)[:n]
        d1_return_5 = _returns(d1_c, 5)
        features["d1_return_5d"] = np.repeat(d1_return_5, ratio)[:n]
        d1_range = d1_h - d1_l
        d1_adr = _sma(d1_range, 14)
        features["d1_adr"] = np.repeat(d1_adr, ratio)[:n]
        features["d1_range_consumed"] = np.repeat(
            np.where(d1_adr > 0, d1_range / d1_adr, 0), ratio
        )[:n]
    else:
        for k in ["d1_bias", "d1_atr_ratio", "d1_rsi", "d1_return_5d", "d1_adr", "d1_range_consumed"]:
            features[k] = np.zeros(n)

    # ── COT (weekly, forward-filled) (8) ─────────────────────────
    if include_cot and symbol in ("US30", "XAUUSD"):
        try:
            from app.services.ml.features_cot import add_cot_features
            add_cot_features(features, times=times, symbol=symbol)
        except Exception:
            pass

    # ── Pad missing features ─────────────────────────────────────
    for k in features:
        arr = features[k]
        if len(arr) < n:
            features[k] = np.concatenate([arr, np.zeros(n - len(arr))])
        elif len(arr) > n:
            features[k] = arr[:n]

    # ── Assemble output ──────────────────────────────────────────
    feature_names = list(features.keys())
    X = np.column_stack([features[k] for k in feature_names])
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    return feature_names, X
