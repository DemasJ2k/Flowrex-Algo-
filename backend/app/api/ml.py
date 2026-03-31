from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.models.ml import MLModel, RetrainRun
from app.schemas.ml import (
    TrainRequest, ModelResponse,
    RetrainRequest, RetrainRunResponse, RetrainScheduleResponse,
)

router = APIRouter(prefix="/api/ml", tags=["ml"])

# Simple in-memory training status tracker
_training_status: dict = {"active": False, "symbol": None, "pipeline": None, "progress": ""}
_retrain_status: dict = {"active": False, "symbol": None, "progress": "", "results": []}


@router.get("/models", response_model=list[ModelResponse])
def list_models(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    models = db.query(MLModel).order_by(MLModel.trained_at.desc()).all()
    return models


@router.get("/models/{model_id}", response_model=ModelResponse)
def get_model(
    model_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    model = db.query(MLModel).filter(MLModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return model


@router.post("/train")
def trigger_training(
    body: TrainRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    if _training_status["active"]:
        return {"status": "busy", "message": f"Already training {_training_status['symbol']} ({_training_status['pipeline']})"}

    _training_status.update({"active": True, "symbol": body.symbol, "pipeline": body.pipeline, "progress": "starting"})

    def run_training():
        try:
            _training_status["progress"] = "training"
            if body.pipeline == "scalping":
                from scripts.train_scalping_pipeline import train_symbol
                train_symbol(body.symbol, n_trials=30)
            else:
                from scripts.train_expert_agent import train_symbol
                train_symbol(body.symbol, n_trials=30)
            _training_status["progress"] = "done"
        except Exception as e:
            _training_status["progress"] = f"error: {e}"
        finally:
            _training_status["active"] = False

    background_tasks.add_task(run_training)
    return {"status": "started", "symbol": body.symbol, "pipeline": body.pipeline}


@router.get("/training-status")
def get_training_status(current_user: User = Depends(get_current_user)):
    return _training_status


# ── Monthly Retrain Endpoints ───────────────────────────────────────────────


@router.post("/retrain")
def trigger_retrain(
    body: RetrainRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Trigger a monthly retrain for a single symbol."""
    if _retrain_status["active"] or _training_status["active"]:
        busy = _retrain_status.get("symbol") or _training_status.get("symbol")
        return {"status": "busy", "message": f"Already training {busy}"}

    _retrain_status.update({"active": True, "symbol": body.symbol, "progress": "starting", "results": []})

    def _run_retrain():
        try:
            from scripts.retrain_monthly import retrain_symbol, _record_retrain_run
            result = retrain_symbol(
                body.symbol,
                n_trials=body.n_trials,
                train_months=body.train_months,
                holdout_days=body.holdout_days,
                triggered_by="manual",
                progress_callback=lambda msg: _retrain_status.update({"progress": msg}),
            )
            _record_retrain_run(result)
            _retrain_status["results"] = [result]
            _retrain_status["progress"] = "done"

            # Hot-reload agents
            if result.get("swapped"):
                try:
                    from app.services.agent.engine import get_algo_engine
                    get_algo_engine().reload_models_for_symbol(body.symbol)
                except Exception:
                    pass
        except Exception as e:
            _retrain_status["progress"] = f"error: {e}"
        finally:
            _retrain_status["active"] = False

    background_tasks.add_task(_run_retrain)
    return {"status": "started", "symbol": body.symbol}


@router.post("/retrain/all")
def trigger_retrain_all(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    n_trials: int = Query(25),
    train_months: int = Query(12),
    holdout_days: int = Query(14),
):
    """Retrain all 3 symbols sequentially."""
    if _retrain_status["active"] or _training_status["active"]:
        return {"status": "busy", "message": "Training already in progress"}

    _retrain_status.update({"active": True, "symbol": "ALL", "progress": "starting", "results": []})

    def _run_all():
        from scripts.retrain_monthly import retrain_symbol, _record_retrain_run, ALL_SYMBOLS
        try:
            for sym in ALL_SYMBOLS:
                _retrain_status["symbol"] = sym
                _retrain_status["progress"] = f"retraining {sym}"
                result = retrain_symbol(
                    sym, n_trials=n_trials, train_months=train_months,
                    holdout_days=holdout_days, triggered_by="manual",
                    progress_callback=lambda msg: _retrain_status.update({"progress": f"{sym}: {msg}"}),
                )
                _record_retrain_run(result)
                _retrain_status["results"].append(result)

                if result.get("swapped"):
                    try:
                        from app.services.agent.engine import get_algo_engine
                        get_algo_engine().reload_models_for_symbol(sym)
                    except Exception:
                        pass

            _retrain_status["progress"] = "done"
        except Exception as e:
            _retrain_status["progress"] = f"error: {e}"
        finally:
            _retrain_status["active"] = False

    background_tasks.add_task(_run_all)
    return {"status": "started", "symbols": ["BTCUSD", "XAUUSD", "US30"]}


@router.get("/retrain/status")
def get_retrain_status(current_user: User = Depends(get_current_user)):
    """Poll current retrain progress."""
    return _retrain_status


@router.get("/retrain/history", response_model=list[RetrainRunResponse])
def get_retrain_history(
    symbol: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List past retrain runs."""
    q = db.query(RetrainRun).order_by(RetrainRun.started_at.desc())
    if symbol:
        q = q.filter(RetrainRun.symbol == symbol.upper())
    return q.limit(limit).all()


@router.get("/retrain/schedule", response_model=RetrainScheduleResponse)
def get_retrain_schedule(current_user: User = Depends(get_current_user)):
    """Get current retrain schedule."""
    try:
        from app.services.ml.retrain_scheduler import get_schedule_info
        return get_schedule_info()
    except Exception:
        return {"enabled": False, "cron_expression": "0 0 1 * *", "next_run": None}


@router.post("/retrain/schedule", response_model=RetrainScheduleResponse)
def set_retrain_schedule(
    body: RetrainScheduleResponse,
    current_user: User = Depends(get_current_user),
):
    """Update retrain schedule."""
    try:
        from app.services.ml.retrain_scheduler import update_schedule
        return update_schedule(body.cron_expression, body.enabled)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
