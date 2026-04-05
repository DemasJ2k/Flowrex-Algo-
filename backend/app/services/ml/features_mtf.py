"""
Multi-timeframe feature engineering — 130+ technical features.
Main entry: compute_expert_features(m5_bars, m15_bars, h1_bars, h4_bars, d1_bars)

Tier-1 additions (features_tier1.py):
  Yang-Zhang volatility, Amihud illiquidity, CVD proxy, MTF divergence,
  MTF momentum magnitude, rolling max drawdown, session proximity, DOM cyclical,
  time-of-day range ratio

Calendar features (features_calendar.py):
  FOMC drift flag, OPEX week, quad-witching, BTC halving cycle,
  gold seasonality, buyback blackout, crypto OPEX

External macro features (features_external.py):
  VIX, TIPS real yield, 2s10s spread, BTC funding rate, BTC dominance,
  ETH/BTC ratio — all forward-filled with no lookahead bias

Regime × feature interactions:
  regime_x_rsi, regime_x_macd_hist — highest expected alpha signals
"""
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from app.services.backtest.indicators import (
    ema, sma, rsi, atr, macd, bollinger_bands, stochastic,
    cci, williams_r, obv, roc, keltner_channels,
)


def _to_arrays(bars: list[dict] | pd.DataFrame):
    """Convert bars to numpy arrays (time, o, h, l, c, v)."""
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


def compute_expert_features(
    m5_bars,
    h1_bars=None,
    h4_bars=None,
    d1_bars=None,
    symbol: str = "BTCUSD",
    include_external: bool = True,
    other_m5: dict | None = None,
    m15_bars=None,
) -> tuple[list[str], np.ndarray]:
    """
    Compute 130+ features from M5 bars + optional HTF context.
    Returns (feature_names, X_matrix) where X has shape (n_bars, n_features).
    NaN/Inf values replaced with 0.

    Args:
        m5_bars:          M5 OHLCV bars (list of dicts or DataFrame)
        h1_bars:          H1 bars for HTF alignment (optional)
        h4_bars:          H4 bars for HTF alignment (optional)
        d1_bars:          D1 bars for HTF alignment (optional)
        symbol:           "BTCUSD", "XAUUSD", or "US30" -- enables symbol-specific features
        include_external: Load macro/external features from data/macro/ cache
        other_m5:         dict[symbol -> M5 DataFrame] for peer symbols.
                          When provided, cross-symbol correlation features are appended.
        m15_bars:         M15 bars for intermediate TF context (3 M5 bars per M15).
                          Adds 7 features: m15_trend, m15_rsi, m15_atr, m15_above_ema50,
                          m15_macd_hist, m15_ema_slope, m15_momentum_4.
    """
    times, opens, highs, lows, closes, volumes = _to_arrays(m5_bars)
    n = len(closes)
    features = {}

    # ── Price-based (10) ───────────────────────────────────────────
    features["return_1"] = _returns(closes, 1)
    features["return_3"] = _returns(closes, 3)
    features["return_5"] = _returns(closes, 5)
    features["return_10"] = _returns(closes, 10)
    features["log_return"] = _log_returns(closes)
    atr_14 = atr(highs, lows, closes, 14)
    hl_range = highs - lows
    features["hl_range_atr_ratio"] = np.where(atr_14 > 0, hl_range / atr_14, 0)
    body = closes - opens
    features["body_size_ratio"] = np.where(hl_range > 0, body / hl_range, 0)
    features["upper_wick_ratio"] = np.where(hl_range > 0, (highs - np.maximum(opens, closes)) / hl_range, 0)
    features["lower_wick_ratio"] = np.where(hl_range > 0, (np.minimum(opens, closes) - lows) / hl_range, 0)
    gap = np.zeros(n)
    gap[1:] = opens[1:] - closes[:-1]
    features["gap"] = gap
    features["return_20"] = _returns(closes, 20)
    features["abs_return_1"] = np.abs(_returns(closes, 1))
    features["high_low_pct"] = np.where(closes > 0, hl_range / closes, 0)

    # ── Moving Averages (15) ──────────────────────────────────────
    for p in [8, 21, 50, 200]:
        e = ema(closes, p)
        features[f"ema_{p}_dist"] = np.where(closes > 0, (closes - e) / closes, 0)
    for p in [10, 20, 50]:
        s = sma(closes, p)
        features[f"sma_{p}_dist"] = np.where(closes > 0, (closes - s) / closes, 0)
    # Crossover signals
    ema8 = ema(closes, 8)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    features["ema_8_21_cross"] = _crossover_signal(ema8, ema21)
    features["ema_21_50_cross"] = _crossover_signal(ema21, ema50)
    features["ema_8_slope"] = _slope(ema8, 5)
    features["ema_21_slope"] = _slope(ema21, 5)
    features["price_above_ema200"] = np.where(closes > ema200, 1.0, -1.0)
    features["ema_spread_8_21"] = np.where(closes > 0, (ema8 - ema21) / closes, 0)

    # ── Momentum (12) ────────────────────────────────────────────
    features["rsi_14"] = rsi(closes, 14)
    stoch_k, stoch_d = stochastic(highs, lows, closes, 14, 3)
    features["stoch_k"] = stoch_k
    features["stoch_d"] = stoch_d
    macd_line, macd_signal, macd_hist = macd(closes, 12, 26, 9)
    features["macd_line"] = macd_line
    features["macd_signal"] = macd_signal
    features["macd_hist"] = macd_hist
    features["cci_20"] = cci(highs, lows, closes, 20)
    features["williams_r"] = williams_r(highs, lows, closes, 14)
    features["roc_10"] = roc(closes, 10)
    features["rsi_slope"] = _slope(rsi(closes, 14), 5)
    features["macd_cross"] = _crossover_signal(macd_line, macd_signal)
    features["roc_5"] = roc(closes, 5)
    features["rsi_overbought"] = (rsi(closes, 14) > 70).astype(float)
    features["rsi_oversold"] = (rsi(closes, 14) < 30).astype(float)
    features["stoch_cross"] = _crossover_signal(stoch_k, stoch_d)
    features["cci_extreme"] = np.where(cci(highs, lows, closes, 20) > 100, 1.0,
                              np.where(cci(highs, lows, closes, 20) < -100, -1.0, 0.0))
    features["momentum_5"] = closes - np.roll(closes, 5)

    # ── Volatility (10) ───────────────────────────────────────────
    features["atr_14"] = atr_14
    features["atr_ratio"] = np.where(closes > 0, atr_14 / closes, 0)
    bb_upper, bb_lower, bb_mid, bb_pct_b, bb_bandwidth = bollinger_bands(closes, 20, 2.0)
    features["bb_upper_dist"] = np.where(closes > 0, (bb_upper - closes) / closes, 0)
    features["bb_lower_dist"] = np.where(closes > 0, (closes - bb_lower) / closes, 0)
    features["bb_pct_b"] = bb_pct_b
    features["bb_bandwidth"] = bb_bandwidth
    features["atr_change"] = _returns(atr_14, 5)
    # Yang-Zhang volatility (replaces close-to-close hist_vol_20; 14x more efficient)
    from app.services.ml.features_tier1 import yang_zhang_vol as _yz_vol
    features["hist_vol_20"] = _yz_vol(opens, highs, lows, closes, window=20, bars_per_year=252 * 288)
    # Keltner channel position
    kc_upper, kc_lower, kc_mid = keltner_channels(highs, lows, closes, 20, 14, 1.5)
    kc_range = kc_upper - kc_lower
    features["keltner_position"] = np.where(kc_range > 0, (closes - kc_lower) / kc_range, 0.5)

    # ── Volume (5) ─────────────────────────────────────────────────
    vol_sma20 = sma(volumes, 20)
    features["volume_ratio"] = np.where(vol_sma20 > 0, volumes / vol_sma20, 1.0)
    # Volume trend (5-bar slope) — vectorised via _slope
    features["volume_trend"] = _slope(volumes, 5)
    features["obv"] = obv(closes, volumes)
    # Volume-price correlation (20-bar rolling) — vectorised via pandas rolling
    _vc = pd.Series(volumes - pd.Series(volumes).rolling(20, min_periods=1).mean())
    _cc = pd.Series(closes  - pd.Series(closes).rolling(20, min_periods=1).mean())
    _num = (_vc * _cc).rolling(20, min_periods=1).mean()
    _std_v = pd.Series(volumes).rolling(20, min_periods=1).std().clip(lower=1e-10)
    _std_c = pd.Series(closes).rolling(20, min_periods=1).std().clip(lower=1e-10)
    features["vol_price_corr"] = (_num / (_std_v * _std_c)).clip(-1, 1).fillna(0).values
    # VWAP proxy (cumulative)
    cum_vol = np.cumsum(volumes)
    cum_vp = np.cumsum(closes * volumes)
    features["vwap_dist"] = np.where(
        (cum_vol > 0) & (closes > 0),
        (closes - cum_vp / cum_vol) / closes,
        0,
    )

    # ── Structure (8) ──────────────────────────────────────────────
    swing_hi, swing_lo = _swing_levels(highs, lows, window=10)
    features["dist_swing_high"] = np.where(closes > 0, (swing_hi - closes) / closes, 0)
    features["dist_swing_low"] = np.where(closes > 0, (closes - swing_lo) / closes, 0)
    features["sr_proximity"] = _sr_proximity(closes, highs, lows, window=50)
    features["break_of_structure"] = _break_of_structure(highs, lows, window=10)
    features["higher_high"] = _higher_high_lower_low(highs, lows, window=5)
    features["trend_strength_5"] = _trend_strength(closes, 5)
    features["trend_strength_20"] = _trend_strength(closes, 20)
    features["price_momentum"] = _returns(closes, 20)

    # ── Session (8) ────────────────────────────────────────────────
    hours = _extract_hours(times)
    features["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    features["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    dow = _extract_dow(times)
    features["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    features["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    features["is_london"] = ((hours >= 8) & (hours < 16)).astype(float)
    features["is_ny"] = ((hours >= 13) & (hours < 21)).astype(float)
    features["is_asian"] = ((hours >= 0) & (hours < 8)).astype(float)
    features["is_killzone"] = ((hours >= 13) & (hours < 16)).astype(float)

    # ── Multi-timeframe (M15 intermediate tier + H1/H4/D1) ────────────
    # Store raw HTF arrays for tier1 MTF momentum magnitude
    _h1_c_aligned = None; _h1_atr_aligned = None
    _h4_c_aligned = None; _h4_atr_aligned = None

    # M15 features — 3 M5 bars per M15 bar (intermediate trend context)
    if m15_bars is not None and len(m15_bars) > 50:
        try:
            _, _, m15_h, m15_l, m15_c, _ = _to_arrays(m15_bars)
            m15_ema20  = ema(m15_c, 20)
            m15_ema50  = ema(m15_c, 50)
            m15_rsi14  = rsi(m15_c, 14)
            m15_atr14  = atr(m15_h, m15_l, m15_c, 14)
            m15_ml, _, m15_mh = macd(m15_c, 12, 26, 9)
            # Trend: +1 if price > EMA20, -1 below
            m15_trend_raw = np.where(m15_c > m15_ema20, 1.0, -1.0)
            # EMA20 slope (5-bar normalised slope on M15)
            m15_slope_raw = _slope(m15_ema20, 5)
            features["m15_trend"]      = _align_htf(m15_trend_raw, n, 3)
            features["m15_rsi"]        = _align_htf(m15_rsi14, n, 3)
            features["m15_atr"]        = _align_htf(
                np.where(m15_c > 0, m15_atr14 / m15_c, 0), n, 3)  # ATR as % of price
            features["m15_above_ema50"]= _align_htf(
                np.where(m15_c > m15_ema50, 1.0, -1.0), n, 3)
            features["m15_macd_hist"]  = _align_htf(m15_mh, n, 3)
            features["m15_ema_slope"]  = _align_htf(m15_slope_raw, n, 3)
            features["m15_momentum_4"] = _align_htf(_returns(m15_c, 4), n, 3)  # 60-min momentum
        except Exception:
            pass  # M15 features are optional — non-fatal

    # M15 placeholders if not provided (keeps consistent feature count when M15 missing)
    for k in ["m15_trend", "m15_rsi", "m15_atr", "m15_above_ema50",
              "m15_macd_hist", "m15_ema_slope", "m15_momentum_4"]:
        if k not in features:
            features[k] = np.zeros(n)

    if h1_bars is not None and len(h1_bars) > 50:
        _, _, h1_h, h1_l, h1_c, _ = _to_arrays(h1_bars)
        h1_ema21 = ema(h1_c, 21)
        h1_trend = np.where(h1_c > h1_ema21, 1.0, -1.0)
        h1_atr_raw = atr(h1_h, h1_l, h1_c, 14)
        features["h1_trend"] = _align_htf(h1_trend, n, 12)  # 12 M5 bars per H1
        features["h1_rsi"] = _align_htf(rsi(h1_c, 14), n, 12)
        features["h1_atr"] = _align_htf(h1_atr_raw, n, 12)
        _h1_c_aligned   = _align_htf(h1_c, n, 12)
        _h1_atr_aligned = _align_htf(h1_atr_raw, n, 12)

    if h4_bars is not None and len(h4_bars) > 50:
        _, _, h4_h, h4_l, h4_c, _ = _to_arrays(h4_bars)
        h4_ema21 = ema(h4_c, 21)
        h4_trend = np.where(h4_c > h4_ema21, 1.0, -1.0)
        h4_atr_raw = atr(h4_h, h4_l, h4_c, 14)
        features["h4_trend"] = _align_htf(h4_trend, n, 48)  # 48 M5 bars per H4
        features["h4_rsi"] = _align_htf(rsi(h4_c, 14), n, 48)
        _h4_c_aligned   = _align_htf(h4_c, n, 48)
        _h4_atr_aligned = _align_htf(h4_atr_raw, n, 48)

    if d1_bars is not None and len(d1_bars) > 50:
        _, _, d1_h, d1_l, d1_c, _ = _to_arrays(d1_bars)
        d1_ema50 = ema(d1_c, 50)
        d1_bias = np.where(d1_c > d1_ema50, 1.0, -1.0)
        features["d1_bias"] = _align_htf(d1_bias, n, 288)  # 288 M5 bars per D1
        features["d1_atr"] = _align_htf(atr(d1_h, d1_l, d1_c, 14), n, 288)

    # If HTF not provided, add zero-filled placeholders to maintain consistent feature count
    htf_keys = ["h1_trend", "h1_rsi", "h1_atr", "h4_trend", "h4_rsi", "d1_bias", "d1_atr"]
    for k in htf_keys:
        if k not in features:
            features[k] = np.zeros(n)

    # ── HTF alignment score (includes M15 when available) ─────────
    # Sum all available TF trend signals then normalise to [-1, 1]
    htf_sum = features["h1_trend"] + features["h4_trend"] + features["d1_bias"]
    htf_n   = 3
    if np.any(features["m15_trend"] != 0):          # M15 was loaded
        htf_sum = htf_sum + features["m15_trend"]
        htf_n   = 4
    features["htf_alignment"] = htf_sum / htf_n

    # ── Smart Money Concepts (13 features) ────────────────────────
    try:
        from app.services.ml.smc_features import compute_smc_features
        smc = compute_smc_features(opens, highs, lows, closes)
        for k, v in smc.items():
            features[k] = v
    except Exception:
        # Fallback: add zero-filled SMC placeholders
        smc_keys = [
            "smc_bos", "smc_choch", "smc_ob_bull_proximity", "smc_ob_bear_proximity",
            "smc_fvg_bull", "smc_fvg_bear", "smc_fvg_bull_proximity", "smc_fvg_bear_proximity",
            "smc_liquidity_above", "smc_liquidity_below", "smc_premium_discount",
            "smc_displacement",
        ]
        for k in smc_keys:
            features[k] = np.zeros(n)

    # ── ICT Features (20) ──────────────────────────────────────────
    try:
        from app.services.ml.features_ict import compute_ict_features
        ict = compute_ict_features(opens, highs, lows, closes, volumes, times, atr_values=atr_14)
        features.update(ict)
    except Exception:
        pass  # ICT features are optional — non-fatal

    # ── Institutional Features (18: VWAP, Volume Profile, S/D Zones, Wyckoff) ──
    try:
        from app.services.ml.features_institutional import compute_institutional_features
        inst = compute_institutional_features(opens, highs, lows, closes, volumes, times, atr_values=atr_14)
        features.update(inst)
    except Exception:
        pass  # Institutional features are optional — non-fatal

    # ── Divergence & Breakout Features (15) ──────────────────────────
    try:
        from app.services.ml.features_divergence import compute_divergence_features
        div = compute_divergence_features(opens, highs, lows, closes, volumes, times, atr_values=atr_14)
        features.update(div)
    except Exception:
        pass  # Divergence features are optional — non-fatal

    # ── Symbol-Specific Features (5) ──────────────────────────────
    hours = _extract_hours(times)

    # Session momentum: current HL range vs 24-bar rolling mean HL range (vectorised)
    hl_range_raw = highs - lows
    hl_roll_mean = pd.Series(hl_range_raw).rolling(24, min_periods=1).mean().shift(1).values
    features["session_momentum"] = np.where(hl_roll_mean > 0, hl_range_raw / np.maximum(hl_roll_mean, 1e-8), 1.0)

    # Daily range consumed: rolling 288-bar high-low range / ATR (vectorised)
    roll_high = pd.Series(highs).rolling(288, min_periods=1).max().values
    roll_low  = pd.Series(lows).rolling(288, min_periods=1).min().values
    daily_range = roll_high - roll_low
    features["daily_range_consumed"] = np.where(atr_14 > 0, daily_range / np.maximum(atr_14, 1e-8), 0)

    # Opening range position: close position within 12-bar (1h) rolling high-low range (vectorised)
    roll_high_12 = pd.Series(highs).rolling(12, min_periods=1).max().shift(1).values
    roll_low_12  = pd.Series(lows).rolling(12, min_periods=1).min().shift(1).values
    rng_12 = roll_high_12 - roll_low_12
    features["opening_range_position"] = np.where(rng_12 > 0, (closes - roll_low_12) / np.maximum(rng_12, 1e-8), 0.5)

    # Weekend flag (crypto-relevant: Saturday=5, Sunday=6)
    dow = _extract_dow(times)
    features["is_weekend"] = ((dow >= 5)).astype(float)

    # Pre-market gap: open vs previous close
    premarket_gap = np.zeros(n)
    premarket_gap[1:] = np.where(closes[:-1] > 0, (opens[1:] - closes[:-1]) / closes[:-1], 0)
    features["premarket_gap"] = premarket_gap

    # ── Tier-1 enhanced features ───────────────────────────────────
    try:
        from app.services.ml.features_tier1 import add_tier1_features
        add_tier1_features(
            features,
            opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes,
            times=times,
            h1_trend=features.get("h1_trend"),
            h4_trend=features.get("h4_trend"),
            d1_bias=features.get("d1_bias"),
            h1_closes=_h1_c_aligned,
            h4_closes=_h4_c_aligned,
            h1_atr=_h1_atr_aligned,
            h4_atr=_h4_atr_aligned,
            bars_per_year=252 * 288,
        )
    except Exception:
        pass  # tier1 failure is non-fatal; base features still present

    # ── Calendar / event features ──────────────────────────────────
    try:
        from app.services.ml.features_calendar import add_calendar_features
        add_calendar_features(features, times=times, symbol=symbol)
    except Exception:
        pass

    # ── External macro features ────────────────────────────────────
    if include_external:
        try:
            from app.services.ml.features_external import add_external_features
            add_external_features(features, times=times, symbol=symbol)
        except Exception:
            pass

    # ── Regime × feature interactions (highest expected alpha) ─────
    # HMM regime state is: 0 = bear/trending-down, 1 = ranging, 2 = bull/trending-up
    # These interaction features encode: "RSI signal is only reliable in ranging regime"
    # and "MACD trend signal is only reliable in trending regime".
    try:
        from app.services.ml.regime_detector import detect_regime
        regime = detect_regime(closes, volumes)  # returns int array 0/1/2 per bar
        # Encode regime as continuous [-1, 0, 1]
        regime_encoded = np.where(regime == 2, 1.0, np.where(regime == 0, -1.0, 0.0))
        features["regime_state"] = regime_encoded

        rsi_14 = features.get("rsi_14", np.zeros(n))
        macd_hist_arr = features.get("macd_hist", np.zeros(n))

        # RSI is most predictive in ranging regime (regime ≈ 0 encoded)
        is_ranging = (regime == 1).astype(float)
        features["regime_x_rsi"] = is_ranging * (rsi_14 - 50) / 50.0

        # MACD is most predictive in trending regime (regime != 1)
        is_trending = (regime != 1).astype(float)
        features["regime_x_macd_hist"] = is_trending * macd_hist_arr

        # HTF alignment × regime confirmation
        features["regime_x_htf_align"] = regime_encoded * features.get("htf_alignment", np.zeros(n))

    except Exception:
        for k in ["regime_state", "regime_x_rsi", "regime_x_macd_hist", "regime_x_htf_align"]:
            features[k] = np.zeros(n)

    # ── Cross-symbol correlation features ─────────────────────────
    if other_m5:
        try:
            from app.services.ml.features_correlation import compute_correlation_features
            corr_names, X_corr = compute_correlation_features(
                symbol=symbol,
                m5=m5_bars if isinstance(m5_bars, pd.DataFrame) else pd.DataFrame(m5_bars),
                other_m5=other_m5,
            )
            for name, col in zip(corr_names, X_corr.T):
                features[name] = col
        except Exception:
            pass  # correlation features are non-fatal

    # ── Assemble output ────────────────────────────────────────────
    feature_names = list(features.keys())
    X = np.column_stack([features[k] for k in feature_names])

    # Clean NaN/Inf
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    return feature_names, X


# ── Helper functions ───────────────────────────────────────────────────


def _returns(closes, period):
    r = np.zeros(len(closes))
    r[period:] = (closes[period:] - closes[:-period]) / np.where(closes[:-period] != 0, closes[:-period], 1)
    return r


def _log_returns(closes):
    r = np.zeros(len(closes))
    r[1:] = np.log(closes[1:] / np.where(closes[:-1] > 0, closes[:-1], 1))
    return r


def _slope(values, period=5):
    """
    Normalised rolling linear-regression slope over `period` bars.
    Vectorised: uses the closed-form OLS formula to avoid polyfit per bar.
    slope = (Σ(x*y) - n*mean_x*mean_y) / (Σ(x²) - n*mean_x²)
    normalised by |last value| so it's scale-invariant.
    """
    n = len(values)
    result = np.zeros(n)
    x     = np.arange(period, dtype=float)
    mean_x = np.mean(x)
    denom_x = np.sum((x - mean_x) ** 2)  # constant for fixed period

    if denom_x == 0:
        return result

    s = pd.Series(values)
    # Build cross-products column-by-column and sum (each column = lag 0..period-1)
    # Σ(x_i * y_{t-period+1+i}) for i in 0..period-1
    xy_sum = np.zeros(n)
    y_sum  = np.zeros(n)
    for i, xi in enumerate(x):
        yi = s.shift(period - 1 - i).values
        xy_sum += xi * np.nan_to_num(yi)
        y_sum  += np.nan_to_num(yi)

    slope_raw = (xy_sum - period * mean_x * (y_sum / period)) / denom_x

    # Normalise by |last value in window|
    last_val = s.values
    denom = np.where(np.abs(last_val) > 0, np.abs(last_val), 1.0)
    result = slope_raw / denom

    # Zero out warmup
    result[:period - 1] = 0.0
    return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def _crossover_signal(fast, slow):
    """1 when fast crosses above slow, -1 below, 0 otherwise. Vectorised."""
    fast = np.asarray(fast, dtype=float)
    slow = np.asarray(slow, dtype=float)
    prev_fast = np.concatenate([[fast[0]], fast[:-1]])
    prev_slow = np.concatenate([[slow[0]], slow[:-1]])
    cross_up   = (fast > slow) & (prev_fast <= prev_slow)
    cross_down = (fast < slow) & (prev_fast >= prev_slow)
    signal = np.where(cross_up, 1.0, np.where(cross_down, -1.0, 0.0))
    # Mask NaN positions
    nan_mask = np.isnan(fast) | np.isnan(slow) | np.isnan(prev_fast) | np.isnan(prev_slow)
    signal[nan_mask] = 0.0
    return signal


def _swing_levels(highs, lows, window=10):
    """Rolling swing high and swing low."""
    n = len(highs)
    swing_hi = np.full(n, np.nan)
    swing_lo = np.full(n, np.nan)
    for i in range(window, n):
        swing_hi[i] = np.max(highs[i - window : i + 1])
        swing_lo[i] = np.min(lows[i - window : i + 1])
    # Forward fill
    for i in range(1, n):
        if np.isnan(swing_hi[i]):
            swing_hi[i] = swing_hi[i-1] if not np.isnan(swing_hi[i-1]) else highs[i]
        if np.isnan(swing_lo[i]):
            swing_lo[i] = swing_lo[i-1] if not np.isnan(swing_lo[i-1]) else lows[i]
    return swing_hi, swing_lo


def _sr_proximity(closes, highs, lows, window=50):
    """How close price is to a support/resistance cluster (0-1 scale)."""
    n = len(closes)
    prox = np.zeros(n)
    for i in range(window, n):
        levels = np.concatenate([highs[i - window : i], lows[i - window : i]])
        if len(levels) == 0:
            continue
        dists = np.abs(levels - closes[i])
        min_dist = np.min(dists)
        price_range = np.max(highs[i - window : i]) - np.min(lows[i - window : i])
        prox[i] = 1.0 - (min_dist / price_range) if price_range > 0 else 0.0
    return prox


def _break_of_structure(highs, lows, window=10):
    """Detect break of structure: 1 if new high broken, -1 if new low, 0 otherwise."""
    n = len(highs)
    bos = np.zeros(n)
    for i in range(window + 1, n):
        prev_high = np.max(highs[i - window - 1 : i - 1])
        prev_low = np.min(lows[i - window - 1 : i - 1])
        if highs[i] > prev_high:
            bos[i] = 1.0
        elif lows[i] < prev_low:
            bos[i] = -1.0
    return bos


def _higher_high_lower_low(highs, lows, window=5):
    """1 if making higher highs, -1 if lower lows, 0 if neutral."""
    n = len(highs)
    result = np.zeros(n)
    for i in range(window * 2, n):
        recent_high = np.max(highs[i - window : i + 1])
        prev_high = np.max(highs[i - window * 2 : i - window])
        recent_low = np.min(lows[i - window : i + 1])
        prev_low = np.min(lows[i - window * 2 : i - window])
        if recent_high > prev_high and recent_low > prev_low:
            result[i] = 1.0
        elif recent_high < prev_high and recent_low < prev_low:
            result[i] = -1.0
    return result


def _trend_strength(closes, period):
    """
    Linear regression slope normalised by price. Vectorised via _slope.
    Reuses the same closed-form rolling OLS implementation.
    """
    return _slope(closes, period)


def _extract_hours(times):
    """Extract hour-of-day from unix timestamps or numeric array."""
    try:
        hours = np.array([datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in times])
    except (ValueError, TypeError, OSError):
        hours = np.zeros(len(times))
    return hours.astype(float)


def _extract_dow(times):
    """Extract day-of-week from unix timestamps."""
    try:
        dow = np.array([datetime.fromtimestamp(int(t), tz=timezone.utc).weekday() for t in times])
    except (ValueError, TypeError, OSError):
        dow = np.zeros(len(times))
    return dow.astype(float)


def _align_htf(htf_values, target_len, ratio):
    """
    Align higher-timeframe values to M5 length by repeating each value.
    Each HTF bar maps to `ratio` M5 bars.
    """
    result = np.zeros(target_len)
    for i in range(len(htf_values)):
        start = i * ratio
        end = min((i + 1) * ratio, target_len)
        if start < target_len:
            result[start:end] = htf_values[i]
    return result
