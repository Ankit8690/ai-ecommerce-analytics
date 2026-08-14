"""
Customer Experience Analytics & Experience-Risk Predictive Model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text
from sqlalchemy.engine import Engine


def analyze_customer_experience_correlations(engine: Engine) -> dict:
    """
    Investigate statistical relationships between delivery metrics and review outcomes.
    """
    # 1. Delivery delay vs negative review rate
    query_late = """
    SELECT
        CASE WHEN order_delivered_customer_date > order_estimated_delivery_date THEN 'Late' ELSE 'On-Time' END AS delivery_status,
        COUNT(*) AS total_orders,
        AVG(review_score) AS avg_review_score,
        COUNT(CASE WHEN review_score <= 2 THEN 1 END) AS negative_reviews,
        ROUND(100.0 * COUNT(CASE WHEN review_score <= 2 THEN 1 END) / COUNT(*), 2) AS negative_review_rate_pct
    FROM analytics.v_order_summary
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
      AND review_score IS NOT NULL
    GROUP BY 1
    """
    
    # 2. Highest risk categories (> 500 orders)
    query_cat_risk = """
    SELECT
        product_category_name,
        order_count,
        avg_review_score,
        negative_review_count,
        negative_review_rate_pct
    FROM analytics.v_category_performance
    WHERE order_count >= 500
    ORDER BY negative_review_rate_pct DESC
    LIMIT 5
    """
    
    with engine.connect() as conn:
        late_analysis = [dict(row) for row in conn.execute(text(query_late)).mappings()]
        cat_risk = [dict(row) for row in conn.execute(text(query_cat_risk)).mappings()]
        
    return {
        "delivery_status_impact": late_analysis,
        "top_high_risk_categories": cat_cat_risk if 'cat_cat_risk' in locals() else cat_risk
    }


def train_experience_risk_model(
    df_experience: pd.DataFrame,
    random_state: int = 42
) -> dict:
    """
    Train a predictive binary classification model for customer experience risk (is_negative_review).
    Uses non-leaking features available at or before delivery.
    """
    features = [
        "item_count", "item_price_total", "freight_total", "product_gmv",
        "payment_count", "delivery_days", "is_late", "delay_vs_estimate_days"
    ]
    
    X = df_experience[features].copy()
    y = df_experience["is_negative_review"].values
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=random_state, stratify=y
    )
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # 1. Baseline: Logistic Regression
    lr = LogisticRegression(max_iter=1000, random_state=random_state)
    lr.fit(X_train_scaled, y_train)
    lr_preds = lr.predict(X_test_scaled)
    lr_probs = lr.predict_proba(X_test_scaled)[:, 1]
    
    lr_metrics = {
        "accuracy": round(float(accuracy_score(y_test, lr_preds)), 4),
        "precision": round(float(precision_score(y_test, lr_preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, lr_preds, zero_division=0)), 4),
        "f1_score": round(float(f1_score(y_test, lr_preds, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, lr_probs)), 4),
        "confusion_matrix": confusion_matrix(y_test, lr_preds).tolist()
    }
    
    # 2. Random Forest Model
    rf = RandomForestClassifier(n_estimators=100, max_depth=8, random_state=random_state, n_jobs=-1)
    rf.fit(X_train, y_train)
    rf_preds = rf.predict(X_test)
    rf_probs = rf.predict_proba(X_test)[:, 1]
    
    rf_metrics = {
        "accuracy": round(float(accuracy_score(y_test, rf_preds)), 4),
        "precision": round(float(precision_score(y_test, rf_preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, rf_preds, zero_division=0)), 4),
        "f1_score": round(float(f1_score(y_test, rf_preds, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, rf_probs)), 4),
        "confusion_matrix": confusion_matrix(y_test, rf_preds).tolist()
    }
    
    # Feature Importances (Random Forest)
    importances = dict(zip(features, [round(float(val), 4) for val in rf.feature_importances_]))
    sorted_importances = dict(sorted(importances.items(), key=lambda item: item[1], reverse=True))
    
    selected_model_name = "Random Forest" if rf_metrics["roc_auc"] >= lr_metrics["roc_auc"] else "Logistic Regression"
    selected_metrics = rf_metrics if selected_model_name == "Random Forest" else lr_metrics
    
    return {
        "selected_model": selected_model_name,
        "logistic_regression_metrics": lr_metrics,
        "random_forest_metrics": rf_metrics,
        "feature_importances": sorted_importances,
        "target_negative_rate_pct": round(float(np.mean(y) * 100.0), 2),
        "sample_size": len(df_experience)
    }
