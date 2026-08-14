"""
SQL Safety Validator Module.
Enforces read-only SELECT-style queries and rejects destructive or privilege-changing DDL/DML.
"""
from __future__ import annotations

import re

# Strict blacklist of prohibited DDL, DML, and privilege-modifying keywords
PROHIBITED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "GRANT", "REVOKE", "COPY", "EXEC", "EXECUTE", "PG_SLEEP", "VACUUM",
    "REINDEX", "SET", "RESET", "LOCK", "CALL"
}

# Allowed starting statement keywords
ALLOWED_STARTS = {"SELECT", "WITH"}


def validate_sql(sql_query: str) -> tuple[bool, str]:
    """
    Validates a SQL query for safety before execution.
    Returns (is_valid, cleaned_sql_or_error_message).
    """
    if not sql_query or not sql_query.strip():
        return False, "SQL query cannot be empty"

    sql = sql_query.strip()
    
    # 1. Reject comment obfuscation (-- or /* ... */)
    if "--" in sql or "/*" in sql or "*/" in sql:
        return False, "SQL safety violation: Comments (-- or /* */) are prohibited"

    # 2. Reject multiple statements (semicolon check)
    stripped_semicolon = sql.rstrip(";").strip()
    if ";" in stripped_semicolon:
        return False, "SQL safety violation: Multiple SQL statements are prohibited"
        
    sql = stripped_semicolon

    # 3. Check starting keyword
    tokens = re.findall(r"\b[A-Za-z_]+\b", sql)
    if not tokens:
        return False, "SQL safety violation: Invalid SQL tokens"
        
    first_token = tokens[0].upper()
    if first_token not in ALLOWED_STARTS:
        return False, f"SQL safety violation: Query must start with SELECT or WITH (got {first_token})"

    # 4. Check prohibited keywords
    upper_tokens = {t.upper() for t in tokens}
    violations = upper_tokens.intersection(PROHIBITED_KEYWORDS)
    if violations:
        return False, f"SQL safety violation: Prohibited SQL operations detected: {', '.join(sorted(violations))}"

    # 5. Enforce safety limit (max 100 rows)
    if "LIMIT" not in upper_tokens:
        sql += " LIMIT 100"

    return True, sql
