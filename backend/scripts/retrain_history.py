"""
Retrain-run history recording, shared by the retrain API and any trainer.

Extracted from the deleted scripts/retrain_monthly.py (2026-07-13 reorg):
the flowrex_v2 monthly-retrain flow is gone, but the potential retrain path
still records its runs to the retrain_runs table through this helper.
"""
from datetime import datetime, timezone


def _record_retrain_run(result: dict):
    """Save retrain result to retrain_runs DB table."""
    try:
        from app.core.database import SessionLocal
        from app.models.ml import RetrainRun

        db = SessionLocal()
        run = RetrainRun(
            symbol=result["symbol"],
            triggered_by=result["triggered_by"],
            started_at=datetime.now(timezone.utc),  # approximate
            finished_at=datetime.now(timezone.utc),
            status=result["status"],
            old_grade=result.get("old_grade"),
            old_sharpe=result.get("old_sharpe"),
            old_metrics=result.get("old_metrics"),
            new_grade=result.get("new_grade"),
            new_sharpe=result.get("new_sharpe"),
            new_metrics=result.get("new_metrics"),
            swapped=result.get("swapped", False),
            swap_reason=result.get("swap_reason"),
            error_message=result.get("error_message"),
            training_config=result.get("training_config"),
        )
        db.add(run)
        db.commit()
        db.close()
    except Exception as e:
        print(f"  Warning: Failed to record retrain run to DB: {e}")
