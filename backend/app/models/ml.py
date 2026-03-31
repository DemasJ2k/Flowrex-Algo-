from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Float, Boolean, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class MLModel(Base):
    __tablename__ = "ml_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    model_type = Column(String(50), nullable=False)
    pipeline = Column(String(20), nullable=False)
    file_path = Column(String(500), nullable=False)
    grade = Column(String(5), nullable=True)
    metrics = Column(JSON, default=dict)
    trained_at = Column(DateTime(timezone=True), nullable=False)

    creator = relationship("User")


class RetrainRun(Base):
    """Audit log for every monthly retrain attempt."""
    __tablename__ = "retrain_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    triggered_by = Column(String(50), nullable=False)           # "manual", "schedule", "cli"
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="running")  # running/success/failed/skipped

    # Before/after snapshot
    old_grade = Column(String(5), nullable=True)
    old_sharpe = Column(Float, nullable=True)
    old_metrics = Column(JSON, nullable=True)
    new_grade = Column(String(5), nullable=True)
    new_sharpe = Column(Float, nullable=True)
    new_metrics = Column(JSON, nullable=True)

    # Decision
    swapped = Column(Boolean, default=False)
    swap_reason = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)

    # Config snapshot
    training_config = Column(JSON, nullable=True)  # n_trials, train_months, holdout_days
