"""
LSTM-Transformer hybrid model for financial time series classification.

Architecture:
  Feature Projection (input_size -> d_model)
  -> Bidirectional LSTM (captures sequential order)
  -> Multi-Head Self-Attention (captures long-range dependencies)
  -> Dual Pooling (last-step + attention-weighted)
  -> Classification Head (3 classes: SELL/HOLD/BUY)

Designed for M5 bar sequences (60 bars x 210 features).
Inference < 10ms on CPU for real-time trading.
"""
import numpy as np
import torch
import torch.nn as nn


class LSTMTransformer(nn.Module):
    """
    Hybrid LSTM + Transformer for trade signal prediction.

    Args:
        input_size: features per bar (~210)
        d_model: internal dimension (default 128)
        n_lstm_layers: stacked LSTM layers (default 2)
        n_heads: attention heads (default 4)
        n_transformer_layers: transformer encoder layers (default 1)
        num_classes: output classes (default 3: SELL/HOLD/BUY)
        dropout: dropout rate (default 0.3)
    """

    def __init__(
        self,
        input_size: int,
        d_model: int = 128,
        n_lstm_layers: int = 2,
        n_heads: int = 4,
        n_transformer_layers: int = 1,
        num_classes: int = 3,
        dropout: float = 0.3,
    ):
        super().__init__()
        self.input_size = input_size
        self.d_model = d_model

        # 1. Feature projection: reduce high-dim features to d_model
        self.feature_proj = nn.Sequential(
            nn.Linear(input_size, d_model),
            nn.LayerNorm(d_model),
            nn.GELU(),
            nn.Dropout(dropout),
        )

        # 2. Bidirectional LSTM: captures sequential patterns
        self.lstm = nn.LSTM(
            d_model, d_model // 2, n_lstm_layers,
            bidirectional=True, dropout=dropout if n_lstm_layers > 1 else 0,
            batch_first=True,
        )

        # 3. Transformer encoder: captures long-range dependencies
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 2,
            dropout=dropout, batch_first=True, norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, n_transformer_layers)

        # 4. Attention pooling: weighted combination of all timesteps
        self.attn_pool = nn.Linear(d_model, 1)

        # 5. Classification head
        self.classifier = nn.Sequential(
            nn.LayerNorm(d_model * 2),  # concat of last-step + attn-pooled
            nn.Linear(d_model * 2, d_model),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (batch, seq_len, input_size) — raw feature sequences
        Returns:
            logits: (batch, num_classes)
        """
        # Project features
        x = self.feature_proj(x)           # (batch, seq_len, d_model)

        # LSTM
        lstm_out, _ = self.lstm(x)         # (batch, seq_len, d_model)

        # Transformer attention
        attn_out = self.transformer(lstm_out)  # (batch, seq_len, d_model)

        # Dual pooling
        last_step = attn_out[:, -1, :]     # (batch, d_model) — most recent bar

        attn_weights = torch.softmax(self.attn_pool(attn_out).squeeze(-1), dim=1)  # (batch, seq_len)
        attn_pooled = torch.bmm(attn_weights.unsqueeze(1), attn_out).squeeze(1)    # (batch, d_model)

        # Concat and classify
        combined = torch.cat([last_step, attn_pooled], dim=1)  # (batch, d_model*2)
        return self.classifier(combined)   # (batch, num_classes)

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        """Returns softmax probabilities."""
        with torch.no_grad():
            logits = self.forward(x)
            return torch.softmax(logits, dim=1)


# ── Serialization helpers ────────────────────────────────────────────────

def create_lstm_transformer_wrapper(
    model: LSTMTransformer,
    mean: np.ndarray,
    std: np.ndarray,
    input_size: int,
    seq_len: int,
    d_model: int = 128,
    n_heads: int = 4,
    n_lstm_layers: int = 2,
    n_transformer_layers: int = 1,
) -> dict:
    """
    Create a joblib-serializable wrapper dict.
    Same structure as existing LSTM wrappers + extra keys for architecture.
    """
    return {
        "model_type": "lstm_transformer",
        "state_dict": {k: v.cpu().numpy() for k, v in model.state_dict().items()},
        "input_size": input_size,
        "seq_len": seq_len,
        "mean": mean.reshape(-1),
        "std": std.reshape(-1),
        "d_model": d_model,
        "n_heads": n_heads,
        "n_lstm_layers": n_lstm_layers,
        "n_transformer_layers": n_transformer_layers,
    }


def load_lstm_transformer_from_wrapper(
    wrapper: dict,
    device: str = "cpu",
) -> LSTMTransformer:
    """Reconstruct model from wrapper dict."""
    model = LSTMTransformer(
        input_size=wrapper["input_size"],
        d_model=wrapper.get("d_model", 128),
        n_lstm_layers=wrapper.get("n_lstm_layers", 2),
        n_heads=wrapper.get("n_heads", 4),
        n_transformer_layers=wrapper.get("n_transformer_layers", 1),
    )
    state_dict = {k: torch.tensor(v) for k, v in wrapper["state_dict"].items()}
    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    return model
