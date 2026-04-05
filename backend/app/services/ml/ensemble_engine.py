"""
Ensemble signal engine — loads models and produces trading signals.
Handles both scalping (any 1 model >= 55%) and expert (2/3 agreement) voting.
"""
import os
import numpy as np
import joblib
from typing import Optional
from dataclasses import dataclass, field

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")


@dataclass
class Signal:
    direction: int  # 1=buy, -1=sell, 0=no signal
    confidence: float = 0.0
    agreement: int = 0  # how many models agree
    reason: str = ""
    votes: dict = field(default_factory=dict)


class EnsembleSignalEngine:
    """Load models and produce trading signals via ensemble voting."""

    CONFIDENCE_THRESHOLD = 0.55

    def __init__(self, symbol: str, pipeline: str = "scalping"):
        self.symbol = symbol
        self.pipeline = pipeline
        self.models: dict[str, dict] = {}
        self.feature_names: list[str] = []
        self._eval_count = 0
        self._rejection_stats: dict[str, int] = {
            "insufficient_models": 0,
            "no_consensus": 0,
            "low_confidence": 0,
            "nan_features": 0,
        }

    def load_models(self) -> bool:
        """Load all models for this symbol+pipeline from disk."""
        self.models.clear()
        model_types = ["xgboost", "lightgbm"]
        # Always try loading LSTM-Transformer (primary signal generator)
        model_types.append("lstm_transformer")
        if self.pipeline == "expert":
            model_types.append("lstm")

        for mtype in model_types:
            path = os.path.join(MODEL_DIR, f"{self.pipeline}_{self.symbol}_M5_{mtype}.joblib")
            if os.path.exists(path):
                data = joblib.load(path)
                self.models[mtype] = data
                if not self.feature_names and "feature_names" in data:
                    self.feature_names = data["feature_names"]

        return len(self.models) > 0

    def predict(self, feature_vector: np.ndarray, feature_sequence: Optional[np.ndarray] = None) -> Optional[Signal]:
        """
        Run ensemble prediction.
        feature_vector: 1D array of features for current bar
        feature_sequence: 2D array (seq_len, n_features) for LSTM (expert only)
        Returns Signal or None if no valid signal.
        """
        self._eval_count += 1

        if len(self.models) == 0:
            self._rejection_stats["insufficient_models"] += 1
            return None

        # Check for NaN
        if np.any(np.isnan(feature_vector)):
            self._rejection_stats["nan_features"] += 1
            return None

        votes = {}
        X = feature_vector.reshape(1, -1)

        for mtype, data in self.models.items():
            model = data.get("model")
            if model is None:
                continue

            if mtype in ("lstm", "lstm_transformer"):
                # Sequence models need sequence input
                if feature_sequence is None:
                    continue
                pred, conf = self._predict_sequence_model(mtype, data, feature_sequence)
            else:
                try:
                    proba = model.predict_proba(X)[0]
                except ValueError:
                    # Feature count mismatch — model is stale (needs retraining); skip it
                    self._rejection_stats["insufficient_models"] += 1
                    continue
                pred = int(np.argmax(proba))
                conf = float(proba[pred])

            # Map 0=sell, 1=hold, 2=buy to direction
            direction = {0: -1, 1: 0, 2: 1}.get(pred, 0)
            votes[mtype] = {"direction": direction, "confidence": conf, "class": pred}

        if not votes:
            self._rejection_stats["insufficient_models"] += 1
            return None

        # Voting logic: LSTM-primary if available, else fallback
        if "lstm_transformer" in votes or "lstm" in votes:
            return self._lstm_primary_vote(votes)
        elif self.pipeline == "scalping":
            return self._scalping_vote(votes)
        else:
            return self._expert_vote(votes)

    def _scalping_vote(self, votes: dict) -> Optional[Signal]:
        """Scalping: any ONE model with >= 55% confidence on buy/sell fires."""
        for mtype, v in votes.items():
            if v["direction"] != 0 and v["confidence"] >= self.CONFIDENCE_THRESHOLD:
                return Signal(
                    direction=v["direction"],
                    confidence=v["confidence"],
                    agreement=1,
                    reason=f"{mtype} signal",
                    votes=votes,
                )

        self._rejection_stats["low_confidence"] += 1
        return None

    def _expert_vote(self, votes: dict) -> Optional[Signal]:
        """Expert: need 2/3 agreement + min 55% weighted confidence."""
        directions = [v["direction"] for v in votes.values() if v["direction"] != 0]

        if len(directions) < 2:
            self._rejection_stats["no_consensus"] += 1
            return None

        # Count agreement
        buy_count = sum(1 for d in directions if d == 1)
        sell_count = sum(1 for d in directions if d == -1)

        total_models = len(votes)
        required = max(2, int(total_models * 2 / 3))

        if buy_count >= required:
            consensus_dir = 1
        elif sell_count >= required:
            consensus_dir = -1
        else:
            self._rejection_stats["no_consensus"] += 1
            return None

        # Weighted confidence
        confidences = [v["confidence"] for v in votes.values() if v["direction"] == consensus_dir]
        weighted_conf = np.mean(confidences) if confidences else 0

        if weighted_conf < self.CONFIDENCE_THRESHOLD:
            self._rejection_stats["low_confidence"] += 1
            return None

        return Signal(
            direction=consensus_dir,
            confidence=weighted_conf,
            agreement=buy_count if consensus_dir == 1 else sell_count,
            reason=f"expert consensus ({buy_count}B/{sell_count}S)",
            votes=votes,
        )

    def _lstm_primary_vote(self, votes: dict) -> Optional[Signal]:
        """
        LSTM-primary voting: LSTM is the primary signal generator.
        - LSTM >=50% confidence + any tree agrees → signal fires
        - LSTM >=65% confidence → fires alone (high-conviction override)
        - Liberal thresholds: RL handles quality filtering downstream
        """
        # Find the LSTM vote (prefer lstm_transformer over lstm)
        lstm_key = "lstm_transformer" if "lstm_transformer" in votes else "lstm" if "lstm" in votes else None
        if not lstm_key:
            return self._scalping_vote(votes)

        lstm_vote = votes[lstm_key]
        if lstm_vote["direction"] == 0:
            # LSTM says hold — no signal
            return None

        lstm_dir = lstm_vote["direction"]
        lstm_conf = lstm_vote["confidence"]

        # High-conviction override: LSTM fires alone at >=65%
        if lstm_conf >= 0.65:
            return Signal(
                direction=lstm_dir,
                confidence=lstm_conf,
                agreement=1,
                reason=f"{lstm_key} high-conviction ({lstm_conf:.0%})",
                votes=votes,
            )

        # Standard: LSTM >=50% AND any tree model agrees on direction
        if lstm_conf >= 0.50:
            tree_types = [k for k in votes if k not in ("lstm", "lstm_transformer")]
            for tree_key in tree_types:
                tree_vote = votes[tree_key]
                if tree_vote["direction"] == lstm_dir and tree_vote["confidence"] >= 0.50:
                    # Tree confirms LSTM signal
                    return Signal(
                        direction=lstm_dir,
                        confidence=lstm_conf,
                        agreement=2,
                        reason=f"{lstm_key} + {tree_key} confirmed",
                        votes=votes,
                    )

        self._rejection_stats["low_confidence"] += 1
        return None

    def _predict_sequence_model(self, mtype: str, data: dict, sequence: np.ndarray) -> tuple[int, float]:
        """Route to the correct sequence model prediction method."""
        wrapper = data.get("model", data)
        model_type = wrapper.get("model_type", "lstm")  # old wrappers default to "lstm"

        if model_type == "lstm_transformer":
            return self._predict_lstm_transformer(data, sequence)
        else:
            return self._predict_lstm(data, sequence)

    def _predict_lstm_transformer(self, data: dict, sequence: np.ndarray) -> tuple[int, float]:
        """Predict using LSTM-Transformer model wrapper."""
        try:
            import torch
            from app.services.ml.lstm_transformer import load_lstm_transformer_from_wrapper

            wrapper = data["model"]
            seq_len = wrapper["seq_len"]
            mean = wrapper["mean"]
            std = wrapper["std"]
            std_safe = np.where(std == 0, 1.0, std)

            # Normalize and create tensor
            seq = (sequence[-seq_len:] - mean) / std_safe
            seq_tensor = torch.FloatTensor(seq).unsqueeze(0)

            # Load and run model
            model = load_lstm_transformer_from_wrapper(wrapper)
            with torch.no_grad():
                proba = model.predict_proba(seq_tensor)[0].numpy()
                pred = int(np.argmax(proba))
                conf = float(proba[pred])
            return pred, conf
        except Exception:
            return 1, 0.0  # hold with 0 confidence

    def _predict_lstm(self, data: dict, sequence: np.ndarray) -> tuple[int, float]:
        """Predict using LSTM model wrapper."""
        try:
            import torch
            import torch.nn as nn

            wrapper = data["model"]
            input_size = wrapper["input_size"]
            seq_len = wrapper["seq_len"]
            mean = wrapper["mean"]
            std = wrapper["std"]

            # Normalize
            seq = (sequence[-seq_len:] - mean) / std
            seq_tensor = torch.FloatTensor(seq).unsqueeze(0)

            # Rebuild model
            class LSTMClassifier(nn.Module):
                def __init__(self, inp, h1=128, h2=64, nc=3, drop=0.3):
                    super().__init__()
                    self.lstm1 = nn.LSTM(inp, h1, batch_first=True)
                    self.drop1 = nn.Dropout(drop)
                    self.lstm2 = nn.LSTM(h1, h2, batch_first=True)
                    self.drop2 = nn.Dropout(drop)
                    self.fc = nn.Linear(h2, nc)

                def forward(self, x):
                    out, _ = self.lstm1(x)
                    out = self.drop1(out)
                    out, _ = self.lstm2(out)
                    out = self.drop2(out[:, -1, :])
                    return self.fc(out)

            model = LSTMClassifier(input_size)
            state_dict = {k: torch.FloatTensor(v) for k, v in wrapper["state_dict"].items()}
            model.load_state_dict(state_dict)
            model.eval()

            with torch.no_grad():
                output = model(seq_tensor)
                proba = torch.softmax(output, dim=1)[0].numpy()
                pred = int(np.argmax(proba))
                conf = float(proba[pred])
            return pred, conf
        except Exception:
            return 1, 0.0  # hold with 0 confidence

    def get_rejection_stats(self) -> dict:
        return dict(self._rejection_stats)

    def get_eval_count(self) -> int:
        return self._eval_count
