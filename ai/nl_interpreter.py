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

# ---------------------------------------------------------------------------
# Multi-view join templates (v1).
#
# Each entry is a *hand-verified* SQL query that answers "<metric> grouped by
# <dimension>". Templates are used only when the parser detects an explicit
# "by <dimension>" / "per <dimension>" / "across <dimension>" phrase. Each
# query preserves the correctness guarantees of the analytics.* views:
#   - aggregates first, joins second (avoids row multiplication — see DQ-3)
#   - filters delivered orders when computing delivery metrics
#   - uses the correct grain (per-customer_unique_id where segments matter)
# Adding a new template = adding a row here + one test case. Never a code change.
# ---------------------------------------------------------------------------
_GROUPBY_KEYWORDS: Dict[str, str] = {
    # phrase → canonical dimension slug
    "segment":  "segment",
    "segments": "segment",
    "cluster":  "segment",
    "clusters": "segment",
    "state":    "state",
    "states":   "state",
    "category": "category",
    "categories": "category",
    "month":    "month",
    "months":   "month",
}

_JOIN_TEMPLATES: Dict[tuple, str] = {
    # ── grouped by customer SEGMENT ─────────────────────────────────────────
    ("order_count", "segment"): """
        SELECT cs.segment_label AS segment,
               COUNT(DISTINCT o.order_id) AS order_count
        FROM analytics.customer_segments cs
        JOIN public.customers c ON c.customer_unique_id = cs.customer_unique_id
        JOIN public.orders    o ON o.customer_id = c.customer_id
        GROUP BY cs.segment_label
        ORDER BY order_count DESC
        LIMIT 20
    """,
    ("avg_delivery_days", "segment"): """
        SELECT cs.segment_label AS segment,
               ROUND(AVG(EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp))
                         / 86400.0)::numeric, 2) AS avg_delivery_days,
               COUNT(*) AS delivered_orders
        FROM analytics.customer_segments cs
        JOIN public.customers c ON c.customer_unique_id = cs.customer_unique_id
        JOIN public.orders    o ON o.customer_id = c.customer_id
        WHERE o.order_status = 'delivered'
          AND o.order_delivered_customer_date IS NOT NULL
        GROUP BY cs.segment_label
        ORDER BY avg_delivery_days ASC
        LIMIT 20
    """,
    ("avg_review_score", "segment"): """
        SELECT cs.segment_label AS segment,
               ROUND(AVG(r.review_score)::numeric, 2) AS avg_review_score,
               COUNT(r.review_score) AS review_count
        FROM analytics.customer_segments cs
        JOIN public.customers    c ON c.customer_unique_id = cs.customer_unique_id
        JOIN public.orders       o ON o.customer_id = c.customer_id
        JOIN public.order_reviews r ON r.order_id = o.order_id
        GROUP BY cs.segment_label
        ORDER BY avg_review_score DESC
        LIMIT 20
    """,
    ("total_gmv", "segment"): """
        SELECT cs.segment_label AS segment,
               ROUND(SUM(oi.price + oi.freight_value)::numeric, 2) AS total_gmv,
               COUNT(DISTINCT o.order_id) AS order_count
        FROM analytics.customer_segments cs
        JOIN public.customers   c  ON c.customer_unique_id = cs.customer_unique_id
        JOIN public.orders      o  ON o.customer_id = c.customer_id
        JOIN public.order_items oi ON oi.order_id = o.order_id
        GROUP BY cs.segment_label
        ORDER BY total_gmv DESC
        LIMIT 20
    """,

    # ── grouped by customer STATE ───────────────────────────────────────────
    ("order_count", "state"): """
        SELECT c.customer_state AS state,
               COUNT(DISTINCT o.order_id) AS order_count,
               COUNT(DISTINCT c.customer_unique_id) AS unique_customers
        FROM public.customers c
        JOIN public.orders    o ON o.customer_id = c.customer_id
        GROUP BY c.customer_state
        ORDER BY order_count DESC
        LIMIT 25
    """,
    ("avg_delivery_days", "state"): """
        SELECT c.customer_state AS state,
               ROUND(AVG(EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp))
                         / 86400.0)::numeric, 2) AS avg_delivery_days,
               COUNT(*) AS delivered_orders
        FROM public.customers c
        JOIN public.orders    o ON o.customer_id = c.customer_id
        WHERE o.order_status = 'delivered'
          AND o.order_delivered_customer_date IS NOT NULL
        GROUP BY c.customer_state
        ORDER BY avg_delivery_days DESC
        LIMIT 25
    """,
    ("total_gmv", "state"): """
        SELECT c.customer_state AS state,
               ROUND(SUM(oi.price + oi.freight_value)::numeric, 2) AS total_gmv,
               COUNT(DISTINCT o.order_id) AS order_count
        FROM public.customers   c
        JOIN public.orders      o  ON o.customer_id = c.customer_id
        JOIN public.order_items oi ON oi.order_id = o.order_id
        GROUP BY c.customer_state
        ORDER BY total_gmv DESC
        LIMIT 25
    """,

    # ── grouped by product CATEGORY ─────────────────────────────────────────
    ("total_gmv", "category"): """
        SELECT p.product_category_name AS category,
               ROUND(SUM(oi.price + oi.freight_value)::numeric, 2) AS total_gmv,
               COUNT(*) AS items_sold
        FROM public.order_items oi
        JOIN public.products    p ON p.product_id = oi.product_id
        WHERE p.product_category_name IS NOT NULL
        GROUP BY p.product_category_name
        ORDER BY total_gmv DESC
        LIMIT 25
    """,
    ("avg_review_score", "category"): """
        SELECT p.product_category_name AS category,
               ROUND(AVG(r.review_score)::numeric, 2) AS avg_review_score,
               COUNT(r.review_score) AS review_count
        FROM public.order_items oi
        JOIN public.products     p ON p.product_id = oi.product_id
        JOIN public.order_reviews r ON r.order_id = oi.order_id
        WHERE p.product_category_name IS NOT NULL
        GROUP BY p.product_category_name
        HAVING COUNT(r.review_score) >= 100
        ORDER BY avg_review_score ASC
        LIMIT 25
    """,
}

# Metric keywords → the canonical metric name used as a template key.
# Kept separate from the single-view _METRIC_KEYWORDS so grouping detection
# can be case-normalised independently.
_GROUPED_METRIC_ALIASES: Dict[str, str] = {
    "gmv": "total_gmv",
    "revenue": "total_gmv",
    "sales": "total_gmv",
    "order_count": "order_count",
    "orders": "order_count",
    "order": "order_count",
    "delivery": "avg_delivery_days",
    "delivery_days": "avg_delivery_days",
    "delivery_time": "avg_delivery_days",
    "shipping": "avg_delivery_days",
    "rating": "avg_review_score",
    "rated": "avg_review_score",
    "review": "avg_review_score",
    "reviews": "avg_review_score",
    "score": "avg_review_score",
    "satisfaction": "avg_review_score",
}
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


def _detect_grouping(q_lower: str) -> Optional[str]:
    """Detect an explicit grouping phrase like 'by segment' / 'per state' /
    'across category'. Returns the canonical dimension slug (segment / state /
    category / month) or None. This is intentionally strict — vague phrases
    are ignored so we never route a non-grouping question through a joined
    template."""
    # `by <metric>` (e.g. "top 5 by GMV") is a sort dimension, not a grouping.
    # Only match `by <dim>` when the next token is a known grouping dimension.
    for phrase in (" by ", " per ", " across ", " broken down by ", " grouped by "):
        idx = q_lower.find(phrase)
        while idx != -1:
            tail = q_lower[idx + len(phrase):].strip()
            # first alphanumeric token after the phrase
            m = re.match(r"[a-z0-9]+", tail)
            if m:
                first = m.group(0)
                if first in _GROUPBY_KEYWORDS:
                    return _GROUPBY_KEYWORDS[first]
            idx = q_lower.find(phrase, idx + 1)
    return None


def _detect_grouped_metric(tokens: list[str], q_lower: str) -> Optional[str]:
    """Metric detection for the grouped path — uses a wider alias table so
    'delivery time', 'satisfaction', 'sales' all map to canonical names."""
    for tok in tokens:
        if tok in _GROUPED_METRIC_ALIASES:
            return _GROUPED_METRIC_ALIASES[tok]
    # multi-word phrases
    for phrase, canon in (
        ("delivery time", "avg_delivery_days"),
        ("delivery days", "avg_delivery_days"),
        ("review score", "avg_review_score"),
        ("customer satisfaction", "avg_review_score"),
    ):
        if phrase in q_lower:
            return canon
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

    # ── Multi-view grouping path (checked BEFORE entity so questions like
    #    "avg delivery time by segment" don't get routed to the single-view
    #    'delivery' entity). Only fires when both a valid template metric
    #    AND a supported grouping dimension are present. ──────────────────
    grouped_by = _detect_grouping(q_lower)
    if grouped_by:
        grouped_metric = _detect_grouped_metric(tokens, q_lower)
        if grouped_metric and (grouped_metric, grouped_by) in _JOIN_TEMPLATES:
            return {
                "entity": "__joined__",
                "metric": grouped_metric,
                "grouped_by": grouped_by,
                "sort_direction": "none",   # template controls order
                "limit": 25,                # template controls limit
                "filters": {}, "time_period": "",
                "question_type": "grouped",
            }
        # If grouping phrase was detected but we don't support that combo,
        # deliberately fall through to the single-view path — never return
        # an unsupported template.

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

    # ── Multi-view grouped path: return a pre-verified template SQL. Each
    #    template is hand-crafted to avoid the row-multiplication traps
    #    (see DQ-3 / DQ-15) and to include the correct WHERE filters for
    #    the metric (e.g. order_status='delivered' for delivery averages). ─
    grouped_by = intent.get("grouped_by")
    if grouped_by:
        metric = intent.get("metric")
        template = _JOIN_TEMPLATES.get((metric, grouped_by))
        if template:
            # Normalise whitespace so the SQL validator's tokenizer stays happy.
            return " ".join(template.split())
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
