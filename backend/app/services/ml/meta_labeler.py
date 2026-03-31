"""
Meta-labeler: binary classifier that answers "should I take this trade?"
Called after ensemble voting passes.
"""
import os
import numpy as np
import joblib

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ml_models")


class MetaLabeler:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.model = None

    def load(self) -> bool:
        path = os.path.join(MODEL_DIR, f"expert_{self.symbol}_M5_meta_labeler.joblib")
        if not os.path.exists(path):
            return False
        data = joblib.load(path)
        self.model = data.get("model")
        return self.model is not None

    def should_trade(self, features: np.ndarray, direction: int, confidence: float) -> bool:
        """
        Predict whether this trade should be taken.
        Returns True if meta-labeler approves, False otherwise.
        If no model loaded, returns True (fail-open).
        """
        if self.model is None:
            return True

        X = features.reshape(1, -1)
        try:
            pred = self.model.predict(X)[0]
            return bool(pred == 1)
        except Exception:
            return True  # fail-open
