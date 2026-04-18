"""
Monthly Retrain Scheduler
=========================
APScheduler-based cron job that auto-triggers retraining on the 1st of each month.
Config persisted in UserSettings.settings_json["retrain_schedule"].
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_scheduler = None
_schedule_config = {"enabled": False, "cron_expression": "0 0 1 * *"}

JOB_ID = "monthly_retrain"


def init_scheduler():
    """Initialize the scheduler on FastAPI startup. Safe to call multiple times."""
    global _scheduler, _schedule_config

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("apscheduler not installed — retrain scheduler disabled. pip install apscheduler")
        return

    # Load persisted schedule config from DB
    try:
        from app.core.database import SessionLocal
        from app.models.user import UserSettings
        db = SessionLocal()
        settings = db.query(UserSettings).first()
        if settings and settings.settings_json:
            saved = settings.settings_json.get("retrain_schedule", {})
            _schedule_config.update(saved)
        db.close()
    except Exception as e:
        logger.warning(f"Could not load retrain schedule from DB: {e}")

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()

    if _schedule_config.get("enabled"):
        _add_job(_schedule_config["cron_expression"])
        logger.info(f"Retrain scheduler active: {_schedule_config['cron_expression']}")
    else:
        logger.info("Retrain scheduler initialized (disabled)")

    # Always-on daily housekeeping: purge old logs, old access requests,
    # orphaned backtest tempdirs. Runs at 03:00 UTC.
    try:
        from app.services.housekeeping import run_daily_housekeeping
        _scheduler.add_job(
            run_daily_housekeeping,
            trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
            id="daily_housekeeping",
            replace_existing=True,
        )
        logger.info("Daily housekeeping scheduled for 03:00 UTC")
    except Exception as e:
        logger.warning(f"Could not schedule daily housekeeping: {e}")

    # Hourly AI monitoring: sends Telegram summaries to users with LLM + Telegram enabled.
    try:
        _scheduler.add_job(
            _run_hourly_monitoring,
            trigger=CronTrigger(minute=0, timezone="UTC"),  # top of each hour
            id="hourly_ai_monitoring",
            replace_existing=True,
            coalesce=True,  # drop missed runs if backend is down
            max_instances=1,
        )
        logger.info("Hourly AI monitoring scheduled")
    except Exception as e:
        logger.warning(f"Could not schedule hourly monitoring: {e}")


def _run_hourly_monitoring():
    """
    APScheduler-invoked wrapper. BackgroundScheduler uses a thread pool, so each
    call runs in its own thread (no main event loop). asyncio.run() creates a
    fresh loop, runs the coroutine, and cleans up — safe even across restarts.

    If a loop is already running in this thread (shouldn't happen), fall back
    to scheduling via the running loop.
    """
    import asyncio
    from app.services.llm.monitoring import hourly_check_all_users
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(hourly_check_all_users(), loop)
        else:
            asyncio.run(hourly_check_all_users())
    except Exception as e:
        logger.error(f"Hourly monitoring error: {e}")


def shutdown_scheduler():
    """Shutdown the scheduler on FastAPI shutdown."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _add_job(cron_expression: str):
    """Add or replace the retrain cron job."""
    if not _scheduler:
        return

    from apscheduler.triggers.cron import CronTrigger

    # Remove existing job if any
    try:
        _scheduler.remove_job(JOB_ID)
    except Exception:
        pass

    # Parse "M H DoM Mon DoW" into CronTrigger kwargs
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        logger.error(f"Invalid cron expression: {cron_expression}")
        return

    trigger = CronTrigger(
        minute=parts[0], hour=parts[1],
        day=parts[2], month=parts[3], day_of_week=parts[4],
        timezone="UTC",
    )
    _scheduler.add_job(_run_scheduled_retrain, trigger, id=JOB_ID, replace_existing=True)


def _run_scheduled_retrain():
    """Executed by APScheduler on cron trigger."""
    logger.info("Scheduled monthly retrain starting...")
    try:
        from scripts.retrain_monthly import retrain_symbol, _record_retrain_run, ALL_SYMBOLS

        for sym in ALL_SYMBOLS:
            logger.info(f"Retraining {sym}...")
            result = retrain_symbol(sym, triggered_by="schedule")
            _record_retrain_run(result)

            if result.get("swapped"):
                try:
                    from app.services.agent.engine import get_algo_engine
                    get_algo_engine().reload_models_for_symbol(sym)
                except Exception:
                    pass

        logger.info("Scheduled monthly retrain complete.")
    except Exception as e:
        logger.error(f"Scheduled retrain failed: {e}")


def get_schedule_info() -> dict:
    """Return current schedule config + next run time."""
    next_run = None
    if _scheduler:
        job = _scheduler.get_job(JOB_ID)
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    return {
        "enabled": _schedule_config.get("enabled", False),
        "cron_expression": _schedule_config.get("cron_expression", "0 0 1 * *"),
        "next_run": next_run,
    }


def update_schedule(cron_expression: str, enabled: bool) -> dict:
    """Update the retrain schedule and persist to DB."""
    global _schedule_config
    _schedule_config = {"enabled": enabled, "cron_expression": cron_expression}

    if _scheduler:
        if enabled:
            _add_job(cron_expression)
        else:
            try:
                _scheduler.remove_job(JOB_ID)
            except Exception:
                pass

    # Persist to UserSettings
    try:
        from app.core.database import SessionLocal
        from app.models.user import UserSettings
        db = SessionLocal()
        settings = db.query(UserSettings).first()
        if settings:
            sj = settings.settings_json or {}
            sj["retrain_schedule"] = _schedule_config
            settings.settings_json = sj
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(settings, "settings_json")
            db.commit()
        db.close()
    except Exception as e:
        logger.warning(f"Could not persist schedule config: {e}")

    return get_schedule_info()
