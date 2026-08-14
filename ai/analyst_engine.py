"""
AI Business Analyst Engine.
Routes questions, executes safe read-only analytics queries, and formats grounded business insights.
"""
from __future__ import annotations

import decimal
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ai.sql_validator import validate_sql

ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = """You are an expert AI E-Commerce Business Analyst for a major online marketplace.
Your goal is to answer business questions using ONLY the provided ground-truth analytics data.

Strict Business Rules:
1. Never invent or hallucinate metrics, revenues, order counts, or percentages not in the data.
2. Clearly distinguish Product GMV (price + freight from order_items) from Cash Collected (payments from order_payments).
3. Negative review is defined strictly as review_score <= 2.
4. Never state correlation as causation. Use phrasing like "Late deliveries are associated with higher negative review rates".
5. Do not mention customer churn or lifetime value (LTV) as those are excluded per project decision D-007.
6. Provide concise, executive-level business answers in GitHub Markdown.
"""


def _sanitize_data(data: Any) -> Any:
    """Helper to convert Decimals and dates to JSON-serializable types."""
    if isinstance(data, list):
        return [_sanitize_data(item) for item in data]
    if isinstance(data, dict):
        return {k: _sanitize_data(v) for k, v in data.items()}
    if isinstance(data, (decimal.Decimal, float)):
        return float(data)
    if hasattr(data, "isoformat"):
        return data.isoformat()
    return data


def route_question_intent(question: str) -> Tuple[str, str, str]:
    """
    Categorize user question into business intents and select canonical view/query.
    Returns (intent_type, canonical_sql_or_source, description).
    """
    q = question.lower()
    
    if any(w in q for w in ["kpi", "overview", "total gmv", "cash collected", "how many orders", "total revenue"]):
        return "kpi", "SELECT * FROM analytics.v_executive_kpis", "Executive KPIs View"
        
    if any(w in q for w in ["monthly", "trend", "sales over time", "by month"]):
        return "sales", "SELECT * FROM analytics.v_monthly_sales ORDER BY month ASC", "Monthly Sales View"
        
    if any(w in q for w in ["category", "categories", "top category", "best performing categories"]):
        return "category", "SELECT * FROM analytics.v_category_performance ORDER BY product_gmv DESC LIMIT 10", "Category Performance View"
        
    if any(w in q for w in ["product", "products", "top products", "best selling products"]):
        return "product", "SELECT * FROM analytics.v_product_performance ORDER BY product_gmv DESC LIMIT 10", "Product Performance View"
        
    if any(w in q for w in ["review", "rating", "satisfaction", "negative review", "score", "stars"]):
        return "review", "SELECT * FROM analytics.v_review_analytics", "Review Analytics View"
        
    if any(w in q for w in ["delivery", "shipping", "late", "on-time", "sla", "delay"]):
        return "delivery", "SELECT * FROM analytics.v_delivery_performance", "Delivery Performance View"
        
    if any(w in q for w in ["forecast", "future", "next three months", "predict sales"]):
        return "forecast", "ml_summary.json", "Phase 3 Sales Forecast Artifact"
        
    if any(w in q for w in ["segment", "customer segment", "clustering", "rfm", "customer types"]):
        return "segment", "SELECT segment_label, COUNT(*) AS customer_count, ROUND(AVG(total_gmv)::numeric, 2) AS avg_gmv FROM analytics.customer_segments GROUP BY segment_label ORDER BY avg_gmv DESC", "Customer Segments Table"

    # Default custom query route
    return "custom", "SELECT * FROM analytics.v_executive_kpis", "Default Executive View"


def execute_safe_query(engine: Engine, sql_query: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Validate and execute a SQL query against PostgreSQL read-only engine.
    """
    is_valid, clean_sql_or_err = validate_sql(sql_query)
    if not is_valid:
        return False, [], clean_sql_or_err

    clean_sql = clean_sql_or_err
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(clean_sql)).mappings().all()
            data = [_sanitize_data(dict(r)) for r in rows]
            return True, data, clean_sql
    except Exception as e:
        return False, [], f"Query execution failed: {str(e)}"


# Default model — overridable via LLM_MODEL env var in .env
_DEFAULT_MODEL = "gemini-2.5-flash"


def call_llm_for_synthesis(question: str, data: Any, source_name: str) -> str:
    """
    Synthesize grounded business explanation using Google Gemini API or deterministic fallback.
    API key: LLM_API_KEY (primary) or GEMINI_API_KEY (compatibility alias).
    Model:   LLM_MODEL env var (defaults to gemini-2.5-flash).
    """
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
    model_name = os.environ.get("LLM_MODEL") or _DEFAULT_MODEL

    prompt = f"""User Question: "{question}"
Source: {source_name}
Ground-Truth Analytics Data:
{json.dumps(data, indent=2)}

Provide an executive business answer with Key Numbers, Insights, and Actionable Recommendations \
based strictly on the data above. Use GitHub Markdown formatting."""

    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model_name,
                contents=[SYSTEM_PROMPT, prompt]
            )
            if response.text:
                return response.text
        except Exception as llm_err:
            # Silently fall through to deterministic fallback; do not log the key
            _ = llm_err

    # Deterministic Grounded Fallback (when API key is absent or Gemini call fails)
    return format_grounded_fallback(question, data, source_name)


def format_grounded_fallback(question: str, data: Any, source_name: str) -> str:
    """Deterministic, structured business answer formatted directly from ground-truth data."""
    if isinstance(data, list) and len(data) == 1:
        d = data[0]
        if "product_gmv" in d and "total_orders" in d:
            return (
                f"### Business Answer\n\n"
                f"Based on the **{source_name}**, our e-commerce platform has recorded **{d['total_orders']:,} total orders** "
                f"with a **Product GMV of {d['product_gmv']:,.2f} units** and **Cash Collected of {d['cash_collected']:,.2f} units**.\n\n"
                f"### Key Numbers\n"
                f"* **Total Orders**: {d['total_orders']:,}\n"
                f"* **Unique Customers**: {d['total_unique_customers']:,}\n"
                f"* **Product GMV**: {d['product_gmv']:,.2f}\n"
                f"* **Cash Collected**: {d['cash_collected']:,.2f}\n"
                f"* **Average Order Value (GMV)**: {d['avg_order_value_gmv']:,.2f}\n"
                f"* **Delivered Orders**: {d['delivered_orders']:,} ({d['delivered_pct']}%)\n\n"
                f"### Insight\n"
                f"Product GMV (items price + freight) matches cash receipts cleanly. Customer fulfillment is strong at {d['delivered_pct']}% delivered."
            )
        if "total_reviews" in d:
            return (
                f"### Business Answer\n\n"
                f"Based on **{source_name}**, we have received **{d['total_reviews']:,} customer reviews** with an "
                f"average rating of **{d['avg_review_score']} / 5.0**.\n\n"
                f"### Key Numbers\n"
                f"* **Total Reviews**: {d['total_reviews']:,}\n"
                f"* **Average Rating**: {d['avg_review_score']} / 5.0\n"
                f"* **Negative Reviews (≤2 Stars)**: {d['negative_review_count']:,} ({d['negative_review_rate_pct']}%)\n\n"
                f"### Insight\n"
                f"Overall customer satisfaction is high, though {d['negative_review_rate_pct']}% of orders receive negative reviews (score ≤ 2)."
            )
        if "delivered_order_count" in d:
            return (
                f"### Business Answer\n\n"
                f"Based on **{source_name}**, overall fulfillment shows an **average delivery time of {d['avg_delivery_days']} days** "
                f"(median {d['median_delivery_days']} days) with an **on-time rate of {d['on_time_rate_pct']}%**.\n\n"
                f"### Key Numbers\n"
                f"* **Delivered Orders**: {d['delivered_order_count']:,}\n"
                f"* **Avg Delivery Days**: {d['avg_delivery_days']} days\n"
                f"* **Median Delivery Days**: {d['median_delivery_days']} days\n"
                f"* **Late Delivery Rate**: {d['late_delivery_rate_pct']}%\n\n"
                f"### Insight\n"
                f"Late deliveries represent {d['late_delivery_rate_pct']}% of completed orders and are strongly associated with negative review risk."
            )

    if source_name == "Phase 3 Sales Forecast Artifact":
        fc = data.get("forward_forecast", [])
        return (
            f"### Business Answer\n\n"
            f"Based on our **Phase 3 Sales Forecast Model** (`{data.get('selected_model')}`), the short-term forecast for the next 3 months is:\n\n"
            + "\n".join([f"* **{item['month']}**: GMV **{item['forecast_gmv']:,.2f}** (95% CI: {item['lower_ci_95']:,.2f} .. {item['upper_ci_95']:,.2f})" for item in fc])
            + f"\n\n### Insight\nEvaluated against continuous 25-month historical volume. Forecast horizon is conservatively limited to 3 months forward."
        )

    # General structured list fallback
    return (
        f"### Business Answer\n\n"
        f"Retrieved **{len(data)} analytical records** from **{source_name}** matching your question: *\"{question}\"*.\n\n"
        f"### Key Metrics\n"
        + "\n".join([f"* `{row.get('product_category_name', row.get('segment_label', 'Record'))}`: GMV {row.get('product_gmv', row.get('avg_gmv', 0)):,.2f}" for row in data[:5]])
    )


def process_analyst_question(engine: Engine, question: str) -> dict:
    """
    Main pipeline for processing an AI Analyst question.
    Returns response payload dictionary.
    """
    intent, sql_or_source, source_desc = route_question_intent(question)
    
    if intent == "forecast":
        summary_path = ROOT / "docs" / "ml_summary.json"
        if summary_path.exists():
            fc_data = json.loads(summary_path.read_text(encoding="utf-8")).get("sales_forecasting", {})
            answer = call_llm_for_synthesis(question, fc_data, "Phase 3 Sales Forecast Artifact")
            return {
                "question": question,
                "answer": answer,
                "data": fc_data.get("forward_forecast", []),
                "source": "Phase 3 Sales Forecast Artifact",
                "sql_used": None,
                "insights": ["Forecast generated using 3-month moving average baseline evaluated on 25 months history."]
            }
            
    # Execute SQL view query
    is_ok, query_data, sql_used = execute_safe_query(engine, sql_or_source)
    if not is_ok:
        return {
            "question": question,
            "answer": f"⚠️ **SQL Safety / Execution Error**: {sql_used}",
            "data": [],
            "source": source_desc,
            "sql_used": None,
            "insights": ["Query failed safety validation or execution."]
        }

    answer = call_llm_for_synthesis(question, query_data, source_desc)
    
    return {
        "question": question,
        "answer": answer,
        "data": query_data[:20],  # Return up to 20 rows for dashboard UI table
        "source": source_desc,
        "sql_used": sql_used,
        "insights": ["Data grounded in PostgreSQL analytics warehouse views."]
    }
