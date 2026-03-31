def test_broker_status(client):
    resp = client.get("/api/broker/status")
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


def test_broker_positions_empty(client):
    resp = client.get("/api/broker/positions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_broker_candles_empty(client):
    resp = client.get("/api/broker/candles/BTCUSD")
    assert resp.status_code == 200
    assert resp.json() == []
