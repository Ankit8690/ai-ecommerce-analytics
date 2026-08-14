"""
Targeted verification script for Phase 5 Streamlit BI Dashboard.
Verifies dashboard file integrity, API communication, and metric rendering.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.main import app


def main() -> None:
    print("=" * 65)
    print("PHASE 5 — STREAMLIT BI DASHBOARD SMOKE VALIDATION")
    print("=" * 65)
    
    # 1. Verify dashboard.py exists and syntax compiles
    dashboard_path = ROOT / "dashboard.py"
    assert dashboard_path.exists(), "dashboard.py does not exist!"
    code = dashboard_path.read_text(encoding="utf-8")
    compile(code, str(dashboard_path), "exec")
    print("1. dashboard.py file structure & compilation: OK")
    
    # 2. Verify FastAPI API communication using TestClient
    client = TestClient(app)
    endpoints = [
        "/api/kpis",
        "/api/sales/monthly",
        "/api/categories?limit=5",
        "/api/products?limit=5",
        "/api/customers/0000366f3b9a7992bf8c76cfdf3221e2",
        "/api/customers/0000366f3b9a7992bf8c76cfdf3221e2/segment",
        "/api/reviews",
        "/api/delivery",
        "/api/delivery/monthly",
        "/api/forecast"
    ]
    
    print("\n2. Verifying Dashboard API Endpoint Communication:")
    for ep in endpoints:
        if ep.startswith("/api/customers") and "segment" not in ep:
            res = client.get(ep)
            assert res.status_code == 200, f"Failed endpoint {ep}: {res.status_code}"
            print(f"   GET {ep:<55} -> 200 OK")
        else:
            res = client.get(ep)
            assert res.status_code == 200, f"Failed endpoint {ep}: {res.status_code}"
            print(f"   GET {ep:<55} -> 200 OK")
            
    print("\n3. Verifying Executive KPI Metrics Payload for Dashboard Cards:")
    kpi_res = client.get("/api/kpis").json()
    print(f"   Total Orders:        {kpi_res['total_orders']:,}")
    print(f"   Product GMV:         {kpi_res['product_gmv']:,.2f}")
    print(f"   Cash Collected:      {kpi_res['cash_collected']:,.2f}")
    assert kpi_res["total_orders"] == 99_441, "KPI total orders mismatch!"
    
    print("\n[Phase 5 Verification] ALL CHECKS PASSED ✓")


if __name__ == "__main__":
    main()
