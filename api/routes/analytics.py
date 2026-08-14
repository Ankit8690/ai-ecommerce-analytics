"""
Analytics endpoints serving Phase 2 PostgreSQL views.
Uses read-only database connections (ecommerce_readonly).
"""
from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.database import get_db
from api.schemas import (
    CategoryPerformanceItem,
    CustomerPerformanceResponse,
    DeliveryPerformanceResponse,
    ExecutiveKPIsResponse,
    MonthlyDeliveryPerformanceItem,
    MonthlySalesItem,
    ProductPerformanceItem,
    ReviewAnalyticsResponse,
)

router = APIRouter(prefix="/api", tags=["Analytics"])


@router.get("/kpis", response_model=ExecutiveKPIsResponse, summary="Get Executive KPIs")
def get_executive_kpis(db: Connection = Depends(get_db)) -> ExecutiveKPIsResponse:
    """Retrieve high-level business performance metrics from analytics.v_executive_kpis."""
    query = text("SELECT * FROM analytics.v_executive_kpis")
    row = db.execute(query).mappings().first()
    if not row:
        raise HTTPException(status_code=500, detail="KPI data unavailable")
    return ExecutiveKPIsResponse(**dict(row))


@router.get("/sales/monthly", response_model=List[MonthlySalesItem], summary="Get Monthly Sales Time-Series")
def get_monthly_sales(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Connection = Depends(get_db),
) -> List[MonthlySalesItem]:
    """Retrieve monthly sales performance from analytics.v_monthly_sales."""
    sql = "SELECT * FROM analytics.v_monthly_sales WHERE 1=1"
    params = {}
    if start_date:
        sql += " AND month >= :start_date::date"
        params["start_date"] = start_date
    if end_date:
        sql += " AND month <= :end_date::date"
        params["end_date"] = end_date
    sql += " ORDER BY month ASC"
    
    rows = db.execute(text(sql), params).mappings().all()
    results = []
    for r in rows:
        d = dict(r)
        d["month"] = str(d["month"])
        results.append(MonthlySalesItem(**d))
    return results


@router.get("/categories", response_model=List[CategoryPerformanceItem], summary="Get Category Performance")
def get_category_performance(
    limit: int = Query(100, ge=1, le=500),
    db: Connection = Depends(get_db),
) -> List[CategoryPerformanceItem]:
    """Retrieve category-level revenue, volume, and rating metrics."""
    query = text("SELECT * FROM analytics.v_category_performance ORDER BY product_gmv DESC NULLS LAST LIMIT :limit")
    rows = db.execute(query, {"limit": limit}).mappings().all()
    return [CategoryPerformanceItem(**dict(r)) for r in rows]


@router.get("/products", response_model=List[ProductPerformanceItem], summary="Get Product Performance")
def get_product_performance(
    category: Optional[str] = Query(None, description="Filter by product category name"),
    limit: int = Query(100, ge=1, le=1000),
    db: Connection = Depends(get_db),
) -> List[ProductPerformanceItem]:
    """Retrieve product-level metrics from analytics.v_product_performance."""
    sql = "SELECT * FROM analytics.v_product_performance WHERE 1=1"
    params = {"limit": limit}
    if category:
        sql += " AND product_category_name = :category"
        params["category"] = category
    sql += " ORDER BY product_gmv DESC LIMIT :limit"
    
    rows = db.execute(text(sql), params).mappings().all()
    return [ProductPerformanceItem(**dict(r)) for r in rows]


@router.get("/customers/{customer_unique_id}", response_model=CustomerPerformanceResponse, summary="Get Customer Profile")
def get_customer_profile(
    customer_unique_id: str,
    db: Connection = Depends(get_db),
) -> CustomerPerformanceResponse:
    """Retrieve customer RFM metrics by customer_unique_id."""
    query = text("SELECT * FROM analytics.v_customer_performance WHERE customer_unique_id = :cid")
    row = db.execute(query, {"cid": customer_unique_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Customer '{customer_unique_id}' not found")
    
    d = dict(row)
    d["first_order_date"] = str(d["first_order_date"])
    d["latest_order_date"] = str(d["latest_order_date"])
    return CustomerPerformanceResponse(**d)


@router.get("/reviews", response_model=ReviewAnalyticsResponse, summary="Get Review Analytics")
def get_review_analytics(db: Connection = Depends(get_db)) -> ReviewAnalyticsResponse:
    """Retrieve overall customer review rating metrics."""
    query = text("SELECT * FROM analytics.v_review_analytics")
    row = db.execute(query).mappings().first()
    if not row:
        raise HTTPException(status_code=500, detail="Review analytics unavailable")
    return ReviewAnalyticsResponse(**dict(row))


@router.get("/delivery", response_model=DeliveryPerformanceResponse, summary="Get Overall Delivery Performance")
def get_delivery_performance(db: Connection = Depends(get_db)) -> DeliveryPerformanceResponse:
    """Retrieve overall operational delivery performance metrics."""
    query = text("SELECT * FROM analytics.v_delivery_performance")
    row = db.execute(query).mappings().first()
    if not row:
        raise HTTPException(status_code=500, detail="Delivery performance data unavailable")
    return DeliveryPerformanceResponse(**dict(row))


@router.get("/delivery/monthly", response_model=List[MonthlyDeliveryPerformanceItem], summary="Get Monthly Delivery Trends")
def get_monthly_delivery_performance(db: Connection = Depends(get_db)) -> List[MonthlyDeliveryPerformanceItem]:
    """Retrieve time-series monthly operational delivery metrics."""
    query = text("SELECT * FROM analytics.v_monthly_delivery_performance ORDER BY month ASC")
    rows = db.execute(query).mappings().all()
    results = []
    for r in rows:
        d = dict(r)
        d["month"] = str(d["month"])
        results.append(MonthlyDeliveryPerformanceItem(**d))
    return results
