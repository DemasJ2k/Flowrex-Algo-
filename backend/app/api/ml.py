from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os
import glob as glob_mod

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.models.ml import MLModel, RetrainRun
from app.schemas.ml import (
    TrainRequest, ModelResponse,
    RetrainRequest, RetrainRunResponse, RetrainScheduleResponse,
)

router = APIRouter(prefix="/api/ml", tags=["ml"])

# Path to model files
_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ml_models")

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


# ── Potential Agent v2 Model Metadata ────────────────────────────────────

# Asset class lookup
_ASSET_CLASS = {
    "BTCUSD": "Crypto",
    "XAUUSD": "Commodities",
    "US30": "Indices",
    "ES": "Indices",
    "NAS100": "Indices",
}

# Best model per symbol (from walk-forward selection)
_BEST_MODEL = {
    "US30": "lightgbm",
    "BTCUSD": "lightgbm",
    "XAUUSD": "xgboost",
    "ES": "xgboost",
    "NAS100": "lightgbm",
}

# Data source per symbol
_DATA_SOURCE = {
    "US30": "History Data (2010-2025)",
    "BTCUSD": "History Data (2020-2025)",
    "XAUUSD": "History Data (2010-2025)",
    "ES": "Databento (Dec 2024-Mar 2026)",
    "NAS100": "Databento (Dec 2024-Mar 2026)",
}


def _extract_feature_importance(model_obj, feature_names: list[str], top_n: int = 10) -> list[dict]:
    """Extract top N feature importances from a trained model object."""
    try:
        if hasattr(model_obj, "feature_importances_"):
            # LightGBM — importance is split count (int)
            fi = list(zip(feature_names, [float(v) for v in model_obj.feature_importances_]))
        elif hasattr(model_obj, "get_booster"):
            # XGBoost — use gain importance
            booster = model_obj.get_booster()
            scores = booster.get_score(importance_type="gain")
            fi = [(name, scores.get(name, 0.0)) for name in feature_names]
        else:
            return []

        fi.sort(key=lambda x: x[1], reverse=True)
        top = fi[:top_n]
        # Normalize to 0-1 range for display
        max_val = top[0][1] if top and top[0][1] > 0 else 1.0
        return [{"feature": name, "importance": round(val / max_val, 4)} for name, val in top]
    except Exception:
        return []


@router.get("/potential-models")
def list_potential_models(
    current_user: User = Depends(get_current_user),
):
    """
    Scan potential_*.joblib model files and return metadata without
    loading models into memory for prediction. Returns the 'best' model
    per symbol plus feature importance from tree splits.
    """
    import joblib

    model_dir = os.path.abspath(_MODEL_DIR)
    pattern = os.path.join(model_dir, "potential_*_M5_*.joblib")
    files = glob_mod.glob(pattern)

    # Group files by symbol
    symbol_files: dict[str, list[str]] = {}
    for f in files:
        basename = os.path.basename(f)  # potential_US30_M5_lightgbm.joblib
        parts = basename.replace(".joblib", "").split("_")
        # potential_{SYMBOL}_M5_{model_type}
        if len(parts) >= 4:
            sym = parts[1]
            symbol_files.setdefault(sym, []).append(f)

    results = []
    for sym, sym_files in symbol_files.items():
        # Pick the 'best' model type for this symbol
        best_type = _BEST_MODEL.get(sym, "lightgbm")
        best_file = None
        for f in sym_files:
            if best_type in os.path.basename(f).lower():
                best_file = f
                break
        if not best_file:
            best_file = sym_files[0]

        try:
            data = joblib.load(best_file)
        except Exception:
            continue

        oos = data.get("oos_metrics", {})
        feature_names = data.get("feature_names", [])
        model_obj = data.get("model")

        # Extract feature importance from the actual model
        top_features = _extract_feature_importance(model_obj, feature_names) if model_obj else []

        # Determine model type from the filename
        model_type = "LightGBM" if "lightgbm" in os.path.basename(best_file) else "XGBoost"

        results.append({
            "symbol": sym,
            "asset_class": _ASSET_CLASS.get(sym, "Unknown"),
            "model_type": model_type,
            "grade": data.get("grade", "?"),
            "sharpe": round(oos.get("sharpe", 0), 2),
            "win_rate": round(oos.get("win_rate", 0), 1),
            "max_drawdown": round(oos.get("max_drawdown", 0), 2),
            "total_return": round(oos.get("total_return", 0), 2),
            "profit_factor": round(oos.get("profit_factor", 0), 2),
            "total_trades": oos.get("total_trades", 0),
            "accuracy": round(oos.get("accuracy", 0), 4),
            "pipeline_version": data.get("pipeline_version", "unknown"),
            "trained_at": data.get("trained_at", ""),
            "feature_count": len(feature_names),
            "data_source": _DATA_SOURCE.get(sym, "Unknown"),
            "top_features": top_features,
            "oos_start": data.get("oos_start", ""),
            "file_path": os.path.basename(best_file),
        })

    # Sort: US30 first, then alphabetically
    priority = {"US30": 0, "BTCUSD": 1, "XAUUSD": 2, "ES": 3, "NAS100": 4}
    results.sort(key=lambda x: priority.get(x["symbol"], 99))

    return results


@router.get("/flowrex-models")
def list_flowrex_models(
    current_user: User = Depends(get_current_user),
):
    """Scan flowrex_*.joblib model files and return metadata."""
    import joblib

    model_dir = os.path.abspath(_MODEL_DIR)
    pattern = os.path.join(model_dir, "flowrex_*_M5_*.joblib")
    files = glob_mod.glob(pattern)

    symbol_files: dict[str, list[str]] = {}
    for f in files:
        basename = os.path.basename(f)
        parts = basename.replace(".joblib", "").split("_")
        if len(parts) >= 4:
            sym = parts[1]
            symbol_files.setdefault(sym, []).append(f)

    results = []
    for sym, sym_files in symbol_files.items():
        best_file = sym_files[0]
        for f in sym_files:
            if "xgboost" in os.path.basename(f).lower():
                best_file = f
                break

        try:
            data = joblib.load(best_file)
        except Exception:
            continue

        oos = data.get("oos_metrics", {})
        feature_names = data.get("feature_names", [])
        model_obj = data.get("model")
        top_features = _extract_feature_importance(model_obj, feature_names) if model_obj else []
        model_type = "XGBoost"
        if "lightgbm" in os.path.basename(best_file):
            model_type = "LightGBM"
        elif "catboost" in os.path.basename(best_file):
            model_type = "CatBoost"

        results.append({
            "symbol": sym,
            "asset_class": _ASSET_CLASS.get(sym, "Unknown"),
            "model_type": model_type,
            "grade": data.get("grade", "?"),
            "sharpe": round(oos.get("sharpe", 0), 2),
            "win_rate": round(oos.get("win_rate", 0), 1),
            "max_drawdown": round(oos.get("max_drawdown", 0), 2),
            "total_return": round(oos.get("total_return", 0), 2),
            "profit_factor": round(oos.get("profit_factor", 0), 2),
            "total_trades": oos.get("total_trades", 0),
            "accuracy": round(oos.get("accuracy", 0), 4),
            "pipeline_version": data.get("pipeline_version", "unknown"),
            "trained_at": data.get("trained_at", ""),
            "feature_count": len(feature_names),
            "data_source": _DATA_SOURCE.get(sym, "Unknown"),
            "top_features": top_features,
            "oos_start": data.get("oos_start", ""),
            "file_path": os.path.basename(best_file),
            "ensemble_models": len(sym_files),
        })

    priority = {"US30": 0, "BTCUSD": 1, "XAUUSD": 2, "ES": 3, "NAS100": 4}
    results.sort(key=lambda x: priority.get(x["symbol"], 99))
    return results


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

    _retrain_status.update({"active": True, "symbol": body.symbol, "progress": "starting",
                            "pipeline": body.pipeline, "results": []})

    def _run_retrain():
        try:
            # Optional: refresh the persistent CSV via Dukascopy delta-merge
            # before training. Typical cost ~5-25 s; noticeably more on the
            # first fetch for a symbol. Skipped by default.
            if body.refresh_dukascopy:
                _retrain_status["progress"] = "refreshing Dukascopy data"
                try:
                    from app.services.backtest.data_fetcher import get_backtest_fetcher
                    bundle = get_backtest_fetcher().fetch(
                        body.symbol, days=2500, timeframes=["M5", "H1", "H4", "D1"],
                    )
                    _retrain_status["progress"] = (
                        f"Dukascopy refresh: +{bundle.new_rows:,} bars"
                        if not bundle.bootstrap
                        else f"Dukascopy bootstrap: {bundle.new_rows:,} bars"
                    )
                except Exception as _dk_err:
                    _retrain_status["progress"] = f"Dukascopy refresh failed ({_dk_err}) — using existing CSV"

            pipeline = (body.pipeline or "flowrex_v2").lower()
            if pipeline == "potential":
                # Potential pipeline: walk-forward training with no swap-guard.
                # Writes directly to potential_{SYMBOL}_M5_*.joblib. Grade is
                # stamped into the joblib; user checks Models list to verify.
                _retrain_status["progress"] = f"training potential_{body.symbol}"
                from scripts.train_potential import run_potential_training
                from datetime import datetime, timezone, timedelta
                # Approximate months of training data. Potential runs
                # walk-forward internally; train_start just trims the
                # available data before the 4-fold split.
                train_start = (datetime.now(timezone.utc)
                               - timedelta(days=int(body.train_months * 30.44))).strftime("%Y-%m-%d")
                wf_results, oos_results = run_potential_training(
                    body.symbol, n_trials=body.n_trials, n_folds=4,
                    train_start=train_start,
                )
                # Synthesise a retrain-history record using OOS best-model grade
                best_grade = "?"
                best_sharpe = 0.0
                if oos_results:
                    best = max(oos_results, key=lambda x: x.get("sharpe", -999))
                    best_grade = best.get("grade", "?")
                    best_sharpe = float(best.get("sharpe", 0.0))
                result = {
                    "symbol": body.symbol, "triggered_by": "manual",
                    "status": "completed", "swapped": True,
                    "swap_reason": "potential pipeline writes unconditionally",
                    "new_grade": best_grade, "new_sharpe": best_sharpe,
                    "training_config": {
                        "pipeline": "potential",
                        "n_trials": body.n_trials,
                        "train_months": body.train_months,
                        "train_start": train_start,
                    },
                }
                from scripts.retrain_monthly import _record_retrain_run as _rec
                _rec(result)
                _retrain_status["results"] = [result]
                _retrain_status["progress"] = f"done (grade {best_grade}, sharpe {best_sharpe:.2f})"
                try:
                    from app.services.agent.engine import get_algo_engine
                    get_algo_engine().reload_models_for_symbol(body.symbol)
                except Exception:
                    pass
            else:
                # Default: flowrex_v2 retrain_monthly flow with grade-gated swap
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
    return {"status": "started", "symbol": body.symbol, "pipeline": body.pipeline}


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
    """List past retrain runs scoped to symbols the caller owns agents on.

    RetrainRun has no user_id column; scoping by owned symbols keeps the
    history relevant without leaking other users' retrains.
    """
    from app.models.agent import TradingAgent
    owned = {a.symbol for a in db.query(TradingAgent.symbol).filter(
        TradingAgent.created_by == current_user.id,
        TradingAgent.deleted_at.is_(None),
    ).distinct().all()}
    if not owned:
        return []
    q = (
        db.query(RetrainRun)
        .filter(RetrainRun.symbol.in_(owned))
        .order_by(RetrainRun.started_at.desc())
    )
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


# ── Unified ML dashboard endpoints (2026-04-21 rework) ──────────────────


@router.get("/symbols")
def list_symbols_unified(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Symbol-first unified view for the Models page rework.

    Returns one row per symbol. Each row merges:
      - Every deployed model variant (potential + flowrex_v2, per ensemble
        member). Sorted by pipeline then model_type.
      - 14-day live trade performance from agent_trades.
      - Which trading agents target this symbol (with their current status).
      - Last retrain run metadata for the symbol.

    UI uses this as the SINGLE source of truth for the Models page grid.
    Existing /api/ml/potential-models and /api/ml/flowrex-models remain
    untouched for anything else that depends on them.
    """
    import joblib
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import and_

    from app.models.agent import TradingAgent, AgentTrade

    model_dir = os.path.abspath(_MODEL_DIR)

    # ── 1. Walk model files and group per-symbol ────────────────────────
    all_files = glob_mod.glob(os.path.join(model_dir, "*_M5_*.joblib"))
    per_symbol: dict[str, list[dict]] = {}

    for f in all_files:
        basename = os.path.basename(f)
        parts = basename.replace(".joblib", "").split("_")
        # shape: <pipeline>_<SYMBOL>_M5_<model_type>[_...suffix]
        if len(parts) < 4:
            continue
        pipeline, sym, _tf = parts[0], parts[1], parts[2]
        model_type = "_".join(parts[3:])  # catch suffixes
        try:
            data = joblib.load(f)
        except Exception:
            continue

        oos = (data.get("oos_metrics") or {})
        per_symbol.setdefault(sym, []).append({
            "pipeline": pipeline,                     # "potential" | "flowrex"
            "model_type": model_type,                 # xgboost / lightgbm / catboost
            "grade": data.get("grade", "?"),
            "sharpe": round(float(oos.get("sharpe", 0) or 0), 2),
            "win_rate": round(float(oos.get("win_rate", 0) or 0), 1),
            "max_drawdown": round(float(oos.get("max_drawdown", 0) or 0), 2),
            "profit_factor": round(float(oos.get("profit_factor", 0) or 0), 2),
            "total_trades": int(oos.get("total_trades", 0) or 0),
            "trained_at": data.get("trained_at", ""),
            "oos_start": data.get("oos_start", ""),
            "feature_count": len(data.get("feature_names", [])),
            "pipeline_version": data.get("pipeline_version", ""),
            "file": basename,
        })

    # ── 2. 14-day live trade stats per symbol (current user only) ──────
    # SECURITY: previously this query joined AgentTrade↔TradingAgent without
    # filtering by created_by — every user saw every other user's trades in
    # the Models page "live P&L" column. Scoped to the caller's agents now.
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    live_trades = (
        db.query(AgentTrade, TradingAgent)
        .join(TradingAgent, AgentTrade.agent_id == TradingAgent.id)
        .filter(
            TradingAgent.created_by == current_user.id,
            TradingAgent.deleted_at.is_(None),
            AgentTrade.entry_time >= cutoff,
            AgentTrade.status == "closed",
        )
        .all()
    )
    per_symbol_live: dict[str, dict] = {}
    for trade, agent in live_trades:
        sym = trade.symbol
        bucket = per_symbol_live.setdefault(sym, {
            "trades": 0, "wins": 0, "total_pnl": 0.0,
            "wins_pnl": 0.0, "losses_pnl": 0.0,
        })
        bucket["trades"] += 1
        pnl = float(trade.pnl or 0)
        bucket["total_pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
            bucket["wins_pnl"] += pnl
        elif pnl < 0:
            bucket["losses_pnl"] += pnl

    # ── 3. Agents targeting each symbol (current user only) ───────────
    # SECURITY: same leak as above — previously returned all users' agents.
    agents = db.query(TradingAgent).filter(
        TradingAgent.created_by == current_user.id,
        TradingAgent.deleted_at.is_(None),
    ).all()
    per_symbol_agents: dict[str, list[dict]] = {}
    for a in agents:
        per_symbol_agents.setdefault(a.symbol, []).append({
            "id": a.id, "name": a.name, "agent_type": a.agent_type,
            "status": a.status, "broker": a.broker_name,
        })

    # ── 4. Last retrain per symbol ─────────────────────────────────────
    # RetrainRun has no user_id column (migration deferred). Scope to
    # symbols the caller actually owns — cleaner than showing everyone's
    # retrain history, and the model files themselves are global assets
    # anyway so this is a reasonable privacy-vs-utility trade-off.
    user_symbols = {a.symbol for a in agents}
    last_retrain: dict[str, dict] = {}
    if user_symbols:
        recent_retrains = (
            db.query(RetrainRun)
            .filter(RetrainRun.symbol.in_(user_symbols))
            .order_by(RetrainRun.started_at.desc())
            .limit(200).all()
        )
        for r in recent_retrains:
            if r.symbol in last_retrain:
                continue
            last_retrain[r.symbol] = {
                "id": r.id,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "old_grade": r.old_grade, "new_grade": r.new_grade,
                "old_sharpe": r.old_sharpe, "new_sharpe": r.new_sharpe,
                "swapped": r.swapped,
                "error": r.error_message,
            }

    # ── 5. Merge into one row per symbol ────────────────────────────────
    rows = []
    all_syms = set(per_symbol.keys()) | set(per_symbol_agents.keys())
    for sym in all_syms:
        models = sorted(
            per_symbol.get(sym, []),
            key=lambda m: (m["pipeline"], m["model_type"]),
        )
        live = per_symbol_live.get(sym, {})
        trades_count = live.get("trades", 0)
        wr = (live.get("wins", 0) / trades_count * 100) if trades_count else 0.0
        avg_win = (live["wins_pnl"] / live["wins"]) if live.get("wins") else 0.0
        losses_n = trades_count - live.get("wins", 0) if live else 0
        avg_loss = (live["losses_pnl"] / losses_n) if losses_n else 0.0

        rows.append({
            "symbol": sym,
            "asset_class": _ASSET_CLASS.get(sym, "Unknown"),
            "models": models,
            "live_14d": {
                "trades": trades_count,
                "win_rate": round(wr, 1),
                "total_pnl": round(live.get("total_pnl", 0.0), 2),
                "avg_win":  round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
            },
            "agents": per_symbol_agents.get(sym, []),
            "last_retrain": last_retrain.get(sym),
        })

    # Priority order: symbols with agents first, then alpha
    priority = {"US30": 0, "BTCUSD": 1, "XAUUSD": 2, "ES": 3, "NAS100": 4, "ETHUSD": 5,
                "XAGUSD": 6, "AUS200": 7}
    rows.sort(key=lambda r: (priority.get(r["symbol"], 99), r["symbol"]))
    return rows


class MLAnalyzeRequest(BaseModel):
    symbol: str
    pipeline: str = "potential"  # "potential" | "flowrex"


def _format_ml_stats_for_ai(sym_row: dict, pipeline: str) -> str:
    """Pack a symbol row into a compact markdown brief for the supervisor."""
    out = []
    out.append(f"# {sym_row.get('symbol', '?')} · {sym_row.get('asset_class', '?')}")
    out.append("")
    models = [m for m in sym_row.get("models", []) if m.get("pipeline") == pipeline]
    if models:
        out.append(f"## Deployed `{pipeline}` models")
        for m in models:
            out.append(
                f"- {m.get('model_type', '?')}: Grade **{m.get('grade', '?')}**, "
                f"Sharpe {m.get('sharpe', 0)}, WR {m.get('win_rate', 0)}%, "
                f"PF {m.get('profit_factor', 0)}, MaxDD {m.get('max_drawdown', 0)}%, "
                f"trained {str(m.get('trained_at', ''))[:16]} ({m.get('feature_count', 0)} features, "
                f"OOS from {m.get('oos_start', 'n/a')[:10]})"
            )
    else:
        out.append(f"_No `{pipeline}` model deployed for this symbol._")
    out.append("")

    live = sym_row.get("live_14d") or {}
    if live.get("trades"):
        out.append("## Live performance (14 days)")
        out.append(
            f"- {live['trades']} trades · WR {live['win_rate']}% · "
            f"P&L ${live['total_pnl']:,.2f} · "
            f"avg win ${live['avg_win']:,.2f} · avg loss ${live['avg_loss']:,.2f}"
        )
        rr = abs(live["avg_win"] / live["avg_loss"]) if live.get("avg_loss") else 0
        out.append(f"- Risk/reward ratio: {rr:.2f}")
    else:
        out.append("_No live trades in the last 14 days._")
    out.append("")

    agents = sym_row.get("agents", [])
    if agents:
        out.append("## Agents on this symbol")
        for a in agents:
            out.append(f"- id={a['id']} `{a['name']}` ({a['agent_type']}, {a['status']}, {a['broker']})")
    last = sym_row.get("last_retrain")
    if last:
        out.append("")
        out.append("## Last retrain")
        out.append(
            f"- {last.get('started_at', '?')[:16]}: "
            f"{last.get('old_grade')} → {last.get('new_grade')} "
            f"({'swapped' if last.get('swapped') else 'held'}) "
            f"{'· error: ' + last['error'] if last.get('error') else ''}"
        )
    return "\n".join(out)


@router.post("/analyze")
async def analyze_symbol(
    body: MLAnalyzeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    AI insight on a symbol's current model performance. Reuses the
    per-user Claude supervisor — same one the hourly reports use.
    Returns markdown. Requires the user to have configured an API key.
    """
    from app.services.llm.monitoring import _ensure_supervisor_configured
    from app.services.llm.supervisor import get_supervisor

    # Pull the same unified row and filter by pipeline
    rows = list_symbols_unified(current_user=current_user, db=db)  # type: ignore
    row = next((r for r in rows if r["symbol"].upper() == body.symbol.upper()), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"No data for {body.symbol}")

    if not _ensure_supervisor_configured(db, current_user.id):
        raise HTTPException(
            status_code=400,
            detail="AI Supervisor not configured — add your Anthropic API key in Settings.",
        )

    summary = _format_ml_stats_for_ai(row, body.pipeline.lower())
    instruction = (
        "You are reviewing one trading symbol's ML model + live performance. "
        "Using the brief below, produce a concise Markdown report with these "
        "sections. No fluff.\n"
        "  1. **Health verdict** — single-sentence: is the model working, marginal, or broken?\n"
        "  2. **What's working** — which metrics / conditions are producing edge.\n"
        "  3. **What's broken or fragile** — concrete issues (backtest-vs-live gap, "
        "poor R:R, regime drift, low WR).\n"
        "  4. **Recommended action** — exactly one of: {keep running, tune risk, "
        "retrain on recent window, pause and debug}. Justify in 1-2 sentences.\n"
        "  5. **If retrain**: suggest window (months) and whether to use `flowrex_v2` "
        "(grade-gated swap) or `potential` (unconditional write).\n"
        "Keep total length under 300 words. Do NOT invent numbers.\n\n"
        "Brief:\n\n" + summary
    )
    reply = await get_supervisor().chat(current_user.id, instruction, context=None)
    return {"markdown": reply or "_No response from AI._", "symbol": body.symbol, "pipeline": body.pipeline}
