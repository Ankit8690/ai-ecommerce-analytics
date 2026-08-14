"""
Targeted verification script for Phase 4 FastAPI Backend API.
Uses TestClient to test all API endpoints and security constraints.
"""
from __future__ import annotations

import sys
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.main import app

def main() -> None:
    client = TestClient(app)
    
    print("=" * 65)
    print("PHASE 4 — FASTAPI BACKEND API VALIDATION")
    print("=" * 65)
    
    # 1. Health check
    print("\n1. Testing GET /health ...")
    res = client.get("/health")
    print(f"   Status Code: {res.status_code}")
    data = res.json()
    print(f"   Response:    {data}")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert data["status"] == "ok", "Expected status ok"
    assert data["database_connected"] is True, "Expected database_connected True"
    
    # 2. Executive KPIs
    print("\n2. Testing GET /api/kpis ...")
    res = client.get("/api/kpis")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    kpis = res.json()
    print(f"   Total Orders:     {kpis['total_orders']:,}")
    print(f"   Product GMV:      {kpis['product_gmv']:,.2f}")
    print(f"   Cash Collected:   {kpis['cash_collected']:,.2f}")
    assert kpis["total_orders"] == 99_441, "KPI total orders mismatch!"
    assert abs(kpis["product_gmv"] - 15843553.24) < 1.0, "KPI GMV mismatch!"
    
    # 3. Monthly Sales
    print("\n3. Testing GET /api/sales/monthly ...")
    res = client.get("/api/sales/monthly")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    sales = res.json()
    print(f"   Monthly records returned: {len(sales)}")
    assert len(sales) == 25, "Expected 25 monthly records"
    
    # 4. Categories & Products
    print("\n4. Testing GET /api/categories & GET /api/products ...")
    res_cat = client.get("/api/categories?limit=5")
    assert res_cat.status_code == 200
    cats = res_cat.json()
    print(f"   Top Category: {cats[0]['product_category_name']} (GMV: {cats[0]['product_gmv']:,.2f})")
    
    res_prod = client.get("/api/products?limit=5")
    assert res_prod.status_code == 200
    prods = res_prod.json()
    print(f"   Top Product ID: {prods[0]['product_id']} (GMV: {prods[0]['product_gmv']:,.2f})")
    
    # 5. Customer Profile & 404 handling
    print("\n5. Testing GET /api/customers/{id} & 404 error handling ...")
    sample_cid = "0000366f3b9a7992bf8c76cfdf3221e2"
    res_cust = client.get(f"/api/customers/{sample_cid}")
    assert res_cust.status_code == 200, f"Customer endpoint returned {res_cust.status_code}: {res_cust.text}"
    cust = res_cust.json()
    print(f"   Customer {sample_cid[:10]}... orders: {cust['order_count']}, GMV: {cust['total_gmv']}")
    
    # Invalid customer 404 check
    res_404 = client.get("/api/customers/nonexistent_customer_xyz_99999")
    assert res_404.status_code == 404
    print("   Nonexistent customer 404 response verified.")
    
    # 6. Customer Segment ML endpoint
    print("\n6. Testing GET /api/customers/{id}/segment ...")
    res_seg = client.get(f"/api/customers/{sample_cid}/segment")
    assert res_seg.status_code == 200, f"Segment endpoint returned {res_seg.status_code}: {res_seg.text}"
    seg = res_seg.json()
    print(f"   Customer Segment Label: {seg['segment_label']}")
    
    # 7. Sales Forecast ML endpoint
    print("\n7. Testing GET /api/forecast ...")
    res_fc = client.get("/api/forecast")
    assert res_fc.status_code == 200
    fc = res_fc.json()
    print(f"   Selected Forecast Model: {fc['selected_model']}")
    print(f"   Forecast Horizon:        {len(fc['forward_forecast'])} months")
    
    # 8. Experience Risk ML endpoint
    print("\n8. Testing POST /api/experience-risk ...")
    risk_payload = {
        "item_count": 2,
        "item_price_total": 120.0,
        "freight_total": 25.0,
        "product_gmv": 145.0,
        "payment_count": 1,
        "delivery_days": 18.5,
        "is_late": 1,
        "delay_vs_estimate_days": 4.5
    }
    res_risk = client.post("/api/experience-risk", json=risk_payload)
    assert res_risk.status_code == 200
    risk = res_risk.json()
    print(f"   Order Risk Probability: {risk['risk_probability']} ({risk['risk_level']} Risk)")
    
    # 9. Security Audit Check: Confirm no arbitrary SQL endpoint exists
    print("\n9. Security Audit: Confirming no arbitrary SQL endpoints exist ...")
    res_sql = client.post("/api/sql", json={"query": "SELECT * FROM orders"})
    assert res_sql.status_code == 404, "Security violation! Arbitrary SQL endpoint must return 404"
    print("   Arbitrary SQL endpoint is absent (Security check OK).")
    
    print("\n[Phase 4 Verification] ALL CHECKS PASSED ✓")

if __name__ == "__main__":
    main()
