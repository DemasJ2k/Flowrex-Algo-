from app.models.user import User
from app.models.agent import TradingAgent
from scripts.seed import run_seed


def test_seed_creates_admin_and_agents(db_session):
    result = run_seed(db_session)
    assert result["status"] == "created"
    assert result["agents_created"] == 6

    users = db_session.query(User).all()
    assert len(users) == 1
    assert users[0].email == "admin@flowrex.local"
    assert users[0].is_admin is True

    agents = db_session.query(TradingAgent).all()
    assert len(agents) == 6
    symbols = {a.symbol for a in agents}
    assert symbols == {"BTCUSD", "XAUUSD", "US30"}


def test_seed_is_idempotent(db_session):
    run_seed(db_session)
    result = run_seed(db_session)
    assert result["status"] == "skipped"

    # Still only 1 user
    assert db_session.query(User).count() == 1
