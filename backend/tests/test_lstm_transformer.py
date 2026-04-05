"""Tests for LSTM-Transformer model."""
import pytest
import numpy as np
import torch
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.ml.lstm_transformer import (
    LSTMTransformer,
    create_lstm_transformer_wrapper,
    load_lstm_transformer_from_wrapper,
)


def test_forward_shape():
    model = LSTMTransformer(input_size=210, d_model=128)
    x = torch.randn(4, 60, 210)
    out = model(x)
    assert out.shape == (4, 3)


def test_predict_proba_sums_to_one():
    model = LSTMTransformer(input_size=100, d_model=64, n_heads=2)
    x = torch.randn(8, 30, 100)
    proba = model.predict_proba(x)
    sums = proba.sum(dim=1).numpy()
    np.testing.assert_allclose(sums, 1.0, atol=1e-5)


def test_wrapper_serialization():
    model = LSTMTransformer(input_size=50, d_model=32, n_heads=2)
    mean = np.zeros(50)
    std = np.ones(50)
    wrapper = create_lstm_transformer_wrapper(model, mean, std, 50, 60, 32, 2)
    assert wrapper["model_type"] == "lstm_transformer"
    assert wrapper["input_size"] == 50
    assert wrapper["d_model"] == 32
    assert "state_dict" in wrapper


def test_wrapper_roundtrip_loads():
    model = LSTMTransformer(input_size=50, d_model=32, n_heads=2)
    mean = np.zeros(50)
    std = np.ones(50)
    wrapper = create_lstm_transformer_wrapper(model, mean, std, 50, 60, 32, 2)
    model2 = load_lstm_transformer_from_wrapper(wrapper)
    assert isinstance(model2, LSTMTransformer)
    # Both should produce same-shape output
    x = torch.randn(1, 60, 50)
    out1 = model(x)
    out2 = model2(x)
    assert out1.shape == out2.shape


def test_variable_sequence_length():
    model = LSTMTransformer(input_size=100, d_model=64, n_heads=2)
    for seq_len in [10, 30, 60, 120]:
        x = torch.randn(2, seq_len, 100)
        out = model(x)
        assert out.shape == (2, 3), f"Failed for seq_len={seq_len}"


def test_small_model_params():
    model = LSTMTransformer(input_size=210, d_model=128, n_heads=4)
    total = sum(p.numel() for p in model.parameters())
    assert total < 1_000_000, f"Model too large: {total:,} params"
    assert total > 100_000, f"Model too small: {total:,} params"
