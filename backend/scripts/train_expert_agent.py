"""
Expert agent training pipeline.
Trains XGBoost + LightGBM + LSTM + Meta-labeler + Regime Detector per symbol.
Run: python -m scripts.train_expert_agent [--trials 50] [--symbol XAUUSD]
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd
import joblib
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ml.features_mtf import compute_expert_features
from scripts.model_utils import (
    create_labels, walk_forward_split, grade_model, compute_backtest_metrics,
    save_model_record,
)
from scripts.train_scalping_pipeline import train_xgboost, train_lightgbm

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")
SYMBOLS = ["XAUUSD", "BTCUSD", "US30"]

warnings.filterwarnings("ignore")


def train_lstm(X_train, y_train, X_test, y_test, seq_len=60, epochs=50, batch_size=32):
    """Train LSTM sequence model using PyTorch."""
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    n_features = X_train.shape[1]

    # Create sequences
    def make_sequences(X, y, seq_len):
        Xs, ys = [], []
        for i in range(seq_len, len(X)):
            Xs.append(X[i - seq_len : i])
            ys.append(y[i])
        return np.array(Xs), np.array(ys)

    X_train_seq, y_train_seq = make_sequences(X_train, y_train, seq_len)
    X_test_seq, y_test_seq = make_sequences(X_test, y_test, seq_len)

    if len(X_train_seq) == 0 or len(X_test_seq) == 0:
        return None, 0.0

    # Normalize
    mean = X_train_seq.reshape(-1, n_features).mean(axis=0)
    std = X_train_seq.reshape(-1, n_features).std(axis=0)
    std[std == 0] = 1
    X_train_seq = (X_train_seq - mean) / std
    X_test_seq = (X_test_seq - mean) / std

    # PyTorch tensors
    device = torch.device("cpu")
    train_ds = TensorDataset(
        torch.FloatTensor(X_train_seq), torch.LongTensor(y_train_seq)
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

    # Model
    class LSTMClassifier(nn.Module):
        def __init__(self, input_size, hidden1=128, hidden2=64, num_classes=3, dropout=0.3):
            super().__init__()
            self.lstm1 = nn.LSTM(input_size, hidden1, batch_first=True)
            self.drop1 = nn.Dropout(dropout)
            self.lstm2 = nn.LSTM(hidden1, hidden2, batch_first=True)
            self.drop2 = nn.Dropout(dropout)
            self.fc = nn.Linear(hidden2, num_classes)

        def forward(self, x):
            out, _ = self.lstm1(x)
            out = self.drop1(out)
            out, _ = self.lstm2(out)
            out = self.drop2(out[:, -1, :])
            return self.fc(out)

    model = LSTMClassifier(n_features).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.CrossEntropyLoss()

    # Train
    best_acc = 0
    patience = 10
    no_improve = 0

    for epoch in range(epochs):
        model.train()
        for batch_X, batch_y in train_loader:
            batch_X, batch_y = batch_X.to(device), batch_y.to(device)
            optimizer.zero_grad()
            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()

        # Evaluate
        model.eval()
        with torch.no_grad():
            test_X = torch.FloatTensor(X_test_seq).to(device)
            test_preds = model(test_X).argmax(dim=1).cpu().numpy()
            acc = np.mean(test_preds == y_test_seq)

        if acc > best_acc:
            best_acc = acc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                break

    # Load best model
    model.load_state_dict(best_state)

    # Wrap for joblib serialization
    wrapper = {
        "state_dict": {k: v.numpy() for k, v in best_state.items()},
        "input_size": n_features,
        "seq_len": seq_len,
        "mean": mean,
        "std": std,
    }
    return wrapper, best_acc


def train_meta_labeler(X_train, y_train, preds_train, closes_train,
                       X_test, y_test, preds_test, closes_test, n_trials=30):
    """Train meta-labeler: should-I-trade filter."""
    import xgboost as xgb
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Meta-labeler target: was the trade profitable?
    def make_meta_targets(preds, closes):
        meta_y = np.zeros(len(preds))
        for i in range(len(preds) - 1):
            if preds[i] == 2:  # buy
                profit = closes[i + 1] - closes[i]
            elif preds[i] == 0:  # sell
                profit = closes[i] - closes[i + 1]
            else:
                profit = 0
            meta_y[i] = 1 if profit > 0 else 0
        return meta_y

    meta_y_train = make_meta_targets(preds_train, closes_train)
    meta_y_test = make_meta_targets(preds_test, closes_test)

    # Only use samples where ensemble predicted buy or sell
    trade_mask_train = preds_train != 1
    trade_mask_test = preds_test != 1

    if np.sum(trade_mask_train) < 50 or np.sum(trade_mask_test) < 10:
        return None

    X_meta_train = X_train[trade_mask_train]
    y_meta_train = meta_y_train[trade_mask_train]
    X_meta_test = X_test[trade_mask_test]
    y_meta_test = meta_y_test[trade_mask_test]

    def objective(trial):
        params = {
            "max_depth": trial.suggest_int("max_depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "n_estimators": trial.suggest_int("n_estimators", 50, 300),
            "eval_metric": "logloss",
            "verbosity": 0,
            "random_state": 42,
        }
        m = xgb.XGBClassifier(**params)
        m.fit(X_meta_train, y_meta_train, eval_set=[(X_meta_test, y_meta_test)], verbose=False)
        return m.score(X_meta_test, y_meta_test)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials)

    best_params = study.best_params
    best_params.update({"eval_metric": "logloss", "verbosity": 0, "random_state": 42})
    model = xgb.XGBClassifier(**best_params)
    model.fit(X_meta_train, y_meta_train, eval_set=[(X_meta_test, y_meta_test)], verbose=False)

    return model


def train_regime_detector(closes, volumes, n_states=4):
    """Train HMM regime detector."""
    from hmmlearn.hmm import GaussianHMM

    # Features: returns, volatility, volume ratio
    returns = np.diff(np.log(closes))
    vol = pd.Series(returns).rolling(20).std().values
    vol_ratio = volumes[1:] / pd.Series(volumes[1:]).rolling(20).mean().values

    # Stack features, drop NaN rows
    X = np.column_stack([returns, vol, vol_ratio])
    mask = ~np.any(np.isnan(X), axis=1) & ~np.any(np.isinf(X), axis=1)
    X_clean = X[mask]

    if len(X_clean) < 100:
        return None

    model = GaussianHMM(n_components=n_states, covariance_type="diag", n_iter=100, random_state=42)
    model.fit(X_clean)
    return model


def train_symbol(symbol: str, n_trials: int = 50):
    """Train all expert models for one symbol."""
    print(f"\n{'='*50}")
    print(f"Training expert models for {symbol}")
    print(f"{'='*50}")

    m5_path = os.path.join(DATA_DIR, f"{symbol}_M5.csv")
    h1_path = os.path.join(DATA_DIR, f"{symbol}_H1.csv")
    h4_path = os.path.join(DATA_DIR, f"{symbol}_H4.csv")
    d1_path = os.path.join(DATA_DIR, f"{symbol}_D1.csv")

    if not os.path.exists(m5_path):
        raise FileNotFoundError(f"No M5 data for {symbol}")

    m5 = pd.read_csv(m5_path)
    h1 = pd.read_csv(h1_path) if os.path.exists(h1_path) else None
    h4 = pd.read_csv(h4_path) if os.path.exists(h4_path) else None
    d1 = pd.read_csv(d1_path) if os.path.exists(d1_path) else None

    # Features
    print("  Computing features...")
    feature_names, X = compute_expert_features(m5, h1, h4, d1)
    print(f"  Features: {len(feature_names)}")

    closes = m5["close"].values
    volumes = m5["volume"].values
    atr_vals = X[:, feature_names.index("atr_14")]
    y = create_labels(closes, atr_vals)

    X_train, y_train, X_test, y_test = walk_forward_split(X, y, 0.8)
    closes_train = closes[200 : 200 + len(X_train)]
    closes_test = closes[-len(X_test):]

    os.makedirs(MODEL_DIR, exist_ok=True)
    results = []

    # XGBoost
    print(f"\n  Training XGBoost ({n_trials} trials)...")
    xgb_model, xgb_acc = train_xgboost(X_train, y_train, X_test, y_test, n_trials)
    xgb_metrics = compute_backtest_metrics(xgb_model, X_test, y_test, closes_test)
    xgb_grade = grade_model(xgb_metrics)
    xgb_path = os.path.join(MODEL_DIR, f"expert_{symbol}_M5_xgboost.joblib")
    joblib.dump({"model": xgb_model, "feature_names": feature_names, "grade": xgb_grade, "metrics": xgb_metrics}, xgb_path)
    print(f"  XGBoost: acc={xgb_acc:.4f}, grade={xgb_grade}")
    results.append(("xgboost", xgb_grade, xgb_metrics, xgb_path))

    # LightGBM
    print(f"\n  Training LightGBM ({n_trials} trials)...")
    lgb_model, lgb_acc = train_lightgbm(X_train, y_train, X_test, y_test, n_trials)
    lgb_metrics = compute_backtest_metrics(lgb_model, X_test, y_test, closes_test)
    lgb_grade = grade_model(lgb_metrics)
    lgb_path = os.path.join(MODEL_DIR, f"expert_{symbol}_M5_lightgbm.joblib")
    joblib.dump({"model": lgb_model, "feature_names": feature_names, "grade": lgb_grade, "metrics": lgb_metrics}, lgb_path)
    print(f"  LightGBM: acc={lgb_acc:.4f}, grade={lgb_grade}")
    results.append(("lightgbm", lgb_grade, lgb_metrics, lgb_path))

    # LSTM
    print("\n  Training LSTM...")
    lstm_wrapper, lstm_acc = train_lstm(X_train, y_train, X_test, y_test, seq_len=60, epochs=50)
    if lstm_wrapper:
        lstm_path = os.path.join(MODEL_DIR, f"expert_{symbol}_M5_lstm.joblib")
        joblib.dump({"model": lstm_wrapper, "feature_names": feature_names, "grade": "C", "metrics": {"accuracy": lstm_acc}}, lstm_path)
        print(f"  LSTM: acc={lstm_acc:.4f}")
        results.append(("lstm", "C", {"accuracy": round(lstm_acc, 4)}, lstm_path))
    else:
        print("  LSTM: skipped (insufficient data)")

    # Meta-labeler
    print("\n  Training Meta-labeler...")
    xgb_preds_train = xgb_model.predict(X_train)
    xgb_preds_test = xgb_model.predict(X_test)
    meta_model = train_meta_labeler(X_train, y_train, xgb_preds_train, closes_train,
                                     X_test, y_test, xgb_preds_test, closes_test, n_trials=30)
    if meta_model:
        meta_path = os.path.join(MODEL_DIR, f"expert_{symbol}_M5_meta_labeler.joblib")
        joblib.dump({"model": meta_model, "feature_names": feature_names}, meta_path)
        print("  Meta-labeler: trained")
        results.append(("meta_labeler", "N/A", {}, meta_path))
    else:
        print("  Meta-labeler: skipped (insufficient trade signals)")

    # Regime detector
    print("\n  Training Regime Detector...")
    regime_model = train_regime_detector(closes, volumes)
    if regime_model:
        regime_path = os.path.join(MODEL_DIR, f"expert_{symbol}_M5_regime.joblib")
        joblib.dump({"model": regime_model}, regime_path)
        print("  Regime detector: trained (4 states)")
        results.append(("regime_hmm", "N/A", {}, regime_path))
    else:
        print("  Regime detector: skipped (insufficient data)")

    # Save to DB
    for model_type, grade, metrics, path in results:
        if grade != "N/A":
            save_model_record(symbol, "M5", model_type, "expert", path, grade, metrics)

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    all_results = {}

    for symbol in symbols:
        try:
            all_results[symbol] = train_symbol(symbol, args.trials)
        except FileNotFoundError as e:
            print(f"\n  SKIPPED {symbol}: {e}")

    print(f"\n{'='*50}")
    print("EXPERT TRAINING SUMMARY")
    print(f"{'='*50}")
    for symbol, results in all_results.items():
        for model_type, grade, metrics, _ in results:
            if grade != "N/A":
                print(f"  {symbol} {model_type}: Grade={grade} | Sharpe={metrics.get('sharpe', 0):.2f}")


if __name__ == "__main__":
    main()
