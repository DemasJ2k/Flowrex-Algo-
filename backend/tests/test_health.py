from fastapi.testclient import TestClient
from unittest.mock import patch
from main import app

client = TestClient(app)


def test_health_check_returns_ok():
    """Health endpoint returns 200 with status ok."""
    with patch("main.check_db_connection", return_value=True):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"


def test_health_check_db_disconnected():
    """Health endpoint reports disconnected when DB is down."""
    with patch("main.check_db_connection", return_value=False):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "disconnected"
