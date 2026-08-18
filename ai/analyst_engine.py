"""
AI Business Analyst Engine.
Routes questions, executes safe read-only analytics queries, and formats grounded business insights.
"""
from __future__ import annotations

import decimal
import json
import os
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, List, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ai.sql_validator import validate_sql
from ai.nl_interpreter import (
    interpret_question,
    build_query_from_intent,
    parse_intent_locally,
    _VIEW_MAP,
)
from ai.nl_to_sql import generate_sql_via_gemini

ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = (
    "You are an expert AI E-Commerce Business Analyst. "
    "Answer ONLY from the ground-truth data provided. Never invent numbers. "
    "Distinguish Product GMV (price+freight from order_items) from Cash Collected (order_payments). "
    "Negative review = review_score <= 2. Never claim causation. "
    "Never mention churn or LTV (excluded per decision D-007). "
    "Return concise executive answers in GitHub Markdown."
)

_DEFAULT_MODEL = "gemini-3.6-flash"


def _sanitize_data(data: Any) -> Any:
    if isinstance(data, list):
        return [_sanitize_data(item) for item in data]
    if isinstance(data, dict):
        return {k: _sanitize_data(v) for k, v in data.items()}
    if isinstance(data, decimal.Decimal):
        return float(data)
    if isinstance(data, float):
        return data
    if hasattr(data, "isoformat"):
        return data.isoformat()
    return data


def route_question_intent(question: str) -> Tuple[str, str, str]:
    """Legacy keyword router — used only as last-resort fallback."""
    q = question.lower()
    if any(w in q for w in ["kpi", "overview", "total gmv", "cash collected", "how many orders", "total revenue"]):
        return "kpi", "SELECT * FROM analytics.v_executive_kpis", "Executive KPIs View"
    if any(w in q for w in ["monthly", "trend", "sales over time", "by month"]):
        return "sales", "SELECT * FROM analytics.v_monthly_sales ORDER BY month ASC", "Monthly Sales View"
    if any(w in q for w in ["category", "categories"]):
        return "category", "SELECT * FROM analytics.v_category_performance ORDER BY product_gmv DESC LIMIT 10", "Category Performance View"
    if any(w in q for w in ["product", "products"]):
        return "product", "SELECT * FROM analytics.v_product_performance ORDER BY product_gmv DESC NULLS LAST LIMIT 10", "Product Performance View"
    if any(w in q for w in ["review", "rating", "satisfaction", "score", "stars"]):
        return "review", "SELECT * FROM analytics.v_review_analytics", "Review Analytics View"
    if any(w in q for w in ["delivery", "shipping", "late", "on-time", "sla", "delay"]):
        return "delivery", "SELECT * FROM analytics.v_delivery_performance", "Delivery Performance View"
    if any(w in q for w in ["segment", "clustering", "rfm"]):
        return "segment", "SELECT segment_label, COUNT(*) AS customer_count, ROUND(AVG(total_gmv)::numeric, 2) AS avg_gmv FROM analytics.customer_segments GROUP BY segment_label ORDER BY avg_gmv DESC", "Customer Segments Table"
    return "custom", "SELECT * FROM analytics.v_executive_kpis", "Default Executive View"


def execute_safe_query(engine: Engine, sql_query: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    """Validate and execute a SQL query against the PostgreSQL read-only engine."""
    is_valid, clean_sql_or_err = validate_sql(sql_query)
    if not is_valid:
        return False, [], clean_sql_or_err

    clean_sql = clean_sql_or_err
    try:
        with engine.connect() as conn:
            result = conn.execute(text(clean_sql))
            rows = [_sanitize_data(dict(r._mapping)) for r in result]
        return True, rows, clean_sql
    except Exception as e:
        return False, [], f"Database execution error: {str(e)[:200]}"


def _try_gemini_synthesis(question: str, data: Any, source_name: str,
                          api_key: str, model_name: str, timeout_s: int = 45) -> str | None:
    """Single Gemini synthesis call. Returns text or None on failure/timeout."""
    try:
        from google import genai
        try:
            from google.genai import types  # type: ignore
        except Exception:
            types = None  # type: ignore
        from ai import gemini_cache

        client = genai.Client(api_key=api_key)

        payload = data
        if isinstance(data, list) and len(data) > 10:
            payload = data[:10]
        data_json = json.dumps(_sanitize_data(payload), default=str)

        prompt = (
            f'Question: "{question}"\n'
            f"Source: {source_name}\n"
            f"Ground-truth data (JSON, up to 10 rows):\n{data_json}\n\n"
            "Write a concise executive answer in GitHub Markdown with:\n"
            "**Key Numbers**, **Insight**, **Recommendation**. "
            "Keep under 180 words. Use ONLY numbers present in the data."
        )

        cached = gemini_cache.get(model_name, prompt)
        if cached:
            return cached

        config = None
        if types is not None:
            try:
                config = types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.2,
                    max_output_tokens=600,
                )
            except Exception:
                config = None

        def _gen():
            if config is not None:
                return client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
            return client.models.generate_content(
                model=model_name, contents=f"{SYSTEM_PROMPT}\n\n{prompt}"
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            resp = executor.submit(_gen).result(timeout=timeout_s)
        text = resp.text if getattr(resp, "text", None) else None
        if text:
            gemini_cache.put(model_name, prompt, text)
        return text
    except Exception:
        return None


def call_llm_for_synthesis(question: str, data: Any, source_name: str) -> str:
    """Synthesize a grounded business explanation via Gemini, else deterministic fallback."""
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
    model_name = os.environ.get("LLM_MODEL") or _DEFAULT_MODEL

    if api_key:
        text_answer = _try_gemini_synthesis(question, data, source_name, api_key, model_name)
        if text_answer:
            return text_answer

    return format_grounded_fallback(question, data, source_name)


def format_grounded_fallback(question: str, data: Any, source_name: str) -> str:
    """Deterministic, structured business answer formatted directly from ground-truth data."""
    if isinstance(data, list) and len(data) == 1:
        d = data[0]
        if "product_gmv" in d and "total_orders" in d:
            return (
                f"### Business Answer\n\n"
                f"Based on the **{source_name}**, the platform has recorded **{d['total_orders']:,} orders** "
                f"with **Product GMV of {d['product_gmv']:,.2f} units** and **Cash Collected of {d['cash_collected']:,.2f} units**.\n\n"
                f"### Key Numbers\n"
                f"* Total Orders: {d['total_orders']:,}\n"
                f"* Unique Customers: {d['total_unique_customers']:,}\n"
                f"* Product GMV: {d['product_gmv']:,.2f}\n"
                f"* Cash Collected: {d['cash_collected']:,.2f}\n"
                f"* AOV (GMV): {d['avg_order_value_gmv']:,.2f}\n"
                f"* Delivered: {d['delivered_orders']:,} ({d['delivered_pct']}%)\n"
            )
        if "total_reviews" in d:
            return (
                f"### Business Answer\n\n"
                f"Based on **{source_name}**, **{d['total_reviews']:,} reviews** with average "
                f"**{d['avg_review_score']} / 5.0**. Negative-review rate: **{d['negative_review_rate_pct']}%**.\n"
            )
        if "delivered_order_count" in d:
            return (
                f"### Business Answer\n\n"
                f"Based on **{source_name}**, avg delivery **{d['avg_delivery_days']} days**, "
                f"on-time rate **{d['on_time_rate_pct']}%**, late-delivery rate **{d['late_delivery_rate_pct']}%**.\n"
            )

    if source_name == "Phase 3 Sales Forecast Artifact":
        fc = data.get("forward_forecast", []) if isinstance(data, dict) else []
        return (
            f"### Business Answer\n\n"
            f"Based on the **Phase 3 Sales Forecast** model, next-3-month GMV:\n\n"
            + "\n".join([
                f"* **{item['month']}**: {item['forecast_gmv']:,.2f} "
                f"(95% CI {item['lower_ci_95']:,.2f}..{item['upper_ci_95']:,.2f})"
                for item in fc
            ])
        )

    if isinstance(data, list) and data:
        lines = [f"### Business Answer\n\nRetrieved **{len(data)} records** from **{source_name}** for: *\"{question}\"*.\n"]
        lines.append("### Key Rows")
        for row in data[:10]:
            label = (row.get("product_category_name")
                     or row.get("product_id")
                     or row.get("segment_label")
                     or row.get("customer_unique_id")
                     or row.get("month")
                     or "Record")
            metric_bits = []
            for k in ("product_gmv", "quantity_sold", "review_count", "avg_review_score",
                      "order_count", "avg_delivery_days", "late_delivery_rate_pct"):
                if k in row and row[k] is not None:
                    v = row[k]
                    metric_bits.append(f"{k}={v:,.2f}" if isinstance(v, (int, float)) else f"{k}={v}")
            lines.append(f"* `{label}` — {', '.join(metric_bits) if metric_bits else 'record'}")
        return "\n".join(lines)

    return f"### Business Answer\n\nNo results for *\"{question}\"* from **{source_name}**."


def _forecast_intent(q_lower: str) -> bool:
    return any(w in q_lower for w in ["forecast", "predict sales", "next three months", "next 3 months", "future sales"])


def process_analyst_question(engine: Engine, question: str) -> dict:
    """
    Main pipeline for processing an AI Analyst question.

    Flow:
      1. Deterministic local intent parser (fast path, no LLM).
      2. If local parse fails, ask Gemini to extract structured intent (single LLM call).
      3. If both fail, use legacy keyword router.
      4. Execute the resulting safe SQL.
      5. One Gemini synthesis call (with deterministic fallback).
    """
    q_lower = question.lower()

    # Forecast branch (special-case artifact, not SQL)
    if _forecast_intent(q_lower):
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
                "insights": ["Baseline evaluated on 25 months of history; horizon 3 months forward."],
            }

    sql: str | None = None
    source_desc: str | None = None
    intent_source: str = "local"

    # 1. Primary path: deterministic local parser. When it recognizes the
    #    question, the generated SQL is guaranteed correct (right ORDER BY,
    #    right LIMIT, right filter). No hallucination risk. Preferred over
    #    Gemini because Gemini occasionally drops ORDER BY or invents columns,
    #    producing plausible-but-wrong answers.
    intent = parse_intent_locally(question)
    if intent:
        built = build_query_from_intent(intent)
        if built:
            is_valid, cleaned = validate_sql(built)
            if is_valid:
                sql = cleaned
                source_desc = f"{_VIEW_MAP.get(intent['entity'], 'analytics view')} (local parser)"

    # 2. Fallback: Gemini generates SQL from schema + rules. Only used when
    #    the local parser can't understand the question.
    if not sql:
        gemini_sql = generate_sql_via_gemini(question)
        if gemini_sql:
            is_valid, cleaned = validate_sql(gemini_sql)
            if is_valid:
                sql = cleaned
                source_desc = "Dynamic SQL (Gemini NL→SQL)"
                intent_source = "gemini-sql"
            else:
                _validator_reason = cleaned

    # 3. Last-resort keyword router (only when both above fail).
    if not sql:
        _, legacy_sql, legacy_desc = route_question_intent(question)
        is_valid, cleaned = validate_sql(legacy_sql)
        if is_valid:
            sql = cleaned
            source_desc = f"{legacy_desc} (keyword fallback)"
            intent_source = "legacy"

    if not sql:
        return {
            "question": question,
            "answer": "⚠️ Unable to generate a safe SQL query for this question. Try rephrasing.",
            "data": [],
            "source": source_desc or "Unknown",
            "sql_used": None,
            "insights": ["NL→SQL generation and all fallbacks failed."],
        }

    is_ok, query_data, sql_used = execute_safe_query(engine, sql)
    if not is_ok:
        return {
            "question": question,
            "answer": f"⚠️ **Query error**: {sql_used}",
            "data": [],
            "source": source_desc or "Unknown",
            "sql_used": sql,
            "insights": ["Query failed safety validation or execution."],
        }

    # Handle the "unsupported" sentinel Gemini may return.
    if len(query_data) == 1 and str(query_data[0].get("reason", "")).upper() == "UNSUPPORTED":
        return {
            "question": question,
            "answer": ("⚠️ This question can't be answered from the current analytics views. "
                       "Try rephrasing or ask about products, categories, customers, sales, "
                       "reviews, delivery, or KPIs."),
            "data": [],
            "source": source_desc or "Unknown",
            "sql_used": sql_used,
            "insights": ["Gemini marked the request as unsupported by available schema."],
        }

    answer = call_llm_for_synthesis(question, query_data, source_desc or "Analytics View")

    return {
        "question": question,
        "answer": answer,
        "data": query_data[:20],
        "source": source_desc or "Analytics View",
        "sql_used": sql_used,
        "insights": [
            f"Data grounded in PostgreSQL analytics warehouse (path: {intent_source}).",
        ],
    }
