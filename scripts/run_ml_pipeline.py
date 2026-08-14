"""
Phase 3 ML Pipeline Runner & Validation Engine.
Executes Segmentation, Sales Forecasting, and Experience Risk Modeling.
Persists customer segments to PostgreSQL and outputs summary artifacts.
"""
from __future__ import annotations

import decimal
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.features import get_customer_features, get_monthly_sales_data, get_experience_risk_features
from ml.segmentation import train_customer_segmentation, save_customer_segments_to_db
from ml.forecasting import train_sales_forecast
from ml.experience import analyze_customer_experience_correlations, train_experience_risk_model


def load_env() -> dict[str, str]:
    load_dotenv(ROOT / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL missing from .env")
    return {"DATABASE_URL": db_url}


def json_serializer(obj):
    if isinstance(obj, (decimal.Decimal, float)):
        return float(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def main() -> None:
    cfg = load_env()
    engine = create_engine(cfg["DATABASE_URL"], future=True)
    
    print("=" * 65)
    print("PHASE 3 — ML & ADVANCED ANALYTICS PIPELINE")
    print("=" * 65)
    
    # -----------------------------------------------------------------
    # 1. Customer Segmentation
    # -----------------------------------------------------------------
    print("\n[1/3] Running Customer Segmentation (RFM + K-Means) ...")
    df_customers = get_customer_features(engine)
    print(f"      Extracted {len(df_customers):,} customer records.")
    
    df_segmented, seg_summary = train_customer_segmentation(df_customers, n_clusters=4, random_state=42)
    print(f"      Silhouette Score: {seg_summary['silhouette_score']}")
    print("      Cluster Distribution:")
    for label, count in seg_summary["cluster_counts"].items():
        print(f"        - {label:<28}: {count:>6,} customers")
        
    save_customer_segments_to_db(engine, df_segmented)
    
    # -----------------------------------------------------------------
    # 2. Sales Forecasting
    # -----------------------------------------------------------------
    print("\n[2/3] Running Sales Forecasting (Monthly GMV) ...")
    df_monthly = get_monthly_sales_data(engine)
    print(f"      Extracted {len(df_monthly)} monthly observations.")
    
    forecast_summary = train_sales_forecast(df_monthly, forecast_horizon=3, test_size=5, random_state=42)
    print(f"      Selected Model:  {forecast_summary['selected_model']}")
    print(f"      Baseline MAE:    {forecast_summary['baseline_metrics']['MAE']}")
    print(f"      Model MAE:       {forecast_summary['model_metrics']['MAE']} (MAPE: {forecast_summary['model_metrics']['MAPE_pct']}%)")
    print("      3-Month Forward Forecast:")
    for rec in forecast_summary["forward_forecast"]:
        print(f"        - {rec['month']}: GMV {rec['forecast_gmv']:>12,.2f}  (95% CI: {rec['lower_ci_95']:,.2f} .. {rec['upper_ci_95']:,.2f})")
        
    # -----------------------------------------------------------------
    # 3. Customer Experience Analytics & Risk Model
    # -----------------------------------------------------------------
    print("\n[3/3] Running Customer Experience Analytics & Risk Model ...")
    exp_correlations = analyze_customer_experience_correlations(engine)
    print("      Delivery Status vs Negative Review Rate:")
    for row in exp_correlations["delivery_status_impact"]:
        print(f"        - {row['delivery_status']:<8}: {row['total_orders']:>6,} orders, "
              f"avg review={float(row['avg_review_score']):.2f}, "
              f"negative rate={float(row['negative_review_rate_pct']):.2f}%")
        
    df_experience = get_experience_risk_features(engine)
    print(f"      Extracted {len(df_experience):,} delivered orders for risk modeling.")
    
    risk_summary = train_experience_risk_model(df_experience, random_state=42)
    print(f"      Selected Model:  {risk_summary['selected_model']}")
    print(f"      ROC-AUC Score:   {risk_summary['logistic_regression_metrics']['roc_auc']} (Logistic) | "
          f"{risk_summary['random_forest_metrics']['roc_auc']} (RandomForest)")
    print("      Top Risk Feature Importances:")
    for feat, imp in list(risk_summary["feature_importances"].items())[:5]:
        print(f"        - {feat:<24}: {imp:.4f}")
        
    # -----------------------------------------------------------------
    # Save Pipeline Summary Artifact
    # -----------------------------------------------------------------
    summary_artifact = {
        "phase": "Phase 3 — ML & Advanced Analytics",
        "segmentation": seg_summary,
        "sales_forecasting": forecast_summary,
        "customer_experience_correlations": exp_correlations,
        "experience_risk_model": risk_summary
    }
    
    artifacts_dir = ROOT / "docs"
    artifacts_dir.mkdir(exist_ok=True)
    summary_path = artifacts_dir / "ml_summary.json"
    summary_path.write_text(json.dumps(summary_artifact, indent=2, default=json_serializer), encoding="utf-8")
    print(f"\n[Artifacts] Pipeline summary saved to {summary_path}")
    
    # -----------------------------------------------------------------
    # Targeted Validation Checks
    # -----------------------------------------------------------------
    print("\n[Phase 3 Validation] -------------------------------------")
    with engine.connect() as conn:
        db_seg_count = conn.execute(text("SELECT COUNT(*) FROM analytics.customer_segments")).scalar_one()
        print(f"1. Database customer_segments count: {db_seg_count:,} (Expected: 96,096)  OK")
        assert db_seg_count == 96_096, "Customer segmentation row count mismatch!"
        
        ro_engine = create_engine(os.environ["DATABASE_URL_READONLY"], future=True)
        with ro_engine.connect() as ro_conn:
            ro_seg_count = ro_conn.execute(text("SELECT COUNT(*) FROM analytics.customer_segments")).scalar_one()
            print(f"2. Read-only user queryable:          {ro_seg_count:,}  OK")
        
    assert seg_summary["silhouette_score"] > 0.20, "Silhouette score below acceptable threshold!"
    assert len(forecast_summary["forward_forecast"]) == 3, "Forecast horizon count mismatch!"
    assert risk_summary["random_forest_metrics"]["roc_auc"] > 0.60, "Risk model ROC-AUC below expected threshold!"
    
    print("\n[Phase 3 Verification] ALL CHECKS PASSED ✓")


if __name__ == "__main__":
    main()
