"""Unit tests for MT5 filling mode auto-detection."""
import pytest
from unittest.mock import patch, MagicMock

from app.services.broker.mt5 import MT5_AVAILABLE

pytestmark = pytest.mark.skipif(not MT5_AVAILABLE, reason="MetaTrader5 not installed")


@pytest.fixture
def mock_mt5():
    import MetaTrader5 as mt5
    return mt5


def test_filling_candidates_ioc_first_when_supported(mock_mt5):
    """IOC (bit 1) should be first candidate when supported."""
    from app.services.broker.mt5 import _get_filling_candidates

    sym_info = MagicMock()
    sym_info.filling_mode = 2  # IOC only

    with patch.object(mock_mt5, "symbol_info", return_value=sym_info):
        result = _get_filling_candidates("XAUUSD")
    assert result[0] == mock_mt5.ORDER_FILLING_IOC


def test_filling_candidates_fok_when_ioc_not_available(mock_mt5):
    """FOK (bit 0) should appear before fallbacks when IOC not supported."""
    from app.services.broker.mt5 import _get_filling_candidates

    sym_info = MagicMock()
    sym_info.filling_mode = 1  # FOK only

    with patch.object(mock_mt5, "symbol_info", return_value=sym_info):
        result = _get_filling_candidates("BTCUSD")
    assert result[0] == mock_mt5.ORDER_FILLING_FOK


def test_filling_candidates_always_returns_all_three(mock_mt5):
    """Candidate list always contains IOC, FOK, and RETURN."""
    from app.services.broker.mt5 import _get_filling_candidates

    sym_info = MagicMock()
    sym_info.filling_mode = 0  # No bits set

    with patch.object(mock_mt5, "symbol_info", return_value=sym_info):
        result = _get_filling_candidates("US30")

    assert mock_mt5.ORDER_FILLING_IOC in result
    assert mock_mt5.ORDER_FILLING_FOK in result
    assert mock_mt5.ORDER_FILLING_RETURN in result
    assert len(result) == 3


def test_filling_candidates_fallback_on_no_symbol(mock_mt5):
    """Should still return all 3 candidates when symbol_info returns None."""
    from app.services.broker.mt5 import _get_filling_candidates

    with patch.object(mock_mt5, "symbol_info", return_value=None):
        result = _get_filling_candidates("INVALID")

    assert len(result) == 3
    assert mock_mt5.ORDER_FILLING_IOC in result


def test_filling_candidates_ioc_before_fok_when_both_available(mock_mt5):
    """When both IOC and FOK are supported, IOC should be first (better for market orders)."""
    from app.services.broker.mt5 import _get_filling_candidates

    sym_info = MagicMock()
    sym_info.filling_mode = 3  # FOK + IOC (bits 0+1)

    with patch.object(mock_mt5, "symbol_info", return_value=sym_info):
        result = _get_filling_candidates("EURUSD")

    assert result[0] == mock_mt5.ORDER_FILLING_IOC
    assert result[1] == mock_mt5.ORDER_FILLING_FOK


def test_filling_candidates_no_duplicates(mock_mt5):
    """Candidate list should have no duplicate entries."""
    from app.services.broker.mt5 import _get_filling_candidates

    sym_info = MagicMock()
    sym_info.filling_mode = 3  # FOK + IOC

    with patch.object(mock_mt5, "symbol_info", return_value=sym_info):
        result = _get_filling_candidates("BTCUSD")

    assert len(result) == len(set(result))


def test_filling_candidates_boc_not_included(mock_mt5):
    """BOC (bit 2) should not be in the candidate list — not valid for market orders."""
    from app.services.broker.mt5 import _get_filling_candidates

    sym_info = MagicMock()
    sym_info.filling_mode = 7  # FOK + IOC + BOC (bits 0+1+2)

    with patch.object(mock_mt5, "symbol_info", return_value=sym_info):
        result = _get_filling_candidates("ES")

    # BOC constant is 3 in MT5
    assert mock_mt5.ORDER_FILLING_BOC not in result
