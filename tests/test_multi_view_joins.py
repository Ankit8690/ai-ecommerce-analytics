"""
Tests for the Phase 10 multi-view join extension of the NL→SQL parser.

Verifies:
  1. Grouping phrases route through the new template path.
  2. Every declared template produces valid, non-empty SQL that passes the
     safety validator.
  3. Every template returns real rows against the live DB with sensible values.
  4. Backward compat: single-view questions still route through the original
     path (never the grouped path).
  5. Unsupported (metric, dimension) pairs fall back to single-view path
     rather than crashing.
"""
from __future__ import annotations

import pytest

from ai.nl_interpreter import (
    parse_intent_locally,
    build_query_from_intent,
    _JOIN_TEMPLATES,
    _detect_grouping,
)
from ai.sql_validator import validate_sql


# ---------------------------------------------------------------------------
# 1. Grouping-phrase detection
# ---------------------------------------------------------------------------
class TestGroupingDetection:
    @pytest.mark.parametrize("q,expected", [
        ("orders by segment",                      "segment"),
        ("avg delivery time by segments",          "segment"),
        ("total gmv per state",                    "state"),
        ("sales grouped by category",              "category"),
        ("orders broken down by state",            "state"),
        ("satisfaction across segments",           "segment"),
    ])
    def test_positive(self, q, expected):
        assert _detect_grouping(q) == expected

    @pytest.mark.parametrize("q", [
        "top 5 products by GMV",     # `by GMV` is sort-metric, not grouping
        "which 10 products sold the most",
        "show me monthly sales",
        "what is our total revenue",
    ])
    def test_negative(self, q):
        # These questions must NOT be detected as grouping requests, otherwise
        # they'd route through an unsupported template.
        assert _detect_grouping(q) is None


# ---------------------------------------------------------------------------
# 2. All declared templates pass the safety validator
# ---------------------------------------------------------------------------
class TestTemplateSafety:
    @pytest.mark.parametrize("key", list(_JOIN_TEMPLATES.keys()),
                             ids=lambda k: f"{k[0]}_by_{k[1]}")
    def test_template_passes_validator(self, key):
        sql = " ".join(_JOIN_TEMPLATES[key].split())
        ok, cleaned = validate_sql(sql)
        assert ok, f"validator rejected {key}: {cleaned}"


# ---------------------------------------------------------------------------
# 3. End-to-end: every template returns real rows
# ---------------------------------------------------------------------------
@pytest.mark.db
class TestTemplateExecution:
    @pytest.mark.parametrize("metric,dim,dim_col", [
        ("order_count",       "segment",  "segment"),
        ("avg_delivery_days", "segment",  "segment"),
        ("avg_review_score",  "segment",  "segment"),
        ("total_gmv",         "segment",  "segment"),
        ("order_count",       "state",    "state"),
        ("avg_delivery_days", "state",    "state"),
        ("total_gmv",         "state",    "state"),
        ("total_gmv",         "category", "category"),
        ("avg_review_score",  "category", "category"),
    ])
    def test_template_returns_sensible_rows(self, metric, dim, dim_col, db_engine):
        from sqlalchemy import text
        sql = " ".join(_JOIN_TEMPLATES[(metric, dim)].split())
        with db_engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(text(sql))]
        assert len(rows) > 0, f"({metric},{dim}) returned no rows"
        # First row must have the grouping dimension column, non-null.
        assert dim_col in rows[0], f"missing '{dim_col}' column in first row"
        assert rows[0][dim_col] is not None
        # Metric column should exist and be non-null in first row.
        assert metric in rows[0], f"missing '{metric}' column"
        assert rows[0][metric] is not None


# ---------------------------------------------------------------------------
# 4. Parser routing: grouped vs single-view
# ---------------------------------------------------------------------------
class TestParserRouting:
    @pytest.mark.parametrize("q,expected_metric,expected_dim", [
        ("average delivery time by segment", "avg_delivery_days", "segment"),
        ("orders by segment",                "order_count",       "segment"),
        ("total gmv by state",               "total_gmv",         "state"),
        ("satisfaction by segment",          "avg_review_score",  "segment"),
        ("gmv by category",                  "total_gmv",         "category"),
    ])
    def test_grouped_intent(self, q, expected_metric, expected_dim):
        intent = parse_intent_locally(q)
        assert intent is not None, f"no intent for {q!r}"
        assert intent.get("grouped_by") == expected_dim
        assert intent.get("metric") == expected_metric
        assert intent.get("entity") == "__joined__"

    @pytest.mark.parametrize("q", [
        "top 5 products by GMV",
        "top 3 least reviewed products",
        "show me monthly sales",
        "what is our total revenue",
    ])
    def test_backward_compat_single_view(self, q):
        """Single-view questions must NOT be routed to the grouped path."""
        intent = parse_intent_locally(q)
        assert intent is not None
        assert intent.get("grouped_by") is None
        assert intent.get("entity") != "__joined__"


# ---------------------------------------------------------------------------
# 5. Unsupported combinations: fall through gracefully
# ---------------------------------------------------------------------------
class TestUnsupportedCombinations:
    def test_unsupported_metric_dim_pair_returns_none_or_single_view(self):
        # "quantity sold by segment" — no template for this combo. Parser
        # should either return None or fall through to the single-view path
        # — never claim it can group.
        intent = parse_intent_locally("quantity sold by segment")
        if intent is not None:
            # If it returns something, it must be a single-view intent (no group_by)
            assert intent.get("grouped_by") is None

    def test_builder_returns_none_for_unknown_template(self):
        fake_intent = {
            "entity": "__joined__",
            "metric": "totally_fake_metric",
            "grouped_by": "segment",
            "sort_direction": "none",
            "limit": 25,
        }
        assert build_query_from_intent(fake_intent) is None
