"""
SQL Safety Validator Module.
Enforces read-only SELECT-style queries and rejects destructive or
privilege-changing DDL/DML, catalog probing, or unknown-schema references.
"""
from __future__ import annotations

import re

# Strict blacklist of prohibited DDL, DML, and privilege-modifying keywords
PROHIBITED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "GRANT", "REVOKE", "COPY", "EXEC", "EXECUTE", "PG_SLEEP", "VACUUM",
    "REINDEX", "SET", "RESET", "LOCK", "CALL", "MERGE", "REFRESH",
    "LISTEN", "NOTIFY", "SECURITY", "DEFINER",
}

# Allowed starting statement keywords
ALLOWED_STARTS = {"SELECT", "WITH"}

# Only these schemas may be referenced by dotted identifiers.
ALLOWED_SCHEMAS = {"analytics", "public"}

# Substrings that must never appear (catalog / system probing)
PROHIBITED_SUBSTRINGS = ("pg_catalog", "information_schema", "pg_read_", "pg_ls_")

_DOTTED_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*")

# Canonical set of every table / view the analyst tools may reference.
# Anything else in a FROM/JOIN target is rejected with a helpful hint.
KNOWN_TABLES: set[str] = {
    # public schema — 8 raw dataset tables
    "public.customers", "public.geo_location", "public.orders",
    "public.order_items", "public.order_payments", "public.order_reviews",
    "public.products", "public.sellers",
    # analytics schema — curated views + ML output table
    "analytics.customer_segments",
    "analytics.v_order_summary",
    "analytics.v_executive_kpis",
    "analytics.v_monthly_sales",
    "analytics.v_category_performance",
    "analytics.v_product_performance",
    "analytics.v_customer_performance",
    "analytics.v_review_analytics",
    "analytics.v_delivery_performance",
    "analytics.v_monthly_delivery_performance",
}


def extract_table_refs(sql: str) -> list[str]:
    """Return unique schema-qualified table references from FROM/JOIN clauses."""
    refs: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_\.]*)", sql, re.IGNORECASE):
        ref = m.group(1)
        if "." in ref and ref.lower() not in seen:
            seen.add(ref.lower())
            refs.append(ref)
    return refs


def check_known_tables(sql: str, cte_names: set[str] | None = None) -> tuple[bool, str, list[str]]:
    """
    Ensure every schema.qualified table reference in FROM/JOIN is in KNOWN_TABLES.
    Returns (is_ok, offending_ref_or_empty, all_refs).

    Unqualified references (CTE aliases, bare table names) are ignored here —
    they are already constrained by the read-only user's SELECT grants.
    """
    cte_names = cte_names or set()
    refs = extract_table_refs(sql)
    for ref in refs:
        if ref.lower() in cte_names:
            continue
        if ref.lower() not in {t.lower() for t in KNOWN_TABLES}:
            return False, ref, refs
    return True, "", refs


def validate_sql(sql_query: str) -> tuple[bool, str]:
    """
    Validates a SQL query for safety before execution.
    Returns (is_valid, cleaned_sql_or_error_message).
    """
    if not sql_query or not sql_query.strip():
        return False, "SQL query cannot be empty"

    sql = sql_query.strip()

    # 1. Reject comment obfuscation
    if "--" in sql or "/*" in sql or "*/" in sql:
        return False, "SQL safety violation: Comments (-- or /* */) are prohibited"

    # 2. Reject multiple statements (semicolon check)
    stripped_semicolon = sql.rstrip(";").strip()
    if ";" in stripped_semicolon:
        return False, "SQL safety violation: Multiple SQL statements are prohibited"
    sql = stripped_semicolon

    # 3. Reject catalog / system-schema probing anywhere in the query
    lowered = sql.lower()
    for bad in PROHIBITED_SUBSTRINGS:
        if bad in lowered:
            return False, f"SQL safety violation: Reference to `{bad}` is prohibited"

    # 4. Tokens & starting keyword
    tokens = re.findall(r"\b[A-Za-z_]+\b", sql)
    if not tokens:
        return False, "SQL safety violation: Invalid SQL tokens"
    first_token = tokens[0].upper()
    if first_token not in ALLOWED_STARTS:
        return False, f"SQL safety violation: Query must start with SELECT or WITH (got {first_token})"

    # 5. Prohibited keywords
    upper_tokens = {t.upper() for t in tokens}
    violations = upper_tokens.intersection(PROHIBITED_KEYWORDS)
    if violations:
        return False, f"SQL safety violation: Prohibited SQL operations detected: {', '.join(sorted(violations))}"

    # 6. Every dotted schema reference must be in the allow-list
    for m in _DOTTED_REF_RE.finditer(sql):
        schema = m.group(1).lower()
        # `t.column` (CTE / table alias) is fine — those aren't listed schemas.
        # We only enforce when the prefix matches a real schema keyword pattern
        # by rejecting explicit disallowed schemas.
        if schema in {"pg_catalog", "information_schema"}:
            return False, f"SQL safety violation: Schema `{schema}` is not allowed"

    # 7. Require that any FROM/JOIN target either has no schema prefix (alias/CTE)
    #    or uses an allowed schema. This is best-effort and lenient about CTEs.
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_\.]*)", sql, re.IGNORECASE):
        ref = m.group(1)
        if "." in ref:
            schema = ref.split(".", 1)[0].lower()
            if schema not in ALLOWED_SCHEMAS:
                return False, f"SQL safety violation: Table reference `{ref}` uses disallowed schema `{schema}`"

    # 8. Enforce safety limit
    if "LIMIT" not in upper_tokens:
        sql += " LIMIT 100"

    return True, sql
