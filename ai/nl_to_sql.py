"""
Natural-Language → SQL generation via Gemini.

Sends only compact schema metadata + business rules (never dataset rows) and
returns a single read-only SELECT / WITH statement for the SQL validator to
police before it hits PostgreSQL.

Public surface:
    generate_sql_via_gemini(question: str, timeout_s: int = 12) -> str | None
    SCHEMA_PROMPT  # exported for debugging
"""
from __future__ import annotations

import concurrent.futures
import os
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Schema + business-rules prompt.  Kept small — the model needs structure and
# column names, NOT sample rows.
# ---------------------------------------------------------------------------
SCHEMA_PROMPT = """You are a senior PostgreSQL analytics engineer for an e-commerce marketplace.
Translate the user's natural-language business question into ONE safe read-only SQL query.

AVAILABLE ANALYTICS VIEWS (PostgreSQL, schema `analytics`):

analytics.v_executive_kpis  -- 1 row
  columns: total_orders, total_customers, total_unique_customers,
           total_products, total_sellers, product_gmv, cash_collected,
           avg_order_value_gmv, avg_order_value_cash,
           delivered_orders, delivered_pct, cancelled_orders, cancelled_pct,
           unavailable_orders, shipped_orders, invoiced_orders,
           processing_orders, created_orders, approved_orders

analytics.v_monthly_sales  -- one row per calendar month
  columns: month (date), order_count, unique_customers,
           product_gmv, cash_collected, aov_gmv, aov_cash

analytics.v_category_performance  -- one row per product_category_name
  columns: product_category_name, order_count, quantity_sold,
           product_gmv, item_revenue, freight_total,
           avg_item_price, avg_freight_value,
           review_count, avg_review_score,
           negative_review_count, negative_review_rate_pct, gmv_rank

analytics.v_product_performance  -- one row per product_id
  columns: product_id, product_category_name, quantity_sold, order_count,
           product_gmv, item_revenue, freight_total,
           avg_item_price, avg_freight_value,
           review_count, avg_review_score, negative_review_count, gmv_rank

analytics.v_customer_performance  -- one row per customer_unique_id
  columns: customer_unique_id, order_count, total_gmv, total_cash_collected,
           avg_order_value_gmv, first_order_date, latest_order_date,
           is_repeat_customer

analytics.v_review_analytics  -- 1 row
  columns: total_reviews, avg_review_score,
           rating_1_count, rating_2_count, rating_3_count,
           rating_4_count, rating_5_count,
           negative_review_count, negative_review_rate_pct

analytics.v_delivery_performance  -- 1 row
  columns: delivered_order_count, avg_delivery_days, median_delivery_days,
           late_delivery_count, late_delivery_rate_pct, on_time_rate_pct

analytics.v_monthly_delivery_performance  -- one row per month
  columns: month, delivered_order_count, avg_delivery_days,
           median_delivery_days, late_delivery_count,
           late_delivery_rate_pct, on_time_rate_pct

analytics.customer_segments  -- one row per customer_unique_id (Phase 3 ML output)
  columns: customer_unique_id, cluster_id, segment_label,
           recency_days, order_count, total_gmv, avg_order_value

BUSINESS DEFINITIONS:
- Product GMV = SUM(price + freight_value) from order_items.
- Cash Collected = SUM(payment_value) from order_payments.
- Negative review = review_score <= 2.
- Never claim causation. Never mention churn or LTV (excluded per decision D-007).

HARD CONSTRAINTS (violation = invalid response):
1. Output ONE statement only: SELECT ... or WITH ... SELECT ...
2. NO INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE,
   COPY, EXEC, VACUUM, CALL, SET, LOCK, or multi-statement input.
3. Reference only the views listed above (schema `analytics`).
   Never use pg_catalog, information_schema, or public tables directly.
4. Always include LIMIT (<=100) unless the view has exactly 1 row.
5. When ordering DESC, prefer `NULLS LAST`.
6. When ordering by `avg_review_score`, filter tiny-sample noise:
   - categories: WHERE review_count >= 100 AND avg_review_score IS NOT NULL
   - products:   WHERE review_count >= 10  AND avg_review_score IS NOT NULL
7. Never invent column names. Never use columns not listed above.
8. Do NOT wrap the SQL in code fences. Return ONLY the raw SQL, nothing else.
9. Do NOT include SQL comments (-- or /* */).

If the question cannot be answered from these views, return exactly:
  SELECT 'UNSUPPORTED' AS reason LIMIT 1

QUESTION: "{q}"
SQL:
"""


def _clean_sql_output(raw: str) -> str:
    """Strip code fences, prose lead-ins, trailing semicolons."""
    if not raw:
        return ""
    txt = raw.strip()
    # Strip ``` fences
    txt = re.sub(r"^```(?:sql|SQL)?\s*", "", txt)
    txt = re.sub(r"\s*```\s*$", "", txt)
    # If the model prefixed with "SQL:" or similar, drop it
    txt = re.sub(r"^(?:sql|query|answer)\s*[:\-]\s*", "", txt, flags=re.IGNORECASE)
    # Take everything up to (but not including) the first standalone semicolon
    # (validator will reject multi-statement anyway; this guards against trailing junk)
    if ";" in txt:
        head, _, _ = txt.partition(";")
        txt = head
    return txt.strip()


def generate_sql_via_gemini(question: str, timeout_s: int = 45) -> Optional[str]:
    """Call Gemini to translate ``question`` into a single read-only SQL statement.

    Returns the cleaned SQL string or ``None`` on any failure (missing API key,
    timeout, transport error, empty response). The caller MUST still run the
    result through ``ai.sql_validator.validate_sql`` before execution.
    """
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key or not question or not question.strip():
        return None
    model = os.getenv("LLM_MODEL") or "gemini-3.6-flash"

    try:
        from google import genai
        try:
            from google.genai import types  # type: ignore
        except Exception:
            types = None  # type: ignore

        client = genai.Client(api_key=api_key)
        prompt = SCHEMA_PROMPT.format(q=question.replace('"', "'").strip())

        def _gen():
            if types is not None:
                try:
                    cfg = types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=800,
                    )
                    return client.models.generate_content(
                        model=model, contents=prompt, config=cfg
                    )
                except Exception:
                    pass
            return client.models.generate_content(model=model, contents=prompt)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            resp = ex.submit(_gen).result(timeout=timeout_s)

        raw = getattr(resp, "text", None) or ""
        sql = _clean_sql_output(raw)
        if os.getenv("NL_SQL_DEBUG"):
            import sys
            print(f"[nl_to_sql] raw_len={len(raw)} clean_len={len(sql)}", file=sys.stderr)
        return sql or None
    except Exception as err:
        if os.getenv("NL_SQL_DEBUG"):
            import sys
            print(f"[nl_to_sql] error: {type(err).__name__}: {err}", file=sys.stderr)
        return None
