"""Integration tests for broker API endpoints with FakeBrokerAdapter."""


# ── Tests with no broker connected (backward compat) ──────────────────


def test_status_disconnected(client):
    resp = client.get("/api/broker/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["broker"] is None


def test_positions_empty_when_disconnected(client):
    resp = client.get("/api/broker/positions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_candles_empty_when_disconnected(client):
    resp = client.get("/api/broker/candles/BTCUSD")
    assert resp.status_code == 200
    assert resp.json() == []


def test_account_defaults_when_disconnected(client):
    resp = client.get("/api/broker/account")
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance"] == 0.0
    assert data["equity"] == 0.0


def test_order_fails_when_disconnected(client):
    resp = client.post("/api/broker/order", json={
        "symbol": "XAUUSD", "direction": "BUY", "size": 0.1,
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is False


# ── Tests with fake broker connected ──────────────────────────────────


def test_status_connected(client_with_broker):
    resp = client_with_broker.get("/api/broker/status")
    data = resp.json()
    assert data["connected"] is True
    assert data["broker"] == "fake"


def test_account_when_connected(client_with_broker):
    resp = client_with_broker.get("/api/broker/account")
    data = resp.json()
    assert data["balance"] == 10000.0
    assert data["equity"] == 10500.0
    assert data["unrealized_pnl"] == 500.0


def test_positions_when_connected(client_with_broker):
    resp = client_with_broker.get("/api/broker/positions")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "XAUUSD"
    assert data[0]["pnl"] == 100.0


def test_orders_when_connected(client_with_broker):
    resp = client_with_broker.get("/api/broker/orders")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "BTCUSD"


def test_candles_when_connected(client_with_broker):
    resp = client_with_broker.get("/api/broker/candles/XAUUSD?timeframe=M5&count=100")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["open"] == 2000.0


def test_symbols_when_connected(client_with_broker):
    resp = client_with_broker.get("/api/broker/symbols")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "XAUUSD"


def test_place_order_when_connected(client_with_broker):
    resp = client_with_broker.post("/api/broker/order", json={
        "symbol": "XAUUSD", "direction": "BUY", "size": 0.1,
    })
    data = resp.json()
    assert data["success"] is True
    assert data["order_id"] == "TEST123"


def test_close_position_when_connected(client_with_broker):
    resp = client_with_broker.post("/api/broker/close/POS1")
    data = resp.json()
    assert data["success"] is True
    assert data["pnl"] == 50.0


def test_modify_order_when_connected(client_with_broker):
    resp = client_with_broker.put("/api/broker/modify/ORD1", json={"sl": 1990.0, "tp": 2060.0})
    data = resp.json()
    assert data["success"] is True
