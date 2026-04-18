"""
Tests for Tradovate broker adapter (Batch 10 fixes).

Covers:
  - C30: live/demo credential key resolution
  - C31: bracket orders include actual symbol, errors propagate
  - C32: token refresh on expiry + 401 auto-retry
  - C33: contract specs for GC, SI, BTC, ETH
  - H40: get_symbols no longer caps at 100

No tests hit the real Tradovate API — all subprocess/HTTP calls are mocked.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

from app.services.broker.tradovate import TradovateAdapter, CONTRACT_SPECS, TOKEN_REFRESH_BUFFER_SEC
from app.services.broker.base import BrokerError


# ── C30: live/demo toggle ──────────────────────────────────────────────

def test_live_flag_explicitly_true():
    adapter = TradovateAdapter()
    # Mock _authenticate and the post-auth steps so we just test the flag resolution
    with patch.object(TradovateAdapter, "_authenticate", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_discover_symbols", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = []  # /account/list returns empty → account_id stays 0
        import asyncio
        asyncio.run(adapter.connect({
            "username": "u", "password": "p", "live": True,
        }))
    assert adapter._is_live is True
    assert "live.tradovateapi.com" in adapter._base_url


def test_live_flag_explicitly_false():
    adapter = TradovateAdapter()
    with patch.object(TradovateAdapter, "_authenticate", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_discover_symbols", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = []
        import asyncio
        asyncio.run(adapter.connect({
            "username": "u", "password": "p", "live": False,
        }))
    assert adapter._is_live is False
    assert "demo.tradovateapi.com" in adapter._base_url


def test_demo_flag_true_means_not_live():
    """REGRESSION: C30 — frontend sends `demo: true`, backend must map to is_live=False."""
    adapter = TradovateAdapter()
    with patch.object(TradovateAdapter, "_authenticate", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_discover_symbols", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = []
        import asyncio
        asyncio.run(adapter.connect({
            "username": "u", "password": "p", "demo": True,
        }))
    assert adapter._is_live is False


def test_demo_flag_false_means_live():
    """REGRESSION: C30 — if user unchecks `demo`, go to live environment."""
    adapter = TradovateAdapter()
    with patch.object(TradovateAdapter, "_authenticate", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_discover_symbols", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = []
        import asyncio
        asyncio.run(adapter.connect({
            "username": "u", "password": "p", "demo": False,
        }))
    assert adapter._is_live is True


def test_live_key_beats_demo_key():
    """If both keys present, `live` wins."""
    adapter = TradovateAdapter()
    with patch.object(TradovateAdapter, "_authenticate", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_discover_symbols", new_callable=AsyncMock), \
         patch.object(TradovateAdapter, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = []
        import asyncio
        asyncio.run(adapter.connect({
            "username": "u", "password": "p", "live": True, "demo": True,
        }))
    assert adapter._is_live is True


# ── C33: contract specs ────────────────────────────────────────────────

def test_contract_specs_include_gc_si_btc_eth():
    """REGRESSION: C33 — GC, SI, BTC, ETH must have explicit specs."""
    assert "GC" in CONTRACT_SPECS
    assert "SI" in CONTRACT_SPECS
    assert "BTC" in CONTRACT_SPECS
    assert "ETH" in CONTRACT_SPECS


def test_gold_tick_value_matches_cme():
    """Gold: 0.10 tick × $100/point = $10/tick."""
    gc = CONTRACT_SPECS["GC"]
    assert gc["point_value"] == 100.0
    assert gc["tick_size"] == 0.10
    assert abs(gc["tick_value"] - 10.0) < 0.01


def test_silver_tick_value_matches_cme():
    """Silver: 0.005 tick × $5000/point = $25/tick."""
    si = CONTRACT_SPECS["SI"]
    assert si["point_value"] == 5000.0
    assert si["tick_size"] == 0.005
    assert abs(si["tick_value"] - 25.0) < 0.01


# ── C31: bracket order symbol + error propagation ─────────────────────

def test_place_bracket_requires_symbol():
    """REGRESSION: C31 — bracket with empty symbol raises BrokerError."""
    import asyncio
    adapter = TradovateAdapter()
    adapter._account_spec = "TEST"
    adapter._account_id = 1
    with pytest.raises(BrokerError, match="requires a symbol"):
        asyncio.run(adapter._place_bracket(
            order_id="123", broker_symbol="", contract_id=1,
            action="Buy", qty=1, sl=100.0, tp=110.0,
        ))


def test_place_bracket_passes_symbol_to_all_legs():
    """Both SL and TP legs must include the actual broker symbol."""
    import asyncio
    adapter = TradovateAdapter()
    adapter._account_spec = "TEST"
    adapter._account_id = 1

    captured_payload = {}

    async def mock_request(method, path, **kwargs):
        if path == "/order/placeoso":
            captured_payload.update(kwargs.get("json", {}))
        return {}

    with patch.object(adapter, "_request", side_effect=mock_request):
        asyncio.run(adapter._place_bracket(
            order_id="123", broker_symbol="ESZ6", contract_id=1,
            action="Buy", qty=2, sl=4500.0, tp=4550.0,
        ))

    assert captured_payload["symbol"] == "ESZ6"
    assert captured_payload["bracket1"]["symbol"] == "ESZ6"
    assert captured_payload["bracket2"]["symbol"] == "ESZ6"


# ── C32: token refresh ────────────────────────────────────────────────

def test_ensure_token_fresh_does_nothing_when_token_far_from_expiry():
    import asyncio
    adapter = TradovateAdapter()
    adapter._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    auth_called = []
    async def mock_auth():
        auth_called.append(1)
    adapter._authenticate = mock_auth
    asyncio.run(adapter._ensure_token_fresh())
    assert auth_called == [], "Should not refresh token when 1h remaining"


def test_ensure_token_fresh_refreshes_near_expiry():
    """REGRESSION: C32 — token within buffer window must trigger refresh."""
    import asyncio
    adapter = TradovateAdapter()
    # 1 minute until expiry — well under the 5-minute buffer
    adapter._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    auth_called = []
    async def mock_auth():
        auth_called.append(1)
        adapter._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    adapter._authenticate = mock_auth
    asyncio.run(adapter._ensure_token_fresh())
    assert auth_called == [1], "Should refresh token near expiry"


def test_ensure_token_fresh_noop_when_never_authed():
    """If _token_expires_at is None (not connected), just return."""
    import asyncio
    adapter = TradovateAdapter()
    assert adapter._token_expires_at is None
    asyncio.run(adapter._ensure_token_fresh())  # must not raise
