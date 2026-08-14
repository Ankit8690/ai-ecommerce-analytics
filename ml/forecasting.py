"""
Monthly sales forecasting pipeline with time-aware holdout validation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error


def _calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    # Avoid zero division in MAPE
    non_zero = y_true != 0
    mape = float(np.mean(np.abs((y_true[non_zero] - y_pred[non_zero]) / y_true[non_zero])) * 100.0)
    return {"MAE": round(mae, 2), "RMSE": round(rmse, 2), "MAPE_pct": round(mape, 2)}


def train_sales_forecast(
    df_monthly: pd.DataFrame,
    forecast_horizon: int = 3,
    test_size: int = 5,
    random_state: int = 42
) -> dict:
    """
    Train and evaluate monthly sales forecasting models (Baseline vs Ridge Trend/Lag).
    Uses a time-aware train/test split.
    Returns evaluation metrics, model parameters, and 3-month forward predictions.
    """
    df = df_monthly.sort_values("month").copy()
    df["time_idx"] = np.arange(len(df))
    df["lag_1"] = df["product_gmv"].shift(1)
    df["lag_2"] = df["product_gmv"].shift(2)
    
    # Drop rows without lag features for regression model training
    df_model = df.dropna().copy()
    
    # Time-based train / test split
    train_df = df_model.iloc[:-test_size].copy()
    test_df = df_model.iloc[-test_size:].copy()
    
    # 1. Baseline: 3-month Moving Average
    # For test set, predict using 3-month moving average of preceding historical train months
    test_preds_baseline = []
    history = list(train_df["product_gmv"].values)
    for _ in range(len(test_df)):
        pred = float(np.mean(history[-3:]))
        test_preds_baseline.append(pred)
        # Add actual to history for rolling evaluation
        history.append(test_df["product_gmv"].values[len(test_preds_baseline) - 1])
        
    baseline_metrics = _calculate_metrics(test_df["product_gmv"].values, np.array(test_preds_baseline))
    
    # 2. Ridge Regression Model (Trend + Lags)
    features = ["time_idx", "lag_1", "lag_2"]
    X_train = train_df[features]
    y_train = train_df["product_gmv"]
    X_test = test_df[features]
    y_test = test_df["product_gmv"]
    
    model = Ridge(alpha=1.0, random_state=random_state)
    model.fit(X_train, y_train)
    test_preds_ridge = model.predict(X_test)
    
    model_metrics = _calculate_metrics(y_test.values, test_preds_ridge)
    
    # Model Selection: Pick model with lowest MAE
    selected_model_name = "Ridge Trend/Lag Model" if model_metrics["MAE"] <= baseline_metrics["MAE"] else "Moving Average Baseline"
    
    # Fit selected model on full dataset for final 3-month forward forecast
    full_X = df_model[features]
    full_y = df_model["product_gmv"]
    
    if selected_model_name == "Ridge Trend/Lag Model":
        final_model = Ridge(alpha=1.0, random_state=random_state)
        final_model.fit(full_X, full_y)
        residuals = full_y - final_model.predict(full_X)
        res_std = float(np.std(residuals))
        
        # Recursive 3-month forward forecast
        last_row = df_model.iloc[-1]
        last_idx = int(last_row["time_idx"])
        last_month = pd.to_datetime(last_row["month"])
        
        forecast_records = []
        curr_lag_1 = float(last_row["product_gmv"])
        curr_lag_2 = float(last_row["lag_1"])
        
        for h in range(1, forecast_horizon + 1):
            next_idx = last_idx + h
            next_month = last_month + pd.DateOffset(months=h)
            next_month_str = next_month.strftime("%Y-%m-%d")
            
            feat_arr = np.array([[next_idx, curr_lag_1, curr_lag_2]])
            pred_gmv = float(final_model.predict(feat_arr)[0])
            
            forecast_records.append({
                "month": next_month_str,
                "forecast_gmv": round(pred_gmv, 2),
                "lower_ci_95": round(max(0, pred_gmv - 1.96 * res_std), 2),
                "upper_ci_95": round(pred_gmv + 1.96 * res_std, 2)
            })
            
            curr_lag_2 = curr_lag_1
            curr_lag_1 = pred_gmv
    else:
        # Moving Average 3-month forward forecast
        last_gmvs = list(df_model["product_gmv"].values[-3:])
        res_std = float(np.std(df_model["product_gmv"].values - np.mean(df_model["product_gmv"].values)))
        last_month = pd.to_datetime(df_model.iloc[-1]["month"])
        
        forecast_records = []
        for h in range(1, forecast_horizon + 1):
            next_month = last_month + pd.DateOffset(months=h)
            pred_gmv = float(np.mean(last_gmvs[-3:]))
            forecast_records.append({
                "month": next_month.strftime("%Y-%m-%d"),
                "forecast_gmv": round(pred_gmv, 2),
                "lower_ci_95": round(max(0, pred_gmv - 1.96 * res_std), 2),
                "upper_ci_95": round(pred_gmv + 1.96 * res_std, 2)
            })
            last_gmvs.append(pred_gmv)

    return {
        "selected_model": selected_model_name,
        "baseline_metrics": baseline_metrics,
        "model_metrics": model_metrics,
        "historical_months_count": len(df_monthly),
        "test_holdout_months": test_size,
        "forecast_horizon_months": forecast_horizon,
        "forward_forecast": forecast_records
    }
