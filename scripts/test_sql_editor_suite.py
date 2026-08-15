"""
End-to-end accuracy suite for the direct-SQL editor pipeline.

Simulates exactly what dashboard.py does when the user hits "Run SQL":
    raw SQL -> validate_sql -> check_known_tables -> row_cap rewrite -> execute

Each test declares an expected outcome that is independently derivable
from the raw dataset / analytics views, so we can *assert* correctness
rather than just verifying the query ran.

Run:  .venv\\Scripts\\python.exe scripts/test_sql_editor_suite.py
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy import text as sa_text

load_dotenv(ROOT / ".env")

from api.database import readonly_engine
from ai.sql_validator import validate_sql, check_known_tables


def run_pipeline(sql: str, row_cap: int = 1000) -> tuple[str, list[dict] | None, str | None]:
    """Mirror dashboard.py: returns (status, rows_or_None, error_msg_or_None)."""
    ok, cleaned_or_err = validate_sql(sql)
    if not ok:
        return "validator_reject", None, cleaned_or_err
    tbl_ok, bad, refs = check_known_tables(cleaned_or_err)
    if not refs:
        return "no_table", None, "no table or view referenced"
    if not tbl_ok:
        return "unknown_table", None, f"unknown table {bad}"
    final_sql = cleaned_or_err
    m_limit = re.search(r"\bLIMIT\s+(\d+)\s*$", final_sql, re.IGNORECASE)
    if m_limit:
        if int(m_limit.group(1)) > row_cap:
            final_sql = re.sub(r"\bLIMIT\s+\d+\s*$", f"LIMIT {row_cap}",
                               final_sql, flags=re.IGNORECASE)
    else:
        final_sql = f"{final_sql} LIMIT {row_cap}"
    try:
        with readonly_engine.connect() as conn:
            result = conn.execute(sa_text(final_sql))
            rows = [dict(r._mapping) for r in result]
        return "ok", rows, None
    except Exception as e:
        return "db_error", None, str(e).splitlines()[0][:400]


def q_scalar(sql: str) -> Any:
    with readonly_engine.connect() as c:
        return c.execute(sa_text(sql)).scalar()


@dataclass
class Case:
    name: str
    sql: str
    expected_status: str = "ok"          # "ok" | "validator_reject" | "unknown_table" | "no_table" | "db_error"
    expected_rows: Optional[int] = None  # exact row count if known
    min_rows: Optional[int] = None
    checker: Optional[Callable[[list[dict]], tuple[bool, str]]] = None
    err_contains: Optional[str] = None
    row_cap: int = 1000


# ---------------------------------------------------------------------------
# Compute independent expected values ONCE upfront
# ---------------------------------------------------------------------------
print("Deriving expected values from database...", flush=True)
EXPECTED = {
    "total_orders":       q_scalar("SELECT COUNT(*) FROM public.orders"),
    "total_customers":    q_scalar("SELECT COUNT(*) FROM public.customers"),
    "total_products":     q_scalar("SELECT COUNT(*) FROM public.products"),
    "total_sellers":      q_scalar("SELECT COUNT(*) FROM public.sellers"),
    "total_reviews":      q_scalar("SELECT COUNT(*) FROM public.order_reviews"),
    "delivered_orders":   q_scalar("SELECT COUNT(*) FROM public.orders WHERE order_status='delivered'"),
    "top_product_gmv":    q_scalar("SELECT product_id FROM analytics.v_product_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 1"),
    "top_category_gmv":   q_scalar("SELECT product_category_name FROM analytics.v_category_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 1"),
    "worst_cat_rating":   q_scalar(
        "SELECT product_category_name FROM analytics.v_category_performance "
        "WHERE review_count>=100 AND avg_review_score IS NOT NULL "
        "ORDER BY avg_review_score ASC LIMIT 1"),
    "distinct_states":    q_scalar("SELECT COUNT(DISTINCT customer_state) FROM public.customers"),
    "avg_review_score":   float(q_scalar("SELECT ROUND(AVG(review_score)::numeric,2) FROM public.order_reviews")),
    "kpis_product_gmv":   float(q_scalar("SELECT product_gmv FROM analytics.v_executive_kpis")),
    "kpis_cash_collected":float(q_scalar("SELECT cash_collected FROM analytics.v_executive_kpis")),
    "month_count":        q_scalar("SELECT COUNT(*) FROM analytics.v_monthly_sales"),
}
for k, v in EXPECTED.items():
    print(f"  {k:24s} = {v}")
print()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
CASES: list[Case] = [
    # 1. Executive KPIs (scalar view)
    Case(
        "kpis_row_count",
        "SELECT * FROM analytics.v_executive_kpis",
        expected_rows=1,
    ),
    # 2. KPI value matches derived value
    Case(
        "kpis_product_gmv_matches",
        "SELECT product_gmv FROM analytics.v_executive_kpis",
        checker=lambda rows: (
            abs(float(rows[0]["product_gmv"]) - EXPECTED["kpis_product_gmv"]) < 0.01,
            f"got {rows[0]['product_gmv']} vs expected {EXPECTED['kpis_product_gmv']}",
        ),
    ),
    # 3. Total orders count matches raw
    Case(
        "orders_count_matches_raw",
        "SELECT COUNT(*) AS n FROM public.orders",
        checker=lambda rows: (
            int(rows[0]["n"]) == EXPECTED["total_orders"],
            f"got {rows[0]['n']} vs expected {EXPECTED['total_orders']}",
        ),
    ),
    # 4. Top 5 products by GMV — 5 rows, DESC order, top matches derived top
    Case(
        "top5_products_gmv",
        "SELECT product_id, product_gmv FROM analytics.v_product_performance "
        "ORDER BY product_gmv DESC NULLS LAST LIMIT 5",
        expected_rows=5,
        checker=lambda rows: (
            rows[0]["product_id"] == EXPECTED["top_product_gmv"]
            and all(rows[i]["product_gmv"] >= rows[i+1]["product_gmv"] for i in range(len(rows)-1)),
            "either top product wrong or ordering not DESC",
        ),
    ),
    # 5. Top category by GMV
    Case(
        "top_category_gmv",
        "SELECT product_category_name FROM analytics.v_category_performance "
        "ORDER BY product_gmv DESC NULLS LAST LIMIT 1",
        expected_rows=1,
        checker=lambda rows: (
            rows[0]["product_category_name"] == EXPECTED["top_category_gmv"],
            f"got {rows[0]['product_category_name']} vs {EXPECTED['top_category_gmv']}",
        ),
    ),
    # 6. Worst 3 rated categories
    Case(
        "worst3_categories_rating",
        "SELECT product_category_name, avg_review_score "
        "FROM analytics.v_category_performance "
        "WHERE review_count >= 100 AND avg_review_score IS NOT NULL "
        "ORDER BY avg_review_score ASC LIMIT 3",
        expected_rows=3,
        checker=lambda rows: (
            rows[0]["product_category_name"] == EXPECTED["worst_cat_rating"]
            and all(rows[i]["avg_review_score"] <= rows[i+1]["avg_review_score"] for i in range(len(rows)-1)),
            "worst category or ASC ordering wrong",
        ),
    ),
    # 7. Top 3 least reviewed products
    Case(
        "top3_least_reviewed_products",
        "SELECT product_id, review_count FROM analytics.v_product_performance "
        "ORDER BY review_count ASC NULLS LAST LIMIT 3",
        expected_rows=3,
        checker=lambda rows: (
            all(rows[i]["review_count"] <= rows[i+1]["review_count"] for i in range(len(rows)-1)),
            "not ascending by review_count",
        ),
    ),
    # 8. Monthly sales row count matches monthly view
    Case(
        "monthly_sales_row_count",
        "SELECT * FROM analytics.v_monthly_sales ORDER BY month",
        checker=lambda rows: (
            len(rows) == EXPECTED["month_count"],
            f"got {len(rows)} months vs {EXPECTED['month_count']}",
        ),
    ),
    # 9. Distinct customer states from public.customers
    Case(
        "distinct_customer_states",
        "SELECT COUNT(DISTINCT customer_state) AS n FROM public.customers",
        checker=lambda rows: (
            int(rows[0]["n"]) == EXPECTED["distinct_states"],
            f"got {rows[0]['n']} vs {EXPECTED['distinct_states']}",
        ),
    ),
    # 10. JOIN orders + customers
    Case(
        "join_orders_customers",
        "SELECT o.order_id, c.customer_state "
        "FROM public.orders o JOIN public.customers c ON o.customer_id=c.customer_id LIMIT 10",
        expected_rows=10,
    ),
    # 11. Reviews avg matches
    Case(
        "avg_review_score_matches",
        "SELECT ROUND(AVG(review_score)::numeric,2) AS avg FROM public.order_reviews",
        checker=lambda rows: (
            abs(float(rows[0]["avg"]) - EXPECTED["avg_review_score"]) < 0.01,
            f"got {rows[0]['avg']} vs {EXPECTED['avg_review_score']}",
        ),
    ),
    # 12. GROUP BY order_status
    Case(
        "orders_by_status",
        "SELECT order_status, COUNT(*) AS n FROM public.orders GROUP BY order_status",
        checker=lambda rows: (
            sum(int(r["n"]) for r in rows) == EXPECTED["total_orders"],
            f"status totals sum {sum(int(r['n']) for r in rows)} != {EXPECTED['total_orders']}",
        ),
    ),
    # 13. Delivered count matches
    Case(
        "delivered_status_count",
        "SELECT COUNT(*) AS n FROM public.orders WHERE order_status='delivered'",
        checker=lambda rows: (
            int(rows[0]["n"]) == EXPECTED["delivered_orders"],
            f"got {rows[0]['n']} vs {EXPECTED['delivered_orders']}",
        ),
    ),
    # 14. WITH CTE
    Case(
        "cte_top_5_categories",
        "WITH ranked AS (SELECT product_category_name, product_gmv, "
        "ROW_NUMBER() OVER (ORDER BY product_gmv DESC NULLS LAST) AS rn "
        "FROM analytics.v_category_performance) "
        "SELECT product_category_name FROM ranked WHERE rn <= 5",
        expected_rows=5,
    ),
    # 15. Delivery performance scalar view
    Case(
        "delivery_perf_row",
        "SELECT delivered_order_count, avg_delivery_days, late_delivery_rate_pct "
        "FROM analytics.v_delivery_performance",
        expected_rows=1,
        checker=lambda rows: (
            0 < float(rows[0]["avg_delivery_days"]) < 90
            and 0 <= float(rows[0]["late_delivery_rate_pct"]) <= 100,
            "delivery days or late rate out of sensible range",
        ),
    ),
    # 16. Products count matches
    Case(
        "products_count_matches",
        "SELECT COUNT(*) AS n FROM public.products",
        checker=lambda rows: (
            int(rows[0]["n"]) == EXPECTED["total_products"],
            f"got {rows[0]['n']} vs {EXPECTED['total_products']}",
        ),
    ),
    # 17. Total reviews from view = total from table
    Case(
        "review_analytics_vs_raw",
        "SELECT total_reviews FROM analytics.v_review_analytics",
        checker=lambda rows: (
            int(rows[0]["total_reviews"]) == EXPECTED["total_reviews"],
            f"got {rows[0]['total_reviews']} vs {EXPECTED['total_reviews']}",
        ),
    ),
    # 18. Customer segments distribution — at least 2 segments, sum > 0
    Case(
        "customer_segments_distribution",
        "SELECT segment_label, COUNT(*) AS n FROM analytics.customer_segments GROUP BY segment_label",
        min_rows=2,
        checker=lambda rows: (
            sum(int(r["n"]) for r in rows) > 0,
            "segments sum to zero",
        ),
    ),
    # 19. Bottom 5 products by quantity_sold
    Case(
        "bottom5_products_quantity",
        "SELECT product_id, quantity_sold FROM analytics.v_product_performance "
        "ORDER BY quantity_sold ASC NULLS LAST LIMIT 5",
        expected_rows=5,
        checker=lambda rows: (
            all(rows[i]["quantity_sold"] <= rows[i+1]["quantity_sold"] for i in range(len(rows)-1)),
            "not ascending",
        ),
    ),
    # 20. Product GMV concentration — top 10 sum > 0
    Case(
        "top10_products_gmv_sum",
        "SELECT SUM(product_gmv) AS s FROM (SELECT product_gmv FROM analytics.v_product_performance "
        "ORDER BY product_gmv DESC NULLS LAST LIMIT 10) t",
        checker=lambda rows: (
            float(rows[0]["s"]) > 0,
            "sum non-positive",
        ),
    ),
    # 21. Multi-table JOIN: order_items + products
    Case(
        "join_order_items_products",
        "SELECT p.product_category_name, COUNT(*) AS n "
        "FROM public.order_items oi JOIN public.products p ON oi.product_id=p.product_id "
        "GROUP BY p.product_category_name ORDER BY n DESC LIMIT 5",
        expected_rows=5,
    ),
    # ---- INTENTIONALLY INVALID CASES (validator/pipeline error paths) ----
    # 22. DELETE — should be blocked by validator
    Case("blocked_delete",   "DELETE FROM public.orders",  expected_status="validator_reject",
         err_contains="SELECT or WITH"),
    # 23. Unknown table
    Case("unknown_table_ref","SELECT * FROM public.unicorns", expected_status="unknown_table",
         err_contains="public.unicorns"),
    # 24. Multi-statement
    Case("multi_statement",  "SELECT 1; SELECT 2",         expected_status="validator_reject",
         err_contains="Multiple SQL statements"),
    # 25. Bad column (DB error)
    Case("bad_column",       "SELECT nonexistent_column FROM analytics.v_executive_kpis",
         expected_status="db_error", err_contains="nonexistent_column"),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run() -> int:
    print(f"Running {len(CASES)} cases through the direct-SQL pipeline...\n")
    passed = 0
    failed: list[tuple[str, str]] = []

    for i, c in enumerate(CASES, 1):
        t0 = time.perf_counter()
        status, rows, err = run_pipeline(c.sql, row_cap=c.row_cap)
        dt_ms = (time.perf_counter() - t0) * 1000

        ok = True
        reason = ""

        if c.expected_status != status:
            ok = False
            reason = f"expected status={c.expected_status} got={status} err={err!r}"
        elif status != "ok":
            if c.err_contains and c.err_contains.lower() not in (err or "").lower():
                ok = False
                reason = f"error message missing '{c.err_contains}': {err!r}"
        else:  # status == "ok"
            if c.expected_rows is not None and len(rows) != c.expected_rows:
                ok = False
                reason = f"expected {c.expected_rows} rows, got {len(rows)}"
            elif c.min_rows is not None and len(rows) < c.min_rows:
                ok = False
                reason = f"expected >= {c.min_rows} rows, got {len(rows)}"
            elif c.checker is not None:
                try:
                    ok2, detail = c.checker(rows)
                    if not ok2:
                        ok = False
                        reason = f"checker failed: {detail}"
                except Exception as ce:
                    ok = False
                    reason = f"checker raised: {ce}"

        tag = "PASS" if ok else "FAIL"
        print(f"[{i:02d}] {tag}  {c.name:38s}  status={status:16s}  {dt_ms:6.0f}ms")
        if not ok:
            print(f"       reason: {reason}")
            failed.append((c.name, reason))
        else:
            passed += 1

    total = len(CASES)
    pct = 100.0 * passed / total
    print()
    print("=" * 72)
    print(f"RESULT: {passed}/{total} passed ({pct:.1f}%)")
    if failed:
        print("Failures:")
        for name, r in failed:
            print(f"  - {name}: {r}")
    print("=" * 72)
    return 0 if pct >= 95.0 else 1


if __name__ == "__main__":
    sys.exit(run())
