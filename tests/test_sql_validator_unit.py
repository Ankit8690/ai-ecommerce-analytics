"""SQL validator unit tests — no DB required."""
from __future__ import annotations

import pytest

from ai.sql_validator import (
    validate_sql, check_known_tables, extract_table_refs,
    KNOWN_TABLES, ALLOWED_SCHEMAS, PROHIBITED_KEYWORDS,
)


class TestValidateSqlRejections:
    @pytest.mark.parametrize("sql,keyword", [
        ("DELETE FROM public.orders",            "DELETE"),
        ("UPDATE public.orders SET x=1",         "UPDATE"),
        ("INSERT INTO public.orders VALUES (1)", "INSERT"),
        ("DROP TABLE public.orders",             "DROP"),
        ("TRUNCATE public.orders",               "TRUNCATE"),
        ("ALTER TABLE public.orders",            "ALTER"),
        ("CREATE TABLE t (id int)",              "CREATE"),
        ("GRANT ALL ON public.orders TO app",    "GRANT"),
        ("REVOKE SELECT ON public.orders",       "REVOKE"),
        ("COPY public.orders FROM stdin",        "COPY"),
        ("CALL foo()",                           "CALL"),
        ("MERGE INTO public.orders",             "MERGE"),
    ])
    def test_starts_with_forbidden_keyword_is_rejected(self, sql, keyword):
        ok, msg = validate_sql(sql)
        assert not ok
        assert keyword in msg or "start" in msg.lower()

    def test_multi_statement_rejected(self):
        ok, msg = validate_sql("SELECT 1; SELECT 2")
        assert not ok and "Multiple" in msg

    def test_line_comment_rejected(self):
        ok, msg = validate_sql("SELECT 1 FROM analytics.v_executive_kpis -- inj")
        assert not ok and "Comments" in msg

    def test_block_comment_rejected(self):
        ok, msg = validate_sql("SELECT /* inj */ 1 FROM analytics.v_executive_kpis")
        assert not ok and "Comments" in msg

    def test_information_schema_rejected(self):
        ok, msg = validate_sql("SELECT * FROM information_schema.tables")
        assert not ok and "information_schema" in msg

    def test_pg_catalog_rejected(self):
        ok, msg = validate_sql("SELECT * FROM pg_catalog.pg_tables")
        assert not ok and "pg_catalog" in msg

    def test_unknown_schema_rejected(self):
        ok, msg = validate_sql("SELECT * FROM secret.stuff")
        assert not ok and "secret" in msg

    def test_empty_query_rejected(self):
        assert validate_sql("")[0] is False
        assert validate_sql("   ")[0] is False


class TestValidateSqlPasses:
    def test_plain_select(self):
        ok, cleaned = validate_sql("SELECT * FROM analytics.v_executive_kpis")
        assert ok
        assert "LIMIT 100" in cleaned.upper()  # auto-added

    def test_select_with_explicit_limit_preserved(self):
        ok, cleaned = validate_sql(
            "SELECT product_id FROM analytics.v_product_performance LIMIT 5"
        )
        assert ok and "LIMIT 5" in cleaned

    def test_with_cte(self):
        ok, _ = validate_sql(
            "WITH t AS (SELECT product_gmv FROM analytics.v_monthly_sales) "
            "SELECT SUM(product_gmv) FROM t"
        )
        assert ok

    def test_two_table_join_public(self):
        ok, _ = validate_sql(
            "SELECT o.order_id FROM public.orders o "
            "JOIN public.customers c ON o.customer_id=c.customer_id LIMIT 3"
        )
        assert ok

    def test_trailing_semicolon_stripped(self):
        ok, cleaned = validate_sql("SELECT 1 FROM analytics.v_executive_kpis;")
        assert ok and ";" not in cleaned


class TestKnownTables:
    def test_all_declared_schemas_valid(self):
        for tbl in KNOWN_TABLES:
            schema = tbl.split(".")[0]
            assert schema in ALLOWED_SCHEMAS, f"{tbl} has bad schema"

    def test_extract_refs_finds_join_and_from(self):
        sql = ("SELECT * FROM analytics.v_product_performance p "
               "JOIN public.products q ON p.product_id=q.product_id")
        refs = extract_table_refs(sql)
        assert "analytics.v_product_performance" in refs
        assert "public.products" in refs

    def test_no_table_returns_empty_refs(self):
        ok, _, refs = check_known_tables("SELECT 1")
        assert refs == [] and ok is True

    def test_unknown_table_flagged(self):
        ok, bad, refs = check_known_tables("SELECT * FROM public.unicorns")
        assert not ok and bad == "public.unicorns"

    def test_mixed_valid_and_ghost(self):
        ok, bad, refs = check_known_tables(
            "SELECT * FROM analytics.v_executive_kpis "
            "JOIN public.ghost_table t ON 1=1"
        )
        assert not ok and bad == "public.ghost_table"
        assert "analytics.v_executive_kpis" in refs


class TestProhibitedKeywordSet:
    def test_essential_keywords_present(self):
        essentials = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
                      "TRUNCATE", "GRANT", "REVOKE", "CALL", "SET"}
        assert essentials.issubset(PROHIBITED_KEYWORDS)
