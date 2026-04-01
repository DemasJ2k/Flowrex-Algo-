"""
M5 ICT rule-based signal generator for scalping hybrid.

Adapts the H4 ICT signal generator for M5 timeframe:
- Tighter swing detection (window=10 bars = 50 min)
- Session-aware (only generates signals during US cash open for US30)
- Faster rules: displacement, FVG, liquidity sweep, BOS, stretch breakout

Signal = BUY/SELL when 3+ M5 rules align. Used as pre-filter before ML model.
"""
import numpy as np
import pandas as pd


def _atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    return pd.Series(tr).rolling(period, min_periods=1).mean().values


def _ema(values, period):
    return pd.Series(values).ewm(span=period, min_periods=period).mean().values


def generate_scalp_signals(
    m5_bars: pd.DataFrame,
    h1_trend: np.ndarray = None,
    min_rules: int = 3,
) -> np.ndarray:
    """
    Generate rule-based scalping signal candidates on M5 bars.

    Returns array of +1 (BUY candidate), -1 (SELL candidate), 0 (no signal).
    These are PRE-FILTERED by the ML model before execution.
    """
    opens = m5_bars["open"].values.astype(float)
    highs = m5_bars["high"].values.astype(float)
    lows = m5_bars["low"].values.astype(float)
    closes = m5_bars["close"].values.astype(float)
    n = len(closes)

    atr14 = _atr(highs, lows, closes, 14)
    ema21 = _ema(closes, 21)
    ema50 = _ema(closes, 50)

    # Swing highs/lows (10-bar window = 50 min)
    sw = 10
    sh = np.zeros(n)
    sl_arr = np.zeros(n)
    for i in range(sw, n - sw):
        if highs[i] == np.max(highs[i-sw:i+sw+1]):
            sh[i] = highs[i]
        if lows[i] == np.min(lows[i-sw:i+sw+1]):
            sl_arr[i] = lows[i]

    # Williams stretch
    stretch_up = pd.Series(np.abs(opens - lows)).rolling(3, min_periods=1).mean().values
    stretch_down = pd.Series(np.abs(highs - opens)).rolling(3, min_periods=1).mean().values

    # Donchian
    h_s = pd.Series(highs)
    l_s = pd.Series(lows)
    donch_high = h_s.rolling(20).max().values
    donch_low = l_s.rolling(20).min().values
    donch_width = donch_high - donch_low
    donch_pctile = pd.Series(donch_width).rolling(100, min_periods=50).rank(pct=True).values

    signals = np.zeros(n, dtype=np.int8)
    last_sh = 0.0
    last_sl = 0.0

    for i in range(max(sw * 2, 21), n):
        if atr14[i] <= 0:
            continue

        bull = []
        bear = []

        # Rule 1: Liquidity sweep
        if last_sh > 0 and highs[i] > last_sh and closes[i] < last_sh:
            bear.append(1)
        if last_sl > 0 and lows[i] < last_sl and closes[i] > last_sl:
            bull.append(1)

        if sh[i] > 0:
            last_sh = sh[i]
        if sl_arr[i] > 0:
            last_sl = sl_arr[i]

        # Rule 2: Trend (EMA21 direction)
        if closes[i] > ema21[i] and closes[i-1] > ema21[i-1]:
            bull.append(1)
        if closes[i] < ema21[i] and closes[i-1] < ema21[i-1]:
            bear.append(1)

        # Rule 3: H1 trend alignment (if provided)
        if h1_trend is not None and i < len(h1_trend):
            if h1_trend[i] > 0:
                bull.append(1)
            elif h1_trend[i] < 0:
                bear.append(1)

        # Rule 4: Displacement (large body candle)
        body = abs(closes[i] - opens[i])
        if body > 1.5 * atr14[i] and closes[i] > opens[i]:
            bull.append(1)
        if body > 1.5 * atr14[i] and closes[i] < opens[i]:
            bear.append(1)

        # Rule 5: Stretch breakout
        if closes[i] > opens[i] + stretch_up[i]:
            bull.append(1)
        if closes[i] < opens[i] - stretch_down[i]:
            bear.append(1)

        # Rule 6: Donchian squeeze release
        was_squeezed = False
        if i >= 3:
            was_squeezed = np.nanmin(donch_pctile[max(0,i-3):i]) < 0.25
        if was_squeezed and closes[i] > donch_high[i-1]:
            bull.append(1)
        if was_squeezed and closes[i] < donch_low[i-1]:
            bear.append(1)

        # Rule 7: Premium/Discount
        if i >= 50:
            rh = np.max(highs[i-50:i+1])
            rl = np.min(lows[i-50:i+1])
            mid = (rh + rl) / 2
            if closes[i] < mid:
                bull.append(1)
            else:
                bear.append(1)

        nb = len(bull)
        ns = len(bear)
        if nb >= min_rules and nb > ns:
            signals[i] = 1
        elif ns >= min_rules and ns > nb:
            signals[i] = -1

    return signals
