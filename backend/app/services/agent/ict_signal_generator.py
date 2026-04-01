"""
Rule-based ICT signal generator for Expert/Swing agent.

Generates candidate BUY/SELL signals using hard-coded ICT/SMC rules.
These signals are then filtered by an ML model (meta-labeler) before execution.

Rules evaluated on H4 bars with D1 context:
  1. Liquidity sweep (wick beyond swing H/L, close back inside)
  2. BOS/CHOCH confirms trend direction
  3. Order Block in OTE zone (62-79% fib retracement)
  4. D1 bias alignment (price vs D1 EMA50)
  5. Williams stretch breakout
  6. Donchian channel breakout with squeeze release
  7. Premium/Discount zone alignment

Signal = BUY/SELL when 3+ rules align. Strength = count of aligned rules (0-7).
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


def _swing_highs_lows(highs, lows, window=5):
    """Detect swing highs and lows."""
    n = len(highs)
    sh = np.zeros(n)
    sl = np.zeros(n)
    for i in range(window, n - window):
        if highs[i] == np.max(highs[i-window:i+window+1]):
            sh[i] = highs[i]
        if lows[i] == np.min(lows[i-window:i+window+1]):
            sl[i] = lows[i]
    return sh, sl


def _ema(values, period):
    return pd.Series(values).ewm(span=period, min_periods=period).mean().values


def generate_swing_signals(
    h4_bars: pd.DataFrame,
    d1_bars: pd.DataFrame = None,
    min_rules: int = 3,
    swing_window: int = 5,
) -> pd.DataFrame:
    """
    Generate rule-based swing trading signals from H4 + D1 data.

    Returns DataFrame with columns:
        signal: +1 (BUY), -1 (SELL), 0 (no signal)
        strength: number of aligned rules (0-7)
        rules: string of which rules triggered
        tp_price: suggested take-profit
        sl_price: suggested stop-loss
    """
    opens = h4_bars["open"].values.astype(float)
    highs = h4_bars["high"].values.astype(float)
    lows = h4_bars["low"].values.astype(float)
    closes = h4_bars["close"].values.astype(float)
    n = len(closes)

    atr14 = _atr(highs, lows, closes, 14)
    sh, sl_arr = _swing_highs_lows(highs, lows, swing_window)

    # Pre-compute indicators
    ema21 = _ema(closes, 21)
    ema50 = _ema(closes, 50)

    # Donchian
    h_s = pd.Series(highs)
    l_s = pd.Series(lows)
    donch_high_20 = h_s.rolling(20).max().values
    donch_low_20 = l_s.rolling(20).min().values
    donch_width = donch_high_20 - donch_low_20
    donch_width_pctile = pd.Series(donch_width).rolling(50, min_periods=20).rank(pct=True).values

    # Williams stretch
    stretch_up = pd.Series(np.abs(opens - lows)).rolling(3, min_periods=1).mean().values
    stretch_down = pd.Series(np.abs(highs - opens)).rolling(3, min_periods=1).mean().values

    # D1 bias
    d1_bias = np.zeros(n)
    if d1_bars is not None and len(d1_bars) > 50:
        d1_c = d1_bars["close"].values.astype(float)
        d1_ema50 = _ema(d1_c, 50)
        d1_raw = np.where(d1_c > d1_ema50, 1.0, -1.0)
        # Align D1 to H4 (6 H4 bars per D1 approximately)
        ratio = max(1, n // len(d1_c))
        d1_aligned = np.repeat(d1_raw, ratio)[:n]
        if len(d1_aligned) < n:
            d1_aligned = np.concatenate([d1_aligned, np.full(n - len(d1_aligned), d1_aligned[-1])])
        d1_bias = d1_aligned

    # Output arrays
    signals = np.zeros(n, dtype=np.int8)
    strengths = np.zeros(n, dtype=np.int8)
    rule_strings = [""] * n
    tp_prices = np.full(n, np.nan)
    sl_prices = np.full(n, np.nan)

    # Track last swing high/low for each bar
    last_sh = 0.0
    last_sl_price = 0.0

    for i in range(max(swing_window * 2, 21), n):
        if atr14[i] <= 0:
            continue

        bull_rules = []
        bear_rules = []

        # ── Rule 1: Liquidity Sweep ──────────────────────────────
        # Buyside sweep: wick above last swing high, close back below
        if last_sh > 0 and highs[i] > last_sh and closes[i] < last_sh:
            bear_rules.append("liq_sweep_high")
        # Sellside sweep: wick below last swing low, close back above
        if last_sl_price > 0 and lows[i] < last_sl_price and closes[i] > last_sl_price:
            bull_rules.append("liq_sweep_low")

        # Update last swing levels
        if sh[i] > 0:
            last_sh = sh[i]
        if sl_arr[i] > 0:
            last_sl_price = sl_arr[i]

        # ── Rule 2: BOS / Trend Direction ────────────────────────
        # Price above EMA21 = bullish structure
        if closes[i] > ema21[i] and closes[i-1] > ema21[i-1]:
            bull_rules.append("bos_bull")
        if closes[i] < ema21[i] and closes[i-1] < ema21[i-1]:
            bear_rules.append("bos_bear")

        # ── Rule 3: OB in OTE Zone (simplified) ─────────────────
        # Bullish: price pulled back 62-79% of last swing up and is at support
        if i >= 20:
            recent_high = np.max(highs[i-20:i])
            recent_low = np.min(lows[i-20:i])
            swing_range = recent_high - recent_low
            if swing_range > 0:
                retracement = (recent_high - closes[i]) / swing_range
                if 0.62 <= retracement <= 0.79 and closes[i] > closes[i-1]:
                    bull_rules.append("ob_in_ote")
                elif 0.62 <= (closes[i] - recent_low) / swing_range <= 0.79 and closes[i] < closes[i-1]:
                    bear_rules.append("ob_in_ote")

        # ── Rule 4: D1 Bias Alignment ───────────────────────────
        if d1_bias[i] > 0:
            bull_rules.append("d1_bull")
        elif d1_bias[i] < 0:
            bear_rules.append("d1_bear")

        # ── Rule 5: Williams Stretch Breakout ────────────────────
        if closes[i] > opens[i] + stretch_up[i]:
            bull_rules.append("stretch_up")
        if closes[i] < opens[i] - stretch_down[i]:
            bear_rules.append("stretch_down")

        # ── Rule 6: Donchian Breakout with Squeeze Release ──────
        # Breakout from compression = high-conviction
        was_squeezed = donch_width_pctile[max(0, i-3):i].min() < 0.25 if i >= 3 else False
        if closes[i] > donch_high_20[i-1] and was_squeezed:
            bull_rules.append("donch_squeeze_break")
        if closes[i] < donch_low_20[i-1] and was_squeezed:
            bear_rules.append("donch_squeeze_break")

        # ── Rule 7: Premium/Discount Zone ────────────────────────
        if i >= 20:
            range_high = np.max(highs[i-20:i+1])
            range_low = np.min(lows[i-20:i+1])
            mid = (range_high + range_low) / 2
            if closes[i] < mid:  # discount = buy zone
                bull_rules.append("discount")
            else:  # premium = sell zone
                bear_rules.append("premium")

        # ── Generate Signal ──────────────────────────────────────
        n_bull = len(bull_rules)
        n_bear = len(bear_rules)

        if n_bull >= min_rules and n_bull > n_bear:
            signals[i] = 1
            strengths[i] = n_bull
            rule_strings[i] = "+".join(bull_rules)
            tp_prices[i] = closes[i] + atr14[i] * 2.0
            sl_prices[i] = closes[i] - atr14[i] * 1.0
        elif n_bear >= min_rules and n_bear > n_bull:
            signals[i] = -1
            strengths[i] = n_bear
            rule_strings[i] = "+".join(bear_rules)
            tp_prices[i] = closes[i] - atr14[i] * 2.0
            sl_prices[i] = closes[i] + atr14[i] * 1.0

    return pd.DataFrame({
        "signal": signals,
        "strength": strengths,
        "rules": rule_strings,
        "tp_price": tp_prices,
        "sl_price": sl_prices,
    }, index=h4_bars.index)
