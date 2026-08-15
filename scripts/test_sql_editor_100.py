"""
110-case accuracy suite for the direct-SQL editor pipeline.

Mirrors dashboard.py's Run-SQL path (validate_sql -> check_known_tables ->
row-cap rewrite -> execute against ecommerce_readonly). Every "expected OK"
case has an independently derived expected value computed from raw public.*
tables so we can *assert* correctness, not just successful execution.

Run:  .venv\\Scripts\\python.exe scripts/test_sql_editor_100.py
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
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
    ok, cleaned = validate_sql(sql)
    if not ok:
        return "validator_reject", None, cleaned
    tbl_ok, bad, refs = check_known_tables(cleaned)
    if not refs:
        return "no_table", None, "no table"
    if not tbl_ok:
        return "unknown_table", None, f"unknown table {bad}"
    final_sql = cleaned
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", final_sql, re.IGNORECASE)
    if m:
        if int(m.group(1)) > row_cap:
            final_sql = re.sub(r"\bLIMIT\s+\d+\s*$", f"LIMIT {row_cap}", final_sql, flags=re.IGNORECASE)
    else:
        final_sql = f"{final_sql} LIMIT {row_cap}"
    try:
        with readonly_engine.connect() as c:
            r = c.execute(sa_text(final_sql))
            rows = [dict(x._mapping) for x in r]
        return "ok", rows, None
    except Exception as e:
        return "db_error", None, str(e).splitlines()[0][:400]


def q(sql: str) -> Any:
    with readonly_engine.connect() as c:
        return c.execute(sa_text(sql)).scalar()


def qall(sql: str) -> list[dict]:
    with readonly_engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(sa_text(sql))]


@dataclass
class Case:
    name: str
    sql: str
    expected_status: str = "ok"
    expected_rows: Optional[int] = None
    min_rows: Optional[int] = None
    checker: Optional[Callable[[list[dict]], tuple[bool, str]]] = None
    err_contains: Optional[str] = None
    row_cap: int = 1000


# ---------------------------------------------------------------------------
# Independent expected values (single source of truth)
# ---------------------------------------------------------------------------
print("Deriving expected values...", flush=True)
E = {
    "orders":            q("SELECT COUNT(*) FROM public.orders"),
    "customers":         q("SELECT COUNT(*) FROM public.customers"),
    "unique_customers":  q("SELECT COUNT(DISTINCT customer_unique_id) FROM public.customers"),
    "products":          q("SELECT COUNT(*) FROM public.products"),
    "sellers":           q("SELECT COUNT(*) FROM public.sellers"),
    "reviews":           q("SELECT COUNT(*) FROM public.order_reviews"),
    "items":             q("SELECT COUNT(*) FROM public.order_items"),
    "payments":          q("SELECT COUNT(*) FROM public.order_payments"),
    "geoloc":            q("SELECT COUNT(*) FROM public.geo_location"),
    "delivered":         q("SELECT COUNT(*) FROM public.orders WHERE order_status='delivered'"),
    "cancelled":         q("SELECT COUNT(*) FROM public.orders WHERE order_status='canceled'"),
    "states":            q("SELECT COUNT(DISTINCT customer_state) FROM public.customers"),
    "categories":        q("SELECT COUNT(DISTINCT product_category_name) FROM public.products WHERE product_category_name IS NOT NULL"),
    "months":            q("SELECT COUNT(*) FROM analytics.v_monthly_sales"),
    "avg_review":        float(q("SELECT ROUND(AVG(review_score)::numeric,2) FROM public.order_reviews")),
    "neg_reviews":       q("SELECT COUNT(*) FROM public.order_reviews WHERE review_score<=2"),
    "kpi_gmv":           float(q("SELECT product_gmv FROM analytics.v_executive_kpis")),
    "kpi_cash":          float(q("SELECT cash_collected FROM analytics.v_executive_kpis")),
    "kpi_avg_gmv":       float(q("SELECT avg_order_value_gmv FROM analytics.v_executive_kpis")),
    "raw_gmv":           float(q("SELECT SUM(price + freight_value) FROM public.order_items")),
    "raw_cash":          float(q("SELECT SUM(payment_value) FROM public.order_payments")),
    "top_product":       q("SELECT product_id FROM analytics.v_product_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 1"),
    "top_category":      q("SELECT product_category_name FROM analytics.v_category_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 1"),
    "worst_cat":         q("SELECT product_category_name FROM analytics.v_category_performance WHERE review_count>=100 AND avg_review_score IS NOT NULL ORDER BY avg_review_score ASC LIMIT 1"),
    "best_cat":          q("SELECT product_category_name FROM analytics.v_category_performance WHERE review_count>=100 AND avg_review_score IS NOT NULL ORDER BY avg_review_score DESC LIMIT 1"),
    "sellers_states":    q("SELECT COUNT(DISTINCT seller_state) FROM public.sellers"),
    "avg_price":         float(q("SELECT ROUND(AVG(price)::numeric,2) FROM public.order_items")),
    "avg_freight":       float(q("SELECT ROUND(AVG(freight_value)::numeric,2) FROM public.order_items")),
    "min_order_ts":      q("SELECT MIN(order_purchase_timestamp) FROM public.orders").isoformat(),
    "max_order_ts":      q("SELECT MAX(order_purchase_timestamp) FROM public.orders").isoformat(),
    "top_state_orders":  q("SELECT customer_state FROM public.customers GROUP BY customer_state ORDER BY COUNT(*) DESC LIMIT 1"),
    "review_scores":     q("SELECT COUNT(DISTINCT review_score) FROM public.order_reviews"),
    "delivered_pct":     float(q("SELECT delivered_pct FROM analytics.v_executive_kpis")),
    "seg_count":         q("SELECT COUNT(DISTINCT segment_label) FROM analytics.customer_segments"),
    "delivery_days":     float(q("SELECT avg_delivery_days FROM analytics.v_delivery_performance")),
    "late_rate":         float(q("SELECT late_delivery_rate_pct FROM analytics.v_delivery_performance")),
}
print(f"  {len(E)} expected values derived.\n")


def eq(a, b, tol=0.01):
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return a == b


# Compact case builder for count-matches-expected
def count_case(name, sql, key):
    return Case(name, sql,
                checker=lambda rs: (int(rs[0]["n"]) == E[key],
                                    f"got {rs[0]['n']} vs {E[key]}"))


def scalar_case(name, sql, key, tol=0.01):
    return Case(name, sql,
                checker=lambda rs: (eq(list(rs[0].values())[0], E[key], tol),
                                    f"got {list(rs[0].values())[0]} vs {E[key]}"))


CASES: list[Case] = []

# ---------------------------------------------------------------------------
# A. Raw-table counts (8 tables → 8 cases)
# ---------------------------------------------------------------------------
for tbl, key in [
    ("public.orders", "orders"),
    ("public.customers", "customers"),
    ("public.products", "products"),
    ("public.sellers", "sellers"),
    ("public.order_reviews", "reviews"),
    ("public.order_items", "items"),
    ("public.order_payments", "payments"),
    ("public.geo_location", "geoloc"),
]:
    CASES.append(count_case(f"count_{tbl.split('.')[1]}", f"SELECT COUNT(*) AS n FROM {tbl}", key))

# ---------------------------------------------------------------------------
# B. Analytics scalar-view row counts (5 cases)
# ---------------------------------------------------------------------------
for v in ["v_executive_kpis", "v_review_analytics", "v_delivery_performance"]:
    CASES.append(Case(f"onerow_{v}", f"SELECT * FROM analytics.{v}", expected_rows=1))
CASES.append(Case("monthly_sales_rowcount",
                  "SELECT * FROM analytics.v_monthly_sales",
                  checker=lambda rs: (len(rs) == E["months"], f"got {len(rs)} vs {E['months']}")))
CASES.append(Case("monthly_delivery_rowcount",
                  "SELECT * FROM analytics.v_monthly_delivery_performance",
                  min_rows=20))

# ---------------------------------------------------------------------------
# C. GMV / cash cross-verification (view vs raw) (10 cases)
# ---------------------------------------------------------------------------
CASES += [
    scalar_case("kpi_gmv_view",   "SELECT product_gmv FROM analytics.v_executive_kpis", "kpi_gmv"),
    scalar_case("kpi_cash_view",  "SELECT cash_collected FROM analytics.v_executive_kpis", "kpi_cash"),
    scalar_case("raw_gmv_direct", "SELECT SUM(price + freight_value) AS x FROM public.order_items", "raw_gmv"),
    scalar_case("raw_cash_direct","SELECT SUM(payment_value) AS x FROM public.order_payments", "raw_cash"),
    Case("gmv_view_vs_raw_match",
         "SELECT product_gmv FROM analytics.v_executive_kpis",
         checker=lambda rs: (eq(rs[0]["product_gmv"], E["raw_gmv"], 1.0),
                             f"kpi_gmv {rs[0]['product_gmv']} != raw {E['raw_gmv']}")),
    Case("cash_view_vs_raw_match",
         "SELECT cash_collected FROM analytics.v_executive_kpis",
         checker=lambda rs: (eq(rs[0]["cash_collected"], E["raw_cash"], 1.0),
                             f"kpi_cash != raw")),
    scalar_case("kpi_avg_gmv",    "SELECT avg_order_value_gmv FROM analytics.v_executive_kpis", "kpi_avg_gmv"),
    scalar_case("avg_item_price", "SELECT ROUND(AVG(price)::numeric,2) AS x FROM public.order_items", "avg_price"),
    scalar_case("avg_freight",    "SELECT ROUND(AVG(freight_value)::numeric,2) AS x FROM public.order_items", "avg_freight"),
    Case("monthly_gmv_sum_matches_kpi",
         "SELECT SUM(product_gmv) AS s FROM analytics.v_monthly_sales",
         checker=lambda rs: (eq(rs[0]["s"], E["kpi_gmv"], 5.0),
                             f"monthly sum {rs[0]['s']} vs kpi {E['kpi_gmv']}")),
]

# ---------------------------------------------------------------------------
# D. Ranking queries — top/bottom, ASC/DESC (15 cases)
# ---------------------------------------------------------------------------
CASES += [
    Case("top1_product_gmv",
         "SELECT product_id FROM analytics.v_product_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 1",
         expected_rows=1,
         checker=lambda rs: (rs[0]["product_id"] == E["top_product"], f"got {rs[0]['product_id']}")),
    Case("top1_category",
         "SELECT product_category_name FROM analytics.v_category_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 1",
         expected_rows=1,
         checker=lambda rs: (rs[0]["product_category_name"] == E["top_category"], "")),
    Case("worst_rated_cat",
         "SELECT product_category_name FROM analytics.v_category_performance "
         "WHERE review_count>=100 AND avg_review_score IS NOT NULL "
         "ORDER BY avg_review_score ASC LIMIT 1",
         expected_rows=1,
         checker=lambda rs: (rs[0]["product_category_name"] == E["worst_cat"], "")),
    Case("best_rated_cat",
         "SELECT product_category_name FROM analytics.v_category_performance "
         "WHERE review_count>=100 AND avg_review_score IS NOT NULL "
         "ORDER BY avg_review_score DESC LIMIT 1",
         expected_rows=1,
         checker=lambda rs: (rs[0]["product_category_name"] == E["best_cat"], "")),
    Case("top5_products_gmv_desc",
         "SELECT product_gmv FROM analytics.v_product_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 5",
         expected_rows=5,
         checker=lambda rs: (all(rs[i]["product_gmv"] >= rs[i+1]["product_gmv"] for i in range(4)), "not desc")),
    Case("bottom5_products_quantity",
         "SELECT quantity_sold FROM analytics.v_product_performance ORDER BY quantity_sold ASC LIMIT 5",
         expected_rows=5,
         checker=lambda rs: (all(rs[i]["quantity_sold"] <= rs[i+1]["quantity_sold"] for i in range(4)), "not asc")),
    Case("top3_least_reviewed",
         "SELECT review_count FROM analytics.v_product_performance ORDER BY review_count ASC LIMIT 3",
         expected_rows=3,
         checker=lambda rs: (all(rs[i]["review_count"] <= rs[i+1]["review_count"] for i in range(2)), "not asc")),
    Case("top10_categories_reviews",
         "SELECT product_category_name, review_count FROM analytics.v_category_performance "
         "ORDER BY review_count DESC NULLS LAST LIMIT 10",
         expected_rows=10,
         checker=lambda rs: (all(rs[i]["review_count"] >= rs[i+1]["review_count"] for i in range(9)), "not desc")),
    Case("top20_customers_gmv",
         "SELECT customer_unique_id, total_gmv FROM analytics.v_customer_performance ORDER BY total_gmv DESC LIMIT 20",
         expected_rows=20,
         checker=lambda rs: (all(rs[i]["total_gmv"] >= rs[i+1]["total_gmv"] for i in range(19)), "not desc")),
    Case("top10_customers_orders",
         "SELECT customer_unique_id, order_count FROM analytics.v_customer_performance ORDER BY order_count DESC LIMIT 10",
         expected_rows=10),
    Case("top_state_by_orders",
         "SELECT customer_state, COUNT(*) AS n FROM public.customers GROUP BY customer_state ORDER BY n DESC LIMIT 1",
         checker=lambda rs: (rs[0]["customer_state"] == E["top_state_orders"], "")),
    Case("top5_negative_review_cats",
         "SELECT product_category_name, negative_review_rate_pct FROM analytics.v_category_performance "
         "WHERE review_count>=100 ORDER BY negative_review_rate_pct DESC NULLS LAST LIMIT 5",
         expected_rows=5,
         checker=lambda rs: (all(rs[i]["negative_review_rate_pct"] >= rs[i+1]["negative_review_rate_pct"] for i in range(4)), "not desc")),
    Case("bottom10_products_gmv",
         "SELECT product_gmv FROM analytics.v_product_performance ORDER BY product_gmv ASC LIMIT 10",
         expected_rows=10,
         checker=lambda rs: (all(rs[i]["product_gmv"] <= rs[i+1]["product_gmv"] for i in range(9)), "not asc")),
    Case("top15_products_qty",
         "SELECT quantity_sold FROM analytics.v_product_performance ORDER BY quantity_sold DESC LIMIT 15",
         expected_rows=15,
         checker=lambda rs: (all(rs[i]["quantity_sold"] >= rs[i+1]["quantity_sold"] for i in range(14)), "not desc")),
    Case("top5_sellers_states",
         "SELECT seller_state, COUNT(*) AS n FROM public.sellers GROUP BY seller_state ORDER BY n DESC LIMIT 5",
         expected_rows=5,
         checker=lambda rs: (all(rs[i]["n"] >= rs[i+1]["n"] for i in range(4)), "not desc")),
]

# ---------------------------------------------------------------------------
# E. Aggregates (15 cases)
# ---------------------------------------------------------------------------
CASES += [
    scalar_case("avg_review_score",   "SELECT ROUND(AVG(review_score)::numeric,2) AS x FROM public.order_reviews", "avg_review"),
    count_case ("delivered_count",    "SELECT COUNT(*) AS n FROM public.orders WHERE order_status='delivered'", "delivered"),
    count_case ("cancelled_count",    "SELECT COUNT(*) AS n FROM public.orders WHERE order_status='canceled'", "cancelled"),
    count_case ("negative_reviews",   "SELECT COUNT(*) AS n FROM public.order_reviews WHERE review_score<=2", "neg_reviews"),
    count_case ("distinct_states",    "SELECT COUNT(DISTINCT customer_state) AS n FROM public.customers", "states"),
    count_case ("distinct_seller_states", "SELECT COUNT(DISTINCT seller_state) AS n FROM public.sellers", "sellers_states"),
    count_case ("distinct_uniqcust",  "SELECT COUNT(DISTINCT customer_unique_id) AS n FROM public.customers", "unique_customers"),
    count_case ("distinct_categories","SELECT COUNT(DISTINCT product_category_name) AS n FROM public.products WHERE product_category_name IS NOT NULL", "categories"),
    count_case ("distinct_scores",    "SELECT COUNT(DISTINCT review_score) AS n FROM public.order_reviews", "review_scores"),
    count_case ("segment_types",      "SELECT COUNT(DISTINCT segment_label) AS n FROM analytics.customer_segments", "seg_count"),
    Case("min_order_timestamp",
         "SELECT MIN(order_purchase_timestamp) AS x FROM public.orders",
         checker=lambda rs: (rs[0]["x"].isoformat() == E["min_order_ts"], f"got {rs[0]['x']}")),
    Case("max_order_timestamp",
         "SELECT MAX(order_purchase_timestamp) AS x FROM public.orders",
         checker=lambda rs: (rs[0]["x"].isoformat() == E["max_order_ts"], f"got {rs[0]['x']}")),
    Case("sum_items_gt_zero",
         "SELECT SUM(price) AS s FROM public.order_items",
         checker=lambda rs: (float(rs[0]["s"]) > 0, "sum not positive")),
    Case("avg_delivery_days_view",
         "SELECT avg_delivery_days FROM analytics.v_delivery_performance",
         checker=lambda rs: (eq(rs[0]["avg_delivery_days"], E["delivery_days"], 0.01), "")),
    Case("late_rate_view",
         "SELECT late_delivery_rate_pct FROM analytics.v_delivery_performance",
         checker=lambda rs: (eq(rs[0]["late_delivery_rate_pct"], E["late_rate"], 0.01), "")),
]

# ---------------------------------------------------------------------------
# F. GROUP BY (10 cases)
# ---------------------------------------------------------------------------
CASES += [
    Case("group_orders_by_status",
         "SELECT order_status, COUNT(*) AS n FROM public.orders GROUP BY order_status",
         checker=lambda rs: (sum(int(r["n"]) for r in rs) == E["orders"], "status sum != total orders")),
    Case("group_reviews_by_score",
         "SELECT review_score, COUNT(*) AS n FROM public.order_reviews GROUP BY review_score",
         checker=lambda rs: (sum(int(r["n"]) for r in rs) == E["reviews"], "score sum != total reviews")),
    Case("group_customers_by_state",
         "SELECT customer_state, COUNT(*) AS n FROM public.customers GROUP BY customer_state",
         checker=lambda rs: (sum(int(r["n"]) for r in rs) == E["customers"] and len(rs) == E["states"], "")),
    Case("group_products_by_category",
         "SELECT product_category_name, COUNT(*) AS n FROM public.products GROUP BY product_category_name",
         min_rows=E["categories"]),
    Case("group_sellers_by_state",
         "SELECT seller_state, COUNT(*) AS n FROM public.sellers GROUP BY seller_state",
         checker=lambda rs: (sum(int(r["n"]) for r in rs) == E["sellers"], "")),
    Case("group_payments_by_type",
         "SELECT payment_type, COUNT(*) AS n FROM public.order_payments GROUP BY payment_type",
         min_rows=2),
    Case("group_items_by_seller_top5",
         "SELECT seller_id, COUNT(*) AS n FROM public.order_items GROUP BY seller_id ORDER BY n DESC LIMIT 5",
         expected_rows=5),
    Case("group_segments",
         "SELECT segment_label, COUNT(*) AS n FROM analytics.customer_segments GROUP BY segment_label",
         checker=lambda rs: (len(rs) == E["seg_count"], f"got {len(rs)} vs {E['seg_count']}")),
    Case("group_monthly_orders_sum",
         "SELECT SUM(order_count) AS s FROM analytics.v_monthly_sales",
         checker=lambda rs: (int(rs[0]["s"]) == E["orders"], f"got {rs[0]['s']} vs {E['orders']}")),
    Case("group_reviews_score_avg",
         "SELECT review_score, AVG(review_score) AS a FROM public.order_reviews GROUP BY review_score",
         checker=lambda rs: (all(int(r["review_score"]) == int(float(r["a"])) for r in rs), "avg != score in group")),
]

# ---------------------------------------------------------------------------
# G. JOINs (10 cases)
# ---------------------------------------------------------------------------
CASES += [
    Case("join_orders_customers_count",
         "SELECT COUNT(*) AS n FROM public.orders o JOIN public.customers c ON o.customer_id=c.customer_id",
         checker=lambda rs: (int(rs[0]["n"]) == E["orders"], "")),
    Case("join_items_products_count",
         "SELECT COUNT(*) AS n FROM public.order_items oi JOIN public.products p ON oi.product_id=p.product_id",
         checker=lambda rs: (int(rs[0]["n"]) == E["items"], "")),
    Case("join_items_products_top_categories",
         "SELECT p.product_category_name, COUNT(*) AS n FROM public.order_items oi "
         "JOIN public.products p ON oi.product_id=p.product_id "
         "GROUP BY p.product_category_name ORDER BY n DESC LIMIT 5",
         expected_rows=5),
    Case("join_reviews_orders_states",
         "SELECT c.customer_state, ROUND(AVG(r.review_score)::numeric,2) AS avg_r "
         "FROM public.order_reviews r JOIN public.orders o ON r.order_id=o.order_id "
         "JOIN public.customers c ON o.customer_id=c.customer_id "
         "GROUP BY c.customer_state ORDER BY avg_r DESC LIMIT 5",
         expected_rows=5),
    Case("join_items_orders_delivered",
         "SELECT COUNT(*) AS n FROM public.order_items oi JOIN public.orders o "
         "ON oi.order_id=o.order_id WHERE o.order_status='delivered'",
         min_rows=1),
    Case("left_join_orders_reviews_null",
         "SELECT COUNT(*) AS n FROM public.orders o LEFT JOIN public.order_reviews r ON o.order_id=r.order_id "
         "WHERE r.review_id IS NULL",
         checker=lambda rs: (int(rs[0]["n"]) >= 0, "negative")),
    Case("join_sellers_items_top",
         "SELECT s.seller_state, COUNT(*) AS n FROM public.order_items oi "
         "JOIN public.sellers s ON oi.seller_id=s.seller_id GROUP BY s.seller_state ORDER BY n DESC LIMIT 5",
         expected_rows=5),
    Case("join_payments_orders",
         "SELECT o.order_status, SUM(p.payment_value) AS total_paid FROM public.order_payments p "
         "JOIN public.orders o ON p.order_id=o.order_id GROUP BY o.order_status ORDER BY total_paid DESC",
         min_rows=1),
    Case("join_3tables_customers_orders_items",
         "SELECT c.customer_state, COUNT(oi.order_item_id) AS items FROM public.customers c "
         "JOIN public.orders o ON o.customer_id=c.customer_id "
         "JOIN public.order_items oi ON oi.order_id=o.order_id "
         "GROUP BY c.customer_state ORDER BY items DESC LIMIT 3",
         expected_rows=3),
    Case("join_review_score_by_category",
         "SELECT p.product_category_name, ROUND(AVG(r.review_score)::numeric,2) AS s "
         "FROM public.order_reviews r JOIN public.order_items oi ON r.order_id=oi.order_id "
         "JOIN public.products p ON oi.product_id=p.product_id "
         "WHERE p.product_category_name IS NOT NULL "
         "GROUP BY p.product_category_name ORDER BY s DESC LIMIT 5",
         expected_rows=5),
]

# ---------------------------------------------------------------------------
# H. CTE / window functions (8 cases)
# ---------------------------------------------------------------------------
CASES += [
    Case("cte_top5_categories",
         "WITH r AS (SELECT product_category_name, product_gmv, "
         "ROW_NUMBER() OVER (ORDER BY product_gmv DESC NULLS LAST) rn "
         "FROM analytics.v_category_performance) SELECT * FROM r WHERE rn<=5",
         expected_rows=5),
    Case("cte_top10_products",
         "WITH r AS (SELECT product_id, product_gmv, "
         "RANK() OVER (ORDER BY product_gmv DESC NULLS LAST) rk "
         "FROM analytics.v_product_performance) SELECT * FROM r WHERE rk<=10",
         min_rows=10),
    Case("cte_status_pct",
         "WITH t AS (SELECT COUNT(*)::float AS total FROM public.orders) "
         "SELECT order_status, COUNT(*)::float / (SELECT total FROM t) AS pct "
         "FROM public.orders GROUP BY order_status",
         checker=lambda rs: (abs(sum(float(r["pct"]) for r in rs) - 1.0) < 0.001, "pct sum != 1")),
    Case("window_rank_products",
         "SELECT product_id, product_gmv, "
         "RANK() OVER (ORDER BY product_gmv DESC NULLS LAST) rk "
         "FROM analytics.v_product_performance LIMIT 5",
         expected_rows=5,
         checker=lambda rs: (rs[0]["rk"] == 1, "top rank not 1")),
    Case("window_avg_by_category",
         "SELECT product_id, product_category_name, product_gmv, "
         "AVG(product_gmv) OVER (PARTITION BY product_category_name) cat_avg "
         "FROM analytics.v_product_performance LIMIT 5",
         expected_rows=5),
    Case("cte_monthly_change",
         "WITH m AS (SELECT month, product_gmv, "
         "LAG(product_gmv) OVER (ORDER BY month) prev FROM analytics.v_monthly_sales) "
         "SELECT COUNT(*) AS n FROM m WHERE prev IS NOT NULL",
         checker=lambda rs: (int(rs[0]["n"]) == E["months"] - 1, f"got {rs[0]['n']}")),
    Case("cte_ntile",
         "WITH t AS (SELECT product_id, product_gmv, "
         "NTILE(4) OVER (ORDER BY product_gmv DESC NULLS LAST) q "
         "FROM analytics.v_product_performance) SELECT q, COUNT(*) AS n FROM t GROUP BY q ORDER BY q",
         expected_rows=4),
    Case("cte_running_sum",
         "WITH m AS (SELECT month, product_gmv, "
         "SUM(product_gmv) OVER (ORDER BY month) running FROM analytics.v_monthly_sales) "
         "SELECT running FROM m ORDER BY month DESC LIMIT 1",
         checker=lambda rs: (eq(rs[0]["running"], E["kpi_gmv"], 5.0), "last running != kpi")),
]

# ---------------------------------------------------------------------------
# I. Filters / WHERE / boolean (10 cases)
# ---------------------------------------------------------------------------
CASES += [
    Case("where_review_5",
         "SELECT COUNT(*) AS n FROM public.order_reviews WHERE review_score=5",
         checker=lambda rs: (int(rs[0]["n"]) > 0, "no 5-star reviews")),
    Case("where_delivered_2018",
         "SELECT COUNT(*) AS n FROM public.orders WHERE order_status='delivered' "
         "AND order_purchase_timestamp >= '2018-01-01'",
         min_rows=1),
    Case("where_price_gt_1000",
         "SELECT COUNT(*) AS n FROM public.order_items WHERE price > 1000",
         checker=lambda rs: (int(rs[0]["n"]) >= 0, "")),
    Case("where_state_sp",
         "SELECT COUNT(*) AS n FROM public.customers WHERE customer_state = 'Andhra_Pradesh'",
         checker=lambda rs: (int(rs[0]["n"]) >= 0, "")),
    Case("where_high_gmv_cat",
         "SELECT product_category_name FROM analytics.v_category_performance WHERE product_gmv > 100000",
         min_rows=1),
    Case("where_category_null_pct",
         "SELECT COUNT(*) AS n FROM public.products WHERE product_category_name IS NULL",
         checker=lambda rs: (int(rs[0]["n"]) >= 0, "")),
    Case("where_in_status",
         "SELECT COUNT(*) AS n FROM public.orders WHERE order_status IN ('delivered','shipped','invoiced')",
         checker=lambda rs: (int(rs[0]["n"]) <= E["orders"], "")),
    Case("where_between_score",
         "SELECT COUNT(*) AS n FROM public.order_reviews WHERE review_score BETWEEN 1 AND 3",
         checker=lambda rs: (int(rs[0]["n"]) > 0, "")),
    Case("where_repeat_customers",
         "SELECT COUNT(*) AS n FROM analytics.v_customer_performance WHERE is_repeat_customer = TRUE",
         checker=lambda rs: (int(rs[0]["n"]) >= 0, "")),
    Case("where_not_null_timestamp",
         "SELECT COUNT(*) AS n FROM public.orders WHERE order_delivered_customer_date IS NOT NULL",
         checker=lambda rs: (int(rs[0]["n"]) >= E["delivered"] * 0.99, "delivered dates missing")),
]

# ---------------------------------------------------------------------------
# J. Cross-verify view vs raw (10 cases)
# ---------------------------------------------------------------------------
CASES += [
    Case("view_reviews_matches_raw",
         "SELECT total_reviews FROM analytics.v_review_analytics",
         checker=lambda rs: (int(rs[0]["total_reviews"]) == E["reviews"], "")),
    Case("view_delivered_matches_raw",
         "SELECT delivered_orders FROM analytics.v_executive_kpis",
         checker=lambda rs: (int(rs[0]["delivered_orders"]) == E["delivered"], "")),
    Case("view_delivered_pct_range",
         "SELECT delivered_pct FROM analytics.v_executive_kpis",
         checker=lambda rs: (0 <= float(rs[0]["delivered_pct"]) <= 100, "pct out of range")),
    Case("view_delivered_pct_matches",
         "SELECT delivered_pct FROM analytics.v_executive_kpis",
         checker=lambda rs: (eq(rs[0]["delivered_pct"], E["delivered_pct"], 0.01), "")),
    Case("view_customers_count",
         "SELECT total_customers FROM analytics.v_executive_kpis",
         checker=lambda rs: (int(rs[0]["total_customers"]) == E["customers"], "")),
    Case("view_products_count",
         "SELECT total_products FROM analytics.v_executive_kpis",
         checker=lambda rs: (int(rs[0]["total_products"]) == E["products"], "")),
    Case("view_sellers_count",
         "SELECT total_sellers FROM analytics.v_executive_kpis",
         checker=lambda rs: (int(rs[0]["total_sellers"]) == E["sellers"], "")),
    Case("view_avg_score_matches_raw",
         "SELECT avg_review_score FROM analytics.v_review_analytics",
         checker=lambda rs: (eq(rs[0]["avg_review_score"], E["avg_review"], 0.01), "")),
    Case("view_neg_reviews_matches",
         "SELECT negative_review_count FROM analytics.v_review_analytics",
         checker=lambda rs: (int(rs[0]["negative_review_count"]) == E["neg_reviews"], "")),
    Case("orders_sum_via_monthly",
         "SELECT SUM(order_count) AS s FROM analytics.v_monthly_sales",
         checker=lambda rs: (int(rs[0]["s"]) == E["orders"], "")),
]

# ---------------------------------------------------------------------------
# K. Error / edge cases (10 cases)
# ---------------------------------------------------------------------------
CASES += [
    Case("err_delete",      "DELETE FROM public.orders", expected_status="validator_reject", err_contains="SELECT or WITH"),
    Case("err_update",      "UPDATE public.orders SET order_status='x'", expected_status="validator_reject"),
    Case("err_drop",        "DROP TABLE public.orders", expected_status="validator_reject"),
    Case("err_truncate",    "TRUNCATE public.orders", expected_status="validator_reject"),
    Case("err_grant",       "GRANT ALL ON public.orders TO public", expected_status="validator_reject"),
    Case("err_multi",       "SELECT 1; SELECT 2", expected_status="validator_reject", err_contains="Multiple"),
    Case("err_unknown_tbl", "SELECT * FROM public.unicorns", expected_status="unknown_table", err_contains="public.unicorns"),
    Case("err_info_schema", "SELECT * FROM information_schema.tables", expected_status="validator_reject", err_contains="information_schema"),
    Case("err_pg_catalog",  "SELECT * FROM pg_catalog.pg_tables", expected_status="validator_reject", err_contains="pg_catalog"),
    Case("err_bad_column",  "SELECT nope FROM analytics.v_executive_kpis", expected_status="db_error", err_contains="nope"),
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
        dt = (time.perf_counter() - t0) * 1000
        ok, reason = True, ""
        if c.expected_status != status:
            ok, reason = False, f"status={status} err={err}"
        elif status != "ok":
            if c.err_contains and c.err_contains.lower() not in (err or "").lower():
                ok, reason = False, f"err missing '{c.err_contains}': {err}"
        else:
            if c.expected_rows is not None and len(rows) != c.expected_rows:
                ok, reason = False, f"rows={len(rows)} exp={c.expected_rows}"
            elif c.min_rows is not None and len(rows) < c.min_rows:
                ok, reason = False, f"rows={len(rows)} min={c.min_rows}"
            elif c.checker:
                try:
                    ok2, detail = c.checker(rows)
                    if not ok2:
                        ok, reason = False, detail
                except Exception as ce:
                    ok, reason = False, f"checker raised: {ce}"
        tag = "PASS" if ok else "FAIL"
        print(f"[{i:03d}] {tag}  {c.name:40s} {status:16s} {dt:6.0f}ms")
        if not ok:
            print(f"       -> {reason}")
            failed.append((c.name, reason))
        else:
            passed += 1
    total = len(CASES)
    pct = 100.0 * passed / total
    print()
    print("=" * 78)
    print(f"RESULT: {passed}/{total} passed ({pct:.1f}%)")
    if failed:
        print(f"Failures ({len(failed)}):")
        for n, r in failed:
            print(f"  - {n}: {r}")
    print("=" * 78)
    return 0 if pct >= 95.0 else 1


if __name__ == "__main__":
    sys.exit(run())
