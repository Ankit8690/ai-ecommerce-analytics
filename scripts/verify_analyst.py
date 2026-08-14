"""
Targeted verification script for Phase 6 AI Business Analyst.
Validates question routing, grounded metric synthesis, SQL safety validation, and API integration.
"""
from __future__ import annotations

import sys
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.sql_validator import validate_sql
from api.main import app


def main() -> None:
    client = TestClient(app)
    
    print("=" * 65)
    print("PHASE 6 — AI BUSINESS ANALYST VALIDATION")
    print("=" * 65)
    
    # 1. Health check regression
    print("\n1. Testing GET /health (Regression check) ...")
    res_health = client.get("/health")
    assert res_health.status_code == 200
    print(f"   Status: {res_health.json()}")
    
    # 2. Test natural-language question routing & grounded response
    print("\n2. Testing POST /api/analyst (Executive KPI question) ...")
    req_kpi = {"question": "What are our overall executive KPIs and revenue numbers?"}
    res_kpi = client.post("/api/analyst", json=req_kpi)
    assert res_kpi.status_code == 200, f"Failed KPI question: {res_kpi.text}"
    data_kpi = res_kpi.json()
    print(f"   Source Used: {data_kpi['source']}")
    print(f"   Answer Snippet:\n   {data_kpi['answer'][:180]}...")
    assert "99,441" in data_kpi["answer"] or "15,843,553.24" in data_kpi["answer"], "Grounded KPI fact missing!"

    # 3. Test category performance question
    print("\n3. Testing POST /api/analyst (Category performance question) ...")
    req_cat = {"question": "What are our best performing product categories by GMV?"}
    res_cat = client.post("/api/analyst", json=req_cat)
    assert res_cat.status_code == 200
    data_cat = res_cat.json()
    print(f"   Source Used: {data_cat['source']}")
    print(f"   Records Returned: {len(data_cat['data'])}")
    assert len(data_cat["data"]) > 0, "Category data payload empty!"

    # 4. Test forecasting question
    print("\n4. Testing POST /api/analyst (Sales forecasting question) ...")
    req_fc = {"question": "What is the 3-month sales forecast?"}
    res_fc = client.post("/api/analyst", json=req_fc)
    assert res_fc.status_code == 200
    data_fc = res_fc.json()
    print(f"   Source Used: {data_fc['source']}")
    assert "Forecast" in data_fc["source"] or "Forecast" in data_fc["answer"], "Forecast intent routing failed!"

    # 5. Test SQL Safety Validator unit tests
    print("\n5. Testing SQL Safety Validator (ai/sql_validator.py) ...")
    safe_sql = "SELECT * FROM analytics.v_executive_kpis"
    is_safe, clean_sql = validate_sql(safe_sql)
    assert is_safe is True, "Valid SELECT query incorrectly rejected!"
    print(f"   Safe Query Test:   PASSED ({clean_sql})")

    unsafe_queries = [
        "DROP TABLE public.orders",
        "DELETE FROM analytics.v_executive_kpis",
        "UPDATE public.orders SET order_status = 'canceled'",
        "SELECT * FROM orders; DROP TABLE orders;",
        "SELECT * FROM orders -- malicious comment",
        "INSERT INTO public.customers VALUES ('test')",
        "ALTER TABLE public.orders ADD COLUMN test INT",
        "GRANT ALL ON DATABASE ecommerce_ai TO public"
    ]

    for bad_q in unsafe_queries:
        is_safe, err_msg = validate_sql(bad_q)
        assert is_safe is False, f"SQL safety violation! Failed to block: {bad_q}"
        print(f"   Blocked Unsafe:     {bad_q[:40]:<40} -> {err_msg}")

    print("\n6. Dashboard File Integrity Check ...")
    dashboard_path = ROOT / "dashboard.py"
    assert dashboard_path.exists()
    compile(dashboard_path.read_text(encoding="utf-8"), str(dashboard_path), "exec")
    print("   dashboard.py structure & compilation OK.")

    print("\n[Phase 6 Verification] ALL CHECKS PASSED ✓")


if __name__ == "__main__":
    main()
