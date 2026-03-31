# Phase 5 — ML Pipeline

## Objective
Build the complete ML infrastructure: data collection scripts, feature engineering engine (80+ features), model training pipelines (XGBoost, LightGBM, LSTM, meta-labeler, regime detector), model grading, and model persistence. By the end of this phase, trained models exist for BTCUSD, XAUUSD, and US30.

---

## Prompt

```
You are building Flowrex Algo. This is Phase 5 of 10.

READ ARCHITECTURE.md Section 6 (ML Pipeline) for feature engineering details, model specs, and grading criteria.

Phases 1-4 are complete — we have the full backend API, broker adapters, and frontend UI.

### What to build in this phase:

**1. Data Collection**
Create `backend/scripts/collect_data.py`:
- Fetch historical M5 candle data from broker APIs (Oanda preferred for historical data)
- Support all 3 starting symbols: BTCUSD, XAUUSD, US30
- Save to CSV files in `backend/data/` with columns: time, open, high, low, close, volume
- Collect at least 2 years of M5 data (2024-2026) for training
- Also collect H1, H4, D1 candles for multi-timeframe features
- Support incremental collection (append new data, don't re-download everything)
- Also support loading from existing CSV files (user may provide their own data)

**2. Feature Engineering**
Create `backend/app/services/ml/features_mtf.py`:
- Main function: compute_expert_features(m5_bars, h1_bars=None, h4_bars=None, d1_bars=None) -> (feature_names, X_matrix)
- Compute 80+ features from ARCHITECTURE.md Section 6:

Price-based (10+):
  - returns (1-bar, 3-bar, 5-bar, 10-bar)
  - log_returns
  - high_low_range / ATR ratio
  - candle body size (close-open) / range
  - upper_wick_ratio, lower_wick_ratio
  - gap from previous close

Moving Averages (12+):
  - EMA(8, 21, 50, 200)
  - SMA(10, 20, 50)
  - Price distance from each MA (as ratio)
  - EMA crossover signals (8/21, 21/50)

Momentum (10+):
  - RSI(14)
  - Stochastic %K(14,3) and %D
  - MACD line, signal, histogram
  - CCI(20)
  - Williams %R(14)
  - Rate of Change (ROC)

Volatility (8+):
  - ATR(14)
  - ATR ratio (ATR/close)
  - Bollinger Bands: upper, lower, %B, bandwidth
  - Historical volatility (20-bar)
  - Keltner Channel position

Volume (5+):
  - Volume ratio (current / 20-bar avg)
  - Volume trend (5-bar slope)
  - OBV (On-Balance Volume)
  - Volume-price correlation
  - VWAP proxy

Structure (8+):
  - Swing high/low detection (local extremes in 10-bar window)
  - Distance to nearest swing high/low
  - Support/resistance proximity (cluster-based)
  - Break of structure signal
  - Higher high / lower low sequence

Session (8+):
  - hour_sin, hour_cos (cyclical encoding)
  - day_of_week_sin, day_of_week_cos
  - is_london_session, is_ny_session, is_asian_session
  - is_killzone (London/NY open overlap)

Multi-timeframe (10+ when HTF data available):
  - H1 EMA(21) trend direction
  - H1 RSI(14)
  - H1 ATR(14)
  - H4 trend direction
  - H4 RSI
  - D1 bias (above/below 50 EMA)
  - D1 ATR
  - HTF trend alignment score

Handle NaN/Inf gracefully — replace with 0 after computation. Return numpy array.

**3. Technical Indicators Library**
Create `backend/app/services/backtest/indicators.py`:
- Pure Python/numpy implementations (no TA-Lib dependency):
  - ema(values, period)
  - sma(values, period)
  - rsi(values, period)
  - atr(highs, lows, closes, period)
  - macd(values, fast, slow, signal)
  - bollinger_bands(values, period, std_dev)
  - stochastic(highs, lows, closes, k_period, d_period)
  - cci(highs, lows, closes, period)
  - williams_r(highs, lows, closes, period)
  - obv(closes, volumes)

**4. Scalping Training Pipeline**
Create `backend/scripts/train_scalping_pipeline.py`:
- For each symbol (BTCUSD, XAUUSD, US30):
  - Load M5 + H1 CSV data
  - Compute features using compute_expert_features()
  - Create 3-class target: 0=sell, 1=hold, 2=buy
    - Label based on forward returns: if price goes up by more than 1 ATR in next 5-10 bars -> buy(2)
    - If price goes down by more than 1 ATR -> sell(0)
    - Otherwise -> hold(1)
  - Train/test split: walk-forward (train on older data, test on newer)
  - Train XGBoost with Optuna hyperparameter tuning (50-100 trials):
    - Tune: max_depth, learning_rate, n_estimators, subsample, colsample_bytree, min_child_weight
    - Objective: maximize accuracy on test set
  - Train LightGBM with Optuna (50-100 trials):
    - Tune: num_leaves, max_depth, learning_rate, n_estimators, subsample, colsample_bytree
  - Save each model as joblib: {model, feature_names, grade, metrics}
  - File naming: scalping_{SYMBOL}_M5_xgboost.joblib, scalping_{SYMBOL}_M5_lightgbm.joblib

**5. Expert Training Pipeline**
Create `backend/scripts/train_expert_agent.py`:
- For each symbol (BTCUSD, XAUUSD, US30):
  - Same feature engineering as scalping
  - Train XGBoost + LightGBM (same as scalping pipeline)
  - Train LSTM sequence model:
    - Input: last 60 bars of features (sequence)
    - Architecture: LSTM(128) -> Dropout(0.3) -> LSTM(64) -> Dropout(0.3) -> Dense(3, softmax)
    - Train with early stopping, batch size 32, max 100 epochs
    - Save as: expert_{SYMBOL}_M5_lstm.joblib (or .h5/.keras)
  - Train Meta-Labeler:
    - Binary classifier: given the ensemble's prediction, should we trade?
    - Features: same 80+ features + ensemble's predicted direction + ensemble confidence
    - Target: 1 if the trade would have been profitable, 0 otherwise
    - Use XGBoost for the meta-labeler
    - Save as: expert_{SYMBOL}_M5_meta_labeler.joblib
  - Train HMM Regime Detector:
    - Input features: returns, volatility, volume
    - Hidden states: 4 (trending_up, trending_down, ranging, volatile)
    - Use hmmlearn GaussianHMM
    - Save as: expert_{SYMBOL}_M5_regime.joblib

**6. Model Grading**
Implement the grading system from ARCHITECTURE.md Section 6:
- After training, run a backtest simulation on the test set
- Calculate: Sharpe ratio, Win Rate, Max Drawdown, Total Return
- Assign grade: A/B/C/D/F based on thresholds
- Store grade + metrics in the MLModel DB record

**7. Ensemble Engine**
Create `backend/app/services/ml/ensemble_engine.py`:
- EnsembleSignalEngine class:
  - load_models() — load XGB, LGB, LSTM models from disk
  - predict(feature_vector, feature_sequence) -> dict or None
  - Voting logic:
    - Each model predicts independently (3-class: sell/hold/buy)
    - For scalping: any ONE model with >=55% confidence fires
    - For expert: need 2/3 agreement + min 55% weighted confidence
  - Returns: {direction: 1/-1/0, confidence: float, agreement: int, reason: str, votes: dict}
  - Track rejection stats: count rejections by reason (insufficient_models, no_consensus, low_confidence, nan_features, meta_rejected)
  - Log periodic summary every 50 evaluations

**8. Meta-Labeler**
Create `backend/app/services/ml/meta_labeler.py`:
- Load meta-labeler model
- predict(features, direction, confidence) -> bool (should_trade)
- Only called after ensemble voting passes

**9. Regime Detector**
Create `backend/app/services/ml/regime_detector.py`:
- Load HMM model
- predict_regime(bars) -> {regime: str, confidence: float}
- Regimes: "trending_up", "trending_down", "ranging", "volatile"

**10. ML API Endpoints**
Wire up the ML endpoints:
- GET /api/ml/models — list all trained models with grades and metrics
- GET /api/ml/models/{id} — model detail
- POST /api/ml/train — trigger training (background task)
- GET /api/ml/training-status — check if training is in progress

**11. Frontend — Models Page**
Update the Models page to display:
- Table of all trained models
- Columns: Symbol, Type (badge), Pipeline, Grade (color-coded badge: A=green, B=blue, C=yellow, D=orange, F=red), Accuracy, Sharpe, Trained date
- Model detail view with full metrics

### Testing Requirements
- Write unit tests for the feature engineering (verify feature count, no NaN in output)
- Write unit tests for each indicator function (compare against known values)
- Write unit tests for the ensemble voting logic
- Test that training scripts run end-to-end (can use small data subset for speed)
- Verify model files are saved correctly and can be loaded
- Verify the ML API returns correct model data
- Use preview tool to verify the Models page
- Run ALL tests

### Important Notes
- Add these ML dependencies to requirements.txt: scikit-learn, xgboost, lightgbm, optuna, tensorflow (or keras), hmmlearn, joblib, pandas
- Training can be slow — use background tasks or run as separate scripts
- For data collection: if no broker credentials available, generate synthetic M5 data for testing (random walk with realistic spreads)
- LSTM training requires TensorFlow — ensure it's in dependencies

### When you're done, present a CHECKPOINT REPORT:
1. List every file created/modified
2. Test results
3. Number of features computed
4. Model grades for each symbol x pipeline
5. Any training issues or decisions
6. What Phase 6 will build

Then ask me:
- "Here are the model grades: [table]. Any symbols performing poorly?"
- "Training took [X] per symbol. Want to adjust Optuna trial count?"
- "Feature count is [N]. Want to add or remove any features?"
- "Ready for Phase 6?"
```

---

## Expected Deliverables
- [ ] Data collection script (broker API + CSV support)
- [ ] Feature engineering (80+ features)
- [ ] Technical indicators library
- [ ] Scalping training pipeline (XGB + LGB per symbol)
- [ ] Expert training pipeline (XGB + LGB + LSTM + Meta + Regime per symbol)
- [ ] Model grading system
- [ ] Ensemble engine with voting logic
- [ ] Meta-labeler and regime detector services
- [ ] ML API endpoints wired up
- [ ] Frontend Models page
- [ ] Trained models for BTCUSD, XAUUSD, US30
- [ ] All tests passing
