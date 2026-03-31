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
    train_months: int = 12
    holdout_days: int = 14


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
