"""FastAPI smoke tests via TestClient — requires DB reachable."""
from __future__ import annotations

import pytest


@pytest.mark.api
class TestHealth:
    def test_root(self, api_client):
        r = api_client.get("/")
        assert r.status_code == 200
        assert "health" in r.json()

    def test_health(self, api_client):
        r = api_client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["database_connected"] is True


@pytest.mark.api
class TestAnalytics:
    def test_kpis_shape(self, api_client):
        r = api_client.get("/api/kpis")
        assert r.status_code == 200
        body = r.json()
        for key in ("total_orders", "product_gmv", "cash_collected", "delivered_pct"):
            assert key in body
        assert body["total_orders"] > 0
        assert body["product_gmv"] > 0

    def test_monthly_sales_is_list(self, api_client):
        r = api_client.get("/api/sales/monthly")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list) and len(body) >= 20
        assert set(body[0].keys()) >= {"month", "order_count", "product_gmv"}

    def test_categories_limit_respected(self, api_client):
        r = api_client.get("/api/categories", params={"limit": 5})
        assert r.status_code == 200
        assert len(r.json()) == 5

    def test_products_limit_respected(self, api_client):
        r = api_client.get("/api/products", params={"limit": 3})
        assert r.status_code == 200
        assert len(r.json()) == 3

    def test_reviews_shape(self, api_client):
        r = api_client.get("/api/reviews")
        assert r.status_code == 200
        body = r.json()
        assert body["total_reviews"] > 0
        assert 0 <= body["negative_review_rate_pct"] <= 100

    def test_delivery_shape(self, api_client):
        r = api_client.get("/api/delivery")
        assert r.status_code == 200
        body = r.json()
        assert body["delivered_order_count"] > 0
        assert 0 <= body["late_delivery_rate_pct"] <= 100


@pytest.mark.api
class TestAnalystEndpoint:
    def test_empty_question_rejected(self, api_client):
        r = api_client.post("/api/analyst", json={"question": "  "})
        assert r.status_code in (400, 422)

    def test_missing_field_rejected(self, api_client):
        r = api_client.post("/api/analyst", json={})
        assert r.status_code == 422
