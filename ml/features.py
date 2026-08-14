"""
Feature extraction module consuming PostgreSQL analytics views (analytics.*).
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


def get_customer_features(engine: Engine) -> pd.DataFrame:
    """
    Extract customer RFM and tenure features from analytics.v_customer_performance.
    Calculates recency in days relative to the max purchase date in the dataset.
    """
    query = """
    WITH max_date AS (
        SELECT MAX(latest_order_date) AS max_purchase_ts FROM analytics.v_customer_performance
    )
    SELECT
        c.customer_unique_id,
        c.order_count,
        c.total_gmv,
        c.total_cash_collected,
        c.avg_order_value_gmv                              AS avg_order_value,
        c.first_order_date,
        c.latest_order_date,
        c.is_repeat_customer,
        EXTRACT(EPOCH FROM (m.max_purchase_ts - c.latest_order_date))/86400.0 AS recency_days,
        EXTRACT(EPOCH FROM (c.latest_order_date - c.first_order_date))/86400.0 AS tenure_days
    FROM analytics.v_customer_performance c, max_date m
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
    
    # Fill any null tenure_days with 0.0 (single order customers)
    df["tenure_days"] = df["tenure_days"].fillna(0.0)
    df["recency_days"] = df["recency_days"].fillna(df["recency_days"].median())
    return df


def get_monthly_sales_data(engine: Engine) -> pd.DataFrame:
    """
    Extract monthly sales time-series from analytics.v_monthly_sales.
    """
    query = """
    SELECT
        month,
        order_count,
        unique_customers,
        product_gmv,
        cash_collected,
        aov_gmv,
        aov_cash
    FROM analytics.v_monthly_sales
    ORDER BY month ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
    
    df["month"] = pd.to_datetime(df["month"])
    return df


def get_experience_risk_features(engine: Engine) -> pd.DataFrame:
    """
    Extract order-level features for customer experience risk prediction.
    Only includes features available at or before order delivery to prevent data leakage.
    Target: is_negative_review (review_score <= 2).
    """
    query = """
    SELECT
        order_id,
        customer_state,
        order_status,
        item_count,
        item_price_total,
        freight_total,
        product_gmv,
        payment_count,
        cash_collected,
        EXTRACT(EPOCH FROM (order_delivered_customer_date - order_purchase_timestamp))/86400.0 AS delivery_days,
        CASE 
            WHEN order_delivered_customer_date > order_estimated_delivery_date THEN 1 
            ELSE 0 
        END AS is_late,
        EXTRACT(EPOCH FROM (order_delivered_customer_date - order_estimated_delivery_date))/86400.0 AS delay_vs_estimate_days,
        review_score,
        is_negative_review
    FROM analytics.v_order_summary
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
      AND review_score IS NOT NULL
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
    
    # Fill missing values if any
    df["delivery_days"] = df["delivery_days"].fillna(df["delivery_days"].median())
    df["delay_vs_estimate_days"] = df["delay_vs_estimate_days"].fillna(0.0)
    return df
