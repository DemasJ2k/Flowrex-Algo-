from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    timeframe: str
    model_type: str
    pipeline: str
    grade: Optional[str] = None
    metrics: dict = {}
    trained_at: Optional[datetime] = None


class TrainRequest(BaseModel):
    symbol: str
    timeframe: str = "M5"
    pipeline: str


class RetrainRequest(BaseModel):
    symbol: str
    n_trials: int = 25
    # Default lowered from 12 → 6 months (2026-04-20). Shorter window tracks
    # recent regime better — the 12m window included the 2024-11 regime
    # break that made ES/NAS100 models Grade F. If you genuinely want a
    # longer window, set this explicitly.
    train_months: int = 6
    holdout_days: int = 14
    # Which pipeline to retrain. "flowrex_v2" uses the 120-feature
    # retrain_monthly flow with grade-gated swap. "potential" uses the
    # 85-feature train_potential.py walk-forward and writes unconditionally
    # (no swap guard — check the new grade before re-enabling the agent).
    pipeline: str = "flowrex_v2"
    # If True, delta-merge Dukascopy into the persistent CSV before training
    # so the last bars the model sees match what's actually happening now.
    # Off by default — saves 5-25s of Dukascopy latency on each retrain.
    refresh_dukascopy: bool = False


class RetrainRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    triggered_by: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    old_grade: Optional[str] = None
    old_sharpe: Optional[float] = None
    old_metrics: Optional[dict] = None
    new_grade: Optional[str] = None
    new_sharpe: Optional[float] = None
    new_metrics: Optional[dict] = None
    swapped: bool = False
    swap_reason: Optional[str] = None
    error_message: Optional[str] = None
    training_config: Optional[dict] = None


class RetrainScheduleResponse(BaseModel):
    enabled: bool = False
    cron_expression: str = "0 0 1 * *"
    next_run: Optional[str] = None
