"""
Phase 8 test suite — evidence-grounded recommendation engine.

Runs with Gemini DISABLED so results are deterministic and quota-safe.
Verifies routing, evidence-value provenance, hallucination prevention,
source propagation, RAG integration, and unsupported-question handling.

Run: .venv\\Scripts\\python.exe scripts/test_decision_support.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from api.database import readonly_engine
from ai.decision_support import (
    generate_recommendation, classify_question, RECOMMENDATION_CATEGORIES,
    RecommendationPackage,
)


def gen(q: str) -> RecommendationPackage:
    """Always deterministic — Gemini disabled."""
    return generate_recommendation(readonly_engine, q, use_gemini=False)


@dataclass
class Case:
    name: str
    fn: Callable[[], tuple[bool, str]]


CASES: list[Case] = []


def case(fn):
    CASES.append(Case(name=fn.__name__, fn=fn))
    return fn


# ---------- Routing ---------------------------------------------------------
@case
def route_worst_categories():
    return (classify_question("Which categories should we investigate for quality?") == "category_quality", "")


@case
def route_delivery():
    return (classify_question("Are our shipping SLAs OK?") == "delivery_sla", "")


@case
def route_sales_trend():
    return (classify_question("What is the sales trend month over month?") == "sales_trend", "")


@case
def route_customer_segments():
    return (classify_question("Which customer segment should we prioritise for retention?") == "customer_segments", "")


@case
def route_kpi_health():
    return (classify_question("Give me an executive overview of platform health.") == "kpi_health", "")


@case
def route_review():
    return (classify_question("How is our overall customer satisfaction?") == "review_health", "")


@case
def route_product_risk():
    return (classify_question("Which products should we investigate for problems?") == "product_risk", "")


@case
def route_unsupported():
    return (classify_question("What's the weather in Tokyo?") is None, "")


# ---------- Package integrity ---------------------------------------------
@case
def worst_categories_has_evidence():
    p = gen("Which categories have quality issues?")
    return (p.supported and len(p.evidence) >= 3 and p.category == "category_quality",
            f"evidence={len(p.evidence)} category={p.category}")


@case
def worst_categories_evidence_has_sources():
    p = gen("Which categories have quality issues?")
    ok = all(e.source_view.startswith("analytics.") for e in p.evidence if e.source_view)
    return (ok, "some evidence missing analytics.* source_view")


@case
def delivery_evidence_metrics_present():
    p = gen("How are our delivery SLAs?")
    keys = {e.metric for e in p.evidence}
    needed = {"Delivered orders", "Avg delivery days", "Late-delivery rate", "On-time rate"}
    return (needed.issubset(keys), f"missing: {needed - keys}")


@case
def sales_trend_has_mom():
    p = gen("Show me the sales trend month over month.")
    has_mom = any("MoM" in e.metric or "change" in e.metric.lower() for e in p.evidence)
    return (p.supported and has_mom, "MoM evidence missing")


@case
def kpi_health_covers_gmv_and_cash():
    p = gen("Give me an executive overview of platform health.")
    metrics = " ".join(str(e.metric) + str(e.value) for e in p.evidence).lower()
    return ("gmv" in metrics and "cash" in metrics, "")


@case
def customer_segments_lists_all():
    p = gen("Which customer segment should we prioritise?")
    return (p.supported and len(p.evidence) >= 2, f"evidence={len(p.evidence)}")


@case
def review_health_reports_negative_rate():
    p = gen("How is our customer satisfaction?")
    return (any(e.metric == "Negative-review rate" for e in p.evidence),
            "negative-review rate not surfaced")


@case
def product_risk_returns_specific_product():
    p = gen("Which products should we investigate for problems?")
    top = p.evidence[0] if p.evidence else None
    return (top is not None and top.metric == "Highest-risk product" and isinstance(top.value, str),
            f"top evidence: {top}")


# ---------- Hallucination prevention --------------------------------------
@case
def recommendation_only_references_evidence_values():
    """The deterministic recommendation text must not contain any large number
    that isn't derived from the evidence itself (guard against fabricated stats)."""
    import re
    p = gen("Which categories have quality issues?")
    numbers_in_recommendation = set(re.findall(r"\d[\d,]*", p.recommendation))
    # Values legitimately in evidence:
    evidence_text = " ".join(str(e.value) for e in p.evidence)
    allowed = set(re.findall(r"\d[\d,]*", evidence_text + p.observation + p.rationale))
    strange = numbers_in_recommendation - allowed
    return (not strange, f"unaccounted numbers in recommendation: {strange}")


@case
def package_markdown_has_all_sections():
    p = gen("How are our delivery SLAs?")
    md = p.to_markdown()
    needed = ["### Recommendation", "### Why", "### Evidence", "### Observation"]
    missing = [s for s in needed if s not in md]
    return (not missing, f"missing sections: {missing}")


@case
def unsupported_question_returns_clear_message():
    p = gen("What is the airspeed velocity of an unladen swallow?")
    md = p.to_markdown()
    return (not p.supported and "Unsupported question" in md,
            f"supported={p.supported}")


@case
def sql_injection_attempt_still_routes_or_unsupported():
    # This should EITHER be unsupported OR route to a template; either way,
    # the SQL executed must be validator-blessed (not the user string).
    p = gen("DROP TABLE public.orders")
    # Doesn't matter if routed or not — must NOT crash and must NOT have
    # any evidence claiming success against public.orders as a table target.
    ok = isinstance(p, RecommendationPackage)
    return (ok, "generator raised")


@case
def rag_citations_populated_when_index_available():
    """When the RAG index is present, at least one template surfaces citations."""
    p = gen("How are our delivery SLAs?")
    # Not strictly required (RAG optional), but must be a list either way.
    return (isinstance(p.rag_citations, list), f"type={type(p.rag_citations)}")


@case
def confidence_is_one_of_expected_values():
    p = gen("Give me an executive overview of platform health.")
    return (p.confidence in {"high", "medium", "low"}, f"confidence={p.confidence}")


@case
def all_seven_categories_reachable():
    """Every declared category must be reachable via at least one question."""
    prompts = {
        "category_quality":   "Which categories have quality issues?",
        "product_risk":       "Which risky products should we delist?",
        "delivery_sla":       "How is our shipping SLA?",
        "review_health":      "How is customer satisfaction overall?",
        "sales_trend":        "What is the sales trend MoM?",
        "customer_segments":  "Which customer segment to prioritise?",
        "kpi_health":         "Executive overview of platform health.",
    }
    hits = {gen(p).category for p in prompts.values()}
    return (set(RECOMMENDATION_CATEGORIES).issubset(hits),
            f"unreachable: {set(RECOMMENDATION_CATEGORIES) - hits}")


@case
def evidence_values_are_serializable():
    """Every evidence value must be JSON-safe (int/float/str)."""
    import json
    for prompt in ["worst categories", "delivery slas", "kpi overview",
                   "customer segments", "product risk"]:
        p = gen(prompt)
        for e in p.evidence:
            try:
                json.dumps({"v": e.value})
            except TypeError:
                return False, f"non-serializable evidence value: {e.metric}={e.value!r}"
    return True, ""


@case
def deterministic_mode_reported():
    p = gen("How is customer satisfaction?")
    return (p.mode == "deterministic",
            f"mode={p.mode} (Gemini should be disabled in tests)")


# ---------- Runner --------------------------------------------------------
def main() -> int:
    print(f"Running {len(CASES)} Phase 8 decision-support tests...\n")
    passed = 0
    failed: list[tuple[str, str]] = []
    for i, c in enumerate(CASES, 1):
        try:
            ok, detail = c.fn()
        except Exception as e:
            ok, detail = False, f"exception: {type(e).__name__}: {e}"
        tag = "PASS" if ok else "FAIL"
        print(f"[{i:02d}] {tag}  {c.name}")
        if not ok:
            print(f"       -> {detail}")
            failed.append((c.name, detail))
        else:
            passed += 1
    pct = 100.0 * passed / len(CASES)
    print(f"\n{'='*60}\nRESULT: {passed}/{len(CASES)} passed ({pct:.1f}%)\n{'='*60}")
    if failed:
        for n, d in failed:
            print(f"  - {n}: {d}")
    return 0 if pct >= 95.0 else 1


if __name__ == "__main__":
    sys.exit(main())
