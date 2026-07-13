def test_ml_models_empty(client):
    resp = client.get("/api/ml/models")
    assert resp.status_code == 200
    # Returns list (may be empty or have models from previous training)
    assert isinstance(resp.json(), list)


def test_ml_train_starts(client):
    resp = client.post("/api/ml/train", json={
        "symbol": "BTCUSD",
        "pipeline": "scalping",
    })
    assert resp.status_code == 200
    # Legacy pipelines were removed in the 2026-07-13 reorg
    assert resp.json()["status"] == "disabled"


def test_ml_training_status(client):
    resp = client.get("/api/ml/training-status")
    assert resp.status_code == 200
    assert "active" in resp.json()
