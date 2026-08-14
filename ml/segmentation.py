"""
Customer Segmentation pipeline using RFM-style features & K-Means clustering.
"""
from __future__ import annotations

import datetime
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text
from sqlalchemy.engine import Engine


def train_customer_segmentation(
    df_customers: pd.DataFrame,
    n_clusters: int = 4,
    random_state: int = 42
) -> tuple[pd.DataFrame, dict]:
    """
    Train K-Means clustering model on customer RFM features.
    Returns segmented dataframe and summary evaluation metrics dictionary.
    """
    features = ["recency_days", "order_count", "total_gmv", "avg_order_value"]
    X = df_customers[features].copy()
    
    # Log-transform right-skewed features for stable distance computation
    X_log = np.log1p(np.maximum(X, 0))
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_log)
    
    kmeans = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=10)
    cluster_labels = kmeans.fit_predict(X_scaled)
    
    df_result = df_customers.copy()
    df_result["cluster_id"] = cluster_labels
    
    # Calculate silhouette score (using sample_size=2500 for memory efficiency)
    if len(X_scaled) > 2500:
        np.random.seed(random_state)
        sample_idx = np.random.choice(len(X_scaled), size=2500, replace=False)
        sil_score = float(silhouette_score(X_scaled[sample_idx], cluster_labels[sample_idx]))
    else:
        sil_score = float(silhouette_score(X_scaled, cluster_labels))
        
    # Analyze cluster characteristics to assign business labels
    cluster_stats = df_result.groupby("cluster_id")[features].mean()
    cluster_counts = df_result["cluster_id"].value_counts().to_dict()
    
    # Sort cluster IDs by mean total_gmv and order_count
    sorted_clusters = cluster_stats.sort_values(by=["total_gmv", "order_count"], ascending=False).index.tolist()
    
    # Mapping rule:
    # 1. Top GMV/Order cluster -> High-Value Champions
    # 2. High repeat rate / frequency cluster -> Repeat Loyalists
    # 3. Low recency (recent) one-time -> Recent One-Time Buyers
    # 4. High recency (older) low spend -> Lapsed / Low-Spend Buyers
    label_mapping = {}
    label_options = [
        "High-Value Champions",
        "Repeat Loyalists",
        "Recent One-Time Buyers",
        "Lapsed / Low-Spend Buyers"
    ]
    
    for i, cid in enumerate(sorted_clusters):
        label_mapping[cid] = label_options[i] if i < len(label_options) else f"Segment {cid}"
        
    df_result["segment_label"] = df_result["cluster_id"].map(label_mapping)
    
    summary = {
        "n_clusters": n_clusters,
        "silhouette_score": round(sil_score, 4),
        "cluster_counts": {label_mapping[cid]: int(cluster_counts[cid]) for cid in sorted_clusters},
        "cluster_stats": {
            label_mapping[cid]: cluster_stats.loc[cid].round(2).to_dict()
            for cid in sorted_clusters
        }
    }
    
    return df_result, summary


def save_customer_segments_to_db(engine: Engine, df_segmented: pd.DataFrame) -> None:
    """
    Persist customer segments into database table analytics.customer_segments.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS analytics.customer_segments (
        customer_unique_id VARCHAR(32) PRIMARY KEY,
        cluster_id         SMALLINT    NOT NULL,
        segment_label      VARCHAR(64) NOT NULL,
        recency_days       NUMERIC(10,2) NOT NULL,
        order_count        SMALLINT    NOT NULL,
        total_gmv          NUMERIC(10,2) NOT NULL,
        avg_order_value    NUMERIC(10,2) NOT NULL,
        updated_at         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
    
    with engine.begin() as conn:
        conn.execute(text(create_table_sql))
        conn.execute(text("TRUNCATE TABLE analytics.customer_segments;"))
        conn.execute(text("GRANT SELECT ON analytics.customer_segments TO ecommerce_readonly;"))
        
    cols_to_save = [
        "customer_unique_id", "cluster_id", "segment_label",
        "recency_days", "order_count", "total_gmv", "avg_order_value"
    ]
    df_to_save = df_segmented[cols_to_save].copy()
    
    with engine.begin() as conn:
        df_to_save.to_sql(
            "customer_segments",
            conn,
            schema="analytics",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=2000
        )
    print(f"[Segmentation] Saved {len(df_to_save):,} rows to analytics.customer_segments.")
