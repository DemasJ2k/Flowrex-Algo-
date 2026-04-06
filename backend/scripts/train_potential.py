"""
train_potential.py - Potential Agent Walk-Forward Training

Institutional strategies: VWAP, Volume Profile, ADX, ORB, EMA structure.
Architecture: XGBoost + LightGBM + optional LSTM diversity signal.
Simple risk: 10% max DD, 3% daily loss, 1% per trade. No prop firm filters.

Usage:
    cd backend
    python -m scripts.train_potential --symbol US30 [--trials 15] [--folds 4]
"""
import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd
import joblib
import optuna
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

from app.services.ml.features_potential import compute_potential_features
from app.services.ml.symbol_config import get_symbol_config
from scripts.model_utils import create_labels, compute_backtest_metrics, grade_model, shap_feature_filter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
HIST_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "History Data", "data")
)
OOS_START = "2026-01-01"
WARMUP = 500
MAX_M5_BARS = 500_000

RISK_CONFIG = {
    "max_drawdown_pct": 0.10,
    "daily_loss_limit_pct": 0.03,
    "risk_per_trade_pct": 0.01,
    "max_trades_per_day": 10,
}

STRATEGY_GROUPS = {
    "VWAP": lambda n: "vwap" in n,
    "Volume Profile": lambda n: "poc" in n or "vah" in n or "val" in n or "value_area" in n,
    "ADX/Trend": lambda n: "adx" in n or "plus_di" in n or "minus_di" in n,
    "EMA Structure": lambda n: "ema" in n or "200_cross" in n or "fan" in n,
    "ORB": lambda n: "orb" in n,
    "RSI": lambda n: "rsi" in n,
    "MACD/Momentum": lambda n: "macd" in n or "momentum" in n,
    "Volatility": lambda n: "atr" in n or "bb_" in n,
    "CVD/Flow": lambda n: "cvd" in n or "absorption" in n or "vol_imbalance" in n or "vol_spike" in n,
    "Session/Time": lambda n: "hour" in n or "dow" in n or "cash" in n,
    "Structure": lambda n: "return" in n or "body" in n or "wick" in n or "gap" in n or "range" in n,
    "Breakout": lambda n: "donch" in n or "retest" in n or "expansion" in n,
    "HTF": lambda n: "h1_" in n or "h4_" in n or "d1_" in n or "htf" in n,
    "LSTM": lambda n: "lstm" in n,
}


# ── Data loading ──────────────────────────────────────────────────────────

def _normalize_ohlcv(df):
    df = df.copy()
    if "ts_event" in df.columns and "time" not in df.columns:
        df["time"] = (
            pd.to_datetime(df["ts_event"]).values.astype("datetime64[s]").astype(np.int64)
        )
        df = df.drop(columns=["ts_event"])
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("time").reset_index(drop=True)


def _load_tf(symbol, tf):
    hist_path = os.path.join(HIST_DATA_DIR, symbol, f"{symbol}_{tf}.csv")
    curr_path = os.path.join(DATA_DIR, f"{symbol}_{tf}.csv")
    if os.path.exists(hist_path):
        return _normalize_ohlcv(pd.read_csv(hist_path))
    if os.path.exists(curr_path):
        return pd.read_csv(curr_path)
    return None


def load_ohlcv(symbol):
    m5 = _load_tf(symbol, "M5")
    h1 = _load_tf(symbol, "H1")
    h4 = _load_tf(symbol, "H4")
    d1 = _load_tf(symbol, "D1")
    if m5 is None:
        raise FileNotFoundError(f"No M5 data for {symbol}")
    if len(m5) > MAX_M5_BARS:
        print(f"  [INFO] Capping M5 from {len(m5):,} to {MAX_M5_BARS:,} bars")
        m5 = m5.iloc[-MAX_M5_BARS:].reset_index(drop=True)
        start_ts = m5["time"].iloc[0]
        for df_name in [h1, h4, d1]:
            pass
        if h1 is not None:
            h1 = h1[h1["time"] >= start_ts].reset_index(drop=True)
        if h4 is not None:
            h4 = h4[h4["time"] >= start_ts].reset_index(drop=True)
        if d1 is not None:
            d1 = d1[d1["time"] >= start_ts].reset_index(drop=True)
    return m5, h1, h4, d1


# ── Walk-forward folds ────────────────────────────────────────────────────

def get_wf_folds(timestamps, oos_start, n_folds=4):
    oos_ts = int(pd.Timestamp(oos_start, tz="UTC").timestamp())
    oos_idx = int(np.argmax(timestamps >= oos_ts)) if (timestamps >= oos_ts).any() else len(timestamps)
    n_train = oos_idx - WARMUP
    if n_train < n_folds * 500:
        n_folds = max(2, n_folds // 2)
    chunk = n_train // (n_folds + 1)
    folds = []
    for k in range(n_folds):
        train_end = WARMUP + (k + 1) * chunk
        test_start = train_end
        test_end = WARMUP + (k + 2) * chunk
        folds.append({"fold": k+1, "train_end": train_end, "test_start": test_start, "test_end": test_end})
    return folds, oos_idx


# ── LSTM diversity signal ─────────────────────────────────────────────────

def _build_lstm_features(closes, opens, highs, lows, volumes, y_all, seq_len=60):
    """Train a small LSTM and return predictions as an extra feature."""
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError:
        print("  [WARN] PyTorch not available — skipping LSTM diversity signal")
        return None

    n = len(closes)
    # Normalize OHLCV with rolling stats
    data = np.column_stack([opens, highs, lows, closes, volumes]).astype(np.float32)
    roll_mean = pd.DataFrame(data).rolling(100, min_periods=1).mean().values
    roll_std = pd.DataFrame(data).rolling(100, min_periods=1).std().fillna(1).values
    roll_std = np.where(roll_std < 1e-8, 1.0, roll_std)
    normed = ((data - roll_mean) / roll_std).astype(np.float32)

    # Create sequences using strided view (memory efficient)
    # Subsample for training (max 50k), predict all via batched inference
    from numpy.lib.stride_tricks import sliding_window_view
    seq_data = sliding_window_view(normed, (seq_len, 5)).squeeze(axis=1)  # (n-seq_len+1, seq_len, 5)
    # Align: seq_data[i] = normed[i:i+seq_len], label = y_all[i+seq_len-1]
    y_seq = y_all[seq_len-1:seq_len-1+len(seq_data)].astype(np.int64)

    # Subsample for LSTM training (50k max to keep CPU time ~2min)
    MAX_LSTM_TRAIN = 50_000
    total_seq = len(seq_data)
    if total_seq > MAX_LSTM_TRAIN:
        # Stratified subsample from first 80%
        split_80 = int(total_seq * 0.8)
        rng = np.random.RandomState(42)
        train_idx = rng.choice(split_80, size=min(MAX_LSTM_TRAIN, split_80), replace=False)
        train_idx.sort()
        X_train = seq_data[train_idx].copy().astype(np.float32)
        y_train = y_seq[train_idx].copy()
    else:
        split_80 = int(total_seq * 0.8)
        X_train = seq_data[:split_80].copy().astype(np.float32)
        y_train = y_seq[:split_80].copy()

    class SmallLSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm1 = nn.LSTM(5, 64, batch_first=True)
            self.lstm2 = nn.LSTM(64, 32, batch_first=True)
            self.fc = nn.Linear(32, 3)

        def forward(self, x):
            x, _ = self.lstm1(x)
            x, _ = self.lstm2(x)
            return self.fc(x[:, -1, :])

    model = SmallLSTM()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()
    dataset = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    loader = DataLoader(dataset, batch_size=512, shuffle=True)

    print(f"  Training LSTM (20 epochs, {len(X_train):,} samples)...", end=" ", flush=True)
    model.train()
    for epoch in range(20):
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
    print("done", flush=True)

    # Generate predictions for ALL sequences in batches
    model.eval()
    all_preds = np.zeros(total_seq, dtype=np.float32)
    batch_sz = 2048
    with torch.no_grad():
        for start in range(0, total_seq, batch_sz):
            end = min(start + batch_sz, total_seq)
            batch = torch.from_numpy(seq_data[start:end].copy().astype(np.float32))
            all_preds[start:end] = model(batch).argmax(dim=1).numpy()

    # Pad to full length (seq_data starts at index seq_len-1)
    lstm_feat = np.zeros(n, dtype=np.float32)
    lstm_feat[seq_len-1:seq_len-1+total_seq] = all_preds
    return lstm_feat


# ── Model training ────────────────────────────────────────────────────────

def _xgb_objective(trial, Xtr, ytr, Xval, yval):
    import xgboost as xgb
    params = {
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 100, 500),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "min_child_weight": trial.suggest_int("min_child_weight", 1, 20),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 5.0),
        "objective": "multi:softprob", "num_class": 3,
        "eval_metric": "mlogloss", "verbosity": 0, "random_state": 42,
        "early_stopping_rounds": 20,
    }
    m = xgb.XGBClassifier(**params)
    m.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    return m.score(Xval, yval)


def _lgb_objective(trial, Xtr, ytr, Xval, yval):
    import lightgbm as lgb
    params = {
        "num_leaves": trial.suggest_int("num_leaves", 15, 80),
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 100, 500),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 5.0),
        "min_child_samples": trial.suggest_int("min_child_samples", 10, 60),
        "objective": "multiclass", "num_class": 3,
        "metric": "multi_logloss", "verbosity": -1, "random_state": 42,
    }
    m = lgb.LGBMClassifier(**params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(Xtr, ytr, eval_set=[(Xval, yval)],
              callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)])
    return m.score(Xval, yval)


def train_model(model_type, Xtr, ytr, Xval, yval, n_trials):
    if model_type == "xgboost":
        obj = lambda t: _xgb_objective(t, Xtr, ytr, Xval, yval)
    else:
        obj = lambda t: _lgb_objective(t, Xtr, ytr, Xval, yval)
    study = optuna.create_study(direction="maximize")
    study.optimize(obj, n_trials=n_trials, show_progress_bar=False)
    best = study.best_params
    if model_type == "xgboost":
        import xgboost as xgb
        best.update({"objective": "multi:softprob", "num_class": 3,
                     "eval_metric": "mlogloss", "verbosity": 0,
                     "random_state": 42, "early_stopping_rounds": 20})
        model = xgb.XGBClassifier(**best)
        model.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    else:
        import lightgbm as lgb
        best.update({"objective": "multiclass", "num_class": 3,
                     "metric": "multi_logloss", "verbosity": -1, "random_state": 42})
        model = lgb.LGBMClassifier(**best)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(Xtr, ytr, eval_set=[(Xval, yval)],
                      callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)])
    return model, study.best_value


# ── SHAP ──────────────────────────────────────────────────────────────────

def shap_importance_table(model, X, feature_names, top_n=20):
    try:
        import shap
        sample = X[:3000] if len(X) > 3000 else X
        explainer = shap.TreeExplainer(model)
        shap_vals = explainer.shap_values(sample)
        if isinstance(shap_vals, list):
            mean_abs = np.mean([np.abs(sv).mean(axis=0) for sv in shap_vals], axis=0)
        elif np.asarray(shap_vals).ndim == 3:
            mean_abs = np.abs(shap_vals).mean(axis=(0, 2))
        else:
            mean_abs = np.abs(shap_vals).mean(axis=0)
        total = mean_abs.sum()
        ranked = sorted(zip(feature_names, mean_abs / total), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]
    except Exception as e:
        print(f"  [WARN] SHAP failed: {e}")
        return []


# ── Main ──────────────────────────────────────────────────────────────────

def run_potential_training(symbol="US30", n_trials=15, n_folds=4):
    cfg = get_symbol_config(symbol)
    cost_bps = cfg.get("cost_bps", 5.0)
    slippage_bps = cfg.get("slippage_bps", 1.0)
    tp_mult = 1.2
    sl_mult = 0.8
    bpd = cfg.get("bars_per_day", 288)
    hold_bars = 10

    print(f"\n{'='*65}")
    print(f"  POTENTIAL AGENT: {symbol}  |  {n_folds} folds  |  {n_trials} trials/fold")
    print(f"  Institutional strategies + LSTM diversity")
    print(f"{'='*65}")

    # 1. Load data
    m5, h1, h4, d1 = load_ohlcv(symbol)
    print(f"  M5={len(m5):,}  H1={len(h1) if h1 is not None else 0:,}  "
          f"H4={len(h4) if h4 is not None else 0:,}  D1={len(d1) if d1 is not None else 0:,}")

    timestamps = m5["time"].values.astype(np.int64)
    closes = m5["close"].values
    opens = m5["open"].values
    highs = m5["high"].values
    lows = m5["low"].values

    # 2. Compute potential features
    print("  Computing potential features...", flush=True)
    feature_names, X_all = compute_potential_features(m5, h1, h4, d1, symbol=symbol)
    print(f"  Features: {len(feature_names)}")

    # 3. Create labels (triple barrier)
    from app.services.backtest.indicators import atr as atr_fn
    atr_vals = atr_fn(highs, lows, closes, 14)
    y_all = create_labels(closes, atr_vals, forward_bars=hold_bars,
                          atr_mult=tp_mult)

    # Force HOLD in low-vol periods (ATR < 25th percentile)
    valid_atr = atr_vals[~np.isnan(atr_vals)]
    if len(valid_atr) > 0:
        atr_25 = np.percentile(valid_atr, 25)
        low_vol = atr_vals < atr_25
        low_vol[np.isnan(atr_vals)] = True
        forced = low_vol & (y_all != 1)
        y_all[forced] = 1
        print(f"  Low-vol filter: {forced.sum():,} labels forced to HOLD ({forced.sum()/len(y_all)*100:.1f}%)")

    print(f"  Labels: sell={np.sum(y_all==0):,}  hold={np.sum(y_all==1):,}  buy={np.sum(y_all==2):,}")

    # 4. LSTM diversity signal
    lstm_feat = _build_lstm_features(closes, opens, highs, lows,
                                     m5["volume"].values if "volume" in m5.columns else np.ones(len(closes)),
                                     y_all)
    if lstm_feat is not None:
        feature_names.append("pot_lstm_pred")
        X_all = np.column_stack([X_all, lstm_feat.reshape(-1, 1)])
        print(f"  Features after LSTM: {len(feature_names)}")

    # 5. Walk-forward
    folds, oos_idx = get_wf_folds(timestamps, OOS_START, n_folds)

    X_oos = X_all[oos_idx:]
    y_oos = y_all[oos_idx:]
    closes_oos = closes[oos_idx:]
    opens_oos = opens[oos_idx:]
    highs_oos = highs[oos_idx:]
    lows_oos = lows[oos_idx:]
    atr_oos = atr_vals[oos_idx:]

    print(f"  Train rows: {oos_idx:,}  |  OOS rows: {len(X_oos):,}")
    print(f"\n  {'-'*60}")
    print(f"  Walk-Forward ({len(folds)} folds)")
    print(f"  {'-'*60}")

    os.makedirs(MODEL_DIR, exist_ok=True)
    wf_results = []
    fold_preds = {"xgboost": [], "lightgbm": []}

    for fold_info in folds:
        fnum = fold_info["fold"]
        tr_end = fold_info["train_end"]
        ts_start = fold_info["test_start"]
        ts_end = fold_info["test_end"]

        X_tr = X_all[WARMUP:tr_end]
        y_tr = y_all[WARMUP:tr_end]
        X_val = X_all[ts_start:ts_end]
        y_val = y_all[ts_start:ts_end]

        val_split = int(len(X_tr) * 0.8)
        Xtr_opt, ytr_opt = X_tr[:val_split], y_tr[:val_split]
        Xv_opt, yv_opt = X_tr[val_split:], y_tr[val_split:]

        ts_start_h = pd.to_datetime(timestamps[ts_start], unit="s", utc=True).strftime("%Y-%m")
        ts_end_h = pd.to_datetime(timestamps[min(ts_end-1, len(timestamps)-1)], unit="s", utc=True).strftime("%Y-%m")

        print(f"\n  Fold {fnum}: Train ({len(X_tr):,}) | Test {ts_start_h}->{ts_end_h} ({len(X_val):,})")

        for model_type in ["xgboost", "lightgbm"]:
            print(f"    [{model_type}] {n_trials} trials...", end=" ", flush=True)
            model, cv_acc = train_model(model_type, Xtr_opt, ytr_opt, Xv_opt, yv_opt, n_trials)
            print(f"cv_acc={cv_acc:.4f}", flush=True)

            c_val = closes[ts_start:ts_end]
            o_val = opens[ts_start:ts_end]
            h_val = highs[ts_start:ts_end]
            lo_val = lows[ts_start:ts_end]
            a_val = atr_vals[ts_start:ts_end]
            t_val = timestamps[ts_start:ts_end]

            metrics = compute_backtest_metrics(
                model, X_val, y_val, c_val,
                opens_test=o_val, highs_test=h_val, lows_test=lo_val,
                atr_test=a_val, times_test=t_val,
                cost_bps=cost_bps, slippage_bps=slippage_bps,
                bars_per_day=bpd, hold_bars=hold_bars,
                tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
                trend_filter=False,
            )
            grade = grade_model(metrics)
            print(f"    [{model_type}] Grade={grade}  Sharpe={metrics['sharpe']:.3f}  "
                  f"WR={metrics['win_rate']:.1f}%  DD={metrics['max_drawdown']:.1f}%  "
                  f"Trades={metrics['total_trades']}")

            wf_results.append({"fold": fnum, "model": model_type, "grade": grade, **metrics})
            fold_preds[model_type].append({
                "preds": model.predict(X_val), "y": y_val, "c": c_val,
                "o": o_val, "h": h_val, "lo": lo_val, "atr": a_val,
            })

    # 6. Final models on full training set
    print(f"\n  {'-'*60}")
    print(f"  Final Models (full train -> {OOS_START})")
    print(f"  {'-'*60}")

    X_full_tr = X_all[WARMUP:oos_idx]
    y_full_tr = y_all[WARMUP:oos_idx]
    val_split = int(len(X_full_tr) * 0.85)

    final_models = {}
    for model_type in ["xgboost", "lightgbm"]:
        print(f"  [{model_type}] {n_trials*2} trials...", end=" ", flush=True)
        model, cv_acc = train_model(
            model_type, X_full_tr[:val_split], y_full_tr[:val_split],
            X_full_tr[val_split:], y_full_tr[val_split:], n_trials * 2
        )
        print(f"cv_acc={cv_acc:.4f}", flush=True)
        final_models[model_type] = model

    # 7. OOS evaluation
    print(f"\n  {'-'*60}")
    print(f"  True OOS: {OOS_START} -> present ({len(X_oos):,} bars)")
    print(f"  {'-'*60}")

    oos_results = {}
    for model_type, model in final_models.items():
        m = compute_backtest_metrics(
            model, X_oos, y_oos, closes_oos,
            opens_test=opens_oos, highs_test=highs_oos, lows_test=lows_oos,
            atr_test=atr_oos, times_test=timestamps[oos_idx:],
            cost_bps=cost_bps, slippage_bps=slippage_bps,
            bars_per_day=bpd, hold_bars=hold_bars,
            tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
            trend_filter=False,
        )
        g = grade_model(m)
        oos_results[model_type] = (g, m)
        print(f"  [{model_type}] Grade={g}  Sharpe={m['sharpe']:.3f}  "
              f"WR={m['win_rate']:.1f}%  DD={m['max_drawdown']:.1f}%  "
              f"Return={m['total_return']:.1f}%  Trades={m['total_trades']}")

    # 8. SHAP per strategy group
    print(f"\n  {'-'*60}")
    print(f"  SHAP Feature Importance (XGBoost)")
    print(f"  {'-'*60}")

    ranked = shap_importance_table(final_models["xgboost"], X_full_tr[:val_split],
                                   feature_names, top_n=20)
    if ranked:
        for rank, (fname, imp) in enumerate(ranked, 1):
            bar = "#" * int(imp * 300)
            print(f"  {rank:2d}. {fname:<35}  {imp*100:5.2f}%  {bar}")

    # Strategy group SHAP
    if ranked:
        all_ranked = shap_importance_table(final_models["xgboost"], X_full_tr[:val_split],
                                           feature_names, top_n=len(feature_names))
        print(f"\n  {'-'*60}")
        print(f"  Strategy Group SHAP")
        print(f"  {'-'*60}")
        assigned = set()
        group_scores = {}
        for gname, matcher in STRATEGY_GROUPS.items():
            score = 0.0
            count = 0
            for fname, imp in all_ranked:
                if fname not in assigned and matcher(fname):
                    score += imp
                    count += 1
                    assigned.add(fname)
            group_scores[gname] = (score, count)
        for gname in sorted(group_scores, key=lambda x: -group_scores[x][0]):
            score, count = group_scores[gname]
            bar = "#" * int(score * 200)
            print(f"  {gname:<15}  {score*100:5.1f}%  ({count:3d} features)  {bar}")

    # 9. Save models
    print(f"\n  {'-'*60}")
    for model_type, model in final_models.items():
        grade, oos_m = oos_results[model_type]
        path = os.path.join(MODEL_DIR, f"potential_{symbol}_M5_{model_type}.joblib")
        save_dict = {
            "model": model,
            "feature_names": feature_names,
            "grade": grade,
            "oos_metrics": oos_m,
            "wf_results": wf_results,
            "symbol": symbol,
            "oos_start": OOS_START,
            "risk_config": RISK_CONFIG,
            "execution": {
                "fill": "open[i+1]",
                "tp_atr_mult": tp_mult,
                "sl_atr_mult": sl_mult,
                "cost_bps": cost_bps,
                "slippage_bps": slippage_bps,
                "hold_bars": hold_bars,
            },
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "potential_v1",
            "agent_type": "potential",
        }
        joblib.dump(save_dict, path)
        print(f"  Saved: {os.path.basename(path)}  (Grade={grade})")

    return wf_results, oos_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Potential Agent Training")
    parser.add_argument("--symbol", default="US30")
    parser.add_argument("--trials", type=int, default=15)
    parser.add_argument("--folds", type=int, default=4)
    args = parser.parse_args()
    run_potential_training(args.symbol, args.trials, args.folds)
