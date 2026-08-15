"""Natural-Language Interpreter for the AI Analyst.

Provides three surfaces:

- ``parse_intent_locally``  — fast, deterministic rule-based parser (no LLM).
- ``interpret_question``    — single Gemini call that returns a structured intent
                              (used only when the local parser cannot understand).
- ``build_query_from_intent`` — deterministically translates a structured intent
                              into a safe read-only SELECT against known analytics views.

Structured intent shape (all optional except entity + metric for a ranking):
    {
      "entity":         "products|categories|customers|sales|reviews|delivery|kpis",
      "metric":         "product_gmv|quantity_sold|order_count|review_count|avg_review_score|...",
      "sort_direction": "asc|desc|none",
      "limit":          int (1..100),
      "filters":        {},
      "time_period":    "",
      "question_type":  "ranking|trend|comparison|lookup|summary|other"
    }
"""
from __future__ import annotations

import os
import re
import json
from typing import Any, Dict, Optional

# --- Entity keywords → analytics view -------------------------------------
_VIEW_MAP: Dict[str, str] = {
    "products":  "analytics.v_product_performance",
    "categories": "analytics.v_category_performance",
    "customers": "analytics.v_customer_performance",
    "sales":     "analytics.v_monthly_sales",
    "reviews":   "analytics.v_review_analytics",
    "delivery":  "analytics.v_delivery_performance",
    "kpis":      "analytics.v_executive_kpis",
    "forecast":  "ml_summary.json",  # handled outside SQL path
}

# Singular / alternative forms map to canonical entity keys above
_ENTITY_ALIASES: Dict[str, str] = {
    "product": "products", "products": "products", "item": "products", "items": "products", "sku": "products", "skus": "products",
    "category": "categories", "categories": "categories",
    "customer": "customers", "customers": "customers", "buyer": "customers", "buyers": "customers",
    "sale": "sales", "sales": "sales", "revenue": "sales", "monthly": "sales",
    "review": "reviews", "reviews": "reviews", "rating": "reviews", "ratings": "reviews",
    "delivery": "delivery", "shipping": "delivery", "fulfillment": "delivery",
    "kpi": "kpis", "kpis": "kpis", "overview": "kpis",
    "forecast": "forecast",
}

# Metric column canonical set (must exist in the matched view)
_VIEW_COLUMNS: Dict[str, set] = {
    "analytics.v_product_performance": {
        "quantity_sold", "order_count", "product_gmv", "item_revenue",
        "freight_total", "avg_item_price", "avg_freight_value",
        "review_count", "avg_review_score", "negative_review_count",
    },
    "analytics.v_category_performance": {
        "order_count", "quantity_sold", "product_gmv", "item_revenue",
        "freight_total", "avg_item_price", "avg_freight_value",
        "review_count", "avg_review_score", "negative_review_count",
        "negative_review_rate_pct",
    },
    "analytics.v_customer_performance": {
        "order_count", "total_gmv", "total_cash_collected", "avg_order_value_gmv",
    },
    "analytics.v_monthly_sales": {
        "order_count", "unique_customers", "product_gmv", "cash_collected",
        "aov_gmv", "aov_cash",
    },
    "analytics.v_delivery_performance": {
        "delivered_order_count", "avg_delivery_days", "median_delivery_days",
        "late_delivery_count", "late_delivery_rate_pct", "on_time_rate_pct",
    },
    "analytics.v_review_analytics": {
        "total_reviews", "avg_review_score", "rating_1_count", "rating_2_count",
        "rating_3_count", "rating_4_count", "rating_5_count",
        "negative_review_count", "negative_review_rate_pct",
    },
    "analytics.v_executive_kpis": set(),  # scalar view
}

# Word → canonical metric column
_METRIC_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    # order matters: more specific first
    (("gmv", "revenue", "sales_value"),                       "product_gmv"),
    (("cash", "collected", "payments"),                       "cash_collected"),
    (("quantity", "sold", "units", "volume"),                 "quantity_sold"),
    (("orders", "order"),                                     "order_count"),
    (("review_count", "reviewed", "reviews"),                 "review_count"),
    (("rating", "rated", "score", "stars", "satisfaction"),   "avg_review_score"),
    (("delivery_days", "delivery"),                           "avg_delivery_days"),
    (("late", "late_rate"),                                   "late_delivery_rate_pct"),
    (("on_time", "on-time", "sla"),                           "on_time_rate_pct"),
]

_ASC_WORDS  = {"least", "lowest", "worst", "bottom", "fewest", "smallest", "poorest", "weakest"}
_DESC_WORDS = {"most", "highest", "best", "top", "largest", "biggest", "greatest", "strongest"}
_RANKING_HINTS = _ASC_WORDS | _DESC_WORDS | {"rank", "ranking", "which"}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twenty": 20, "fifty": 50, "hundred": 100,
}


def _tokens(question: str) -> list[str]:
    return re.findall(r"[a-z0-9\-]+", question.lower())


def _detect_entity(tokens: list[str]) -> Optional[str]:
    for t in tokens:
        if t in _ENTITY_ALIASES:
            return _ENTITY_ALIASES[t]
    return None


def _detect_limit(tokens: list[str], q_lower: str) -> Optional[int]:
    # explicit digit
    m = re.search(r"\btop\s+(\d{1,3})\b", q_lower) or re.search(r"\bbottom\s+(\d{1,3})\b", q_lower) \
        or re.search(r"\bfirst\s+(\d{1,3})\b", q_lower) or re.search(r"\blast\s+(\d{1,3})\b", q_lower) \
        or re.search(r"\b(\d{1,3})\s+(?:products?|categories|customers|categor)", q_lower) \
        or re.search(r"\b(\d{1,3})\b", q_lower)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 100:
                return n
        except ValueError:
            pass
    for w in tokens:
        if w in _NUMBER_WORDS:
            return _NUMBER_WORDS[w]
    return None


def _detect_direction(tokens: list[str]) -> Optional[str]:
    tset = set(tokens)
    if tset & _ASC_WORDS:
        return "asc"
    if tset & _DESC_WORDS:
        return "desc"
    return None


def _detect_metric(tokens: list[str], q_lower: str) -> Optional[str]:
    # explicit "by <metric>" wins
    m = re.search(r"\bby\s+([a-z_]+)", q_lower)
    if m:
        candidate = m.group(1)
        for keywords, col in _METRIC_KEYWORDS:
            if candidate in keywords:
                return col
    # word-by-word scan
    for keywords, col in _METRIC_KEYWORDS:
        for kw in keywords:
            if kw in tokens or kw in q_lower:
                return col
    return None


def parse_intent_locally(question: str) -> Optional[Dict[str, Any]]:
    """Rule-based interpreter. Returns intent dict or None if not confident."""
    if not question or not question.strip():
        return None
    q_lower = question.lower().strip()
    tokens = _tokens(q_lower)

    entity = _detect_entity(tokens)
    if not entity:
        return None
    if entity == "forecast":
        return None  # handled by process_analyst_question forecast branch

    direction = _detect_direction(tokens)
    limit = _detect_limit(tokens, q_lower)
    metric = _detect_metric(tokens, q_lower)

    is_ranking = bool(direction) or (limit is not None) or any(w in tokens for w in _RANKING_HINTS)

    # For entity-only summary questions (no direction/metric/ranking) use scalar/summary views.
    if entity in ("reviews", "delivery", "kpis", "sales") and not is_ranking and metric is None:
        return {
            "entity": entity, "metric": None, "sort_direction": "none",
            "limit": 100, "filters": {}, "time_period": "",
            "question_type": "summary",
        }

    # For ranking questions we require a metric
    if is_ranking and metric is None:
        # heuristic defaults per entity
        default_metric = {
            "products":   "product_gmv",
            "categories": "product_gmv",
            "customers":  "total_gmv",
        }.get(entity)
        if default_metric is None:
            return None
        metric = default_metric

    if metric is None:
        return None

    # Direction defaults: asc-words → asc; desc-words or absent → desc for ranking
    if direction is None:
        if metric == "avg_review_score":
            direction = "desc"  # 'best rated' if unspecified
        else:
            direction = "desc"

    return {
        "entity": entity,
        "metric": metric,
        "sort_direction": direction,
        "limit": limit if limit is not None else 10,
        "filters": {},
        "time_period": "",
        "question_type": "ranking" if is_ranking else "lookup",
    }


# ---------------------------------------------------------------------------
# Gemini fallback for ambiguous questions (single lightweight call)
# ---------------------------------------------------------------------------

_GEMINI_INTENT_PROMPT = (
    "Convert this business question into a compact JSON object with EXACT keys "
    "entity, metric, sort_direction, limit, filters, time_period, question_type.\n"
    "Allowed entity values: products, categories, customers, sales, reviews, delivery, kpis.\n"
    "Allowed metric values: product_gmv, quantity_sold, order_count, review_count, "
    "avg_review_score, avg_delivery_days, late_delivery_rate_pct, on_time_rate_pct, "
    "total_gmv, cash_collected.\n"
    "sort_direction ∈ asc|desc|none. limit is int 1..100. "
    "If a value cannot be inferred use null (or {} for filters).\n"
    "Respond with ONLY the JSON, no prose, no code fences.\n"
    'Question: "{q}"'
)


def _call_gemini_for_intent(question: str) -> Optional[Dict[str, Any]]:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("LLM_MODEL") or "gemini-2.5-flash"
    try:
        import concurrent.futures
        from google import genai
        try:
            from google.genai import types  # type: ignore
        except Exception:
            types = None  # type: ignore

        client = genai.Client(api_key=api_key)
        prompt = _GEMINI_INTENT_PROMPT.format(q=question.replace('"', "'"))

        def _gen():
            if types is not None:
                try:
                    cfg = types.GenerateContentConfig(temperature=0.0, max_output_tokens=250)
                    return client.models.generate_content(model=model, contents=prompt, config=cfg)
                except Exception:
                    pass
            return client.models.generate_content(model=model, contents=prompt)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            resp = ex.submit(_gen).result(timeout=8)
        text = (resp.text or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception:
        return None


def interpret_question(question: str) -> Optional[Dict[str, Any]]:
    """Return a structured intent dict from Gemini, or None."""
    intent = _call_gemini_for_intent(question)
    if not isinstance(intent, dict):
        return None
    required = {"entity", "metric", "sort_direction"}
    if not required.issubset(intent.keys()):
        return None
    # normalize
    ent = str(intent.get("entity") or "").lower()
    intent["entity"] = _ENTITY_ALIASES.get(ent, ent)
    intent.setdefault("limit", 10)
    intent.setdefault("filters", {})
    intent.setdefault("time_period", "")
    intent.setdefault("question_type", "ranking")
    return intent


# ---------------------------------------------------------------------------
# Safe SQL builder
# ---------------------------------------------------------------------------

def build_query_from_intent(intent: Dict[str, Any]) -> Optional[str]:
    """Deterministically build a safe read-only SELECT from a structured intent."""
    if not isinstance(intent, dict):
        return None
    entity = intent.get("entity")
    view = _VIEW_MAP.get(entity)
    if not view or entity == "forecast":
        return None

    metric = intent.get("metric")
    direction = (intent.get("sort_direction") or "none").lower()
    try:
        limit = int(intent.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 100))

    valid_cols = _VIEW_COLUMNS.get(view, set())

    # Scalar/summary views: SELECT * (no ORDER BY, no WHERE)
    if not valid_cols:
        return f"SELECT * FROM {view} LIMIT 1"

    # Summary intent → return the whole small view
    if intent.get("question_type") == "summary" or (metric is None and direction == "none"):
        return f"SELECT * FROM {view} LIMIT {limit}"

    if metric not in valid_cols:
        # Entity-specific remap for near-synonyms
        entity_remap = {
            ("customers", "product_gmv"): "total_gmv",
            ("customers", "cash_collected"): "total_cash_collected",
            ("sales", "quantity_sold"): "order_count",
        }.get((entity, metric))
        if entity_remap and entity_remap in valid_cols:
            metric = entity_remap
        else:
            alias = {
                "gmv": "product_gmv", "revenue": "product_gmv",
                "quantity": "quantity_sold", "units_sold": "quantity_sold",
                "reviews": "review_count", "rating": "avg_review_score",
            }.get(str(metric).lower())
            if alias and alias in valid_cols:
                metric = alias
            else:
                return None

    where_parts: list[str] = []
    if metric == "avg_review_score":
        if entity == "categories" and "review_count" in valid_cols:
            where_parts.append("review_count >= 100")
        elif entity == "products" and "review_count" in valid_cols:
            where_parts.append("review_count >= 10")
        where_parts.append("avg_review_score IS NOT NULL")

    order_clause = ""
    if direction in ("asc", "desc"):
        order_clause = f" ORDER BY {metric} {direction.upper()} NULLS LAST"

    where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return f"SELECT * FROM {view}{where_clause}{order_clause} LIMIT {limit}"
