"""
LSTM-Transformer Walk-Forward Training Pipeline
================================================
Trains the LSTM-Transformer hybrid model using expanding-window walk-forward
validation on M5 data with 210+ features.

Usage:
  python -m scripts.train_lstm_transformer --symbol US30
  python -m scripts.train_lstm_transformer --symbol US30 --epochs 50 --folds 2
  python -m scripts.train_lstm_transformer --all
"""
import os
import sys
import warnings
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import TensorDataset, DataLoader
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.symbol_config import get_symbol_config
from app.services.ml.lstm_transformer import (
    LSTMTransformer,
    create_lstm_transformer_wrapper,
)
from scripts.model_utils import (
    create_labels,
    compute_backtest_metrics,
    grade_model,
    save_model_record,
)
from scripts.train_walkforward import (
    load_ohlcv,
    load_peer_m5,
    get_wf_folds,
    OOS_START,
    WARMUP,
)
import joblib

DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")

ALL_SYMBOLS = ["US30", "BTCUSD", "XAUUSD"]
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def make_sequences(X: np.ndarray, y: np.ndarray, seq_len: int = 60):
    """Create sliding window sequences for LSTM input."""
    n = len(X)
    if n <= seq_len:
        return np.array([]), np.array([])
    X_seq = np.lib.stride_tricks.sliding_window_view(X, (seq_len, X.shape[1]))
    X_seq = X_seq.squeeze(axis=1)  # (n - seq_len + 1, seq_len, n_features)
    # Labels correspond to the LAST bar in each window
    y_seq = y[seq_len - 1:]
    # Trim to match
    min_len = min(len(X_seq), len(y_seq))
    return X_seq[:min_len].astype(np.float32), y_seq[:min_len]


def train_one_fold(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    seq_len: int = 60,
    epochs: int = 80,
    batch_size: int = 64,
    d_model: int = 128,
    n_heads: int = 4,
    lr: float = 0.0005,
) -> tuple[dict, float, dict]:
    """
    Train LSTM-Transformer on one fold.
    Returns (wrapper_dict, best_accuracy, test_info).
    """
    n_features = X_train.shape[1]

    # Normalize
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0)
    std[std == 0] = 1.0
    X_train_norm = (X_train - mean) / std
    X_test_norm = (X_test - mean) / std

    # Create sequences
    X_tr_seq, y_tr_seq = make_sequences(X_train_norm, y_train, seq_len)
    X_te_seq, y_te_seq = make_sequences(X_test_norm, y_test, seq_len)

    if len(X_tr_seq) < 100 or len(X_te_seq) < 50:
        return None, 0.0, {}

    # Class weights for imbalanced labels
    classes, counts = np.unique(y_tr_seq, return_counts=True)
    total = len(y_tr_seq)
    weights = torch.tensor([total / (len(classes) * c) for c in counts], dtype=torch.float32).to(DEVICE)

    # DataLoaders
    train_ds = TensorDataset(
        torch.tensor(X_tr_seq, dtype=torch.float32),
        torch.tensor(y_tr_seq, dtype=torch.long),
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)

    # Model
    model = LSTMTransformer(
        input_size=n_features, d_model=d_model, n_heads=n_heads,
    ).to(DEVICE)

    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=10, T_mult=2)

    # Training loop
    best_acc = 0.0
    best_state = None
    patience = 15
    no_improve = 0

    X_te_tensor = torch.tensor(X_te_seq, dtype=torch.float32).to(DEVICE)
    y_te_tensor = torch.tensor(y_te_seq, dtype=torch.long).to(DEVICE)

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for xb, yb in train_loader:
            xb, yb = xb.to(DEVICE), yb.to(DEVICE)
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()
        scheduler.step()

        # Validate
        model.eval()
        with torch.no_grad():
            preds = model(X_te_tensor).argmax(dim=1)
            acc = (preds == y_te_tensor).float().mean().item()

        if acc > best_acc:
            best_acc = acc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1

        if no_improve >= patience:
            break

    # Load best and create wrapper
    if best_state is None:
        return None, 0.0, {}

    model.load_state_dict(best_state)
    model.eval()

    wrapper = create_lstm_transformer_wrapper(
        model, mean, std, n_features, seq_len, d_model, n_heads,
    )

    return wrapper, best_acc, {"epochs_trained": epoch + 1}


def run_walkforward_lstm(
    symbol: str,
    n_folds: int = 4,
    seq_len: int = 60,
    epochs: int = 80,
    d_model: int = 128,
):
    """Full walk-forward LSTM-Transformer training pipeline."""
    cfg = get_symbol_config(symbol)
    cost_bps     = cfg.get("cost_bps", 5.0)
    slippage_bps = cfg.get("slippage_bps", 1.0)
    tp_mult      = cfg.get("tp_atr_mult", 1.0)
    sl_mult      = cfg.get("sl_atr_mult", 0.8)
    bpd          = cfg.get("bars_per_day", 288)
    hold_bars    = cfg.get("hold_bars", cfg.get("label_forward_bars", 10))
    use_trend    = cfg.get("trend_filter", True)

    print(f"\n{'='*65}")
    print(f"  LSTM-TRANSFORMER WALK-FORWARD: {symbol}")
    print(f"  {n_folds} folds | seq_len={seq_len} | d_model={d_model} | epochs={epochs}")
    print(f"  Device: {DEVICE}")
    print(f"{'='*65}")

    # Load data
    m5, m15, h1, h4, d1 = load_ohlcv(symbol)
    peer_m5 = load_peer_m5(symbol)
    print(f"  M5={len(m5):,} bars")

    # Compute features
    print("  Computing features...", flush=True)
    feature_names, X_all = compute_expert_features(
        m5, h1, h4, d1, symbol=symbol, include_external=True,
        other_m5=peer_m5 if peer_m5 else None, m15_bars=m15,
    )
    print(f"  Features: {len(feature_names)}")

    # Labels
    timestamps = m5["time"].values.astype(np.int64)
    closes = m5["close"].values
    opens  = m5["open"].values
    highs  = m5["high"].values
    lows   = m5["low"].values
    atr_idx = feature_names.index("atr_14") if "atr_14" in feature_names else None
    atr_vals = X_all[:, atr_idx] if atr_idx is not None else None
    y_all = create_labels(closes, atr_vals, config=cfg)
    print(f"  Labels: sell={np.sum(y_all==0):,} hold={np.sum(y_all==1):,} buy={np.sum(y_all==2):,}")

    # Walk-forward folds
    folds, oos_idx = get_wf_folds(timestamps, OOS_START, n_folds)
    print(f"  Train rows: {oos_idx:,} | OOS rows: {len(X_all) - oos_idx:,}")

    # OOS data
    X_oos = X_all[oos_idx:]
    y_oos = y_all[oos_idx:]
    closes_oos = closes[oos_idx:]
    opens_oos = opens[oos_idx:]
    highs_oos = highs[oos_idx:]
    lows_oos = lows[oos_idx:]
    atr_oos = atr_vals[oos_idx:] if atr_vals is not None else None

    wf_results = []
    best_wrapper = None
    best_fold_acc = 0.0

    print(f"\n  {'─'*60}")

    for fold_info in folds:
        fnum = fold_info["fold"]
        tr_end = fold_info["train_end"]
        ts_start = fold_info["test_start"]
        ts_end = fold_info["test_end"]

        X_tr = X_all[WARMUP:tr_end]
        y_tr = y_all[WARMUP:tr_end]
        X_te = X_all[ts_start:ts_end]
        y_te = y_all[ts_start:ts_end]

        tr_start_dt = pd.to_datetime(timestamps[WARMUP], unit="s", utc=True).strftime("%Y-%m")
        tr_end_dt = pd.to_datetime(timestamps[tr_end-1], unit="s", utc=True).strftime("%Y-%m")
        te_start_dt = pd.to_datetime(timestamps[ts_start], unit="s", utc=True).strftime("%Y-%m")
        te_end_dt = pd.to_datetime(timestamps[min(ts_end-1, len(timestamps)-1)], unit="s", utc=True).strftime("%Y-%m")

        print(f"\n  Fold {fnum}: Train {tr_start_dt}->{tr_end_dt} ({len(X_tr):,}) | "
              f"Test {te_start_dt}->{te_end_dt} ({len(X_te):,})")

        wrapper, acc, info = train_one_fold(
            X_tr, y_tr, X_te, y_te,
            seq_len=seq_len, epochs=epochs, d_model=d_model,
        )

        if wrapper is None:
            print(f"    Skipped (insufficient data)")
            continue

        print(f"    Accuracy: {acc:.4f}  Epochs: {info.get('epochs_trained', '?')}")

        # Backtest on fold test data
        # Create a simple predictor from wrapper for backtest
        from app.services.ml.lstm_transformer import load_lstm_transformer_from_wrapper
        model = load_lstm_transformer_from_wrapper(wrapper, DEVICE)
        mean = wrapper["mean"]
        std = wrapper["std"]
        std[std == 0] = 1.0

        X_te_norm = (X_te - mean) / std
        X_te_seq, y_te_seq = make_sequences(X_te_norm, y_te, seq_len)

        if len(X_te_seq) > 0:
            with torch.no_grad():
                te_tensor = torch.tensor(X_te_seq, dtype=torch.float32).to(DEVICE)
                preds = model(te_tensor).argmax(dim=1).cpu().numpy()

            # Compute backtest metrics on the sequenced portion
            c_te = closes[ts_start + seq_len - 1:ts_end][:len(preds)]
            o_te = opens[ts_start + seq_len - 1:ts_end][:len(preds)]
            h_te = highs[ts_start + seq_len - 1:ts_end][:len(preds)]
            l_te = lows[ts_start + seq_len - 1:ts_end][:len(preds)]
            a_te = atr_vals[ts_start + seq_len - 1:ts_end][:len(preds)] if atr_vals is not None else None

            # Create a dummy model for compute_backtest_metrics
            class _PredModel:
                def __init__(self, p): self._p = p
                def predict(self, X): return self._p

            metrics = compute_backtest_metrics(
                _PredModel(preds), None, y_te_seq[:len(preds)], c_te,
                opens_test=o_te, highs_test=h_te, lows_test=l_te, atr_test=a_te,
                cost_bps=cost_bps, slippage_bps=slippage_bps,
                bars_per_day=bpd, hold_bars=hold_bars,
                tp_atr_mult=tp_mult, sl_atr_mult=sl_mult,
                trend_filter=use_trend,
            )
            grade = grade_model(metrics)
            print(f"    Grade={grade}  Sharpe={metrics['sharpe']:.3f}  "
                  f"WR={metrics['win_rate']:.1f}%  DD={metrics['max_drawdown']:.1f}%  "
                  f"Trades={metrics['total_trades']}")

            wf_results.append({"fold": fnum, "grade": grade, "acc": acc, **metrics})

        if acc > best_fold_acc:
            best_fold_acc = acc
            best_wrapper = wrapper

    # Train final model on all data before OOS
    print(f"\n  {'─'*60}")
    print(f"  Final Model (full train -> {OOS_START})")

    X_full = X_all[WARMUP:oos_idx]
    y_full = y_all[WARMUP:oos_idx]
    final_wrapper, final_acc, final_info = train_one_fold(
        X_full, y_full, X_oos, y_oos,
        seq_len=seq_len, epochs=epochs, d_model=d_model,
    )

    if final_wrapper:
        best_wrapper = final_wrapper
        print(f"  Final accuracy: {final_acc:.4f}")

    # Save model
    if best_wrapper:
        path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_lstm_transformer.joblib")
        joblib.dump({
            "model": best_wrapper,
            "feature_names": feature_names,
            "grade": wf_results[-1]["grade"] if wf_results else "?",
            "oos_metrics": wf_results[-1] if wf_results else {},
            "symbol": symbol,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }, path)
        print(f"  Saved: {os.path.basename(path)}")

        try:
            save_model_record(
                symbol, "M5", "lstm_transformer", "scalping",
                path, wf_results[-1]["grade"] if wf_results else "?",
                wf_results[-1] if wf_results else {},
            )
        except Exception as e:
            print(f"  DB record failed: {e}")

    print(f"\n{'='*65}")
    print(f"  LSTM-Transformer training complete for {symbol}")
    print(f"{'='*65}\n")

    return wf_results


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LSTM-Transformer walk-forward training")
    parser.add_argument("--symbol", type=str, help="Symbol (e.g. US30)")
    parser.add_argument("--all", action="store_true", help="Train all symbols")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--seq-len", type=int, default=60)
    parser.add_argument("--d-model", type=int, default=128)
    args = parser.parse_args()

    if not args.symbol and not args.all:
        parser.error("Specify --symbol US30 or --all")

    symbols = ALL_SYMBOLS if args.all else [args.symbol.upper()]
    for sym in symbols:
        run_walkforward_lstm(sym, args.folds, args.seq_len, args.epochs, args.d_model)


if __name__ == "__main__":
    main()
