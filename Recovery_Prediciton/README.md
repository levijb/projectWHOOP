# Recovery Score Prediction with Continuous Learning

## Overview

Build an **adaptive recovery prediction system** that learns continuously from personal Whoop data to predict tomorrow's recovery score with increasing accuracy over time.

### Core Research Question

**Can a simple, interpretable machine learning model match Whoop's proprietary recovery algorithm using daily adaptive learning on coarser temporal data?**

---

## Why Whoop Recovery Score as Benchmark?

Whoop's recovery score is the ideal ground truth because it:

1. **Clinically Validated** - Based on peer-reviewed physiological research
2. **Personally Calibrated** - Already adapted to individual baseline physiology  
3. **State-of-the-Art** - Leading metric for athletic performance optimization
4. **Rich Data Source** - Computed from high-frequency multi-sensor streams (1Hz heart rate, continuous HRV, sleep staging)

### Our Challenge

Match Whoop's accuracy using:
- **Coarser temporal resolution** (daily aggregates vs. continuous monitoring)
- **Fewer features** (8-10 inputs vs. continuous multi-sensor data)
- **Simple, interpretable models** (linear regression vs. proprietary black-box algorithms)

---

## Project Scope

**Focus:** Daily adaptive model updates with online learning

**Deferred:** Weekly meta-updates and monthly architecture reviews (future work)

---

## Methodology

### Initial Model (v1.0)

**Algorithm:** Linear Regression with Ridge regularization
- Simple, interpretable, fast training
- Easy incremental updates
- Coefficients show feature importance directly

**Features (n=8):**
```
Physiological:
├── HRV RMSSD (ms) - primary recovery indicator
├── Resting Heart Rate (bpm) - cardiovascular stress
├── Respiratory Rate (rpm) - sleep quality signal

Sleep:
├── Total Sleep Duration (hours)
├── Sleep Efficiency (%)
├── Sleep Debt (cumulative hours)

Strain:
├── Previous Day Strain

Temporal:
└── Day of Week (one-hot encoded)
```

**Training Strategy:**
- **Initial training:** First 60 days (2x Whoop's calibration period)
- **Train/validation split:** 70/30 (days 1-42 train, days 43-60 validate)
- **Deployment:** Day 61 - begin daily predictions and updates

**Expected Performance:**
- Initial MAE: 10-15%
- Initial R²: 0.4-0.6
- Target improvement: -0.5% MAE per 30 days

---

### Daily Update Cycle

**Schedule:** Every morning at 9:00 AM (after Whoop recovery score available)

#### 1. Data Collection
```
├── Retrieve yesterday's actual recovery score (ground truth)
├── Retrieve yesterday's prediction
├── Calculate prediction error
└── Store in database
```

#### 2. Error Analysis
```
├── Compute daily MAE
├── Identify systematic bias (over/under prediction)
├── Detect outliers (error > 2 std deviations)
└── Analyze error patterns (day of week, trends)
```

#### 3. Feature Update
```
├── Recalculate 7-day and 30-day rolling statistics
├── Update personal baselines (HRV, RHR)
├── Add yesterday's data to training set
└── Maintain sliding window (last 90 days primary)
```

#### 4. Model Update Decision

**Update Triggers:**

| Condition | Action | Type |
|-----------|--------|------|
| Error > 15% | Immediate retrain | Emergency |
| 7-day bias > ±5% | Next-day retrain | Bias correction |
| MAE trending up | Next-day retrain | Drift correction |
| 7 days since update | Scheduled retrain | Routine |
| Stable performance | Skip update | None |

#### 5. Incremental Update
```
├── Perform partial_fit (online learning)
├── Update feature weights
├── Adjust for recent drift
├── Validate on last 7 days
└── Rollback if performance degrades
```

**Online Learning Algorithm:** Incremental Ridge Regression
- Stable with regularization
- Fast updates (<1 min)
- Interpretable coefficients
- Handles limited data well

#### 6. Model Versioning
```
├── Increment version (v1.0.1, v1.0.2, ...)
├── Save model snapshot
├── Log update reason and changes
├── Track performance delta
└── Store in model_registry database
```

#### 7. Generate Tomorrow's Prediction
```
├── Load current model
├── Prepare today's features
├── Generate point estimate
├── Calculate confidence interval (bootstrap)
├── Compute feature contributions
└── Store prediction in database
```

---

## Success Metrics

**Primary:**
- MAE (Mean Absolute Error) in recovery score percentage points
- R² Score (variance explained)
- Prediction calibration (confidence interval accuracy)

**Targets:**
- **60 days:** MAE < 10%
- **120 days:** MAE < 7%  
- **180 days:** MAE < 5%

**Secondary:**
- Model stability (error variance decreasing)
- Update frequency (fewer updates as model matures)
- Improvement rate (MAE reduction per 30 days)

---

## Database Schema (Core Tables)
```sql
-- Model registry
model_registry
├── model_id, version, algorithm
├── deployment_date, status
├── hyperparameters, features
└── performance_metrics

-- Daily predictions
recovery_predictions
├── prediction_id, date
├── model_version
├── input_features (JSON snapshot)
├── predicted_recovery, confidence_interval
└── actual_recovery, error

-- Model updates
model_updates
├── update_id, date
├── trigger_reason, changes_made
├── mae_before, mae_after
└── training_time

-- Performance tracking
model_performance_metrics
├── date, model_version
├── mae, rmse, r2
├── bias, variance
└── rolling_7d_mae, rolling_30d_mae
```

---

## Key Questions to Answer

1. **Accuracy:** Can we match Whoop's recovery score within 5% MAE?
2. **Learning:** Does daily adaptation improve predictions over time?
3. **Stability:** Do model updates remain stable or cause oscillations?
4. **Interpretability:** Which features consistently drive recovery predictions?
5. **Personalization:** How quickly does the model adapt to individual patterns?

---

## Future Extensions

- Weekly meta-updates (feature engineering, hyperparameter tuning)
- Monthly architecture reviews (Random Forest, XGBoost, LSTM)
- Behavioral data integration (alcohol, stress, calendar events)
- Confidence interval calibration improvements
- Multi-user transfer learning

---

## Technical Stack

**Core:**
- Python 3.10+
- scikit-learn (online learning)
- PostgreSQL (data storage)
- pandas/numpy (data processing)

**Optional:**
- Jupyter notebooks (exploration)
- Plotly/Matplotlib (visualization)
- Docker (reproducibility)

---

## Expected Outcomes

1. **Working prediction system** - Daily recovery forecasts with confidence intervals
2. **Model evolution tracking** - Complete history of how model adapts
3. **Performance insights** - Understanding of which features matter most
4. **Adaptive learning demonstration** - Proof that simple models can learn continuously
5. **Foundation for research** - Dataset for studying personal ML systems

---

## Getting Started
```bash
# 1. Collect 60 days of Whoop data via API
# 2. Train initial model on days 1-60
# 3. Deploy v1.0 on day 61
# 4. Begin daily prediction + update cycle
# 5. Monitor performance and model evolution
```

---

## Repository Structure
```
recovery-prediction/
├── README.md (this file)
├── data/
│   ├── raw/          # Whoop API responses
│   ├── processed/    # Cleaned features
│   └── models/       # Saved model versions
├── src/
│   ├── data_collection.py
│   ├── feature_engineering.py
│   ├── model_training.py
│   ├── daily_update.py
│   └── prediction.py
├── notebooks/
│   ├── 01_exploration.ipynb
│   ├── 02_initial_model.ipynb
│   └── 03_performance_analysis.ipynb
├── sql/
│   └── schema.sql
└── requirements.txt
```
