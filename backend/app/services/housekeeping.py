"""
Background housekeeping jobs.

Scheduled via APScheduler (see retrain_scheduler.init_scheduler).
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Retention windows — tune if agent_logs grows faster than expected
AGENT_LOGS_RETENTION_DAYS = 30
ACCESS_REQUESTS_RETENTION_DAYS = 90  # rejected requests — GDPR minimization
BACKTEST_TEMPDIR_RETENTION_HOURS = 24  # see backtest data_fetcher
BACKTEST_TMP_ROOT = "/tmp/flowrex-backtest"


def purge_old_agent_logs() -> int:
    """Delete agent_logs older than AGENT_LOGS_RETENTION_DAYS. Returns count deleted."""
    try:
        from app.core.database import SessionLocal
        from app.models.agent import AgentLog
    except Exception as e:
        logger.warning(f"purge_old_agent_logs: import failed: {e}")
        return 0

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=AGENT_LOGS_RETENTION_DAYS)
        deleted = db.query(AgentLog).filter(AgentLog.created_at < cutoff).delete(
            synchronize_session=False
        )
        db.commit()
        if deleted > 0:
            logger.info(f"purge_old_agent_logs: deleted {deleted} rows older than {cutoff.isoformat()}")
        return deleted
    except Exception as e:
        logger.error(f"purge_old_agent_logs failed: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def purge_old_access_requests() -> int:
    """Delete rejected access_requests older than retention window (GDPR minimization)."""
    try:
        from app.core.database import SessionLocal
        from app.models.feedback import AccessRequest
    except Exception as e:
        logger.warning(f"purge_old_access_requests: import failed: {e}")
        return 0

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ACCESS_REQUESTS_RETENTION_DAYS)
        deleted = db.query(AccessRequest).filter(
            AccessRequest.status == "rejected",
            AccessRequest.created_at < cutoff,
        ).delete(synchronize_session=False)
        db.commit()
        if deleted > 0:
            logger.info(f"purge_old_access_requests: deleted {deleted} rejected requests")
        return deleted
    except Exception as e:
        logger.error(f"purge_old_access_requests failed: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def purge_orphaned_backtest_tempdirs() -> int:
    """Remove orphaned /tmp/flowrex-backtest/* directories older than N hours.

    These are normally cleaned up by the backtest completion hook, but if a
    backtest crashes mid-run the tempdir can be orphaned.
    """
    import os
    import shutil
    import time

    base = BACKTEST_TMP_ROOT
    if not os.path.isdir(base):
        return 0

    deleted = 0
    cutoff = time.time() - (BACKTEST_TEMPDIR_RETENTION_HOURS * 3600)
    try:
        for entry in os.listdir(base):
            path = os.path.join(base, entry)
            try:
                if os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    deleted += 1
            except Exception:
                pass
    except Exception as e:
        logger.error(f"purge_orphaned_backtest_tempdirs failed: {e}")
        return deleted

    if deleted > 0:
        logger.info(f"purge_orphaned_backtest_tempdirs: removed {deleted} stale directories")
    return deleted


def run_daily_housekeeping():
    """Run all housekeeping jobs. Scheduled daily via APScheduler."""
    logger.info("Daily housekeeping starting...")
    n_logs = purge_old_agent_logs()
    n_reqs = purge_old_access_requests()
    n_tmp = purge_orphaned_backtest_tempdirs()
    logger.info(
        f"Daily housekeeping complete: "
        f"agent_logs={n_logs}, access_requests={n_reqs}, tempdirs={n_tmp}"
    )
