import pytest


def test_get_default_settings(client):
    resp = client.get("/api/settings/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["theme"] == "dark"
    assert data["notifications_enabled"] is True


def test_update_settings(client):
    resp = client.put("/api/settings/", json={"theme": "light"})
    assert resp.status_code == 200
    assert resp.json()["theme"] == "light"

    # Verify persistence
    resp = client.get("/api/settings/")
    assert resp.json()["theme"] == "light"


def test_upsert_settings_twice(client):
    client.put("/api/settings/", json={"theme": "light"})
    resp = client.put("/api/settings/", json={"notifications_enabled": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["theme"] == "light"
    assert data["notifications_enabled"] is False


def test_save_trading_defaults_in_settings_json(client):
    """Trading defaults stored in settings_json.trading should persist and round-trip correctly."""
    trading = {
        "risk_per_trade": 0.0025,
        "max_daily_loss_pct": 0.04,
        "max_positions": 6,
        "cooldown_bars": 3,
    }
    resp = client.put("/api/settings/", json={"settings_json": {"trading": trading}})
    assert resp.status_code == 200

    resp = client.get("/api/settings/")
    assert resp.status_code == 200
    saved = resp.json()["settings_json"]["trading"]
    assert saved["risk_per_trade"] == pytest.approx(0.0025)
    assert saved["max_daily_loss_pct"] == pytest.approx(0.04)
    assert saved["max_positions"] == 6
    assert saved["cooldown_bars"] == 3


def test_save_api_keys_in_settings_json(client):
    """News API keys stored in settings_json.api_keys should persist."""
    api_keys = {"finnhub": "pk_test123", "alphavantage": "AV456", "newsapi": "NA789"}
    resp = client.put("/api/settings/", json={"settings_json": {"api_keys": api_keys}})
    assert resp.status_code == 200

    resp = client.get("/api/settings/")
    saved = resp.json()["settings_json"]["api_keys"]
    assert saved["finnhub"] == "pk_test123"
    assert saved["alphavantage"] == "AV456"
    assert saved["newsapi"] == "NA789"


def test_settings_json_merges_without_overwriting_other_keys(client):
    """Saving trading defaults should not erase previously saved API keys."""
    # First save API keys
    client.put("/api/settings/", json={"settings_json": {"api_keys": {"finnhub": "pk_abc"}}})
    # Then save trading defaults
    client.put("/api/settings/", json={"settings_json": {"api_keys": {"finnhub": "pk_abc"}, "trading": {"risk_per_trade": 0.005}}})

    resp = client.get("/api/settings/")
    sj = resp.json()["settings_json"]
    assert sj["api_keys"]["finnhub"] == "pk_abc"
    assert sj["trading"]["risk_per_trade"] == pytest.approx(0.005)


def test_trading_defaults_boundary_values(client):
    """Edge-case values for trading defaults should be stored correctly."""
    resp = client.put("/api/settings/", json={"settings_json": {"trading": {
        "risk_per_trade": 0.0001,  # 0.01% min
        "max_daily_loss_pct": 0.20,  # 20% max
        "max_positions": 1,
        "cooldown_bars": 0,
    }}})
    assert resp.status_code == 200
    saved = resp.json()["settings_json"]["trading"]
    assert saved["risk_per_trade"] == pytest.approx(0.0001)
    assert saved["max_daily_loss_pct"] == pytest.approx(0.20)
    assert saved["max_positions"] == 1
    assert saved["cooldown_bars"] == 0
