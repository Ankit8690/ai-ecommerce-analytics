"""
ML endpoints serving customer segmentation, sales forecasting, and experience risk predictions.
"""
from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.database import get_db
from api.schemas import (
    CustomerSegmentResponse,
    ExperienceRiskRequest,
    ExperienceRiskResponse,
)
from ml.experience import predict_experience_risk

ROOT = Path(__file__).resolve().parent.parent.parent
router = APIRouter(prefix="/api", tags=["Machine Learning"])


@router.get("/customers/{customer_unique_id}/segment", response_model=CustomerSegmentResponse, summary="Get Customer ML Segment")
def get_customer_segment(
    customer_unique_id: str,
    db: Connection = Depends(get_db),
) -> CustomerSegmentResponse:
    """Retrieve customer RFM segmentation metrics and cluster label from analytics.customer_segments."""
    query = text("SELECT * FROM analytics.customer_segments WHERE customer_unique_id = :cid")
    row = db.execute(query, {"cid": customer_unique_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Segment for customer '{customer_unique_id}' not found")
    return CustomerSegmentResponse(**dict(row))


@router.get("/forecast", summary="Get Sales Forecast")
def get_sales_forecast() -> dict:
    """Retrieve 3-month forward sales forecast with confidence intervals."""
    summary_path = ROOT / "docs" / "ml_summary.json"
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            if "sales_forecasting" in data:
                return data["sales_forecasting"]
        except Exception:
            pass
            
    raise HTTPException(status_code=500, detail="Sales forecast artifact unavailable. Run ML pipeline first.")


@router.post("/experience-risk", response_model=ExperienceRiskResponse, summary="Predict Order Experience Risk")
def predict_risk_endpoint(payload: ExperienceRiskRequest) -> ExperienceRiskResponse:
    """Predict customer experience risk (negative review probability) for an order using pre-delivery metrics."""
    result = predict_experience_risk(payload.model_dump())
    return ExperienceRiskResponse(**result)
