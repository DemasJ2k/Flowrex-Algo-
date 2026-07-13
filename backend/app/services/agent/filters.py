"""
Shared trading-filter primitives used by BOTH the live agent and the
backtest simulation.

Extracted in the 2026-07-13 reorg: the UTC session classifier existed as
four verbatim copies (potential_agent, flowrex_agent_v2, twice in
api/backtest). One drift and backtest ≠ live, silently. Keeping a single
implementation makes backtest-live parity structural rather than hopeful.
"""

SESSIONS = ("asian", "london", "ny_open", "ny_close", "off_hours")


def classify_session(hour_utc: int) -> str:
    """Map a UTC hour to the session bucket used across the platform."""
    if hour_utc < 8:
        return "asian"
    if hour_utc < 13:
        return "london"
    if hour_utc < 17:
        return "ny_open"
    if hour_utc < 21:
        return "ny_close"
    return "off_hours"
