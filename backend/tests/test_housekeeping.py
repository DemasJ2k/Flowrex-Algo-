"""
Tests for the daily housekeeping job (Batch 3, audit C15).
"""
import os
import shutil
import time
from datetime import datetime, timezone, timedelta

import pytest

from app.services.housekeeping import (
    purge_old_agent_logs,
    purge_old_access_requests,
    purge_orphaned_backtest_tempdirs,
    AGENT_LOGS_RETENTION_DAYS,
    BACKTEST_TMP_ROOT,
)


def test_purge_old_agent_logs_callable_and_safe():
    """
    purge_old_agent_logs must be callable without crashing.

    Note: this calls the live SessionLocal — in a test environment that's
    SQLite in-memory. It returns 0 if the table doesn't exist OR if no rows
    qualify. The point is to verify the function imports cleanly, handles
    missing-table errors gracefully, and doesn't raise.
    """
    deleted = purge_old_agent_logs()
    assert isinstance(deleted, int)
    assert deleted >= 0


def test_purge_orphaned_backtest_tempdirs():
    """Tempdirs older than 24h are removed; newer ones survive."""
    os.makedirs(BACKTEST_TMP_ROOT, exist_ok=True)

    # Old tempdir
    old_dir = os.path.join(BACKTEST_TMP_ROOT, "test-old-orphan")
    os.makedirs(old_dir, exist_ok=True)
    with open(os.path.join(old_dir, "marker.txt"), "w") as f:
        f.write("")
    # Set mtime to 25h ago
    old_time = time.time() - (25 * 3600)
    os.utime(old_dir, (old_time, old_time))

    # Fresh tempdir
    new_dir = os.path.join(BACKTEST_TMP_ROOT, "test-fresh")
    os.makedirs(new_dir, exist_ok=True)

    try:
        deleted = purge_orphaned_backtest_tempdirs()
        assert deleted >= 1
        assert not os.path.exists(old_dir)
        assert os.path.exists(new_dir)
    finally:
        shutil.rmtree(new_dir, ignore_errors=True)
        shutil.rmtree(old_dir, ignore_errors=True)
