from datetime import datetime, timezone
from app.models.agent import TradingAgent, AgentLog, AgentTrade


def _create_agent(client, **overrides):
    payload = {
        "name": "Test Agent",
        "symbol": "BTCUSD",
        "agent_type": "scalping",
        "broker_name": "oanda",
    }
    payload.update(overrides)
    resp = client.post("/api/agents/", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ── CRUD ───────────────────────────────────────────────────────────────


def test_create_agent(client):
    data = _create_agent(client)
    assert data["name"] == "Test Agent"
    assert data["symbol"] == "BTCUSD"
    assert data["status"] == "stopped"
    assert data["trade_count"] == 0
    assert data["total_pnl"] == 0.0


def test_list_agents(client):
    _create_agent(client, name="Agent A")
    _create_agent(client, name="Agent B")
    resp = client.get("/api/agents/")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 2


def test_get_single_agent(client):
    created = _create_agent(client)
    resp = client.get(f"/api/agents/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_update_agent(client):
    created = _create_agent(client)
    resp = client.put(f"/api/agents/{created['id']}", json={"name": "Updated Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


def test_soft_delete_agent(client, db_session, test_user):
    created = _create_agent(client)
    agent_id = created["id"]
    resp = client.delete(f"/api/agents/{agent_id}")
    assert resp.status_code == 200

    # Should not appear in list
    resp = client.get("/api/agents/")
    assert len(resp.json()) == 0

    # DB still has it with deleted_at set
    agent = db_session.get(TradingAgent, agent_id)
    assert agent.deleted_at is not None


def test_get_deleted_agent_returns_404(client):
    created = _create_agent(client)
    client.delete(f"/api/agents/{created['id']}")
    resp = client.get(f"/api/agents/{created['id']}")
    assert resp.status_code == 404


# ── Static routes before parameterized ─────────────────────────────────


def test_engine_logs_route_not_confused_with_id(client):
    """GET /engine-logs should return 200, not try to parse as int ID."""
    resp = client.get("/api/agents/engine-logs")
    assert resp.status_code == 200


def test_all_trades_route_not_confused_with_id(client):
    resp = client.get("/api/agents/all-trades")
    assert resp.status_code == 200


def test_pnl_summary_route_not_confused_with_id(client):
    resp = client.get("/api/agents/pnl-summary")
    assert resp.status_code == 200


# ── Logs and Trades ────────────────────────────────────────────────────


def test_engine_logs_returns_logs(client, db_session, test_user):
    created = _create_agent(client)
    log = AgentLog(
        agent_id=created["id"],
        level="info",
        message="Test log message",
    )
    db_session.add(log)
    db_session.commit()

    resp = client.get("/api/agents/engine-logs")
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) == 1
    assert logs[0]["message"] == "Test log message"


def test_all_trades_returns_trades(client, db_session, test_user):
    created = _create_agent(client)
    trade = AgentTrade(
        agent_id=created["id"],
        symbol="BTCUSD",
        direction="BUY",
        entry_price=50000.0,
        stop_loss=49500.0,
        take_profit=51000.0,
        lot_size=0.01,
        status="open",
        entry_time=datetime.now(timezone.utc),
    )
    db_session.add(trade)
    db_session.commit()

    resp = client.get("/api/agents/all-trades")
    assert len(resp.json()) == 1


def test_pnl_summary(client, db_session, test_user):
    created = _create_agent(client)
    now = datetime.now(timezone.utc)

    # 2 winners, 1 loser
    trades = [
        AgentTrade(agent_id=created["id"], symbol="BTCUSD", direction="BUY",
                   entry_price=50000, stop_loss=49500, take_profit=51000,
                   lot_size=0.01, pnl=100.0, status="closed", entry_time=now, exit_time=now),
        AgentTrade(agent_id=created["id"], symbol="BTCUSD", direction="BUY",
                   entry_price=50000, stop_loss=49500, take_profit=51000,
                   lot_size=0.01, pnl=200.0, status="closed", entry_time=now, exit_time=now),
        AgentTrade(agent_id=created["id"], symbol="BTCUSD", direction="SELL",
                   entry_price=50000, stop_loss=50500, take_profit=49000,
                   lot_size=0.01, pnl=-50.0, status="closed", entry_time=now, exit_time=now),
    ]
    db_session.add_all(trades)
    db_session.commit()

    resp = client.get("/api/agents/pnl-summary")
    assert resp.status_code == 200
    summary = resp.json()
    assert len(summary) == 1
    item = summary[0]
    assert item["trade_count"] == 3
    assert item["total_pnl"] == 250.0
    assert item["win_count"] == 2
    assert item["loss_count"] == 1


# ── Agent start/stop/pause stubs ──────────────────────────────────────


def test_start_stop_pause_stubs(client, db_session):
    created = _create_agent(client)
    aid = created["id"]

    resp = client.post(f"/api/agents/{aid}/start")
    assert resp.json()["status"] == "running"

    resp = client.post(f"/api/agents/{aid}/pause")
    assert resp.json()["status"] == "paused"

    resp = client.post(f"/api/agents/{aid}/stop")
    assert resp.json()["status"] == "stopped"


# ── Agent-specific logs/trades/performance ─────────────────────────────


def test_agent_logs_pagination(client, db_session, test_user):
    created = _create_agent(client)
    for i in range(5):
        db_session.add(AgentLog(agent_id=created["id"], level="info", message=f"Log {i}"))
    db_session.commit()

    resp = client.get(f"/api/agents/{created['id']}/logs?limit=3")
    assert len(resp.json()) == 3


def test_agent_trades_endpoint(client, db_session, test_user):
    created = _create_agent(client)
    now = datetime.now(timezone.utc)
    db_session.add(AgentTrade(
        agent_id=created["id"], symbol="BTCUSD", direction="BUY",
        entry_price=50000, stop_loss=49500, take_profit=51000,
        lot_size=0.01, status="open", entry_time=now,
    ))
    db_session.commit()

    resp = client.get(f"/api/agents/{created['id']}/trades")
    assert len(resp.json()) == 1


def test_agent_performance(client, db_session, test_user):
    created = _create_agent(client)
    now = datetime.now(timezone.utc)
    trades = [
        AgentTrade(agent_id=created["id"], symbol="BTCUSD", direction="BUY",
                   entry_price=50000, stop_loss=49500, take_profit=51000,
                   lot_size=0.01, pnl=100.0, status="closed", entry_time=now, exit_time=now),
        AgentTrade(agent_id=created["id"], symbol="BTCUSD", direction="SELL",
                   entry_price=50000, stop_loss=50500, take_profit=49000,
                   lot_size=0.01, pnl=-30.0, status="closed", entry_time=now, exit_time=now),
    ]
    db_session.add_all(trades)
    db_session.commit()

    resp = client.get(f"/api/agents/{created['id']}/performance")
    perf = resp.json()
    assert perf["closed_trades"] == 2
    assert perf["win_rate"] == 50.0
    assert perf["total_pnl"] == 70.0
    assert perf["best_trade"] == 100.0
    assert perf["worst_trade"] == -30.0


# ── Log clearing ────────────────────────────────────────────────────────


def _seed_logs(db_session, agent_id: int, count: int = 5):
    """Insert test log entries for an agent."""
    logs = [
        AgentLog(agent_id=agent_id, level="info", message=f"Log entry {i}")
        for i in range(count)
    ]
    db_session.add_all(logs)
    db_session.commit()
    return count


def test_clear_logs_deletes_all_entries(client, db_session):
    """DELETE /api/agents/logs should remove all log entries for the user."""
    agent = _create_agent(client)
    _seed_logs(db_session, agent["id"], count=8)

    # Confirm logs exist before clearing
    resp = client.get("/api/agents/engine-logs?limit=100")
    assert len(resp.json()) >= 8

    # Clear them
    resp = client.delete("/api/agents/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] >= 8
    assert "Cleared" in data["message"]


def test_clear_logs_response_contains_count(client, db_session):
    """Cleared count in response should match the number of log rows deleted."""
    agent = _create_agent(client)
    _seed_logs(db_session, agent["id"], count=3)

    resp = client.delete("/api/agents/logs")
    assert resp.status_code == 200
    assert resp.json()["deleted"] >= 3


def test_clear_logs_leaves_trades_intact(client, db_session):
    """Clearing logs must not delete trades or agents."""
    from datetime import timezone
    agent = _create_agent(client)
    _seed_logs(db_session, agent["id"], count=4)

    now = __import__("datetime").datetime.now(timezone.utc)
    db_session.add(AgentTrade(
        agent_id=agent["id"], symbol="BTCUSD", direction="BUY",
        entry_price=50000, stop_loss=49000, take_profit=51000,
        lot_size=0.01, status="closed", entry_time=now, exit_time=now,
    ))
    db_session.commit()

    client.delete("/api/agents/logs")

    # Agent still exists
    assert client.get(f"/api/agents/{agent['id']}").status_code == 200
    # Trade still exists
    trades = client.get(f"/api/agents/{agent['id']}/trades").json()
    assert len(trades) == 1


def test_clear_logs_when_empty_returns_zero(client):
    """Clearing when no logs exist should return deleted=0 without error."""
    resp = client.delete("/api/agents/logs")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0
