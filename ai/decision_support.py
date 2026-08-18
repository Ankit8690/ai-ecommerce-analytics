"""
Phase 8 — AI Business Recommendations / Decision Support.

Composes existing pieces (no rewrites):
    - api.database.readonly_engine       — safe SELECT-only DB access
    - ai.sql_validator.validate_sql      — validator (same one Phase 6 uses)
    - rag.Retriever + rag.synthesizer    — knowledge context

Design principles
-----------------
1. Evidence-first. Every recommendation is built from an ``EvidencePackage``
   whose values came from a validated SQL query against an analytics view.
2. The deterministic narrative references only values present in the evidence.
   Gemini is an OPTIONAL reasoning layer — when it fails or is unavailable,
   the deterministic package is what the user sees. No fabrication.
3. Templates (7 categories) map keywords → SQL query + optional RAG query +
   deterministic-narrative builder. Unknown questions return an explicit
   "unsupported" package rather than guessing.
4. All DB access flows through the read-only engine and the validator.
"""
from __future__ import annotations

import concurrent.futures
import decimal
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from ai.sql_validator import validate_sql

# RAG is optional at import time so the module works even before the index
# has been built. Retrieval failures degrade gracefully to SQL-only mode.
try:
    from rag.retriever import Retriever, RetrievalResult
    _RAG_AVAILABLE = True
except Exception:
    _RAG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Structured evidence + package
# ---------------------------------------------------------------------------
def _clean(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


@dataclass
class Evidence:
    """One SQL-derived fact used to justify a recommendation."""
    metric: str
    value: Any
    unit: str = ""
    comparison_value: Any = None
    comparison_label: str = ""
    source_view: str = ""
    source_sql: str = ""


@dataclass
class RecommendationPackage:
    """The full evidence-grounded output returned to callers/UI."""
    question: str
    category: str
    observation: str
    context: str
    interpretation: str
    recommendation: str
    rationale: str
    limitations: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    rag_citations: list[str] = field(default_factory=list)
    confidence: str = "medium"           # "high" | "medium" | "low"
    mode: str = "deterministic"          # "deterministic" | "gemini"
    supported: bool = True

    def to_markdown(self) -> str:
        """Format for dashboard display (Observation / … / Sources)."""
        if not self.supported:
            return (
                f"### ⚠️ Unsupported question\n\n"
                f"*\"{self.question}\"* does not match any Phase 8 recommendation category "
                f"grounded in the current analytics views.\n\n"
                f"**Supported categories:** {', '.join(RECOMMENDATION_CATEGORIES)}."
            )
        parts = [
            f"### Recommendation\n{self.recommendation}\n",
            f"### Why\n{self.rationale}\n",
        ]
        if self.observation:
            parts.append(f"### Observation\n{self.observation}\n")
        if self.context:
            parts.append(f"### Context (knowledge base)\n{self.context}\n")
        if self.interpretation:
            parts.append(f"### Interpretation\n{self.interpretation}\n")
        if self.evidence:
            lines = []
            for e in self.evidence:
                bits = [f"- **{e.metric}**: `{e.value}`"]
                if e.unit:
                    bits.append(f" {e.unit}")
                if e.comparison_value is not None:
                    bits.append(f"  *(vs {e.comparison_label} `{e.comparison_value}`)*")
                if e.source_view:
                    bits.append(f"  · source: `{e.source_view}`")
                lines.append("".join(bits))
            parts.append("### Evidence\n" + "\n".join(lines) + "\n")
        if self.limitations:
            parts.append("### Limitations\n" + "\n".join(f"- {l}" for l in self.limitations) + "\n")
        if self.rag_citations:
            parts.append("### Sources\n" + "\n".join(f"- {c}" for c in self.rag_citations) + "\n")
        parts.append(f"*Category: **{self.category}** · confidence: **{self.confidence}** · synthesis mode: **{self.mode}***")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Safe SQL runner (validator-first, read-only)
# ---------------------------------------------------------------------------
def _run_sql(engine: Engine, sql: str) -> tuple[list[dict], str]:
    """Validate + execute a SELECT. Raises ValueError on validation failure."""
    ok, cleaned = validate_sql(sql)
    if not ok:
        raise ValueError(f"Validator rejected recommendation SQL: {cleaned}")
    with engine.connect() as conn:
        rows = [{k: _clean(v) for k, v in r._mapping.items()}
                for r in conn.execute(sa_text(cleaned))]
    return rows, cleaned


# ---------------------------------------------------------------------------
# RAG helper (silent no-op when index missing)
# ---------------------------------------------------------------------------
_retriever_singleton: Optional["Retriever"] = None


def _get_retriever() -> Optional["Retriever"]:
    global _retriever_singleton
    if _retriever_singleton is not None:
        return _retriever_singleton
    if not _RAG_AVAILABLE:
        return None
    try:
        _retriever_singleton = Retriever()
        return _retriever_singleton
    except Exception:
        return None


def _rag_lookup(query: str, k: int = 3) -> tuple[str, list[str]]:
    """Return (concatenated_context, citation_strings)."""
    r = _get_retriever()
    if r is None:
        return "", []
    hits = r.retrieve(query, k=k)
    if not hits:
        return "", []
    ctx_pieces = [f"[{i}] {h.chunk.text.strip()}" for i, h in enumerate(hits, 1)]
    cites = [f"[{i}] {h.citation()} (relevance {h.score:.2f})"
             for i, h in enumerate(hits, 1)]
    return "\n\n".join(ctx_pieces), cites


# ---------------------------------------------------------------------------
# Templates — each category has (matcher, builder)
# ---------------------------------------------------------------------------
def _has_any(q: str, kws: list[str]) -> bool:
    ql = q.lower()
    return any(k in ql for k in kws)


# --- Category 1: category health / quality issues ---------------------------
def _tpl_worst_categories(engine: Engine, question: str) -> RecommendationPackage:
    sql = (
        "SELECT product_category_name, review_count, avg_review_score, "
        "negative_review_rate_pct, product_gmv "
        "FROM analytics.v_category_performance "
        "WHERE review_count >= 100 AND avg_review_score IS NOT NULL "
        "ORDER BY avg_review_score ASC LIMIT 5"
    )
    rows, cleaned = _run_sql(engine, sql)
    if not rows:
        return _insufficient("category quality", question,
                             "No categories have >= 100 reviews.")
    top = rows[0]
    ev = [
        Evidence("Worst-rated category", top["product_category_name"],
                 source_view="analytics.v_category_performance", source_sql=cleaned),
        Evidence("Avg review score", top["avg_review_score"], unit="/ 5.0",
                 source_view="analytics.v_category_performance"),
        Evidence("Review count", top["review_count"],
                 source_view="analytics.v_category_performance"),
        Evidence("Negative-review rate", top["negative_review_rate_pct"], unit="%",
                 source_view="analytics.v_category_performance"),
        Evidence("Category product GMV", f"{top['product_gmv']:,.2f}", unit="units",
                 source_view="analytics.v_category_performance"),
    ]
    ctx, cites = _rag_lookup("How is a negative review defined and how is it interpreted?")
    other_cats = ", ".join(f"{r['product_category_name']} ({r['avg_review_score']})"
                           for r in rows[1:])
    return RecommendationPackage(
        question=question,
        category="category_quality",
        observation=(f"Among categories with >= 100 reviews, "
                     f"**{top['product_category_name']}** has the lowest average review "
                     f"score at **{top['avg_review_score']} / 5.0** across "
                     f"**{top['review_count']:,}** reviews, with a "
                     f"**{top['negative_review_rate_pct']}%** negative-review rate. "
                     f"Runners-up: {other_cats}."),
        context=ctx,
        interpretation=("The category shows sustained customer dissatisfaction at "
                        "meaningful review volume. This is a correlation, not proven "
                        "causation — the root driver could be product, seller, or logistics."),
        recommendation=(f"Prioritize a quality investigation of **{top['product_category_name']}**: "
                        "audit top-selling SKUs' review comments (external), review seller "
                        "concentration, and cross-check delivery SLA for the category before "
                        "acting on pricing or promotions."),
        rationale=("Category-level avg review score < 4.0 with hundreds of reviews indicates "
                   "a systemic (not sampling) issue. Fixing quality here protects the "
                   f"category's {top['product_gmv']:,.2f} units of GMV."),
        limitations=["Correlation only — not proof of causation.",
                     "Review text is not available (DQ-14); only the numeric score."],
        evidence=ev,
        rag_citations=cites,
        confidence="high" if top["review_count"] >= 500 else "medium",
    )


# --- Category 2: product-level review risk ---------------------------------
def _tpl_high_risk_products(engine: Engine, question: str) -> RecommendationPackage:
    sql = (
        "SELECT product_id, product_category_name, review_count, "
        "avg_review_score, negative_review_count, product_gmv "
        "FROM analytics.v_product_performance "
        "WHERE review_count >= 20 "
        "ORDER BY negative_review_count DESC NULLS LAST LIMIT 5"
    )
    rows, cleaned = _run_sql(engine, sql)
    if not rows:
        return _insufficient("product risk", question,
                             "No products meet the review-volume threshold.")
    top = rows[0]
    ev = [
        Evidence("Highest-risk product", top["product_id"],
                 source_view="analytics.v_product_performance"),
        Evidence("Category", top["product_category_name"],
                 source_view="analytics.v_product_performance"),
        Evidence("Negative review count", top["negative_review_count"],
                 source_view="analytics.v_product_performance"),
        Evidence("Total reviews", top["review_count"],
                 source_view="analytics.v_product_performance"),
        Evidence("Avg review score", top["avg_review_score"], unit="/ 5.0",
                 source_view="analytics.v_product_performance"),
    ]
    ctx, cites = _rag_lookup("negative review threshold definition")
    return RecommendationPackage(
        question=question,
        category="product_risk",
        observation=(f"Product `{top['product_id']}` in **{top['product_category_name']}** "
                     f"has accumulated **{top['negative_review_count']}** negative reviews "
                     f"(out of {top['review_count']}, avg score "
                     f"**{top['avg_review_score']}**)."),
        context=ctx,
        interpretation=("This SKU is a concentrated source of dissatisfaction within its category."),
        recommendation=(f"Flag `{top['product_id']}` for merchant/seller review. Options: "
                        "pull from featured listings, request seller remediation, or gate the "
                        "listing behind a quality re-audit."),
        rationale="Products with high absolute negative-review counts are the most visible "
                  "drivers of category-level dissatisfaction and disproportionately shape "
                  "buyer perception.",
        limitations=["Only the numeric score is available; no free-text review content.",
                     "GMV impact of delisting is not modelled here."],
        evidence=ev,
        rag_citations=cites,
        confidence="medium",
    )


# --- Category 3: delivery / logistics SLA ---------------------------------
def _tpl_delivery_sla(engine: Engine, question: str) -> RecommendationPackage:
    sql = ("SELECT delivered_order_count, avg_delivery_days, median_delivery_days, "
           "late_delivery_count, late_delivery_rate_pct, on_time_rate_pct "
           "FROM analytics.v_delivery_performance")
    rows, cleaned = _run_sql(engine, sql)
    if not rows:
        return _insufficient("delivery", question, "Delivery view returned no rows.")
    r = rows[0]
    ev = [
        Evidence("Delivered orders", r["delivered_order_count"],
                 source_view="analytics.v_delivery_performance"),
        Evidence("Avg delivery days", r["avg_delivery_days"], unit="days",
                 source_view="analytics.v_delivery_performance"),
        Evidence("Median delivery days", r["median_delivery_days"], unit="days",
                 source_view="analytics.v_delivery_performance"),
        Evidence("Late-delivery rate", r["late_delivery_rate_pct"], unit="%",
                 source_view="analytics.v_delivery_performance"),
        Evidence("On-time rate", r["on_time_rate_pct"], unit="%",
                 source_view="analytics.v_delivery_performance"),
    ]
    ctx, cites = _rag_lookup("late delivery and negative review association")
    late = float(r["late_delivery_rate_pct"])
    if late >= 10:
        rec = ("Elevate delivery SLA as an operational priority: audit carrier partners in "
               "the states with the highest late-delivery contribution, and tighten the "
               "estimated-delivery-date buffer to align expectation with reality.")
        conf = "high"
    elif late >= 5:
        rec = ("Monitor delivery timeliness. Investigate months with the largest late-rate "
               "spikes and confirm they are not concentrated in specific states or carriers.")
        conf = "medium"
    else:
        rec = ("Current delivery SLA is healthy; maintain existing operational cadence and "
               "continue monthly late-rate monitoring.")
        conf = "high"
    return RecommendationPackage(
        question=question, category="delivery_sla",
        observation=(f"Across **{r['delivered_order_count']:,}** delivered orders, average "
                     f"delivery is **{r['avg_delivery_days']} days** (median "
                     f"**{r['median_delivery_days']}**), late-delivery rate "
                     f"**{late}%**, on-time rate **{r['on_time_rate_pct']}%**."),
        context=ctx,
        interpretation=("Late deliveries are strongly associated with negative reviews (Phase 3 "
                        "finding: 54.6% vs 9.5% negative-review rate for late vs on-time)."),
        recommendation=rec,
        rationale="Delivery timeliness is one of the largest observable predictors of customer "
                  "experience risk in this dataset.",
        limitations=["Association, not proven causation.",
                     "Carrier-level attribution is not in the current warehouse."],
        evidence=ev, rag_citations=cites, confidence=conf,
    )


# --- Category 4: overall review / satisfaction health ----------------------
def _tpl_review_health(engine: Engine, question: str) -> RecommendationPackage:
    sql = ("SELECT total_reviews, avg_review_score, negative_review_count, "
           "negative_review_rate_pct FROM analytics.v_review_analytics")
    rows, cleaned = _run_sql(engine, sql)
    r = rows[0]
    ev = [
        Evidence("Total reviews", r["total_reviews"],
                 source_view="analytics.v_review_analytics"),
        Evidence("Avg review score", r["avg_review_score"], unit="/ 5.0",
                 source_view="analytics.v_review_analytics"),
        Evidence("Negative reviews", r["negative_review_count"],
                 source_view="analytics.v_review_analytics"),
        Evidence("Negative-review rate", r["negative_review_rate_pct"], unit="%",
                 source_view="analytics.v_review_analytics"),
    ]
    ctx, cites = _rag_lookup("negative review definition")
    rate = float(r["negative_review_rate_pct"])
    rec = ("Reduce negative-review rate by targeting the two biggest observable drivers: "
           "late deliveries (see delivery_sla category) and worst-rated categories "
           "(see category_quality category). Both are already visible in the warehouse.") \
        if rate >= 10 else \
          ("Overall satisfaction is broadly healthy. Focus on the lowest-rated categories "
           "and highest-risk SKUs rather than platform-wide interventions.")
    return RecommendationPackage(
        question=question, category="review_health",
        observation=(f"Across **{r['total_reviews']:,}** reviews, average score is "
                     f"**{r['avg_review_score']} / 5.0** with a "
                     f"**{r['negative_review_rate_pct']}%** negative-review rate "
                     f"({r['negative_review_count']:,} reviews at score <= 2)."),
        context=ctx,
        interpretation="Aggregate satisfaction is healthy; risk is concentrated by category / SKU.",
        recommendation=rec,
        rationale="A platform-wide average masks concentration; drill-down categories exist for "
                  "targeted action.",
        limitations=["No free-text review data (DQ-14)."],
        evidence=ev, rag_citations=cites, confidence="high",
    )


# --- Category 5: sales trend ----------------------------------------------
def _tpl_sales_trend(engine: Engine, question: str) -> RecommendationPackage:
    sql = ("SELECT month, product_gmv, cash_collected, order_count "
           "FROM analytics.v_monthly_sales ORDER BY month DESC LIMIT 6")
    rows, cleaned = _run_sql(engine, sql)
    if len(rows) < 2:
        return _insufficient("sales_trend", question,
                             "Not enough monthly history to compare.")
    latest, prev = rows[0], rows[1]
    delta = float(latest["product_gmv"]) - float(prev["product_gmv"])
    pct = 100.0 * delta / float(prev["product_gmv"]) if prev["product_gmv"] else 0.0
    ev = [
        Evidence("Latest month", latest["month"],
                 source_view="analytics.v_monthly_sales"),
        Evidence("Latest GMV", f"{latest['product_gmv']:,.2f}", unit="units",
                 comparison_value=f"{prev['product_gmv']:,.2f}",
                 comparison_label=f"month {prev['month']}",
                 source_view="analytics.v_monthly_sales"),
        Evidence("MoM change", f"{pct:+.1f}", unit="%",
                 source_view="analytics.v_monthly_sales"),
        Evidence("Latest order count", latest["order_count"],
                 comparison_value=prev["order_count"],
                 comparison_label=f"month {prev['month']}",
                 source_view="analytics.v_monthly_sales"),
    ]
    ctx, cites = _rag_lookup("truncated sales tail forecast window")
    if pct <= -10:
        rec = ("Investigate the month-over-month GMV decline before committing new marketing "
               "spend. Cross-check the delivery late-rate and category ratings for the same month.")
        conf = "medium"
    elif pct >= 10:
        rec = ("GMV is up materially MoM — evaluate scaling investment in the top-performing "
               "categories (see category-ranking analytics) while capacity holds.")
        conf = "medium"
    else:
        rec = "Trend is stable; continue current allocation and revisit next month."
        conf = "high"
    return RecommendationPackage(
        question=question, category="sales_trend",
        observation=(f"GMV moved from **{prev['product_gmv']:,.2f}** in {prev['month']} to "
                     f"**{latest['product_gmv']:,.2f}** in {latest['month']} "
                     f"(**{pct:+.1f}% MoM**)."),
        context=ctx,
        interpretation="Short-window MoM is noisy — always corroborate with quarter or category cuts.",
        recommendation=rec,
        rationale="Recent trend direction shapes near-term investment prioritisation.",
        limitations=["Sales tail is truncated after 2018-08 (DQ-5) — beware boundary effects.",
                     "MoM is one signal, not a trend proof."],
        evidence=ev, rag_citations=cites, confidence=conf,
    )


# --- Category 6: customer segment retention priority ----------------------
def _tpl_customer_segments(engine: Engine, question: str) -> RecommendationPackage:
    sql = ("SELECT segment_label, COUNT(*) AS customers, "
           "ROUND(AVG(total_gmv)::numeric,2) AS avg_gmv, "
           "ROUND(AVG(recency_days)::numeric,1) AS avg_recency_days "
           "FROM analytics.customer_segments GROUP BY segment_label "
           "ORDER BY avg_gmv DESC")
    rows, cleaned = _run_sql(engine, sql)
    if not rows:
        return _insufficient("customer_segments", question, "No segments loaded.")
    top = rows[0]
    ev = [Evidence(f"Segment: {r['segment_label']}",
                   f"{int(r['customers']):,} customers",
                   comparison_value=f"avg GMV {r['avg_gmv']}",
                   comparison_label="",
                   source_view="analytics.customer_segments") for r in rows]
    ctx, cites = _rag_lookup("customer segments RFM churn out of scope")
    return RecommendationPackage(
        question=question, category="customer_segments",
        observation=(f"Highest-value segment is **{top['segment_label']}** with "
                     f"**{int(top['customers']):,}** customers and an avg GMV of "
                     f"**{top['avg_gmv']}**. Full distribution: " +
                     ", ".join(f"{r['segment_label']} ({int(r['customers']):,})" for r in rows) + "."),
        context=ctx,
        interpretation=("Because repeat-purchase rate is only 3.12% (DQ-10), retention economics "
                        "are unlike a subscription business — invest in acquisition quality first."),
        recommendation=(f"Focus retention effort on **{top['segment_label']}** since expected "
                        "spend per retained customer is highest. Do not attempt churn "
                        "prediction (locked out of scope per D-007)."),
        rationale="Concentrating on the segment with highest observable AOV maximizes "
                  "revenue-per-retention-dollar in a low-repeat marketplace.",
        limitations=["Churn/LTV are out of scope per decision D-007."],
        evidence=ev, rag_citations=cites, confidence="medium",
    )


# --- Category 7: executive KPI health -------------------------------------
def _tpl_kpi_health(engine: Engine, question: str) -> RecommendationPackage:
    sql = ("SELECT total_orders, delivered_orders, delivered_pct, cancelled_orders, "
           "cancelled_pct, product_gmv, cash_collected, avg_order_value_gmv "
           "FROM analytics.v_executive_kpis")
    rows, cleaned = _run_sql(engine, sql)
    r = rows[0]
    ev = [
        Evidence("Total orders", int(r["total_orders"]),
                 source_view="analytics.v_executive_kpis"),
        Evidence("Delivered rate", r["delivered_pct"], unit="%",
                 source_view="analytics.v_executive_kpis"),
        Evidence("Cancellation rate", r["cancelled_pct"], unit="%",
                 source_view="analytics.v_executive_kpis"),
        Evidence("Product GMV", f"{r['product_gmv']:,.2f}", unit="units",
                 source_view="analytics.v_executive_kpis"),
        Evidence("Cash collected", f"{r['cash_collected']:,.2f}", unit="units",
                 source_view="analytics.v_executive_kpis"),
        Evidence("Avg order value (GMV)", r["avg_order_value_gmv"], unit="units",
                 source_view="analytics.v_executive_kpis"),
    ]
    ctx, cites = _rag_lookup("Product GMV vs cash collected DQ-15")
    cancel = float(r["cancelled_pct"])
    rec = ("Overall platform KPIs are healthy. Focus improvement effort on category-level "
           "and delivery-level drill-downs rather than platform-wide interventions.") \
          if cancel < 5 else \
          ("Cancellation rate is elevated; investigate the top cancellation reasons by "
           "category and seller before scaling acquisition spend.")
    return RecommendationPackage(
        question=question, category="kpi_health",
        observation=(f"Platform has processed **{int(r['total_orders']):,}** orders with a "
                     f"**{r['delivered_pct']}%** delivered rate and "
                     f"**{r['cancelled_pct']}%** cancellation. Product GMV "
                     f"**{r['product_gmv']:,.2f}** vs cash collected "
                     f"**{r['cash_collected']:,.2f}**."),
        context=ctx,
        interpretation=("GMV and cash collected differ because of installment payments; both "
                        "are legitimate measures depending on the question (DQ-15)."),
        recommendation=rec,
        rationale="Executive KPIs frame where deeper analysis should focus, not what to do — "
                  "always pair with a category / delivery drill-down.",
        limitations=["Aggregate view — masks category- and state-level variance."],
        evidence=ev, rag_citations=cites, confidence="high",
    )


# --- Insufficient / unsupported helper ------------------------------------
def _insufficient(category: str, question: str, reason: str) -> RecommendationPackage:
    return RecommendationPackage(
        question=question, category=category,
        observation="", context="", interpretation="",
        recommendation="No recommendation — insufficient evidence.",
        rationale=reason,
        limitations=[reason],
        evidence=[], rag_citations=[], confidence="low", supported=True,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
RECOMMENDATION_CATEGORIES = [
    "category_quality", "product_risk", "delivery_sla", "review_health",
    "sales_trend", "customer_segments", "kpi_health",
]

_TEMPLATES: list[tuple[Callable[[str], bool], Callable[[Engine, str], RecommendationPackage], str]] = [
    (lambda q: _has_any(q, ["worst categor", "category quality", "which categor", "bad categor",
                            "poorly rated categor", "categories to investigate"]),
     _tpl_worst_categories, "category_quality"),
    (lambda q: _has_any(q, ["risky product", "high-risk product", "problem product",
                            "worst product", "bad product", "product to remove",
                            "product to delist", "which products", "which product",
                            "investigate product", "products to investigate",
                            "products to flag", "product quality", "product issue"]),
     _tpl_high_risk_products, "product_risk"),
    (lambda q: _has_any(q, ["delivery", "shipping", "late", "on-time", "sla", "logistic"]),
     _tpl_delivery_sla, "delivery_sla"),
    (lambda q: _has_any(q, ["review", "rating", "satisfaction", "customer experience",
                            "negative review"]),
     _tpl_review_health, "review_health"),
    (lambda q: _has_any(q, ["trend", "month over month", "mom", "growth", "declin",
                            "sales trend", "revenue direction"]),
     _tpl_sales_trend, "sales_trend"),
    (lambda q: _has_any(q, ["segment", "retention", "champion", "loyal", "high-value customer",
                            "rfm"]),
     _tpl_customer_segments, "customer_segments"),
    (lambda q: _has_any(q, ["kpi", "overview", "health", "executive", "platform", "overall"]),
     _tpl_kpi_health, "kpi_health"),
]


def classify_question(question: str) -> Optional[str]:
    """Return the category slug the router would choose, or None."""
    q = (question or "").lower()
    for matcher, _, slug in _TEMPLATES:
        if matcher(q):
            return slug
    return None


def _unsupported(question: str) -> RecommendationPackage:
    return RecommendationPackage(
        question=question, category="unsupported",
        observation="", context="", interpretation="",
        recommendation="", rationale="",
        limitations=["Question does not map to any supported recommendation category."],
        evidence=[], rag_citations=[], confidence="low",
        mode="deterministic", supported=False,
    )


# ---------------------------------------------------------------------------
# Optional Gemini reasoning layer
# ---------------------------------------------------------------------------
_GEMINI_SYS = (
    "You are an evidence-first e-commerce business advisor. Rewrite the given "
    "recommendation package into a tight executive briefing. Rules:\n"
    "- Use ONLY the numbers and facts inside the evidence and observation.\n"
    "- Never introduce new metrics, categories, causes, or comparisons.\n"
    "- Preserve the recommendation intent; you may sharpen phrasing.\n"
    "- Under 180 words. Use GitHub Markdown with sections: "
    "**Recommendation**, **Why**, **Confidence & limits**."
)


def _try_gemini_reasoning(pkg: RecommendationPackage, timeout_s: int = 30) -> Optional[str]:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key or not pkg.supported:
        return None
    model = os.getenv("LLM_MODEL") or "gemini-3.6-flash"
    try:
        from google import genai
        try:
            from google.genai import types  # type: ignore
        except Exception:
            types = None  # type: ignore
        from ai import gemini_cache

        client = genai.Client(api_key=api_key)
        ev_lines = "\n".join(
            f"- {e.metric}: {e.value} {e.unit}".rstrip() for e in pkg.evidence
        )
        prompt = (
            f"Question: {pkg.question}\n"
            f"Category: {pkg.category}\n"
            f"Observation: {pkg.observation}\n"
            f"Context: {pkg.context}\n"
            f"Interpretation: {pkg.interpretation}\n"
            f"Deterministic recommendation: {pkg.recommendation}\n"
            f"Evidence:\n{ev_lines}\n"
            f"Limitations: {'; '.join(pkg.limitations)}\n"
        )

        cached = gemini_cache.get(model, prompt)
        if cached:
            return cached

        def _gen():
            if types is not None:
                try:
                    cfg = types.GenerateContentConfig(
                        system_instruction=_GEMINI_SYS,
                        temperature=0.2, max_output_tokens=500)
                    return client.models.generate_content(
                        model=model, contents=prompt, config=cfg)
                except Exception:
                    pass
            return client.models.generate_content(
                model=model, contents=f"{_GEMINI_SYS}\n\n{prompt}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            resp = ex.submit(_gen).result(timeout=timeout_s)
        text = getattr(resp, "text", None) or None
        if text:
            gemini_cache.put(model, prompt, text)
        return text
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_recommendation(engine: Engine, question: str,
                            use_gemini: bool = True) -> RecommendationPackage:
    """Top-level entry — build an evidence-grounded RecommendationPackage."""
    slug = classify_question(question)
    if slug is None:
        return _unsupported(question)
    for matcher, builder, s in _TEMPLATES:
        if s == slug:
            pkg = builder(engine, question)
            break
    else:  # pragma: no cover
        return _unsupported(question)

    if use_gemini and pkg.supported and pkg.evidence:
        gtext = _try_gemini_reasoning(pkg)
        if gtext:
            pkg.recommendation = gtext.strip() + \
                f"\n\n*(Rewritten by Gemini from the deterministic package below.)*"
            pkg.mode = "gemini"
    return pkg
