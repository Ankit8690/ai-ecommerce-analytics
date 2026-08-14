"""
Targeted verification script for Phase 2 SQL Analytics Layer.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "database" / "analytics_schema.sql"

def load_env() -> dict[str, str]:
    load_dotenv(ROOT / ".env")
    required = ["DATABASE_URL", "DATABASE_URL_READONLY"]
    cfg = {k: os.environ.get(k, "") for k in required}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"Missing required .env keys: {missing}")
    return cfg

def main() -> None:
    cfg = load_env()
    app_engine = create_engine(cfg["DATABASE_URL"], future=True)
    ro_engine = create_engine(cfg["DATABASE_URL_READONLY"], future=True)

    print("[Phase 2] Applying analytics_schema.sql ...")
    sql_text = SCHEMA_SQL.read_text(encoding="utf-8")
    
    # Split by semicolon to execute each statement reliably
    statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
    with app_engine.begin() as conn:
        for stmt in statements:
            # Skip empty or comment-only statements
            lines = [line for line in stmt.splitlines() if not line.strip().startswith("--")]
            clean_stmt = "\n".join(lines).strip()
            if clean_stmt:
                conn.execute(text(clean_stmt))
    print("[Phase 2] Schema & views created successfully.")

    print("\n[Phase 2 Verification] -------------------------------------")

    with app_engine.connect() as conn:
        # 1. Executive KPIs verification
        print("1. Testing analytics.v_executive_kpis ...")
        kpis = conn.execute(text("SELECT * FROM analytics.v_executive_kpis")).mappings().one()
        print(f"   Total Orders:            {kpis['total_orders']:,} (Expected: 99,441)")
        print(f"   Total Customers:         {kpis['total_customers']:,} (Expected: 99,441)")
        print(f"   Unique Customers:        {kpis['total_unique_customers']:,} (Expected: 96,096)")
        print(f"   Total Products:          {kpis['total_products']:,} (Expected: 32,951)")
        print(f"   Total Sellers:           {kpis['total_sellers']:,} (Expected: 3,095)")
        print(f"   Product GMV:             {kpis['product_gmv']:,.2f}")
        print(f"   Cash Collected:          {kpis['cash_collected']:,.2f}")
        print(f"   AOV (GMV):               {kpis['avg_order_value_gmv']:,.2f}")
        print(f"   AOV (Cash):              {kpis['avg_order_value_cash']:,.2f}")
        print(f"   Delivered Orders:        {kpis['delivered_orders']:,} ({kpis['delivered_pct']}%)")
        print(f"   Cancelled Orders:        {kpis['cancelled_orders']:,} ({kpis['cancelled_pct']}%)")

        assert kpis['total_orders'] == 99_441, f"Unexpected total_orders: {kpis['total_orders']}"
        assert kpis['total_unique_customers'] == 96_096, f"Unexpected total_unique_customers: {kpis['total_unique_customers']}"
        assert kpis['total_products'] == 32_951, f"Unexpected total_products: {kpis['total_products']}"
        assert kpis['total_sellers'] == 3_095, f"Unexpected total_sellers: {kpis['total_sellers']}"

        # 2. Raw reconciliation checks for GMV and Cash Collected
        print("\n2. Reconciling GMV & Cash Collected vs raw tables ...")
        raw_gmv = conn.execute(text("SELECT SUM(price + freight_value) FROM public.order_items")).scalar_one()
        raw_cash = conn.execute(text("SELECT SUM(payment_value) FROM public.order_payments")).scalar_one()
        print(f"   Raw order_items GMV:     {raw_gmv:,.2f}")
        print(f"   Raw order_payments Cash: {raw_cash:,.2f}")
        assert abs(float(kpis['product_gmv']) - float(raw_gmv)) < 0.01, "GMV mismatch!"
        assert abs(float(kpis['cash_collected']) - float(raw_cash)) < 0.01, "Cash collected mismatch!"
        print("   Reconciliation: PERFECT MATCH (No join multiplication).")

        # 3. Monthly Sales check
        print("\n3. Testing analytics.v_monthly_sales ...")
        monthly = conn.execute(text("SELECT COUNT(*) AS months, SUM(order_count) AS total_orders FROM analytics.v_monthly_sales")).mappings().one()
        print(f"   Months recorded:         {monthly['months']}")
        print(f"   Sum of monthly orders:   {monthly['total_orders']:,}")
        assert monthly['total_orders'] == 99_441, "Monthly orders count mismatch!"

        # 4. Category Performance check
        print("\n4. Testing analytics.v_category_performance ...")
        cats = conn.execute(text("SELECT COUNT(*) AS category_count, SUM(quantity_sold) AS items_sold FROM analytics.v_category_performance")).mappings().one()
        print(f"   Categories:              {cats['category_count']}")
        print(f"   Total items sold:        {cats['items_sold']:,} (Expected: 112,650)")
        assert cats['items_sold'] == 112_650, f"Quantity sold mismatch: {cats['items_sold']}"

        # 5. Product Performance check
        print("\n5. Testing analytics.v_product_performance ...")
        prods = conn.execute(text("SELECT COUNT(*) AS product_count FROM analytics.v_product_performance")).scalar_one()
        print(f"   Products returned:       {prods:,} (Expected: 32,951)")
        assert prods == 32_951, f"Product performance count mismatch: {prods}"

        # 6. Customer Performance check
        print("\n6. Testing analytics.v_customer_performance ...")
        custs = conn.execute(text("""
            SELECT COUNT(*) AS total_people,
                   SUM(CASE WHEN is_repeat_customer THEN 1 ELSE 0 END) AS repeaters
            FROM analytics.v_customer_performance
        """)).mappings().one()
        print(f"   Unique people:           {custs['total_people']:,} (Expected: 96,096)")
        print(f"   Repeat customers:        {custs['repeaters']:,} (Expected: 2,997)")
        assert custs['total_people'] == 96_096, "Customer count mismatch!"
        assert custs['repeaters'] == 2_997, "Repeat customer count mismatch!"

        # 7. Review Analytics check
        print("\n7. Testing analytics.v_review_analytics ...")
        revs = conn.execute(text("SELECT * FROM analytics.v_review_analytics")).mappings().one()
        print(f"   Total Reviews:           {revs['total_reviews']:,} (Expected: 99,441)")
        print(f"   Avg Review Score:        {revs['avg_review_score']}")
        print(f"   Negative Reviews (<=2):  {revs['negative_review_count']:,} ({revs['negative_review_rate_pct']}%)")
        assert revs['total_reviews'] == 99_441, "Review count mismatch!"

        # 8. Delivery Performance check
        print("\n8. Testing analytics.v_delivery_performance ...")
        deliv = conn.execute(text("SELECT * FROM analytics.v_delivery_performance")).mappings().one()
        print(f"   Delivered Orders:        {deliv['delivered_order_count']:,}")
        print(f"   Avg Delivery Days:       {deliv['avg_delivery_days']} days")
        print(f"   Median Delivery Days:    {deliv['median_delivery_days']} days")
        print(f"   Late Delivery Rate:      {deliv['late_delivery_rate_pct']}%")

    # 9. Read-only role verification
    print("\n9. Verifying ecommerce_readonly role access ...")
    with ro_engine.connect() as ro_conn:
        views = [
            "v_order_summary", "v_executive_kpis", "v_monthly_sales",
            "v_category_performance", "v_product_performance",
            "v_customer_performance", "v_review_analytics",
            "v_delivery_performance", "v_monthly_delivery_performance"
        ]
        for v in views:
            n = ro_conn.execute(text(f"SELECT COUNT(*) FROM analytics.{v}")).scalar_one()
            print(f"   SELECT FROM analytics.{v:<32} count={n:>7,}  OK")

    print("\n[Phase 2 Verification] ALL CHECKS PASSED ✓")

if __name__ == "__main__":
    main()
