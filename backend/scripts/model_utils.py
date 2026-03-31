"""
Shared utilities for training pipelines: labeling, splitting, grading, DB recording.
"""
import os
import sys
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def create_labels(
    closes: np.ndarray,
    atr_values: np.ndarray = None,
    forward_bars: int = 10,
    atr_mult: float = 1.0,
    config: dict = None,
) -> np.ndarray:
    """
    Create 3-class target labels: 0=sell, 1=hold, 2=buy.

    Label logic (path-based, vectorised):
      BUY  if max(closes[i+1..i+N]) - closes[i] > ATR[i]*mult  AND  up_move > down_move
      SELL if closes[i] - min(closes[i+1..i+N]) > ATR[i]*mult  AND  down_move > up_move
      HOLD otherwise

    NOTE: Labels are bidirectional (no trend filter here). The trend filter belongs in the
    execution layer (compute_backtest_metrics), not in label creation. Applying a trend
    filter to training labels creates directional bias that is harmful in fold transitions
    where training and test periods have opposite trend directions.

    The model learns WHEN conditions are good for each direction from the feature set
    (EMA crossovers, htf_alignment, price_above_ema200, SMC features, etc.).

    Fully vectorised using pandas rolling on a reversed series (no Python loop).
    """
    import pandas as pd

    if config:
        forward_bars = config.get("label_forward_bars", forward_bars)
        atr_mult     = config.get("label_atr_mult", atr_mult)

    n = len(closes)
    labels = np.ones(n, dtype=np.int8)   # default: hold

    # ── Vectorised future max/min over [i+1 .. i+forward_bars] ───────────
    # Trick: reverse series, rolling max/min on window=forward_bars, reverse back,
    # then shift(-1) gives max/min of next N bars from each position.
    s   = pd.Series(closes.astype(np.float64))
    rev = s.iloc[::-1].reset_index(drop=True)

    future_max = (rev.rolling(forward_bars, min_periods=forward_bars)
                     .max()
                     .iloc[::-1]
                     .reset_index(drop=True)
                     .shift(-1)
                     .values)

    future_min = (rev.rolling(forward_bars, min_periods=forward_bars)
                     .min()
                     .iloc[::-1]
                     .reset_index(drop=True)
                     .shift(-1)
                     .values)

    valid = ~(np.isnan(future_max) | np.isnan(future_min))

    if atr_values is not None:
        atr_safe  = np.where(atr_values > 0, atr_values, closes * 0.002)
        thresh    = atr_safe * atr_mult
        up_move   = future_max - closes
        down_move = closes - future_min
    else:
        thresh    = closes * 0.002   # 0.2% fallback
        up_move   = (future_max - closes) / np.where(closes > 0, closes, 1.0)
        down_move = (closes - future_min) / np.where(closes > 0, closes, 1.0)

    buy_mask  = valid & (up_move > thresh) & (up_move > down_move)
    sell_mask = valid & (down_move > thresh) & (down_move > up_move)

    labels[buy_mask]  = 2
    labels[sell_mask] = 0
    return labels


def walk_forward_split(X: np.ndarray, y: np.ndarray, train_ratio: float = 0.8):
    """Walk-forward split: train on older data, test on newer."""
    split_idx = int(len(X) * train_ratio)
    # Skip first 200 bars (warmup for indicators)
    warmup = 200
    return X[warmup:split_idx], y[warmup:split_idx], X[split_idx:], y[split_idx:]


def compute_backtest_metrics(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    closes_test: np.ndarray,
    opens_test: np.ndarray  = None,
    highs_test: np.ndarray  = None,
    lows_test: np.ndarray   = None,
    atr_test: np.ndarray    = None,
    cost_bps: float         = 5.0,
    slippage_bps: float     = 1.0,
    bars_per_day: int       = 288,
    hold_bars: int          = 10,
    tp_atr_mult: float      = 1.0,
    sl_atr_mult: float      = 0.8,
    trend_filter: bool      = True,
    trend_window: int       = None,
) -> dict:
    """
    Realistic trade simulation with TP/SL execution and open-bar fills.

    Execution model:
      - Signal on bar i -> fill at open[i+1] (next-bar fill; avoids close-to-close bias)
      - TP = entry +/- ATR[i] x tp_atr_mult  (exits as soon as intra-bar high/low crosses)
      - SL = entry -/+ ATR[i] x sl_atr_mult  (pessimistic: SL assumed hit before TP if both cross)
      - Timeout: if neither TP nor SL hit within hold_bars, exit at close[i+hold_bars]
      - Cost = (cost_bps + slippage_bps) / 10_000 per trade (one-way slippage + round-trip spread)
      - Sharpe computed on DAILY aggregated returns x sqrt(252)

    Trend filter (execution overlay, NOT label manipulation):
      Applied at prediction time to filter out counter-trend signals:
      - Bull regime (price > EMA[trend_window]): skip SELL (0) predictions
      - Bear regime (price < EMA[trend_window]): skip BUY (2) predictions
      This avoids shorting bull markets or going long in bear markets.
      Default trend_window = 20 trading days (bars_per_day * 20).

    Falls back to close-fill / no-TP-SL when OHLC/ATR not supplied (backward-compatible).

    Args:
        opens_test:    open prices (fill at open[i+1])
        highs_test:    bar highs  (for intra-bar TP detection)
        lows_test:     bar lows   (for intra-bar SL detection)
        atr_test:      ATR values (for TP/SL sizing)
        cost_bps:      round-trip spread in bps
        slippage_bps:  one-way slippage in bps (added to cost)
        bars_per_day:  for daily Sharpe aggregation
        hold_bars:     max bars to hold if TP/SL not hit
        tp_atr_mult:   TP at entry +/- ATR x mult
        sl_atr_mult:   SL at entry -/+ ATR x mult
        trend_filter:  If True, apply EMA regime filter on predictions (default True)
        trend_window:  EMA window for trend detection (default bars_per_day * 20)
    """
    import pandas as pd
    from sklearn.metrics import accuracy_score

    preds    = model.predict(X_test)
    accuracy = accuracy_score(y_test, preds)

    # ── Trend filter: execution-time overlay (not label manipulation) ─────
    # Filter signals based on CURRENT trend direction in the test period.
    # In a confirmed bull trend: skip SHORT (SELL=0) signals.
    # In a confirmed bear trend: skip LONG (BUY=2) signals.
    # This is applied AFTER model prediction, so training is unaffected.
    if trend_filter and len(closes_test) > (bars_per_day * 5):
        tw = trend_window if trend_window else bars_per_day * 20
        tw = min(tw, len(closes_test) // 3)  # don't exceed 1/3 of test length
        close_ser = pd.Series(closes_test.astype(np.float64))
        ema_trend = close_ser.ewm(span=tw, min_periods=tw // 4).mean().values
        bull_regime = closes_test > ema_trend  # price above EMA = bullish
        bear_regime = closes_test < ema_trend  # price below EMA = bearish
        # In bull regime, suppress SELL (0) -> convert to HOLD (1)
        preds = np.where(bull_regime & (preds == 0), np.int64(1), preds)
        # In bear regime, suppress BUY (2) -> convert to HOLD (1)
        preds = np.where(bear_regime & (preds == 2), np.int64(1), preds)

    # ── ATR regime gate: skip trading in low-volatility environments ────
    # When current ATR is below the 25th percentile of rolling 100-bar ATR,
    # the edge compresses and costs eat profits. Convert signals to HOLD.
    if atr_test is not None and len(atr_test) > 100:
        atr_ser = pd.Series(atr_test.astype(np.float64))
        atr_pctile = atr_ser.rolling(100, min_periods=50).rank(pct=True).values
        low_vol = atr_pctile < 0.25
        preds = np.where(low_vol & (preds != 1), np.int64(1), preds)
    total_cost = (cost_bps + slippage_bps) / 10_000   # round-trip + slippage

    n = len(closes_test)
    if n < hold_bars + 2:
        return {"accuracy": accuracy, "sharpe": 0.0, "win_rate": 0.0,
                "max_drawdown": 0.0, "total_return": 0.0, "profit_factor": 0.0,
                "total_trades": 0}

    use_ohlc = (opens_test is not None and highs_test is not None
                and lows_test is not None and atr_test is not None)

    equity_curve  = np.zeros(n)
    trade_returns = []
    i = 0

    while i < n - hold_bars - 1:
        sig = preds[i]
        if sig not in (0, 2):
            i += 1
            continue

        entry_bar = i   # remember where this signal originated

        # ── Entry: fill at open of next bar ──────────────────────────────
        if use_ohlc and opens_test[i + 1] > 0:
            entry = opens_test[i + 1]
        else:
            entry = closes_test[i]   # fallback: close fill

        if entry <= 0:
            i += 1
            continue

        atr = (atr_test[i] if (use_ohlc and atr_test[i] > 0) else entry * 0.001)
        is_long = (sig == 2)

        tp_price = entry + atr * tp_atr_mult if is_long else entry - atr * tp_atr_mult
        sl_price = entry - atr * sl_atr_mult if is_long else entry + atr * sl_atr_mult

        exit_ret  = None
        exit_bar  = i + hold_bars

        # ── Scan forward bars for TP/SL ───────────────────────────────────
        if use_ohlc:
            for j in range(i + 1, min(i + hold_bars + 1, n)):
                hi, lo = highs_test[j], lows_test[j]
                if is_long:
                    sl_hit = lo <= sl_price
                    tp_hit = hi >= tp_price
                else:
                    sl_hit = hi >= sl_price
                    tp_hit = lo <= tp_price

                if sl_hit and tp_hit:
                    # Both hit same bar — pessimistic: SL first
                    exit_ret = -(atr * sl_atr_mult / entry) - total_cost
                    exit_bar = j
                    break
                elif sl_hit:
                    exit_ret = -(atr * sl_atr_mult / entry) - total_cost
                    exit_bar = j
                    break
                elif tp_hit:
                    exit_ret = (atr * tp_atr_mult / entry) - total_cost
                    exit_bar = j
                    break

        # ── Timeout exit ─────────────────────────────────────────────────
        if exit_ret is None:
            c_exit   = closes_test[min(i + hold_bars, n - 1)]
            raw      = (c_exit - entry) / entry
            exit_ret = (raw if is_long else -raw) - total_cost
            exit_bar = min(i + hold_bars, n - 1)

        if exit_bar < n:
            equity_curve[exit_bar] += exit_ret
        trade_returns.append(exit_ret)
        # Enforce full cooldown: always wait hold_bars from entry_bar before next trade
        # (prevents over-trading when TP/SL hits within 1-2 bars)
        i = entry_bar + hold_bars

    total_trades = len(trade_returns)
    if total_trades == 0:
        return {"accuracy": accuracy, "sharpe": 0.0, "win_rate": 0.0,
                "max_drawdown": 0.0, "total_return": 0.0, "profit_factor": 0.0,
                "total_trades": 0}

    trade_returns = np.array(trade_returns)
    wins   = trade_returns[trade_returns > 0]
    losses = trade_returns[trade_returns < 0]

    # ── Sharpe on DAILY aggregated returns × sqrt(252) ───────────────────
    n_days  = max(1, n // bars_per_day)
    trimmed = equity_curve[: n_days * bars_per_day]
    daily   = trimmed.reshape(n_days, bars_per_day).sum(axis=1)
    sharpe  = float(daily.mean() / daily.std() * np.sqrt(252)) if (n_days > 1 and daily.std() > 0) else 0.0

    # ── Equity curve drawdown ─────────────────────────────────────────────
    cum    = np.cumsum(equity_curve)
    peak   = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum) * 100)

    total_return  = float(cum[-1] * 100)
    win_rate      = len(wins) / total_trades * 100
    gross_profit  = float(wins.sum())  if len(wins)   > 0 else 0.0
    gross_loss    = float(abs(losses.sum())) if len(losses) > 0 else 0.0
    profit_factor = (gross_profit / gross_loss if gross_loss > 0
                     else (float("inf") if gross_profit > 0 else 0.0))

    return {
        "accuracy":      round(accuracy, 4),
        "sharpe":        round(sharpe, 4),
        "win_rate":      round(win_rate, 2),
        "max_drawdown":  round(max_dd, 2),
        "total_return":  round(total_return, 4),
        "profit_factor": round(profit_factor, 4),
        "total_trades":  total_trades,
    }


def grade_model(metrics: dict) -> str:
    """
    Assign letter grade based on ARCHITECTURE.md grading system.
    A: Sharpe > 1.5, Win Rate > 55%, Max DD < 15%
    B: Sharpe > 1.0, Win Rate > 50%, Max DD < 20%
    C: Sharpe > 0.5, Win Rate > 45%, Max DD < 25%
    D: Sharpe > 0, positive total return
    F: Negative total return
    """
    sharpe = metrics.get("sharpe", 0)
    win_rate = metrics.get("win_rate", 0)
    max_dd = metrics.get("max_drawdown", 100)
    total_return = metrics.get("total_return", 0)

    if sharpe > 1.5 and win_rate > 55 and max_dd < 15:
        return "A"
    elif sharpe > 1.0 and win_rate > 50 and max_dd < 20:
        return "B"
    elif sharpe > 0.5 and win_rate > 45 and max_dd < 25:
        return "C"
    elif sharpe > 0 and total_return > 0:
        return "D"
    else:
        return "F"


def purged_walk_forward_splits(
    n: int,
    n_folds: int = 3,
    embargo_bars: int = 50,
    warmup_bars: int = 200,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """
    Purged walk-forward cross-validation (de Prado, 2018).

    Splits the usable series (after warmup) into `n_folds` test folds of
    equal size.  For each fold the training set is everything BEFORE the
    fold minus an embargo gap, preventing any bar in the embargo window
    from contaminating the training set with lookahead labels.

    Returns list of (train_indices, test_indices) tuples.

    Visual layout (3 folds, embargo E):
      [warmup | ...train_0... | E | test_0 | ...train_1... | E | test_1 | ...]
    """
    usable = n - warmup_bars
    if usable < (n_folds * 100 + embargo_bars):
        # Not enough bars — fall back to single 80/20 split
        split = int(n * 0.8)
        return [(np.arange(warmup_bars, split), np.arange(split, n))]

    fold_size = usable // n_folds
    splits = []

    for fold in range(n_folds):
        test_start = warmup_bars + fold * fold_size
        test_end   = test_start + fold_size if fold < n_folds - 1 else n

        # Train = [warmup .. test_start - embargo)
        train_end   = test_start - embargo_bars
        if train_end <= warmup_bars + 50:
            continue  # not enough training data for this fold

        train_idx = np.arange(warmup_bars, train_end)
        test_idx  = np.arange(test_start, test_end)

        splits.append((train_idx, test_idx))

    return splits if splits else [(np.arange(warmup_bars, int(n * 0.8)), np.arange(int(n * 0.8), n))]


def shap_feature_filter(
    model,
    X: np.ndarray,
    feature_names: list[str],
    threshold: float = 0.001,
    sample_size: int = 2000,
) -> list[str]:
    """
    Return a list of feature names to KEEP using SHAP mean absolute importance.
    Features with mean |SHAP| < threshold × max_importance are dropped.

    Falls back to keeping all features if shap is unavailable.
    """
    try:
        import shap
        sample = X[:sample_size] if len(X) > sample_size else X
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(sample)

        # For multi-class: shap_values may be list (old API) or 3D array (new API)
        if isinstance(shap_values, list):
            mean_abs = np.mean([np.abs(sv).mean(axis=0) for sv in shap_values], axis=0)
        elif np.asarray(shap_values).ndim == 3:
            # New SHAP API: (n_samples, n_features, n_classes) → mean over samples & classes
            mean_abs = np.abs(shap_values).mean(axis=(0, 2))
        else:
            mean_abs = np.abs(shap_values).mean(axis=0)

        max_imp = mean_abs.max() if mean_abs.max() > 0 else 1.0
        keep_mask = mean_abs >= (threshold * max_imp)
        kept = [fn for fn, keep in zip(feature_names, keep_mask) if keep]

        dropped = len(feature_names) - len(kept)
        if dropped > 0:
            print(f"  [SHAP] Dropped {dropped} near-zero features (kept {len(kept)})")
        return kept

    except ImportError:
        print("  [WARN] shap not installed — skipping feature filter")
        return feature_names
    except Exception as e:
        print(f"  [WARN] SHAP filter failed: {e}")
        return feature_names


def check_train_test_divergence(
    train_metrics: dict,
    test_metrics: dict,
    threshold: float = 0.80,
) -> dict:
    """
    Detect overfitting by comparing train vs test Sharpe and win_rate.

    Returns a dict with:
      overfit:     True if any metric ratio < threshold (test/train < 80%)
      sharpe_ratio:   test_sharpe / train_sharpe
      wr_ratio:       test_wr / train_wr
      warnings:    list of human-readable warning strings
    """
    warnings = []
    sharpe_ratio = 1.0
    wr_ratio = 1.0

    train_sharpe = train_metrics.get("sharpe", 0)
    test_sharpe  = test_metrics.get("sharpe", 0)
    train_wr     = train_metrics.get("win_rate", 50)
    test_wr      = test_metrics.get("win_rate", 50)

    if train_sharpe > 0.1:
        sharpe_ratio = test_sharpe / train_sharpe
        if sharpe_ratio < threshold:
            warnings.append(
                f"OVERFIT: test Sharpe ({test_sharpe:.2f}) is only "
                f"{sharpe_ratio*100:.0f}% of train Sharpe ({train_sharpe:.2f})"
            )

    if train_wr > 0.1:
        wr_ratio = test_wr / train_wr
        if wr_ratio < threshold:
            warnings.append(
                f"OVERFIT: test WR ({test_wr:.1f}%) is only "
                f"{wr_ratio*100:.0f}% of train WR ({train_wr:.1f}%)"
            )

    overfit = len(warnings) > 0
    if overfit:
        for w in warnings:
            print(f"  [WARN] {w}")
    else:
        print(f"  [OK] Divergence check passed: Sharpe ratio={sharpe_ratio:.2f}, WR ratio={wr_ratio:.2f}")

    return {
        "overfit": overfit,
        "sharpe_ratio": round(sharpe_ratio, 3),
        "wr_ratio": round(wr_ratio, 3),
        "warnings": warnings,
    }


def check_min_signals(y_test: np.ndarray, min_signals: int = 75) -> bool:
    """
    Verify OOS test set has enough non-hold signals for meaningful evaluation.
    Returns True if sufficient, prints warning otherwise.
    """
    buy_signals  = int(np.sum(y_test == 2))
    sell_signals = int(np.sum(y_test == 0))
    total = buy_signals + sell_signals

    if total < min_signals:
        print(f"  [WARN] Only {total} non-hold signals in OOS ({buy_signals} buy, "
              f"{sell_signals} sell) — grade may be unreliable (min={min_signals})")
        return False
    print(f"  [OK] OOS signals: {total} ({buy_signals} buy, {sell_signals} sell)")
    return True


def save_model_record(symbol: str, timeframe: str, model_type: str, pipeline: str,
                      file_path: str, grade: str, metrics: dict):
    """Save model metadata to DB (if available)."""
    try:
        from app.core.database import SessionLocal
        from app.models.ml import MLModel

        db = SessionLocal()
        # Check for existing record
        existing = db.query(MLModel).filter(
            MLModel.symbol == symbol,
            MLModel.model_type == model_type,
            MLModel.pipeline == pipeline,
        ).first()

        if existing:
            existing.file_path = file_path
            existing.grade = grade
            existing.metrics = metrics
            existing.trained_at = datetime.now(timezone.utc)
        else:
            record = MLModel(
                created_by=1,  # dev user
                symbol=symbol,
                timeframe=timeframe,
                model_type=model_type,
                pipeline=pipeline,
                file_path=file_path,
                grade=grade,
                metrics=metrics,
                trained_at=datetime.now(timezone.utc),
            )
            db.add(record)

        db.commit()
        db.close()
    except Exception as e:
        print(f"  [WARN] Could not save model to DB: {e}")
