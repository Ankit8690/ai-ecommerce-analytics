"""
Pydantic schemas for FastAPI request and response validation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    database_connected: bool
    version: str = "1.0.0"


class ExecutiveKPIsResponse(BaseModel):
    total_orders: int
    total_customers: int
    total_unique_customers: int
    total_products: int
    total_sellers: int
    product_gmv: float
    cash_collected: float
    avg_order_value_gmv: float
    avg_order_value_cash: float
    delivered_orders: int
    delivered_pct: float
    cancelled_orders: int
    cancelled_pct: float


class MonthlySalesItem(BaseModel):
    month: str
    order_count: int
    unique_customers: int
    product_gmv: float
    cash_collected: float
    aov_gmv: float
    aov_cash: float


class CategoryPerformanceItem(BaseModel):
    product_category_name: str
    order_count: int
    quantity_sold: int
    product_gmv: float
    item_revenue: float
    avg_item_price: Optional[float] = None
    avg_review_score: Optional[float] = None
    negative_review_count: int
    negative_review_rate_pct: Optional[float] = None
    gmv_rank: int


class ProductPerformanceItem(BaseModel):
    product_id: str
    product_category_name: str
    quantity_sold: int
    order_count: int
    product_gmv: float
    avg_item_price: Optional[float] = None
    avg_review_score: Optional[float] = None
    negative_review_count: int
    gmv_rank: int


class CustomerPerformanceResponse(BaseModel):
    customer_unique_id: str
    order_count: int
    total_gmv: float
    total_cash_collected: float
    avg_order_value_gmv: float
    first_order_date: str
    latest_order_date: str
    is_repeat_customer: bool


class ReviewAnalyticsResponse(BaseModel):
    total_reviews: int
    avg_review_score: float
    rating_1_count: int
    rating_2_count: int
    rating_3_count: int
    rating_4_count: int
    rating_5_count: int
    negative_review_count: int
    negative_review_rate_pct: float


class DeliveryPerformanceResponse(BaseModel):
    delivered_order_count: int
    avg_delivery_days: float
    median_delivery_days: float
    late_delivery_count: int
    late_delivery_rate_pct: float
    on_time_rate_pct: float


class MonthlyDeliveryPerformanceItem(BaseModel):
    month: str
    delivered_order_count: int
    avg_delivery_days: float
    median_delivery_days: float
    late_delivery_count: int
    late_delivery_rate_pct: float
    on_time_rate_pct: float


class CustomerSegmentResponse(BaseModel):
    customer_unique_id: str
    cluster_id: int
    segment_label: str
    recency_days: float
    order_count: int
    total_gmv: float
    avg_order_value: float


class ExperienceRiskRequest(BaseModel):
    item_count: int = Field(default=1, ge=1)
    item_price_total: float = Field(default=50.0, ge=0.0)
    freight_total: float = Field(default=15.0, ge=0.0)
    product_gmv: float = Field(default=65.0, ge=0.0)
    payment_count: int = Field(default=1, ge=1)
    delivery_days: float = Field(default=12.0, ge=0.0)
    is_late: int = Field(default=0, ge=0, le=1)
    delay_vs_estimate_days: float = Field(default=0.0)


class ExperienceRiskResponse(BaseModel):
    predicted_negative_review: bool
    risk_probability: float
    risk_level: str
    model_used: str = "Random Forest Classifier"


class AnalystRequest(BaseModel):
    question: str = Field(..., min_length=2, description="Natural language business question")


class AnalystResponse(BaseModel):
    question: str
    answer: str
    data: List[Dict[str, Any]] = []
    source: str
    sql_used: Optional[str] = None
    insights: List[str] = []
